"""Interactive dockable panel: ranked catalog list, details of the selected entry and coverage preview."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from qgis.PyQt.QtCore import Qt
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
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core.db_client import CatalogDatabaseClient, CatalogSourceRecord
from ..core.entry_details import build_details_html
from ..i18n import N_, tr, tr_n
from .actions import (
    capture_canvas_bbox_4326,
    country_display_name,
    load_entry,
    make_intersection_filter,
    read_include_eli_setting,
    show_entry_coverage,
)
from .coverage_preview import CoveragePreview
from .locator_filter import _resolve_entry_icon

MAX_DOCK_RESULTS = 120

# (category code, English label); labels are translated when the combo box is filled.
CATEGORIES: List[Tuple[str, str]] = [
    ("", N_("All categories")),
    ("basemap", N_("Basemaps")),
    ("imagery", N_("Imagery & orthophotos")),
    ("elevation", N_("Elevation, DEM & relief")),
    ("hydrography", N_("Water & hydrography")),
    ("admin", N_("Administrative boundaries & cadastre")),
    ("buildings", N_("Buildings & facilities")),
    ("transport", N_("Transport, roads, rail & cycling")),
    ("utilities", N_("Networks, energy & telecom")),
    ("landuse", N_("Land use, forests & geology")),
    ("crisis", N_("Crisis, rescue & humanitarian")),
    ("population", N_("Population & demography")),
    ("health", N_("Health facilities")),
    ("hazard", N_("Natural hazards & risks")),
    ("weather", N_("Weather & climate")),
]


class GeostrataDockWidget(QDockWidget):
    """Dockable panel listing matching layers, with details and footprint preview."""

    def __init__(
        self,
        iface: Any,
        db_client: CatalogDatabaseClient,
        parent: Optional[QWidget] = None,
        coverage_preview: Optional[CoveragePreview] = None,
    ) -> None:
        super().__init__("Geostrata", parent)
        self._iface = iface
        self._db_client = db_client
        self._current_records: List[CatalogSourceRecord] = []
        self._active_task: Optional[Any] = None
        # Shared with the locator filter when provided by the plugin; owned otherwise.
        self._owns_preview = coverage_preview is None
        self._coverage_preview = coverage_preview or CoveragePreview(iface, self)

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
            tr("Search (e.g. water, cadastre, hospital, lidar, cycling)…")
        )
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self.refresh_results)
        layout.addWidget(self._search_input)

        filter_row = QHBoxLayout()
        self._category_combo = QComboBox(container)
        for cat_code, cat_label in CATEGORIES:
            self._category_combo.addItem(tr(cat_label), cat_code)
        self._category_combo.currentIndexChanged.connect(self.refresh_results)
        filter_row.addWidget(self._category_combo, stretch=1)

        self._spatial_checkbox = QCheckBox(tr("Map extent"), container)
        self._spatial_checkbox.setChecked(True)
        self._spatial_checkbox.setToolTip(
            tr("Only list layers that intersect the current QGIS map extent")
        )
        self._spatial_checkbox.toggled.connect(self.refresh_results)
        filter_row.addWidget(self._spatial_checkbox)
        layout.addLayout(filter_row)

        self._count_label = QLabel("", container)
        layout.addWidget(self._count_label)

        splitter = QSplitter(Qt.Orientation.Vertical, container)

        self._results_list = QListWidget(splitter)
        self._results_list.currentRowChanged.connect(self._on_selection_changed)
        self._results_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        splitter.addWidget(self._results_list)

        self._details_view = QTextBrowser(splitter)
        self._details_view.setOpenExternalLinks(True)
        self._details_view.setMinimumHeight(110)
        splitter.addWidget(self._details_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, stretch=1)

        action_row = QHBoxLayout()
        self._stream_btn = QPushButton(tr("Load"), container)
        self._stream_btn.setToolTip(tr("Add the layer to QGIS (files are downloaded in the background)"))
        self._extract_btn = QPushButton(tr("Extract (.gpkg/.tif)"), container)
        self._extract_btn.setToolTip(
            tr("Clip the layer to the current map extent and save it locally")
        )
        self._zoom_btn = QPushButton(tr("Zoom to coverage"), container)
        self._zoom_btn.setToolTip(
            tr("Show where the selected layer has data and zoom the map to it")
        )

        self._stream_btn.clicked.connect(self._on_stream_clicked)
        self._extract_btn.clicked.connect(self._on_extract_clicked)
        self._zoom_btn.clicked.connect(self._on_zoom_to_entry_clicked)

        action_row.addWidget(self._stream_btn)
        action_row.addWidget(self._extract_btn)
        action_row.addWidget(self._zoom_btn)
        layout.addLayout(action_row)

        self.setWidget(container)
        self._update_details(None)

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
        """Remove any temporary coverage highlight from the map canvas."""
        self._coverage_preview.clear()

    def dispose(self) -> None:
        """Release canvas resources before the dock is deleted (plugin unload)."""
        try:
            if self._iface is not None and self._iface.mapCanvas() is not None:
                self._iface.mapCanvas().extentsChanged.disconnect(
                    self._on_canvas_extents_changed
                )
        except Exception:
            pass
        if self._owns_preview:
            self._coverage_preview.dispose()
        else:
            self._coverage_preview.clear()

    def closeEvent(self, event: Any) -> None:
        """Clear the coverage highlight when the dock panel is closed."""
        self.clear_rubber_band()
        super().closeEvent(event)

    def _update_details(self, entry: Optional[CatalogSourceRecord]) -> None:
        """Fill the details area and enable the action buttons for the selected entry."""
        has_entry = entry is not None
        for button in (self._stream_btn, self._extract_btn, self._zoom_btn):
            button.setEnabled(has_entry)
        if entry is None:
            self._details_view.setHtml(
                f"<p><i>{tr('Select a layer to see its details and coverage.')}</i></p>"
            )
            return
        self._details_view.setHtml(build_details_html(entry, country_display_name))

    def _on_selection_changed(self, row: int) -> None:
        if 0 <= row < len(self._current_records):
            entry = self._current_records[row]
            self._update_details(entry)
            # Same behaviour as the locator action "Show coverage on map", without messages.
            show_entry_coverage(
                self._iface, self._coverage_preview, entry, zoom=False, notify=False
            )
        else:
            self._update_details(None)
            self.clear_rubber_band()

    def _on_zoom_to_entry_clicked(self) -> None:
        entry = self._selected_record()
        if entry is None:
            return
        show_entry_coverage(self._iface, self._coverage_preview, entry, zoom=True)

    def refresh_results(self, *_: Any) -> None:
        """Query the ranked catalog and rebuild the list, keeping the selection when possible."""
        query_text = self._search_input.text().strip()
        selected_category = str(self._category_combo.currentData() or "")
        use_spatial = self._spatial_checkbox.isChecked()
        bbox_4326 = capture_canvas_bbox_4326(self._iface) if use_spatial else None

        previous = self._selected_record()
        previous_id = previous.id if previous is not None else None

        try:
            filtered = self._db_client.search_sources(
                raw_query=query_text,
                canvas_bbox_4326=bbox_4326,
                limit=MAX_DOCK_RESULTS,
                include_eli=read_include_eli_setting(),
                category=selected_category or None,
                accept=make_intersection_filter(bbox_4326),
            )
        except Exception:
            filtered = []

        global_badge = tr("Global")
        restored_row = -1

        self._results_list.blockSignals(True)
        try:
            self._current_records = filtered
            self._results_list.clear()
            for row, entry in enumerate(filtered):
                scope = entry.country_iso if entry.country_iso else global_badge
                title_str = entry.localized_name()
                item = QListWidgetItem(f"[{entry.type.upper()} · {scope}] {title_str}")
                icon = _resolve_entry_icon(entry)
                if icon is not None:
                    item.setIcon(icon)
                item.setToolTip(entry.localized_description() or title_str)
                self._results_list.addItem(item)
                if entry.id == previous_id:
                    restored_row = row
            if restored_row >= 0:
                self._results_list.setCurrentRow(restored_row)
        finally:
            self._results_list.blockSignals(False)

        if restored_row < 0:
            # The previous selection is gone: reset details and highlight.
            self._update_details(None)
            if previous_id is not None:
                self.clear_rubber_band()

        self._count_label.setText(
            tr_n("{count} layer available", "{count} layers available", len(filtered))
        )

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
        task = load_entry(self._iface, entry, offline_extract=offline_extract)
        if task is not None:
            self._active_task = task
