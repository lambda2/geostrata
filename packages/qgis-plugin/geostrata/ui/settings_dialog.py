"""Settings dialog for managing multi-catalog manifest URLs, Overpass API servers, ELI toggle, and custom icons."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.db_client import SETTINGS_LANGUAGE_KEY
from ..core.file_cache import (
    BYTES_PER_GB,
    DEFAULT_MAX_CACHE_GB,
    SETTINGS_CACHE_DIR_KEY,
    SETTINGS_CACHE_MAX_GB_KEY,
    FileCache,
    configured_cache_dir,
    configured_max_bytes,
    default_cache_dir,
    format_size,
)
from ..core.loader_task import open_layer_sources
from ..i18n import tr, tr_n
from ..core.overpass_task import (
    DEFAULT_OVERPASS_SERVERS,
    get_configured_overpass_servers,
    normalize_overpass_endpoint,
    save_configured_overpass_servers,
)
from ..core.sync_manager import DEFAULT_MANIFEST_URL, SyncManager
from .message_tamer import SETTINGS_TAME_MESSAGES_KEY, tame_messages_enabled
from .locator_filter import (
    SETTINGS_ICON_MODE_KEY,
    SETTINGS_INCLUDE_ELI_KEY,
    get_user_custom_icons_dir,
)


class GeostrataSettingsDialog(QDialog):
    """Dialog allowing users to manage catalogs, Overpass servers, ELI layers, and icon appearance."""

    def __init__(
        self,
        sync_manager: SyncManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._sync_manager = sync_manager
        self._overpass_servers: List[Dict[str, Any]] = []
        self.setWindowTitle(tr("Geostrata — Settings, Overpass servers & catalogs"))
        self.setMinimumSize(820, 580)
        self._build_ui()
        self._load_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget(self)
        layout.addWidget(self._tabs, stretch=1)

        # TAB 1: Overpass API Servers (priority order)
        overpass_tab = QWidget(self)
        ovp_layout = QVBoxLayout(overpass_tab)

        ovp_intro = QLabel(
            tr(
                "<b>Overpass API servers (priority order):</b><br>"
                "Geostrata queries the checked servers from top to bottom (regional servers are "
                "skipped automatically when your map is outside their coverage) and falls back to the next one when a server is overloaded.<br>"
                "Double-click a cell to edit the name or the URL (e.g. to replace <code>YOUR_API_KEY</code>)."
            )
        )
        ovp_intro.setWordWrap(True)
        ovp_layout.addWidget(ovp_intro)

        self._ovp_table = QTableWidget(0, 4, overpass_tab)
        self._ovp_table.setHorizontalHeaderLabels(
            [
                tr("Enabled"),
                tr("Server name"),
                tr("Coverage"),
                tr("Endpoint URL (/api/interpreter)"),
            ]
        )
        self._ovp_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._ovp_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        ovp_header = self._ovp_table.horizontalHeader()
        if ovp_header is not None:
            ovp_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            ovp_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            ovp_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            ovp_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        ovp_layout.addWidget(self._ovp_table, stretch=1)

        ovp_btn_row = QHBoxLayout()
        self._ovp_up_btn = QPushButton(tr("↑ Move up (higher priority)"), overpass_tab)
        self._ovp_down_btn = QPushButton(tr("↓ Move down (lower priority)"), overpass_tab)
        self._ovp_add_btn = QPushButton(tr("Add an Overpass server…"), overpass_tab)
        self._ovp_del_btn = QPushButton(tr("Remove"), overpass_tab)
        self._ovp_reset_btn = QPushButton(
            tr("Reset (OpenStreetMap wiki list)"), overpass_tab
        )

        self._ovp_up_btn.clicked.connect(self._on_ovp_move_up)
        self._ovp_down_btn.clicked.connect(self._on_ovp_move_down)
        self._ovp_add_btn.clicked.connect(self._on_ovp_add)
        self._ovp_del_btn.clicked.connect(self._on_ovp_remove)
        self._ovp_reset_btn.clicked.connect(self._on_ovp_reset)

        ovp_btn_row.addWidget(self._ovp_up_btn)
        ovp_btn_row.addWidget(self._ovp_down_btn)
        ovp_btn_row.addWidget(self._ovp_add_btn)
        ovp_btn_row.addWidget(self._ovp_del_btn)
        ovp_btn_row.addStretch()
        ovp_btn_row.addWidget(self._ovp_reset_btn)
        ovp_layout.addLayout(ovp_btn_row)

        self._tabs.addTab(overpass_tab, tr("Overpass API servers"))

        # TAB 2: Options & Catalogs
        general_tab = QWidget(self)
        gen_layout = QVBoxLayout(general_tab)

        form = QFormLayout()
        self._language_combo = QComboBox(general_tab)
        self._language_combo.addItem(
            tr("Automatic (follow the QGIS language)"),
            "auto",
        )
        self._language_combo.addItem(
            "English",
            "en",
        )
        self._language_combo.addItem(
            "Français",
            "fr",
        )
        self._language_combo.setToolTip(
            tr(
                "Language of the Geostrata interface and of layer names and descriptions. "
                "Layers without a translation stay in English; the locator filter title "
                "follows after restarting QGIS."
            )
        )
        form.addRow(tr("Language:"), self._language_combo)

        self._eli_checkbox = QCheckBox(
            tr("Include OSM Editor Layer Index layers (ELI — imagery & maps worldwide)"),
            general_tab,
        )
        form.addRow(tr("ELI sources:"), self._eli_checkbox)

        self._tame_messages_checkbox = QCheckBox(
            tr("Group identical warnings and hide network errors after a few seconds"),
            general_tab,
        )
        self._tame_messages_checkbox.setToolTip(
            tr(
                "A slow or unreachable server makes QGIS repeat the same warning for every request. "
                "Every message stays available in the QGIS log panel."
            )
        )
        form.addRow(tr("Message bar:"), self._tame_messages_checkbox)

        icon_row = QHBoxLayout()
        self._icon_mode_combo = QComboBox(general_tab)
        self._icon_mode_combo.addItem(
            tr("Geostrata coloured thematic icons (recommended)"),
            "custom",
        )
        self._icon_mode_combo.addItem(
            tr("QGIS technical icons (raster / point / line / polygon)"),
            "qgis",
        )
        open_icons_btn = QPushButton(
            tr("Open the custom icons folder…"), general_tab
        )
        open_icons_btn.setToolTip(
            tr(
                "Put a <category>.svg or <layer-id>.svg file in this folder to override any icon."
            )
        )
        open_icons_btn.clicked.connect(self._on_open_custom_icons_dir)
        icon_row.addWidget(self._icon_mode_combo, stretch=1)
        icon_row.addWidget(open_icons_btn)
        form.addRow(tr("Icon style:"), icon_row)
        gen_layout.addLayout(form)

        intro_label = QLabel(
            tr(
                "<b>Configured catalogs (manifest.json):</b><br>"
                "You can combine the public Geostrata catalog with private team or NGO "
                "catalogs (all queried together through ATTACH DATABASE)."
            )
        )
        intro_label.setWordWrap(True)
        gen_layout.addWidget(intro_label)

        self._url_list = QListWidget(general_tab)
        gen_layout.addWidget(self._url_list)

        btn_row = QHBoxLayout()
        self._add_btn = QPushButton(tr("Add a manifest.json URL…"), general_tab)
        self._remove_btn = QPushButton(tr("Remove selected"), general_tab)
        self._reset_btn = QPushButton(tr("Reset to default"), general_tab)
        self._sync_btn = QPushButton(tr("Synchronize now"), general_tab)

        self._add_btn.clicked.connect(self._on_add_url)
        self._remove_btn.clicked.connect(self._on_remove_url)
        self._reset_btn.clicked.connect(self._on_reset_defaults)
        self._sync_btn.clicked.connect(self._on_sync_now)

        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._remove_btn)
        btn_row.addWidget(self._reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._sync_btn)
        gen_layout.addLayout(btn_row)

        status_label = QLabel(tr("<b>Local SQLite cache status:</b>"))
        gen_layout.addWidget(status_label)

        self._status_table = QTableWidget(0, 5, general_tab)
        self._status_table.setHorizontalHeaderLabels(
            ["URL", tr("Cached"), tr("Sources"), tr("Build date"), "SHA-256"]
        )
        header = self._status_table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        gen_layout.addWidget(self._status_table)

        self._tabs.addTab(general_tab, tr("Display & catalogs"))

        # TAB 3: downloaded files (local cache of ZIP archives and GeoJSON documents)
        cache_tab = QWidget(self)
        cache_layout = QVBoxLayout(cache_tab)

        cache_intro = QLabel(
            tr(
                "<b>Downloaded files:</b><br>"
                "ZIP archives and GeoJSON files are downloaded once, in the background, and "
                "kept in this folder: loading them again is immediate and works offline. "
                "Geostrata asks the server whether a file changed (at most once a day for "
                "archives) and downloads it again only then. When the folder exceeds the "
                "maximum size, the files that were not used for the longest time are removed."
            )
        )
        cache_intro.setWordWrap(True)
        cache_layout.addWidget(cache_intro)

        cache_form = QFormLayout()
        dir_row = QHBoxLayout()
        self._cache_dir_edit = QLineEdit(cache_tab)
        self._cache_dir_edit.setReadOnly(True)
        choose_dir_btn = QPushButton(tr("Choose…"), cache_tab)
        default_dir_btn = QPushButton(tr("Default folder"), cache_tab)
        open_dir_btn = QPushButton(tr("Open"), cache_tab)
        choose_dir_btn.clicked.connect(self._on_choose_cache_dir)
        default_dir_btn.clicked.connect(self._on_default_cache_dir)
        open_dir_btn.clicked.connect(self._on_open_cache_dir)
        dir_row.addWidget(self._cache_dir_edit, stretch=1)
        dir_row.addWidget(choose_dir_btn)
        dir_row.addWidget(default_dir_btn)
        dir_row.addWidget(open_dir_btn)
        cache_form.addRow(tr("Folder:"), dir_row)

        self._cache_max_spin = QDoubleSpinBox(cache_tab)
        self._cache_max_spin.setRange(1.0, 2000.0)
        self._cache_max_spin.setDecimals(0)
        self._cache_max_spin.setSuffix(tr(" GB"))
        cache_form.addRow(tr("Maximum size:"), self._cache_max_spin)

        usage_row = QHBoxLayout()
        self._cache_usage_label = QLabel("", cache_tab)
        self._cache_clear_btn = QPushButton(tr("Delete the downloaded files"), cache_tab)
        self._cache_clear_btn.setToolTip(
            tr(
                "Files used by a layer of the open project are kept. Layers of other "
                "projects that point to a deleted file will have to be loaded again."
            )
        )
        self._cache_clear_btn.clicked.connect(self._on_clear_cache)
        usage_row.addWidget(self._cache_usage_label, stretch=1)
        usage_row.addWidget(self._cache_clear_btn)
        cache_form.addRow(tr("Disk space used:"), usage_row)
        cache_layout.addLayout(cache_form)
        cache_layout.addStretch()

        self._tabs.addTab(cache_tab, tr("Downloaded files"))

        footer_row = QHBoxLayout()
        footer_row.addStretch()
        save_btn = QPushButton(tr("Save and close"), self)
        cancel_btn = QPushButton(tr("Cancel"), self)
        save_btn.clicked.connect(self._on_save)
        cancel_btn.clicked.connect(self.reject)
        footer_row.addWidget(cancel_btn)
        footer_row.addWidget(save_btn)
        layout.addLayout(footer_row)

    def _load_state(self) -> None:
        settings = QgsSettings()
        lang_mode = str(settings.value(SETTINGS_LANGUAGE_KEY, "auto") or "auto")
        lang_idx = self._language_combo.findData(lang_mode)
        if lang_idx >= 0:
            self._language_combo.setCurrentIndex(lang_idx)

        raw_eli = settings.value(SETTINGS_INCLUDE_ELI_KEY, True)
        include_eli = str(raw_eli).lower() not in ("false", "0", "no")
        self._eli_checkbox.setChecked(include_eli)
        self._tame_messages_checkbox.setChecked(tame_messages_enabled())

        icon_mode = str(settings.value(SETTINGS_ICON_MODE_KEY, "custom") or "custom")
        idx = self._icon_mode_combo.findData(icon_mode)
        if idx >= 0:
            self._icon_mode_combo.setCurrentIndex(idx)

        self._overpass_servers = get_configured_overpass_servers()
        self._populate_ovp_table()

        self._cache_dir_edit.setText(str(configured_cache_dir()))
        self._cache_max_spin.setValue(configured_max_bytes() / BYTES_PER_GB)
        self._refresh_cache_usage()

        self._url_list.clear()
        for url in self._sync_manager.get_manifest_urls():
            self._url_list.addItem(url)
        self._refresh_status_table()

    def _populate_ovp_table(self) -> None:
        self._ovp_table.setRowCount(len(self._overpass_servers))
        for row_idx, srv in enumerate(self._overpass_servers):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            chk_item.setCheckState(
                Qt.CheckState.Checked
                if srv.get("enabled", True)
                else Qt.CheckState.Unchecked
            )
            self._ovp_table.setItem(row_idx, 0, chk_item)

            name_item = QTableWidgetItem(str(srv.get("name") or ""))
            self._ovp_table.setItem(row_idx, 1, name_item)

            region_item = QTableWidgetItem(str(srv.get("region") or "Global"))
            region_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            self._ovp_table.setItem(row_idx, 2, region_item)

            url_item = QTableWidgetItem(str(srv.get("url") or ""))
            self._ovp_table.setItem(row_idx, 3, url_item)

    def _sync_ovp_list_from_table(self) -> None:
        updated: List[Dict[str, Any]] = []
        for row_idx in range(self._ovp_table.rowCount()):
            chk_item = self._ovp_table.item(row_idx, 0)
            name_item = self._ovp_table.item(row_idx, 1)
            region_item = self._ovp_table.item(row_idx, 2)
            url_item = self._ovp_table.item(row_idx, 3)

            enabled = (
                chk_item is not None
                and chk_item.checkState() == Qt.CheckState.Checked
            )
            name = name_item.text().strip() if name_item is not None else ""
            region = (
                region_item.text().strip() if region_item is not None else "Global"
            )
            url = (
                normalize_overpass_endpoint(url_item.text())
                if url_item is not None
                else ""
            )
            if not url:
                continue

            bbox = None
            if row_idx < len(self._overpass_servers):
                bbox = self._overpass_servers[row_idx].get("bbox")

            updated.append(
                {
                    "name": name or url,
                    "url": url,
                    "enabled": enabled,
                    "region": region or "Global",
                    "bbox": bbox,
                }
            )
        self._overpass_servers = updated

    def _on_ovp_move_up(self) -> None:
        self._sync_ovp_list_from_table()
        row = self._ovp_table.currentRow()
        if row > 0:
            self._overpass_servers[row - 1], self._overpass_servers[row] = (
                self._overpass_servers[row],
                self._overpass_servers[row - 1],
            )
            self._populate_ovp_table()
            self._ovp_table.selectRow(row - 1)

    def _on_ovp_move_down(self) -> None:
        self._sync_ovp_list_from_table()
        row = self._ovp_table.currentRow()
        if 0 <= row < len(self._overpass_servers) - 1:
            self._overpass_servers[row + 1], self._overpass_servers[row] = (
                self._overpass_servers[row],
                self._overpass_servers[row + 1],
            )
            self._populate_ovp_table()
            self._ovp_table.selectRow(row + 1)

    def _on_ovp_add(self) -> None:
        self._sync_ovp_list_from_table()
        url_text, ok = QInputDialog.getText(
            self,
            tr("Add an Overpass API server"),
            tr("Overpass server URL (e.g. https://my-server.org/api/interpreter):"),
        )
        if not ok or not url_text.strip():
            return
        normalized_url = normalize_overpass_endpoint(url_text)
        name_text, ok_name = QInputDialog.getText(
            self,
            tr("Overpass server name"),
            tr("Server name:"),
            text=normalized_url,
        )
        srv_name = name_text.strip() if (ok_name and name_text.strip()) else normalized_url
        # Insert at top of priority list so custom servers are used first
        self._overpass_servers.insert(
            0,
            {
                "name": srv_name,
                "url": normalized_url,
                "enabled": True,
                "region": "Global",
                "bbox": None,
            },
        )
        self._populate_ovp_table()
        self._ovp_table.selectRow(0)

    def _on_ovp_remove(self) -> None:
        self._sync_ovp_list_from_table()
        row = self._ovp_table.currentRow()
        if 0 <= row < len(self._overpass_servers):
            self._overpass_servers.pop(row)
            self._populate_ovp_table()

    def _on_ovp_reset(self) -> None:
        self._overpass_servers = [dict(s) for s in DEFAULT_OVERPASS_SERVERS]
        self._populate_ovp_table()

    def _on_open_custom_icons_dir(self) -> None:
        icons_dir = get_user_custom_icons_dir()
        readme_path = icons_dir / "README.txt"
        if not readme_path.is_file():
            readme_path.write_text(
                tr(
                    "Customizing Geostrata icons\n"
                    "===========================\n"
                    "Put your own .svg or .png files in this folder:\n"
                    "- Per category: basemap.svg, imagery.svg, elevation.svg, hydrography.svg,\n"
                    "  admin.svg, buildings.svg, transport.svg, utilities.svg, landuse.svg, crisis.svg\n"
                    "- Per layer identifier: e.g. fr-grandlyon-bornes-fontaines.svg\n"
                ),
                encoding="utf-8",
            )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(icons_dir)))

    def _refresh_status_table(self) -> None:
        statuses = self._sync_manager.get_cache_status()
        self._status_table.setRowCount(len(statuses))
        for row_idx, status in enumerate(statuses):
            self._status_table.setItem(
                row_idx, 0, QTableWidgetItem(status.get("url", ""))
            )
            self._status_table.setItem(
                row_idx, 1, QTableWidgetItem(status.get("cached", ""))
            )
            self._status_table.setItem(
                row_idx, 2, QTableWidgetItem(status.get("source_count", ""))
            )
            self._status_table.setItem(
                row_idx, 3, QTableWidgetItem(status.get("generated_at", ""))
            )
            self._status_table.setItem(
                row_idx, 4, QTableWidgetItem(status.get("sha256", ""))
            )

    def _collect_urls_from_list(self) -> List[str]:
        urls: List[str] = []
        for idx in range(self._url_list.count()):
            item = self._url_list.item(idx)
            if item is not None and item.text().strip():
                urls.append(item.text().strip())
        return urls

    def _on_add_url(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            tr("Add a catalog"),
            tr("URL of the manifest.json file:"),
        )
        if ok and text.strip():
            self._url_list.addItem(text.strip())

    def _on_remove_url(self) -> None:
        row = self._url_list.currentRow()
        if row >= 0:
            self._url_list.takeItem(row)

    def _on_reset_defaults(self) -> None:
        self._url_list.clear()
        self._url_list.addItem(DEFAULT_MANIFEST_URL)

    def _on_sync_now(self) -> None:
        self._sync_manager.set_manifest_urls(self._collect_urls_from_list())
        self._sync_manager.start_background_sync()
        QMessageBox.information(
            self,
            "Geostrata",
            tr("Synchronization started in the background."),
        )
        self._refresh_status_table()

    def _shown_file_cache(self) -> FileCache:
        """Return the cache of the folder currently displayed in the dialog."""
        return FileCache(
            Path(self._cache_dir_edit.text().strip() or str(default_cache_dir())),
            int(self._cache_max_spin.value() * BYTES_PER_GB),
        )

    def _refresh_cache_usage(self) -> None:
        cache = self._shown_file_cache()
        count = cache.entry_count()
        self._cache_usage_label.setText(
            tr_n(
                "{count} file, {size}",
                "{count} files, {size}",
                count,
                size=format_size(cache.total_size()),
            )
        )
        self._cache_clear_btn.setEnabled(cache.total_size() > 0)

    def _on_choose_cache_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            tr("Folder for downloaded files"),
            self._cache_dir_edit.text(),
        )
        if not chosen:
            return
        folder = Path(chosen)
        # Always a dedicated sub-folder: the cache never mixes with the user's files.
        if folder.name != "geostrata-files":
            folder = folder / "geostrata-files"
        self._cache_dir_edit.setText(str(folder))
        self._refresh_cache_usage()

    def _on_default_cache_dir(self) -> None:
        self._cache_dir_edit.setText(str(default_cache_dir()))
        self._refresh_cache_usage()

    def _on_open_cache_dir(self) -> None:
        folder = Path(self._cache_dir_edit.text().strip() or str(default_cache_dir()))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _on_clear_cache(self) -> None:
        cache = self._shown_file_cache()
        answer = QMessageBox.question(
            self,
            tr("Delete the downloaded files"),
            tr(
                "Delete {size} of downloaded files? They will be downloaded again the "
                "next time their layer is loaded.",
                size=format_size(cache.total_size()),
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        freed, kept = cache.clear(in_use=open_layer_sources())
        self._refresh_cache_usage()
        if kept:
            QMessageBox.information(
                self,
                tr("Delete the downloaded files"),
                tr(
                    "{freed} deleted. {kept} kept: used by a layer of the open project "
                    "or being downloaded.",
                    freed=format_size(freed),
                    kept=format_size(kept),
                ),
            )

    def _on_save(self) -> None:
        self._sync_ovp_list_from_table()
        save_configured_overpass_servers(self._overpass_servers)

        settings = QgsSettings()
        settings.setValue(
            SETTINGS_LANGUAGE_KEY,
            str(self._language_combo.currentData() or "auto"),
        )
        settings.setValue(SETTINGS_INCLUDE_ELI_KEY, self._eli_checkbox.isChecked())
        settings.setValue(
            SETTINGS_TAME_MESSAGES_KEY,
            self._tame_messages_checkbox.isChecked(),
        )
        settings.setValue(
            SETTINGS_ICON_MODE_KEY,
            str(self._icon_mode_combo.currentData() or "custom"),
        )
        cache_dir = self._cache_dir_edit.text().strip()
        if not cache_dir or Path(cache_dir) == default_cache_dir():
            settings.remove(SETTINGS_CACHE_DIR_KEY)
        else:
            settings.setValue(SETTINGS_CACHE_DIR_KEY, cache_dir)
        max_gb = int(self._cache_max_spin.value())
        if max_gb == DEFAULT_MAX_CACHE_GB:
            settings.remove(SETTINGS_CACHE_MAX_GB_KEY)
        else:
            settings.setValue(SETTINGS_CACHE_MAX_GB_KEY, max_gb)
        self._sync_manager.set_manifest_urls(self._collect_urls_from_list())
        self.accept()
