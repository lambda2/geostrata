"""QgsLocatorFilter implementation (Ctrl+K) with 2-step spatial + FTS5 filtering."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from qgis.core import (
    QgsApplication,
    QgsFeedback,
    QgsLocatorContext,
    QgsLocatorFilter,
    QgsLocatorResult,
    QgsSettings,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QApplication

from ..core.db_client import (
    GLOBAL_GROUP_AREA_DEG2,
    CatalogDatabaseClient,
    CatalogSourceRecord,
    global_group_leads,
    parse_search_query,
)
from ..i18n import tr
from .actions import (  # noqa: F401  (SETTINGS_INCLUDE_ELI_KEY re-exported for the settings dialog)
    SETTINGS_INCLUDE_ELI_KEY,
    capture_canvas_bbox_4326,
    load_entry,
    make_intersection_filter,
    read_include_eli_setting,
    show_entry_coverage,
)
from .coverage_preview import CoveragePreview

ACTION_STREAM_ID = 1
ACTION_EXTRACT_OFFLINE_ID = 2
ACTION_SHOW_COVERAGE_ID = 3
ACTION_ZOOM_TO_COVERAGE_ID = 4

MAX_LOCATOR_RESULTS = 50

SETTINGS_ICON_MODE_KEY = "geostrata/icon_mode"


def get_user_custom_icons_dir() -> Path:
    """Return user directory where custom .svg/.png icons can be placed."""
    try:
        base_dir = Path(QgsApplication.qgisSettingsDirPath())
    except Exception:
        base_dir = Path.home() / ".geostrata"
    icons_dir = base_dir / "geostrata" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    return icons_dir


def _resolve_entry_icon(entry: CatalogSourceRecord) -> Any:
    """Resolve custom SVG icon, user override icon, or technical QGIS layer icon."""
    try:
        # 1. Check user custom override directory (<id>.svg/.png or <category>.svg/.png)
        user_icons_dir = get_user_custom_icons_dir()
        for stem in (entry.id, entry.category, entry.type):
            for ext in ("svg", "png"):
                custom_candidate = user_icons_dir / f"{stem}.{ext}"
                if custom_candidate.is_file():
                    return QIcon(str(custom_candidate))

        # 2. Check configured icon mode ('custom' thematic SVG vs 'qgis' technical)
        icon_mode = "custom"
        try:
            icon_mode = str(
                QgsSettings().value(SETTINGS_ICON_MODE_KEY, "custom") or "custom"
            )
        except Exception:
            icon_mode = "custom"

        if icon_mode != "qgis":
            bundled_icons_dir = (
                Path(__file__).resolve().parent.parent / "assets" / "icons"
            )
            cat_svg = bundled_icons_dir / f"{entry.category}.svg"
            if cat_svg.is_file():
                return QIcon(str(cat_svg))
            fallback_svg = bundled_icons_dir / "geostrata.svg"
            if fallback_svg.is_file():
                return QIcon(str(fallback_svg))

        # 3. Technical QGIS icons
        src_type = entry.type.lower()
        if src_type in ("xyz", "tms", "wms", "wmts", "cog", "vrt"):
            return QgsApplication.getThemeIcon("/mIconRaster.svg")
        if src_type == "vectortile":
            return QgsApplication.getThemeIcon("/mIconVectorTileLayer.svg")
        if entry.style_qml:
            if 'symbol type="line"' in entry.style_qml:
                return QgsApplication.getThemeIcon("/mIconLineLayer.svg")
            if 'symbol type="fill"' in entry.style_qml:
                return QgsApplication.getThemeIcon("/mIconPolygonLayer.svg")
            if 'symbol type="marker"' in entry.style_qml:
                return QgsApplication.getThemeIcon("/mIconPointLayer.svg")
        if entry.category in ("admin", "buildings", "landuse"):
            return QgsApplication.getThemeIcon("/mIconPolygonLayer.svg")
        if entry.category in ("transport", "utilities"):
            return QgsApplication.getThemeIcon("/mIconLineLayer.svg")
        return QgsApplication.getThemeIcon("/mIconPointLayer.svg")
    except Exception:
        return None


class GeostrataLocatorFilter(QgsLocatorFilter):
    """Universal search bar filter (Ctrl+K) for Geostrata."""

    def __init__(
        self,
        iface: Any,
        db_client: CatalogDatabaseClient,
        coverage_preview: Optional[CoveragePreview] = None,
    ) -> None:
        super().__init__()
        self._iface = iface
        self._db_client = db_client
        self._coverage_preview = coverage_preview
        self._prepared_bbox_4326: Optional[Tuple[float, float, float, float]] = None
        self._active_extraction_task: Optional[Any] = None
        self._emitted_results: List[QgsLocatorResult] = []

    def name(self) -> str:
        """Return unique internal filter identifier."""
        return "geostrata_filter"

    def displayName(self) -> str:
        """Return user-visible filter title in Ctrl+K."""
        return tr("Geostrata (local & global layers)")

    def prefix(self) -> str:
        """Return filter prefix shortcut (minimum 3 chars in QGIS)."""
        return "gst"

    def priority(self) -> QgsLocatorFilter.Priority:
        """Return high priority so spatial layers appear at the top of Ctrl+K."""
        return QgsLocatorFilter.Priority.High

    def clone(self) -> GeostrataLocatorFilter:
        """Return a thread-safe clone of this filter for worker threads."""
        cloned = GeostrataLocatorFilter(
            self._iface, self._db_client, self._coverage_preview
        )
        cloned._prepared_bbox_4326 = self._prepared_bbox_4326
        return cloned

    def prepare(self, string: str, context: QgsLocatorContext) -> List[str]:
        """Capture and reproject current map canvas extent on the main UI thread."""
        self._prepared_bbox_4326 = capture_canvas_bbox_4326(self._iface)
        return []

    def fetchResults(
        self,
        string: str,
        context: QgsLocatorContext,
        feedback: QgsFeedback,
    ) -> None:
        """Run the ranked catalog search and emit one locator result per source.

        Runs on a worker thread. The SQLite client returns candidates already ranked
        (text relevance + locality + priority, see core/db_client.py) and applies the
        ELI filter and the exact WKT/canvas intersection test *before* its limit.
        """
        query_text = string.strip()
        if not context.usingPrefix and len(query_text) < 2:
            return

        if feedback.isCanceled():
            return

        _, is_global = parse_search_query(query_text)
        bbox_4326 = None if is_global else self._prepared_bbox_4326

        scored_entries = self._db_client.search_sources_scored(
            raw_query=query_text,
            canvas_bbox_4326=bbox_4326,
            limit=MAX_LOCATOR_RESULTS,
            include_eli=read_include_eli_setting(),
            accept=make_intersection_filter(bbox_4326, feedback.isCanceled),
        )

        if feedback.isCanceled() or not scored_entries:
            return

        self._emitted_results.clear()

        # Translate once: tr() reads QgsSettings.
        global_badge = tr("Global")
        hint = tr("Enter: load · Shift+Enter: extract offline")
        degraded_note = tr("link degraded")
        # Groups are sorted by groupScore (QGIS >= 3.40), then by name: hence the numbers.
        global_first = global_group_leads(scored_entries)
        local_group = (
            tr("2. Local & national layers")
            if global_first
            else tr("1. Local & national layers")
        )
        global_group = tr("1. Global layers") if global_first else tr("2. Global layers")
        action_labels = [
            (ACTION_STREAM_ID, tr("Load layer (Enter)")),
            (
                ACTION_EXTRACT_OFFLINE_ID,
                tr("Extract offline to GeoPackage/GeoTIFF (Shift+Enter)"),
            ),
            (ACTION_SHOW_COVERAGE_ID, tr("Show coverage on map")),
            (ACTION_ZOOM_TO_COVERAGE_ID, tr("Zoom to coverage")),
        ]

        for idx, (entry, _rank_score) in enumerate(scored_entries):
            if feedback.isCanceled():
                return

            scope_badge = entry.country_iso if entry.country_iso else global_badge
            type_badge = entry.type.upper()
            display_title = f"[{type_badge} · {scope_badge}] {entry.localized_name()}"
            payload_json = json.dumps(asdict(entry))

            result = QgsLocatorResult(self, display_title, payload_json)
            icon = _resolve_entry_icon(entry)
            if icon is not None:
                result.icon = icon

            facts = [entry.category, entry.attribution]
            if entry.data_date:
                facts.append(entry.data_date)
            if entry.health_status == "degraded":
                facts.append(degraded_note)
            result.description = f"{' | '.join(f for f in facts if f)} ({hint})"
            is_local = entry.area_deg2 < GLOBAL_GROUP_AREA_DEG2
            result.group = local_group if is_local else global_group
            try:
                result.groupScore = 2.0 if is_local != global_first else 1.0
            except Exception:
                pass  # QGIS < 3.40: the numbered group names give the order
            # Entries arrive ranked: keep that order inside each group.
            result.score = max(0.1, 1.0 - (idx * 0.015))

            try:
                result.actions = [
                    QgsLocatorResult.ResultAction(action_id, label)
                    for action_id, label in action_labels
                ]
            except Exception:
                pass

            self._emitted_results.append(result)
            self.resultFetched.emit(result)

    def triggerResult(self, result: QgsLocatorResult) -> None:
        """Handle result activation on main thread (Enter vs Shift+Enter)."""
        modifiers = QApplication.keyboardModifiers()
        shift_pressed = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        action_id = ACTION_EXTRACT_OFFLINE_ID if shift_pressed else ACTION_STREAM_ID
        self.triggerResultFromAction(result, action_id)

    def triggerResultFromAction(
        self,
        result: QgsLocatorResult,
        actionId: int,
    ) -> None:
        """Run the chosen action: load, extract offline, show coverage or zoom to coverage."""
        raw_data = result.userData
        if callable(raw_data):  # QGIS >= 3.36 exposes userData as a method
            try:
                raw_data = raw_data()
            except Exception:
                return
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except Exception:
                return
        if not isinstance(raw_data, dict):
            return

        try:
            entry = self._dict_to_record(raw_data)
        except (KeyError, TypeError, ValueError):
            return

        if actionId in (ACTION_SHOW_COVERAGE_ID, ACTION_ZOOM_TO_COVERAGE_ID):
            show_entry_coverage(
                self._iface,
                self._coverage_preview,
                entry,
                zoom=(actionId == ACTION_ZOOM_TO_COVERAGE_ID),
            )
            return

        task = load_entry(
            self._iface,
            entry,
            offline_extract=(actionId == ACTION_EXTRACT_OFFLINE_ID),
        )
        if task is not None:
            self._active_extraction_task = task

    @staticmethod
    def _dict_to_record(data: Dict[str, Any]) -> CatalogSourceRecord:
        """Reconstruct a CatalogSourceRecord from QgsLocatorResult.userData."""
        return CatalogSourceRecord.from_dict(data)
