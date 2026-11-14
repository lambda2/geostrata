"""Temporary footprint highlight ("where does this layer have data?") on the map canvas."""

from __future__ import annotations

from typing import Any, Optional

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
)
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import QObject, QTimer
from qgis.PyQt.QtGui import QColor

from ..diagnostics import note_ignored
from ..core.db_client import CatalogSourceRecord

try:  # QGIS >= 3.30
    from qgis.core import Qgis

    _POLYGON_GEOMETRY = Qgis.GeometryType.Polygon
except Exception:  # QGIS 3.28
    from qgis.core import QgsWkbTypes

    _POLYGON_GEOMETRY = QgsWkbTypes.GeometryType.PolygonGeometry

PREVIEW_SHOWN = "shown"
PREVIEW_WORLDWIDE = "worldwide"
PREVIEW_FAILED = "failed"

DEFAULT_PREVIEW_DURATION_MS = 8000


class CoveragePreview(QObject):
    """Draws one catalog footprint at a time as a QgsRubberBand and removes it by itself.

    A single instance is shared by the locator filter and the dock, so showing a new
    footprint replaces the previous one. The band disappears after a delay, when
    :meth:`clear` is called, and is removed from the canvas scene by :meth:`dispose`
    (plugin unload). Must only be used from the main thread.
    """

    def __init__(self, iface: Any, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._iface = iface
        self._rubber_band: Optional[QgsRubberBand] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clear)

    def _canvas(self) -> Any:
        try:
            return self._iface.mapCanvas() if self._iface is not None else None
        except Exception:
            return None

    def is_active(self) -> bool:
        """Return True while a footprint is displayed."""
        return self._timer.isActive()

    def show_entry(
        self,
        entry: CatalogSourceRecord,
        zoom: bool = False,
        duration_ms: int = DEFAULT_PREVIEW_DURATION_MS,
    ) -> str:
        """Highlight the entry footprint (EPSG:4326 WKT reprojected to the canvas CRS).

        Returns PREVIEW_SHOWN, PREVIEW_WORLDWIDE (nothing drawn: the layer covers the
        whole world) or PREVIEW_FAILED (no canvas, no/invalid geometry, reprojection error).
        """
        self.clear()
        canvas = self._canvas()
        if canvas is None:
            return PREVIEW_FAILED
        if entry.is_worldwide:
            return PREVIEW_WORLDWIDE
        if not entry.geom_wkt:
            return PREVIEW_FAILED

        try:
            geom = QgsGeometry.fromWkt(entry.geom_wkt)
            if geom.isNull() or geom.isEmpty():
                return PREVIEW_FAILED

            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            dest_crs = canvas.mapSettings().destinationCrs()
            if dest_crs.isValid() and dest_crs != wgs84:
                transform = QgsCoordinateTransform(wgs84, dest_crs, QgsProject.instance())
                geom.transform(transform)

            if self._rubber_band is None:
                band = QgsRubberBand(canvas, _POLYGON_GEOMETRY)
                band.setColor(QColor(0, 140, 255, 190))
                band.setFillColor(QColor(0, 140, 255, 35))
                band.setWidth(2)
                self._rubber_band = band

            self._rubber_band.setToGeometry(geom, None)
            self._rubber_band.show()

            if zoom:
                rect = geom.boundingBox()
                if not rect.isEmpty() and rect.isFinite():
                    rect.scale(1.08)
                    canvas.setExtent(rect)
                    canvas.refresh()

            if duration_ms > 0:
                self._timer.start(duration_ms)
            return PREVIEW_SHOWN
        except Exception:
            self.clear()
            return PREVIEW_FAILED

    def clear(self) -> None:
        """Hide the current footprint (the rubber band object is kept for reuse)."""
        self._timer.stop()
        if self._rubber_band is not None:
            try:
                self._rubber_band.reset(_POLYGON_GEOMETRY)
            except Exception:
                self._rubber_band = None

    def dispose(self) -> None:
        """Remove the rubber band from the canvas scene (call on plugin unload)."""
        self._timer.stop()
        band = self._rubber_band
        self._rubber_band = None
        if band is None:
            return
        try:
            canvas = self._canvas()
            if canvas is not None and canvas.scene() is not None:
                canvas.scene().removeItem(band)
        except Exception as error:
            note_ignored(error)
