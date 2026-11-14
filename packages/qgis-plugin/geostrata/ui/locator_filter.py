"""QgsLocatorFilter implementation (Ctrl+K) with 2-step spatial + FTS5 filtering."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeedback,
    QgsGeometry,
    QgsLocatorContext,
    QgsLocatorFilter,
    QgsLocatorResult,
    QgsMessageLog,
    QgsProject,
    QgsRectangle,
    QgsSettings,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QApplication

from ..core.db_client import CatalogDatabaseClient, CatalogSourceRecord, parse_search_query
from ..core.extractor_task import launch_offline_extraction_task
from ..core.overpass_task import launch_overpass_fetch_task
from ..core.uri_builder import create_qgis_layer

ACTION_STREAM_ID = 1
ACTION_EXTRACT_OFFLINE_ID = 2

DENSE_WFS_KEYWORDS: Tuple[str, ...] = (
    "batiment",
    "parcelle",
    "troncon_de_route",
    "zone_de_vegetation",
)


SETTINGS_ICON_MODE_KEY = "geostrata/icon_mode"
SETTINGS_INCLUDE_ELI_KEY = "geostrata/include_eli"


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
    ) -> None:
        super().__init__()
        self._iface = iface
        self._db_client = db_client
        self._prepared_bbox_4326: Optional[Tuple[float, float, float, float]] = None
        self._active_extraction_task: Optional[Any] = None
        self._emitted_results: List[QgsLocatorResult] = []

    def name(self) -> str:
        """Return unique internal filter identifier."""
        return "geostrata_filter"

    def displayName(self) -> str:
        """Return user-visible filter title in Ctrl+K."""
        return "Geostrata (Couches locales & mondiales)"

    def prefix(self) -> str:
        """Return filter prefix shortcut (minimum 3 chars in QGIS)."""
        return "gst"

    def priority(self) -> QgsLocatorFilter.Priority:
        """Return high priority so spatial layers appear at the top of Ctrl+K."""
        return QgsLocatorFilter.Priority.High

    def clone(self) -> GeostrataLocatorFilter:
        """Return a thread-safe clone of this filter for worker threads."""
        cloned = GeostrataLocatorFilter(self._iface, self._db_client)
        cloned._prepared_bbox_4326 = self._prepared_bbox_4326
        return cloned

    def prepare(self, string: str, context: QgsLocatorContext) -> List[str]:
        """Capture and reproject current map canvas extent on the main UI thread."""
        self._prepared_bbox_4326 = self._capture_canvas_bbox_4326()
        return []

    def _capture_canvas_bbox_4326(
        self,
    ) -> Optional[Tuple[float, float, float, float]]:
        """Read mapCanvas extent on the main thread and transform to EPSG:4326."""
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

    def fetchResults(
        self,
        string: str,
        context: QgsLocatorContext,
        feedback: QgsFeedback,
    ) -> None:
        """Execute 2-step search (SQLite R-Tree + FTS5 -> PyQGIS WKT geometry check)."""
        query_text = string.strip()
        if not context.usingPrefix and len(query_text) < 2:
            return

        if feedback.isCanceled():
            return

        _, is_global = parse_search_query(query_text)
        bbox_4326 = self._prepared_bbox_4326

        # Step 1: SQLite R-Tree + FTS5 (< 1 ms)
        candidates = self._db_client.search_sources(
            raw_query=query_text,
            canvas_bbox_4326=bbox_4326,
            limit=50,
        )

        if feedback.isCanceled() or not candidates:
            return

        # Step 2: Exact geometry intersection in PyQGIS (< 2 ms)
        canvas_geom: Optional[QgsGeometry] = None
        if (not is_global) and (bbox_4326 is not None):
            min_x, min_y, max_x, max_y = bbox_4326
            rect = QgsRectangle(min_x, min_y, max_x, max_y)
            canvas_geom = QgsGeometry.fromRect(rect)

        self._emitted_results.clear()
        include_eli = True
        try:
            raw_eli = QgsSettings().value(SETTINGS_INCLUDE_ELI_KEY, True)
            include_eli = str(raw_eli).lower() not in ("false", "0", "no")
        except Exception:
            include_eli = True

        for idx, entry in enumerate(candidates):
            if feedback.isCanceled():
                return

            if not include_eli and "-eli-" in entry.id:
                continue

            if canvas_geom is not None and entry.geom_wkt:
                source_geom = QgsGeometry.fromWkt(entry.geom_wkt)
                if not source_geom.isNull() and not canvas_geom.intersects(
                    source_geom
                ):
                    continue

            scope_badge = entry.country_iso if entry.country_iso else "Global"
            type_badge = entry.type.upper()
            title_str = entry.localized_name()
            display_title = f"[{type_badge} · {scope_badge}] {title_str}"
            payload_json = json.dumps(asdict(entry))

            result = QgsLocatorResult(self, display_title, payload_json)
            icon = _resolve_entry_icon(entry)
            if icon is not None:
                result.icon = icon
            result.description = (
                f"{entry.category} | {entry.attribution} "
                f"(Entrée: Streaming · Maj+Entrée: Extraire hors-ligne)"
            )
            result.group = (
                "1. Couches locales & nationales"
                if entry.area_deg2 < 10000.0
                else "2. Couches mondiales"
            )
            result.score = max(0.1, 1.0 - (idx * 0.015))

            try:
                result.actions = [
                    QgsLocatorResult.ResultAction(
                        ACTION_STREAM_ID,
                        "Charger en streaming (Entrée)",
                    ),
                    QgsLocatorResult.ResultAction(
                        ACTION_EXTRACT_OFFLINE_ID,
                        "Extraire hors-ligne en GeoPackage/GeoTIFF (Maj + Entrée)",
                    ),
                ]
            except Exception:
                pass

            self._emitted_results.append(result)
            self.resultFetched.emit(result)

    def triggerResult(self, result: QgsLocatorResult) -> None:
        """Handle result activation on main thread (Entrée vs Maj + Entrée)."""
        modifiers = QApplication.keyboardModifiers()
        shift_pressed = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        action_id = ACTION_EXTRACT_OFFLINE_ID if shift_pressed else ACTION_STREAM_ID
        self.triggerResultFromAction(result, action_id)

    def triggerResultFromAction(
        self,
        result: QgsLocatorResult,
        actionId: int,
    ) -> None:
        """Execute streaming load or offline extraction based on actionId."""
        raw_data = result.userData
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except Exception:
                return
        if not isinstance(raw_data, dict):
            return

        entry = self._dict_to_record(raw_data)
        title_str = entry.localized_name()
        bbox_4326 = self._capture_canvas_bbox_4326() or (
            entry.min_x,
            entry.min_y,
            entry.max_x,
            entry.max_y,
        )

        if entry.type.lower() == "overpass":
            self._active_extraction_task = launch_overpass_fetch_task(
                self._iface,
                entry,
                bbox_4326,
                is_offline_extract=(actionId == ACTION_EXTRACT_OFFLINE_ID),
            )
            if self._iface is not None and self._iface.messageBar() is not None:
                self._iface.messageBar().pushMessage(
                    "Geostrata",
                    f"Requête Overpass lancée en tâche de fond (multi-miroirs) : {title_str}",
                    level=Qgis.MessageLevel.Info,
                    duration=4,
                )
            return

        if actionId == ACTION_EXTRACT_OFFLINE_ID:
            self._active_extraction_task = launch_offline_extraction_task(
                self._iface, entry, bbox_4326
            )
            if self._iface is not None and self._iface.messageBar() is not None:
                self._iface.messageBar().pushMessage(
                    "Geostrata",
                    f"Extraction hors-ligne démarrée en tâche de fond : {title_str}",
                    level=Qgis.MessageLevel.Info,
                    duration=4,
                )
            return

        # Default action: Streaming load
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
                        f"Couche WFS dense ajoutée ({title_str}) : zoomez à 1:50 000 pour afficher les entités.",
                        level=Qgis.MessageLevel.Info,
                        duration=6,
                    )
            else:
                if self._iface is not None and self._iface.messageBar() is not None:
                    self._iface.messageBar().pushMessage(
                        "Geostrata",
                        f"Couche chargée en streaming : {title_str} ({entry.attribution})",
                        level=Qgis.MessageLevel.Success,
                        duration=4,
                    )
            QgsProject.instance().addMapLayer(layer)
        else:
            err_msg = (
                f"Impossible de charger le flux '{title_str}' "
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

    @staticmethod
    def _dict_to_record(data: Dict[str, Any]) -> CatalogSourceRecord:
        """Reconstruct a CatalogSourceRecord from QgsLocatorResult.userData."""
        return CatalogSourceRecord(
            id=str(data["id"]),
            name=str(data["name"]),
            description=str(data.get("description") or ""),
            type=str(data["type"]),
            category=str(data["category"]),
            url=str(data["url"]),
            crs=str(data.get("crs") or "EPSG:4326"),
            country_iso=data.get("country_iso"),
            min_x=float(data["min_x"]),
            min_y=float(data["min_y"]),
            max_x=float(data["max_x"]),
            max_y=float(data["max_y"]),
            area_deg2=float(data["area_deg2"]),
            geom_wkt=str(data["geom_wkt"]),
            tags=list(data.get("tags") or []),
            license=str(data.get("license") or ""),
            attribution=str(data.get("attribution") or ""),
            style_qml=data.get("style_qml"),
            min_zoom=data.get("min_zoom"),
            max_zoom=data.get("max_zoom"),
            archive_target=data.get("archive_target"),
            health_status=str(data.get("health_status") or "ok"),
            i18n=dict(data.get("i18n") or {}),
        )
