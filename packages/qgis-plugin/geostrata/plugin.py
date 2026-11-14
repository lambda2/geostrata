"""Main QGIS plugin class registering the QgsLocatorFilter, DockWidget, and menu actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Tuple

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .core.db_client import CatalogDatabaseClient
from .core.loader_task import cancel_active_file_load_tasks
from .core.sync_manager import SyncManager
from .i18n import N_, get_ui_language, tr
from .ui.browser_dock import GeostrataDockWidget
from .ui.coverage_preview import CoveragePreview
from .ui.locator_filter import GeostrataLocatorFilter
from .ui.message_tamer import MessageBarTamer
from .ui.settings_dialog import GeostrataSettingsDialog
from .ui.share_dialog import GeostrataShareDialog

MENU_NAME = "&Geostrata"

DOCK_ACTION_TEXT = N_("Geostrata — Layer browser")
DOCK_ACTION_TOOLTIP = N_(
    "Open the Geostrata layer browser panel (or press Ctrl+K and type gst)"
)
SETTINGS_ACTION_TEXT = N_("Settings & catalogs…")
SHARE_ACTION_TEXT = N_("Share a layer (generate YAML)…")
SYNC_ACTION_TEXT = N_("Synchronize the catalog now")


class GeostrataPlugin:
    """Geostrata QGIS Plugin lifecycle controller."""

    def __init__(self, iface: Any) -> None:
        self._iface = iface
        self._sync_manager: Optional[SyncManager] = None
        self._db_client: Optional[CatalogDatabaseClient] = None
        self._locator_filter: Optional[GeostrataLocatorFilter] = None
        self._dock_widget: Optional[GeostrataDockWidget] = None
        self._coverage_preview: Optional[CoveragePreview] = None
        self._message_tamer: Optional[MessageBarTamer] = None
        self._actions: List[QAction] = []
        # (action, English text, English tooltip or None) for retranslation.
        self._action_texts: List[Tuple[QAction, str, Optional[str]]] = []
        self._toolbar_action: Optional[QAction] = None
        self._ui_language: str = get_ui_language()

    def initGui(self) -> None:
        """Initialize locator filter, dock widget, menu actions, and background catalog sync."""
        self._ui_language = get_ui_language()
        self._sync_manager = SyncManager(
            on_sync_completed=self._on_catalogs_synced
        )
        initial_paths = self._sync_manager.get_cached_sqlite_paths()
        self._db_client = CatalogDatabaseClient(initial_paths)

        # Groups repeated provider warnings and hides network errors after a few seconds.
        self._message_tamer = MessageBarTamer(self._iface)

        # One footprint highlight shared by the locator and the dock.
        self._coverage_preview = CoveragePreview(self._iface)

        self._locator_filter = GeostrataLocatorFilter(
            self._iface,
            self._db_client,
            self._coverage_preview,
        )
        self._iface.registerLocatorFilter(self._locator_filter)

        main_window = self._iface.mainWindow()

        logo_path = (
            Path(__file__).resolve().parent
            / "assets"
            / "icons"
            / "geostrata.svg"
        )
        dock_icon = (
            QIcon(str(logo_path))
            if logo_path.is_file()
            else QgsApplication.getThemeIcon("/mActionAddLayer.svg")
        )
        dock_action = QAction(dock_icon, tr(DOCK_ACTION_TEXT), main_window)
        dock_action.setToolTip(tr(DOCK_ACTION_TOOLTIP))
        dock_action.triggered.connect(self._toggle_dock_widget)
        self._iface.addPluginToWebMenu(MENU_NAME, dock_action)
        self._iface.addWebToolBarIcon(dock_action)
        self._toolbar_action = dock_action
        self._actions.append(dock_action)
        self._action_texts.append((dock_action, DOCK_ACTION_TEXT, DOCK_ACTION_TOOLTIP))

        settings_action = QAction(tr(SETTINGS_ACTION_TEXT), main_window)
        settings_action.triggered.connect(self._open_settings_dialog)
        self._iface.addPluginToWebMenu(MENU_NAME, settings_action)
        self._actions.append(settings_action)
        self._action_texts.append((settings_action, SETTINGS_ACTION_TEXT, None))

        share_action = QAction(tr(SHARE_ACTION_TEXT), main_window)
        share_action.triggered.connect(self._open_share_dialog)
        self._iface.addPluginToWebMenu(MENU_NAME, share_action)
        self._actions.append(share_action)
        self._action_texts.append((share_action, SHARE_ACTION_TEXT, None))

        sync_action = QAction(tr(SYNC_ACTION_TEXT), main_window)
        sync_action.triggered.connect(self._trigger_manual_sync)
        self._iface.addPluginToWebMenu(MENU_NAME, sync_action)
        self._actions.append(sync_action)
        self._action_texts.append((sync_action, SYNC_ACTION_TEXT, None))

        # Trigger non-blocking background update on startup
        self._sync_manager.start_background_sync()

    def unload(self) -> None:
        """Deregister locator filter, dock widget, and remove plugin menu actions."""
        # Running downloads keep their partial file and resume on the next load.
        cancel_active_file_load_tasks()
        if getattr(self, "_message_tamer", None) is not None:
            self._message_tamer.dispose()
            self._message_tamer.deleteLater()
            self._message_tamer = None
        if self._locator_filter is not None:
            self._iface.deregisterLocatorFilter(self._locator_filter)
            self._locator_filter = None

        self._destroy_dock_widget()

        if self._coverage_preview is not None:
            self._coverage_preview.dispose()
            self._coverage_preview.deleteLater()
            self._coverage_preview = None

        if self._toolbar_action is not None:
            self._iface.removeWebToolBarIcon(self._toolbar_action)
            self._toolbar_action = None

        for action in self._actions:
            self._iface.removePluginWebMenu(MENU_NAME, action)
        self._actions.clear()
        self._action_texts.clear()

    def _destroy_dock_widget(self) -> None:
        if self._dock_widget is not None:
            self._dock_widget.dispose()
            self._iface.removeDockWidget(self._dock_widget)
            self._dock_widget.deleteLater()
            self._dock_widget = None

    def _create_dock_widget(self) -> None:
        if self._db_client is None:
            return
        self._dock_widget = GeostrataDockWidget(
            self._iface,
            self._db_client,
            parent=self._iface.mainWindow(),
            coverage_preview=self._coverage_preview,
        )
        self._iface.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self._dock_widget,
        )
        self._dock_widget.show()
        self._dock_widget.raise_()

    def _toggle_dock_widget(self) -> None:
        """Show or hide the dockable Geostrata explorer panel."""
        if self._db_client is None:
            return
        if self._dock_widget is None:
            self._create_dock_widget()
            return

        if self._dock_widget.isVisible():
            self._dock_widget.hide()
            self._dock_widget.clear_rubber_band()
        else:
            self._dock_widget.show()
            self._dock_widget.raise_()
            self._dock_widget.refresh_results()

    def _apply_language_change(self) -> None:
        """Retranslate menu entries and rebuild the dock after the language setting changed.

        The locator filter title (Ctrl+K settings list) is read once by QGIS and only
        follows after a plugin reload or a QGIS restart.
        """
        new_language = get_ui_language()
        if new_language == self._ui_language:
            return
        self._ui_language = new_language
        for action, text, tooltip in self._action_texts:
            action.setText(tr(text))
            if tooltip:
                action.setToolTip(tr(tooltip))
        if self._dock_widget is not None:
            was_visible = self._dock_widget.isVisible()
            self._destroy_dock_widget()
            if was_visible:
                self._create_dock_widget()

    def _on_catalogs_synced(self, updated_paths: List[Path]) -> None:
        """Update SQLite paths in CatalogDatabaseClient when sync finishes."""
        if self._db_client is not None:
            self._db_client.set_sqlite_paths(updated_paths)
        if self._dock_widget is not None and self._dock_widget.isVisible():
            self._dock_widget.refresh_results()

    def _open_settings_dialog(self) -> None:
        if self._sync_manager is None:
            return
        dlg = GeostrataSettingsDialog(
            self._sync_manager,
            parent=self._iface.mainWindow(),
        )
        if dlg.exec():
            if self._db_client is not None:
                self._db_client.set_sqlite_paths(
                    self._sync_manager.get_cached_sqlite_paths()
                )
            self._apply_language_change()
            if self._dock_widget is not None and self._dock_widget.isVisible():
                self._dock_widget.refresh_results()

    def _open_share_dialog(self) -> None:
        dlg = GeostrataShareDialog(parent=self._iface.mainWindow())
        dlg.exec()

    def _trigger_manual_sync(self) -> None:
        if self._sync_manager is not None:
            self._sync_manager.start_background_sync()
