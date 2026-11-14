"""Shared main-thread helpers for the locator filter and the dock (canvas bbox, loading, messages)."""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsMessageLog,
    QgsProject,
    QgsRectangle,
    QgsSettings,
)
from qgis.PyQt.QtCore import QLocale

from ..core.db_client import CatalogSourceRecord
from ..core.extractor_task import launch_offline_extraction_task
from ..core.loader_task import launch_file_load_task
from ..core.overpass_task import launch_overpass_fetch_task
from ..core.uri_builder import create_qgis_layer, entry_display_name, needs_local_copy
from ..i18n import get_ui_language, tr
from .coverage_preview import (
    PREVIEW_FAILED,
    PREVIEW_WORLDWIDE,
    CoveragePreview,
)

SETTINGS_INCLUDE_ELI_KEY = "geostrata/include_eli"

Bbox = Tuple[float, float, float, float]

DENSE_WFS_KEYWORDS: Tuple[str, ...] = (
    "batiment",
    "parcelle",
    "troncon_de_route",
    "zone_de_vegetation",
)


def read_include_eli_setting() -> bool:
    """Return whether OSM Editor Layer Index entries should be listed (default True)."""
    try:
        raw_eli = QgsSettings().value(SETTINGS_INCLUDE_ELI_KEY, True)
        return str(raw_eli).lower() not in ("false", "0", "no")
    except Exception:
        return True


def capture_canvas_bbox_4326(iface: Any) -> Optional[Bbox]:
    """Read the map canvas extent (main thread only) and transform it to EPSG:4326."""
    if iface is None or iface.mapCanvas() is None:
        return None

    try:
        canvas = iface.mapCanvas()
        extent: QgsRectangle = canvas.extent()
        if extent.isEmpty() or not extent.isFinite():
            return None

        src_crs = canvas.mapSettings().destinationCrs()
        wgs84_crs = QgsCoordinateReferenceSystem("EPSG:4326")

        if src_crs.isValid() and src_crs != wgs84_crs:
            transform = QgsCoordinateTransform(src_crs, wgs84_crs, QgsProject.instance())
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


def make_intersection_filter(
    bbox_4326: Optional[Bbox],
    is_canceled: Optional[Callable[[], bool]] = None,
) -> Optional[Callable[[CatalogSourceRecord], bool]]:
    """Build the exact-geometry predicate passed to ``search_sources(accept=...)``.

    Returns None when there is nothing to test (no bbox and no cancellation hook).
    """
    canvas_geom: Optional[QgsGeometry] = None
    if bbox_4326 is not None:
        canvas_geom = QgsGeometry.fromRect(QgsRectangle(*bbox_4326))
    if canvas_geom is None and is_canceled is None:
        return None

    def accept(entry: CatalogSourceRecord) -> bool:
        if is_canceled is not None and is_canceled():
            return False
        if canvas_geom is None or not entry.geom_wkt:
            return True
        source_geom = QgsGeometry.fromWkt(entry.geom_wkt)
        return source_geom.isNull() or canvas_geom.intersects(source_geom)

    return accept


def push_message(
    iface: Any,
    text: str,
    level: Any = Qgis.MessageLevel.Info,
    duration: int = 4,
) -> None:
    """Show a message in the QGIS message bar when an interface is available."""
    try:
        if iface is not None and iface.messageBar() is not None:
            iface.messageBar().pushMessage("Geostrata", text, level=level, duration=duration)
    except Exception:
        pass


def country_display_name(iso_code: str) -> str:
    """Return a country name for an ISO 3166-1 alpha-2 code ('' when Qt cannot resolve it)."""
    try:
        if hasattr(QLocale, "codeToTerritory"):  # Qt >= 6.2
            territory = QLocale.codeToTerritory(iso_code)
            # PyQt6 exposes the Territory enum under its historical name `Country`.
            if territory == QLocale.Country.AnyCountry:
                return ""
            if get_ui_language() != "en":
                localized = QLocale(QLocale(get_ui_language()).language(), territory)
                if localized.territory() == territory:
                    native = localized.nativeTerritoryName()
                    if native:
                        return str(native)
            return str(QLocale.territoryToString(territory))
    except Exception:
        pass
    return ""


