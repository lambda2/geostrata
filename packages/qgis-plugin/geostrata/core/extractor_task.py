"""Non-blocking QgsTask for offline canvas extraction (Shift + Enter)."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Optional, Tuple

from ..i18n import tr
from .db_client import CatalogSourceRecord
from .file_cache import DownloadCancelled, DownloadError, configured_file_cache
from .loader_task import fetch_entry_file, open_layer_sources
from .uri_builder import (
    apply_layer_metadata_and_style,
    build_provider_uri,
    entry_display_name,
    needs_local_copy,
)

_ACTIVE_EXTRACTOR_TASKS: list[Any] = []


def resolve_extraction_output_dir() -> Path:
    """Resolve the local directory for extracted GeoPackage/GeoTIFF files."""
    try:
        from qgis.core import QgsApplication, QgsProject

        project_path = QgsProject.instance().homePath()
        if project_path:
            out_dir = Path(project_path) / "geostrata_extracts"
        else:
            out_dir = (
                Path(QgsApplication.qgisSettingsDirPath())
                / "geostrata"
                / "extracts"
            )
    except Exception:
        out_dir = Path.home() / ".geostrata" / "extracts"

    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def launch_offline_extraction_task(
    iface: Any,
    entry: CatalogSourceRecord,
    canvas_bbox_4326: Tuple[float, float, float, float],
) -> Any:
    """Create and schedule a QgsTask to extract/clip a source to a local file."""
    from qgis.core import (
        Qgis,
        QgsApplication,
        QgsMessageLog,
        QgsProject,
        QgsProcessingFeedback,
        QgsRasterLayer,
        QgsTask,
        QgsVectorLayer,
    )

    # File-based sources are clipped from the cached local copy (downloaded by this
    # task when missing), the others from their streamed remote source.
    uses_cache = needs_local_copy(entry)
    file_cache = configured_file_cache() if uses_cache else None
    in_use = open_layer_sources() if uses_cache else []
    title = entry_display_name(entry)
    remote_spec = build_provider_uri(entry, canvas_bbox_4326)
    output_dir = resolve_extraction_output_dir()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    is_raster = remote_spec.layer_kind == "raster"
    ext = "tif" if is_raster else "gpkg"
    output_path = output_dir / f"{entry.id}_{timestamp}.{ext}"

    min_x, min_y, max_x, max_y = canvas_bbox_4326
    extent_param = f"{min_x},{max_x},{min_y},{max_y} [EPSG:4326]"

    class OfflineExtractorTask(QgsTask):
        """Background task executing native:extractbyextent or gdal:cliprasterbyextent."""

        def __init__(self) -> None:
            super().__init__(
                tr("Geostrata: offline extraction ({name})", name=title),
                QgsTask.CanCancel,
            )
            self.output_file: Path = output_path
            self.error_message: Optional[str] = None
            self.was_canceled: bool = False
            self._feedback = QgsProcessingFeedback()

        def cancel(self) -> None:
            self._feedback.cancel()
            super().cancel()

        def _on_download_progress(self, _phase: str, done: int, total: Optional[int]) -> None:
            if total:
                self.setProgress(60.0 * done / total)

        def run(self) -> bool:
            spec = remote_spec
            if file_cache is not None:
                try:
                    copy = fetch_entry_file(
                        file_cache, entry, self._on_download_progress, self.isCanceled, in_use
                    )
                except DownloadCancelled:
                    self.was_canceled = True
                    return False
                except DownloadError as exc:
                    self.error_message = tr(
                        "Could not load '{name}': {error}", name=title, error=str(exc)
                    )
                    return False
                spec = build_provider_uri(
                    entry, canvas_bbox_4326, local_path=copy.open_path
                )
                self.setProgress(60.0)

            try:
                import processing

                feedback = self._feedback
                base = 60.0 if file_cache is not None else 0.0
                feedback.progressChanged.connect(
                    lambda value: self.setProgress(base + (100.0 - base) * value / 100.0)
                )
                if is_raster:
                    source_layer = QgsRasterLayer(
                        spec.uri, spec.display_name, spec.provider
                    )
                    if not source_layer.isValid():
                        self.error_message = tr(
                            "Could not open the raster source: {uri}", uri=spec.uri
                        )
                        return False

                    processing.run(
                        "gdal:cliprasterbyextent",
                        {
                            "INPUT": source_layer,
                            "PROJWIN": extent_param,
                            "OVERCRS": False,
                            "NODATA": None,
                            "OPTIONS": "COMPRESS=DEFLATE|TILED=YES",
                            "DATA_TYPE": 0,
                            "EXTRA": "",
                            "OUTPUT": str(self.output_file),
                        },
                        feedback=feedback,
                    )
                else:
                    source_layer = QgsVectorLayer(
                        spec.uri, spec.display_name, spec.provider
                    )
                    if not source_layer.isValid():
                        self.error_message = tr(
                            "Could not open the vector source: {uri}", uri=spec.uri
                        )
                        return False

                    processing.run(
                        "native:extractbyextent",
                        {
                            "INPUT": source_layer,
                            "EXTENT": extent_param,
                            "CLIP": True,
                            "OUTPUT": str(self.output_file),
                        },
                        feedback=feedback,
                    )

                return self.output_file.is_file()
            except Exception as exc:
                self.error_message = str(exc)
                return False

        def finished(self, result: bool) -> None:
            try:
                if self in _ACTIVE_EXTRACTOR_TASKS:
                    _ACTIVE_EXTRACTOR_TASKS.remove(self)
            except Exception:
                pass
            if self.was_canceled or (not result and self.isCanceled()):
                if iface is not None and iface.messageBar() is not None:
                    iface.messageBar().pushMessage(
                        "Geostrata",
                        tr("Offline extraction cancelled: {name}", name=title),
                        level=Qgis.MessageLevel.Info,
                        duration=5,
                    )
                return
            if not result or not self.output_file.is_file():
                msg = self.error_message or tr("Offline extraction failed.")
                QgsMessageLog.logMessage(
                    msg, "Geostrata", Qgis.MessageLevel.Critical
                )
                if iface is not None and iface.messageBar() is not None:
                    iface.messageBar().pushMessage(
                        "Geostrata",
                        msg,
                        level=Qgis.MessageLevel.Warning,
                        duration=6,
                    )
                return

            layer_title = f"{title} {tr('[Local]')}"
            if is_raster:
                local_layer = QgsRasterLayer(
                    str(self.output_file), layer_title, "gdal"
                )
            else:
                local_layer = QgsVectorLayer(
                    str(self.output_file), layer_title, "ogr"
                )

            if local_layer.isValid():
                apply_layer_metadata_and_style(local_layer, entry)
                QgsProject.instance().addMapLayer(local_layer)
                if iface is not None and iface.messageBar() is not None:
                    iface.messageBar().pushMessage(
                        "Geostrata",
                        tr("Layer extracted offline: {path}", path=self.output_file),
                        level=Qgis.MessageLevel.Success,
                        duration=5,
                    )
            else:
                if iface is not None and iface.messageBar() is not None:
                    iface.messageBar().pushMessage(
                        "Geostrata",
                        tr("The extracted file is invalid: {path}", path=self.output_file),
                        level=Qgis.MessageLevel.Warning,
                        duration=6,
                    )

    task = OfflineExtractorTask()
    _ACTIVE_EXTRACTOR_TASKS.append(task)
    QgsApplication.taskManager().addTask(task)
    return task
