"""Main QGIS plugin class registering the QgsLocatorFilter, DockWidget, and menu actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .core.db_client import CatalogDatabaseClient
from .core.sync_manager import SyncManager
from .ui.browser_dock import GeostrataDockWidget
from .ui.locator_filter import GeostrataLocatorFilter
from .ui.settings_dialog import GeostrataSettingsDialog
from .ui.share_dialog import GeostrataShareDialog

MENU_NAME = "&Geostrata"


class GeostrataPlugin:
    """Geostrata QGIS Plugin lifecycle controller."""

    def __init__(self, iface: Any) -> None:
        self._iface = iface
        self._sync_manager: Optional[SyncManager] = None
        self._db_client: Optional[CatalogDatabaseClient] = None
        self._locator_filter: Optional[GeostrataLocatorFilter] = None
        self._dock_widget: Optional[GeostrataDockWidget] = None
        self._actions: List[QAction] = []
        self._toolbar_action: Optional[QAction] = None

    def initGui(self) -> None:
        """Initialize locator filter, dock widget, menu actions, and background catalog sync."""
        self._sync_manager = SyncManager(
            on_sync_completed=self._on_catalogs_synced
        )
        initial_paths = self._sync_manager.get_cached_sqlite_paths()
        self._db_client = CatalogDatabaseClient(initial_paths)

        self._locator_filter = GeostrataLocatorFilter(
            self._iface,
            self._db_client,
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
        dock_action = QAction(
            dock_icon,
            "Geostrata — Explorateur de couches",
            main_window,
        )
        dock_action.setToolTip(
            "Ouvrir le panneau d'exploration spatialisé Geostrata (ou utilisez Ctrl+K -> gst)"
        )
        dock_action.triggered.connect(self._toggle_dock_widget)
        self._iface.addPluginToWebMenu(MENU_NAME, dock_action)
        self._iface.addWebToolBarIcon(dock_action)
        self._toolbar_action = dock_action
        self._actions.append(dock_action)

        settings_action = QAction(
            "Paramètres & Catalogues…",
            main_window,
        )
        settings_action.triggered.connect(self._open_settings_dialog)
        self._iface.addPluginToWebMenu(MENU_NAME, settings_action)
        self._actions.append(settings_action)

        share_action = QAction(
            "Partager une couche (Générer YAML)…",
            main_window,
        )
        share_action.triggered.connect(self._open_share_dialog)
        self._iface.addPluginToWebMenu(MENU_NAME, share_action)
        self._actions.append(share_action)

        sync_action = QAction(
            "Synchroniser le catalogue maintenant",
            main_window,
        )
        sync_action.triggered.connect(self._trigger_manual_sync)
        self._iface.addPluginToWebMenu(MENU_NAME, sync_action)
        self._actions.append(sync_action)

        # Trigger non-blocking background update on startup
        self._sync_manager.start_background_sync()

    def unload(self) -> None:
        """Deregister locator filter, dock widget, and remove plugin menu actions."""
        if self._locator_filter is not None:
            self._iface.deregisterLocatorFilter(self._locator_filter)
            self._locator_filter = None

        if self._dock_widget is not None:
            self._iface.removeDockWidget(self._dock_widget)
            self._dock_widget.deleteLater()
            self._dock_widget = None

        if self._toolbar_action is not None:
            self._iface.removeWebToolBarIcon(self._toolbar_action)
            self._toolbar_action = None

        for action in self._actions:
            self._iface.removePluginWebMenu(MENU_NAME, action)
        self._actions.clear()

    def _toggle_dock_widget(self) -> None:
        """Show or hide the dockable Geostrata explorer panel."""
        if self._db_client is None:
            return
        if self._dock_widget is None:
            self._dock_widget = GeostrataDockWidget(
                self._iface,
                self._db_client,
                parent=self._iface.mainWindow(),
            )
            self._iface.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea,
                self._dock_widget,
            )
            self._dock_widget.show()
            self._dock_widget.raise_()
            self._dock_widget.refresh_results()
            return

        if self._dock_widget.isVisible():
            self._dock_widget.hide()
        else:
            self._dock_widget.show()
            self._dock_widget.raise_()
            self._dock_widget.refresh_results()

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
            if self._dock_widget is not None and self._dock_widget.isVisible():
                self._dock_widget.refresh_results()

    def _open_share_dialog(self) -> None:
        dlg = GeostrataShareDialog(parent=self._iface.mainWindow())
        dlg.exec()

    def _trigger_manual_sync(self) -> None:
        if self._sync_manager is not None:
            self._sync_manager.start_background_sync()
