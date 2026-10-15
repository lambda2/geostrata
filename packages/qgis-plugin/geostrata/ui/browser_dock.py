"""Interactive dockable panel for browsing Geostrata with RubberBand preview and extent zoom."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsMessageLog,
    QgsProject,
    QgsRectangle,
    QgsSettings,
    QgsWkbTypes,
)
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import QTimer, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.db_client import CatalogDatabaseClient, CatalogSourceRecord
from ..core.extractor_task import launch_offline_extraction_task
from ..core.overpass_task import launch_overpass_fetch_task
from ..core.uri_builder import create_qgis_layer
from .locator_filter import SETTINGS_INCLUDE_ELI_KEY, _resolve_entry_icon

CATEGORIES: List[Tuple[str, str]] = [
    ("", "Toutes les catégories"),
    ("basemap", "Fonds de carte (basemap)"),
    ("imagery", "Imagerie & Orthophotos (imagery)"),
    ("elevation", "Altitude, MNT & Relief (elevation)"),
    ("hydrography", "Eau & Hydrographie (hydrography)"),
    ("admin", "Limites administratives & Cadastre (admin)"),
    ("buildings", "Bâtiments & Équipements (buildings)"),
    ("transport", "Transport, Routes, Rail & Vélo (transport)"),
    ("utilities", "Réseaux, Énergie & Télécom (utilities)"),
    ("landuse", "Occupation du sol, Forêts & Géologie (landuse)"),
    ("crisis", "Crise, Santé, Secours & Humanitaire (crisis)"),
]

DENSE_WFS_KEYWORDS: Tuple[str, ...] = (
    "batiment",
    "parcelle",
    "troncon_de_route",
    "zone_de_vegetation",
)


class GeostrataDockWidget(QDockWidget):
    """Dockable panel that dynamically updates matching layers and previews extents."""

    def __init__(
        self,
        iface: Any,
        db_client: CatalogDatabaseClient,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__("Geostrata", parent)
        self._iface = iface
        self._db_client = db_client
        self._current_records: List[CatalogSourceRecord] = []
        self._active_task: Optional[Any] = None
        self._rubber_band: Optional[QgsRubberBand] = None
        self._rubber_timer = QTimer(self)
        self._rubber_timer.setSingleShot(True)
        self._rubber_timer.timeout.connect(self.clear_rubber_band)

        self.setObjectName("GeostrataDockWidget")
        self._build_ui()
        self._connect_canvas()
        self.refresh_results()

    def _build_ui(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._search_input = QLineEdit(container)
        self._search_input.setPlaceholderText(
            "Rechercher (ex: eau, cadastre, hopital, lidar, velo)…"
        )
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self.refresh_results)
        layout.addWidget(self._search_input)

        filter_row = QHBoxLayout()
        self._category_combo = QComboBox(container)
        for cat_code, cat_label in CATEGORIES:
            self._category_combo.addItem(cat_label, cat_code)
        self._category_combo.currentIndexChanged.connect(self.refresh_results)
        filter_row.addWidget(self._category_combo, stretch=1)

        self._spatial_checkbox = QCheckBox("Emprise carte", container)
        self._spatial_checkbox.setChecked(True)
        self._spatial_checkbox.setToolTip(
            "Afficher uniquement les couches qui intersectent l'emprise actuelle du canevas QGIS"
        )
        self._spatial_checkbox.toggled.connect(self.refresh_results)
        filter_row.addWidget(self._spatial_checkbox)
        layout.addLayout(filter_row)

        self._count_label = QLabel("0 couche disponible", container)
        layout.addWidget(self._count_label)

        self._results_list = QListWidget(container)
        self._results_list.currentRowChanged.connect(self._on_selection_changed)
        self._results_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._results_list, stretch=1)

        action_row = QHBoxLayout()
        self._stream_btn = QPushButton("Streaming", container)
        self._stream_btn.setToolTip("Charger le flux en direct dans QGIS")
        self._extract_btn = QPushButton("Extraire (.gpkg/.tif)", container)
        self._extract_btn.setToolTip(
            "Découper et sauvegarder hors-ligne sur l'emprise actuelle"
        )
        self._zoom_btn = QPushButton("Zoomer emprise", container)
        self._zoom_btn.setToolTip(
            "Centrer la carte QGIS sur la zone de couverture de la source sélectionnée"
        )

        self._stream_btn.clicked.connect(self._on_stream_clicked)
        self._extract_btn.clicked.connect(self._on_extract_clicked)
        self._zoom_btn.clicked.connect(self._on_zoom_to_entry_clicked)

        action_row.addWidget(self._stream_btn)
        action_row.addWidget(self._extract_btn)
        action_row.addWidget(self._zoom_btn)
        layout.addLayout(action_row)

        self.setWidget(container)

    def _connect_canvas(self) -> None:
        if self._iface is not None and self._iface.mapCanvas() is not None:
            try:
                self._iface.mapCanvas().extentsChanged.connect(
                    self._on_canvas_extents_changed
                )
            except Exception:
                pass

    def _on_canvas_extents_changed(self) -> None:
        if self.isVisible() and self._spatial_checkbox.isChecked():
            self.refresh_results()

    def clear_rubber_band(self) -> None:
        """Remove any temporary extent highlight from the map canvas."""
        if self._rubber_band is not None:
            try:
                self._rubber_band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
            except Exception:
                pass

    def closeEvent(self, event: Any) -> None:
        """Clear RubberBand when the dock panel is closed."""
        self.clear_rubber_band()
        super().closeEvent(event)

    def _preview_entry_footprint(self, entry: CatalogSourceRecord) -> None:
        """Highlight the selected source's WKT geometry on the QGIS map canvas."""
        self.clear_rubber_band()
        if (
            self._iface is None
            or self._iface.mapCanvas() is None
            or entry.area_deg2 >= 20000.0
            or not entry.geom_wkt
        ):
            return

        try:
            canvas = self._iface.mapCanvas()
            geom = QgsGeometry.fromWkt(entry.geom_wkt)
            if geom.isNull():
                return

            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            dest_crs = canvas.mapSettings().destinationCrs()
            if dest_crs.isValid() and dest_crs != wgs84:
                transform = QgsCoordinateTransform(
                    wgs84, dest_crs, QgsProject.instance()
                )
                geom.transform(transform)

            if self._rubber_band is None:
                self._rubber_band = QgsRubberBand(
                    canvas, QgsWkbTypes.GeometryType.PolygonGeometry
                )
                self._rubber_band.setColor(QColor(0, 140, 255, 190))
                self._rubber_band.setFillColor(QColor(0, 140, 255, 35))
                self._rubber_band.setWidth(2)

            self._rubber_band.setToGeometry(geom, None)
            self._rubber_timer.start(2500)
        except Exception:
            pass

    def _on_selection_changed(self, row: int) -> None:
        if 0 <= row < len(self._current_records):
            self._preview_entry_footprint(self._current_records[row])
        else:
            self.clear_rubber_band()

    def _on_zoom_to_entry_clicked(self) -> None:
        entry = self._selected_record()
        if (
            entry is None
            or self._iface is None
            or self._iface.mapCanvas() is None
        ):
            return

        try:
            canvas = self._iface.mapCanvas()
            rect = QgsRectangle(
                entry.min_x, entry.min_y, entry.max_x, entry.max_y
            )
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            dest_crs = canvas.mapSettings().destinationCrs()
            if dest_crs.isValid() and dest_crs != wgs84:
                transform = QgsCoordinateTransform(
                    wgs84, dest_crs, QgsProject.instance()
                )
                rect = transform.transformBoundingBox(rect)

            rect.scale(1.08)
            canvas.setExtent(rect)
            canvas.refresh()
            self._preview_entry_footprint(entry)
        except Exception:
            pass

    def _capture_canvas_bbox_4326(
        self,
    ) -> Optional[Tuple[float, float, float, float]]:
        if self._iface is None or self._iface.mapCanvas() is None:
            return None
        try:
            canvas = self._iface.mapCanvas()
            extent: QgsRectangle = canvas.extent()
            if extent.isEmpty() or not extent.isFinite():
                return None

            src_crs = canvas.mapSettings().destinationCrs()
            wgs84_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            if src_crs.isValid() and src_crs != wgs84_crs:
                transform = QgsCoordinateTransform(
                    src_crs, wgs84_crs, QgsProject.instance()
                )
                extent = transform.transformBoundingBox(extent)

            min_x = max(-180.0, min(180.0, extent.xMinimum()))
            min_y = max(-90.0, min(90.0, extent.yMinimum()))
            max_x = max(-180.0, min(180.0, extent.xMaximum()))
            max_y = max(-90.0, min(90.0, extent.yMaximum()))
            if min_x > max_x or min_y > max_y:
                return None
            return (min_x, min_y, max_x, max_y)
        except Exception:
            return None

    def refresh_results(self, *_: Any) -> None:
        """Query SQLite and update the list widget."""
        query_text = self._search_input.text().strip()
        selected_category = str(self._category_combo.currentData() or "")
        use_spatial = self._spatial_checkbox.isChecked()
        bbox_4326 = self._capture_canvas_bbox_4326() if use_spatial else None

        candidates = self._db_client.search_sources(
            raw_query=query_text,
            canvas_bbox_4326=bbox_4326,
            limit=120,
        )

        canvas_geom: Optional[QgsGeometry] = None
        if bbox_4326 is not None:
            canvas_geom = QgsGeometry.fromRect(QgsRectangle(*bbox_4326))

        include_eli = True
        try:
            raw_eli = QgsSettings().value(SETTINGS_INCLUDE_ELI_KEY, True)
            include_eli = str(raw_eli).lower() not in ("false", "0", "no")
        except Exception:
            include_eli = True

        filtered: List[CatalogSourceRecord] = []
        for entry in candidates:
            if not include_eli and "-eli-" in entry.id:
                continue
            if selected_category and entry.category != selected_category:
                continue
            if canvas_geom is not None and entry.geom_wkt:
                source_geom = QgsGeometry.fromWkt(entry.geom_wkt)
                if not source_geom.isNull() and not canvas_geom.intersects(
                    source_geom
                ):
                    continue
            filtered.append(entry)

        self._current_records = filtered
        self._results_list.clear()

        for entry in filtered:
            scope = entry.country_iso if entry.country_iso else "Global"
            label = f"[{entry.type.upper()} · {scope}] {entry.name}"
            item = QListWidgetItem(label)
            icon = _resolve_entry_icon(entry)
            if icon is not None:
                item.setIcon(icon)
            tooltip = (
                f"<b>{entry.name}</b><br>"
                f"<i>{entry.description}</i><br>"
                f"<b>Type :</b> {entry.type.upper()} | <b>Catégorie :</b> {entry.category}<br>"
                f"<b>CRS :</b> {entry.crs} | <b>Licence :</b> {entry.license}<br>"
                f"<b>Attribution :</b> {entry.attribution}"
            )
            item.setToolTip(tooltip)
            self._results_list.addItem(item)

        count = len(filtered)
        plural = "s" if count > 1 else ""
        self._count_label.setText(f"{count} couche{plural} disponible{plural}")

    def _selected_record(self) -> Optional[CatalogSourceRecord]:
        row = self._results_list.currentRow()
        if 0 <= row < len(self._current_records):
            return self._current_records[row]
        return None

    def _on_item_double_clicked(self, *_: Any) -> None:
        self._load_selected(offline_extract=False)

    def _on_stream_clicked(self) -> None:
        self._load_selected(offline_extract=False)

    def _on_extract_clicked(self) -> None:
        self._load_selected(offline_extract=True)

    def _load_selected(self, offline_extract: bool) -> None:
        entry = self._selected_record()
        if entry is None:
            return

        bbox_4326 = self._capture_canvas_bbox_4326() or (
            entry.min_x,
            entry.min_y,
            entry.max_x,
            entry.max_y,
        )

        if entry.type.lower() == "overpass":
            self._active_task = launch_overpass_fetch_task(
                self._iface,
                entry,
                bbox_4326,
                is_offline_extract=offline_extract,
            )
            return

        if offline_extract:
            self._active_task = launch_offline_extraction_task(
                self._iface,
                entry,
                bbox_4326,
            )
            return

        is_dense_wfs = entry.type.lower() == "wfs" and any(
            kw in entry.url.lower() for kw in DENSE_WFS_KEYWORDS
        )
        canvas_area = (bbox_4326[2] - bbox_4326[0]) * (
            bbox_4326[3] - bbox_4326[1]
        )

        layer, spec = create_qgis_layer(entry, bbox_4326)
        if layer is not None and layer.isValid():
            if is_dense_wfs and canvas_area > 0.25:
                try:
                    layer.setScaleBasedVisibility(True)
                    layer.setMinimumScale(50000.0)
                except Exception:
                    pass
                if self._iface is not None and self._iface.messageBar() is not None:
                    self._iface.messageBar().pushMessage(
                        "Geostrata",
                        f"Couche WFS dense ajoutée ({entry.name}) : zoomez à 1:50 000 pour afficher les entités.",
                        level=Qgis.MessageLevel.Info,
                        duration=6,
                    )
            else:
                if self._iface is not None and self._iface.messageBar() is not None:
                    self._iface.messageBar().pushMessage(
                        "Geostrata",
                        f"Couche chargée : {entry.name}",
                        level=Qgis.MessageLevel.Success,
                        duration=4,
                    )
            QgsProject.instance().addMapLayer(layer)
        else:
            err_msg = (
                f"Impossible de charger '{entry.name}' "
                f"(provider={spec.provider}, uri={spec.uri})"
            )
            QgsMessageLog.logMessage(
                err_msg, "Geostrata", Qgis.MessageLevel.Warning
            )
            if self._iface is not None and self._iface.messageBar() is not None:
                self._iface.messageBar().pushMessage(
                    "Geostrata",
                    err_msg,
                    level=Qgis.MessageLevel.Warning,
                    duration=6,
                )
