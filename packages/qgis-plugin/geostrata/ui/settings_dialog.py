"""Settings dialog for managing multi-catalog manifest URLs, ELI toggle, and custom icons."""

from __future__ import annotations

from typing import List, Optional

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.db_client import SETTINGS_LANGUAGE_KEY
from ..core.sync_manager import DEFAULT_MANIFEST_URL, SyncManager
from .locator_filter import (
    SETTINGS_ICON_MODE_KEY,
    SETTINGS_INCLUDE_ELI_KEY,
    get_user_custom_icons_dir,
)


class GeostrataSettingsDialog(QDialog):
    """Dialog allowing users to manage catalogs, ELI layers, and icon appearance."""

    def __init__(
        self,
        sync_manager: SyncManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._sync_manager = sync_manager
        self.setWindowTitle("Geostrata — Paramètres, Options & Icônes")
        self.setMinimumSize(740, 520)
        self._build_ui()
        self._load_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        options_label = QLabel("<b>Options d'affichage et sources :</b>")
        layout.addWidget(options_label)

        form = QFormLayout()
        self._language_combo = QComboBox(self)
        self._language_combo.addItem(
            "Automatique (selon la langue de QGIS)",
            "auto",
        )
        self._language_combo.addItem(
            "English (Default)",
            "en",
        )
        self._language_combo.addItem(
            "Français (i18n.fr)",
            "fr",
        )
        form.addRow("Langue des couches :", self._language_combo)

        self._eli_checkbox = QCheckBox(
            "Inclure les couches OSM Editor Layer Index (ELI — orthophotos & cartes mondiales)",
            self,
        )
        form.addRow("Sources ELI :", self._eli_checkbox)

        icon_row = QHBoxLayout()
        self._icon_mode_combo = QComboBox(self)
        self._icon_mode_combo.addItem(
            "Icônes thématiques colorées Geostrata (Recommandé)",
            "custom",
        )
        self._icon_mode_combo.addItem(
            "Icônes techniques QGIS (Raster / Point / Ligne / Polygone)",
            "qgis",
        )
        open_icons_btn = QPushButton(
            "Ouvrir le dossier d'icônes personnalisées…", self
        )
        open_icons_btn.setToolTip(
            "Placez un fichier <categorie>.svg ou <id-couche>.svg dans ce dossier pour personnaliser n'importe quelle icône."
        )
        open_icons_btn.clicked.connect(self._on_open_custom_icons_dir)
        icon_row.addWidget(self._icon_mode_combo, stretch=1)
        icon_row.addWidget(open_icons_btn)
        form.addRow("Style des icônes :", icon_row)
        layout.addLayout(form)

        intro_label = QLabel(
            "<b>Catalogues configurés (manifest.json) :</b><br>"
            "Vous pouvez combiner le catalogue public Geostrata avec des "
            "catalogues privés d'équipe ou d'ONG (interrogés simultanément via ATTACH DATABASE)."
        )
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        self._url_list = QListWidget(self)
        layout.addWidget(self._url_list)

        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("Ajouter une URL manifest.json…", self)
        self._remove_btn = QPushButton("Supprimer la sélection", self)
        self._reset_btn = QPushButton("Réinitialiser par défaut", self)
        self._sync_btn = QPushButton("Synchroniser maintenant", self)

        self._add_btn.clicked.connect(self._on_add_url)
        self._remove_btn.clicked.connect(self._on_remove_url)
        self._reset_btn.clicked.connect(self._on_reset_defaults)
        self._sync_btn.clicked.connect(self._on_sync_now)

        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._remove_btn)
        btn_row.addWidget(self._reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._sync_btn)
        layout.addLayout(btn_row)

        status_label = QLabel("<b>État du cache SQLite local :</b>")
        layout.addWidget(status_label)

        self._status_table = QTableWidget(0, 5, self)
        self._status_table.setHorizontalHeaderLabels(
            ["URL", "En cache", "Sources", "Date de build", "SHA-256"]
        )
        header = self._status_table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._status_table)

        footer_row = QHBoxLayout()
        footer_row.addStretch()
        save_btn = QPushButton("Enregistrer et fermer", self)
        cancel_btn = QPushButton("Annuler", self)
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

        icon_mode = str(settings.value(SETTINGS_ICON_MODE_KEY, "custom") or "custom")
        idx = self._icon_mode_combo.findData(icon_mode)
        if idx >= 0:
            self._icon_mode_combo.setCurrentIndex(idx)

        self._url_list.clear()
        for url in self._sync_manager.get_manifest_urls():
            self._url_list.addItem(url)
        self._refresh_status_table()

    def _on_open_custom_icons_dir(self) -> None:
        icons_dir = get_user_custom_icons_dir()
        readme_path = icons_dir / "LISEZMOI.txt"
        if not readme_path.is_file():
            readme_path.write_text(
                "Personnalisation des icônes Geostrata\n"
                "=========================================\n"
                "Placez dans ce dossier vos propres fichiers .svg ou .png :\n"
                "- Par catégorie : basemap.svg, imagery.svg, elevation.svg, hydrography.svg,\n"
                "  admin.svg, buildings.svg, transport.svg, utilities.svg, landuse.svg, crisis.svg\n"
                "- Par identifiant de couche : ex. fr-grandlyon-bornes-fontaines.svg\n",
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
            "Ajouter un catalogue",
            "URL du fichier manifest.json :",
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
            "Synchronisation lancée en arrière-plan (QgsTask).",
        )
        self._refresh_status_table()

    def _on_save(self) -> None:
        settings = QgsSettings()
        settings.setValue(
            SETTINGS_LANGUAGE_KEY,
            str(self._language_combo.currentData() or "auto"),
        )
        settings.setValue(SETTINGS_INCLUDE_ELI_KEY, self._eli_checkbox.isChecked())
        settings.setValue(
            SETTINGS_ICON_MODE_KEY,
            str(self._icon_mode_combo.currentData() or "custom"),
        )
        self._sync_manager.set_manifest_urls(self._collect_urls_from_list())
        self.accept()