def show_entry_coverage(
    iface: Any,
    preview: Optional[CoveragePreview],
    entry: CatalogSourceRecord,
    zoom: bool = False,
    notify: bool = True,
) -> None:
    """Draw the entry footprint on the canvas (optionally zooming to it) and report problems."""
    if preview is None:
        return
    outcome = preview.show_entry(entry, zoom=zoom)
    if not notify:
        return
    if outcome == PREVIEW_WORLDWIDE:
        push_message(
            iface,
            tr("{name} covers the whole world: no footprint to show.", name=entry.localized_name()),
        )
    elif outcome == PREVIEW_FAILED:
        push_message(
            iface,
            tr("The coverage of {name} could not be displayed.", name=entry.localized_name()),
            level=Qgis.MessageLevel.Warning,
        )


def load_entry(
    iface: Any,
    entry: CatalogSourceRecord,
    offline_extract: bool = False,
) -> Optional[Any]:
    """Load an entry in QGIS or start its offline extraction.

    Returns the background QgsTask when one was started (Overpass query, extraction,
    download of a file-based source), None when the layer was added (or failed)
    synchronously: services and range-friendly files (FlatGeobuf, COG, VRT), whose
    providers only fetch what the map needs.
    """
    title_str = entry_display_name(entry)
    bbox_4326 = capture_canvas_bbox_4326(iface) or (
        entry.min_x,
        entry.min_y,
        entry.max_x,
        entry.max_y,
    )

    if entry.type.lower() == "overpass":
        task = launch_overpass_fetch_task(
            iface,
            entry,
            bbox_4326,
            is_offline_extract=offline_extract,
        )
        push_message(
            iface,
            tr("Overpass query started in the background: {name}", name=title_str),
        )
        return task

    if offline_extract:
        task = launch_offline_extraction_task(iface, entry, bbox_4326)
        push_message(
            iface,
            tr("Offline extraction started in the background: {name}", name=title_str),
        )
        return task

    if needs_local_copy(entry):
        # ZIP archives and GeoJSON documents: background download into the file cache,
        # the layer is added by the task when the local copy is ready.
        return launch_file_load_task(iface, entry, bbox_4326)

    is_dense_wfs = entry.type.lower() == "wfs" and any(
        kw in entry.url.lower() for kw in DENSE_WFS_KEYWORDS
    )
    canvas_area = (bbox_4326[2] - bbox_4326[0]) * (bbox_4326[3] - bbox_4326[1])

    layer, spec = create_qgis_layer(entry, bbox_4326)
    if layer is not None and layer.isValid():
        if is_dense_wfs and canvas_area > 0.25:
            try:
                layer.setScaleBasedVisibility(True)
                layer.setMinimumScale(50000.0)
            except Exception:
                pass
            push_message(
                iface,
                tr(
                    "Dense WFS layer added ({name}): zoom in to 1:50,000 to display features.",
                    name=title_str,
                ),
                duration=6,
            )
        else:
            push_message(
                iface,
                tr(
                    "Layer loaded: {name} ({attribution})",
                    name=title_str,
                    attribution=entry.attribution,
                ),
                level=Qgis.MessageLevel.Success,
            )
        QgsProject.instance().addMapLayer(layer)
        return None

    err_msg = tr(
        "Could not load '{name}' (provider={provider}, uri={uri})",
        name=title_str,
        provider=spec.provider,
        uri=spec.uri,
    )
    QgsMessageLog.logMessage(err_msg, "Geostrata", Qgis.MessageLevel.Warning)
    push_message(iface, err_msg, level=Qgis.MessageLevel.Warning, duration=6)
    return None
