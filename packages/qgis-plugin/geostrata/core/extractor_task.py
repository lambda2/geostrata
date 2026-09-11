"""Non-blocking QgsTask for offline canvas extraction (Maj + Entrée)."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Optional, Tuple

from .db_client import CatalogSourceRecord
from .uri_builder import (
    apply_layer_metadata_and_style,
    build_provider_uri,
)


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

    spec = build_provider_uri(entry, canvas_bbox_4326)
    output_dir = resolve_extraction_output_dir()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    is_raster = spec.layer_kind == "raster"
    ext = "tif" if is_raster else "gpkg"
    output_path = output_dir / f"{entry.id}_{timestamp}.{ext}"

    min_x, min_y, max_x, max_y = canvas_bbox_4326
    extent_param = f"{min_x},{max_x},{min_y},{max_y} [EPSG:4326]"

    class OfflineExtractorTask(QgsTask):
        """Background task executing native:extractbyextent or gdal:cliprasterbyextent."""

        def __init__(self) -> None:
            super().__init__(
                f"Geostrata : Extraction locale ({entry.name})",
                QgsTask.CanCancel,
            )
            self.output_file: Path = output_path
            self.error_message: Optional[str] = None

        def run(self) -> bool:
            try:
                import processing

                feedback = QgsProcessingFeedback()
                if is_raster:
                    source_layer = QgsRasterLayer(
                        spec.uri, spec.display_name, spec.provider
                    )
                    if not source_layer.isValid():
                        self.error_message = (
                            f"Impossible d'ouvrir la source raster : {spec.uri}"
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
                        self.error_message = (
                            f"Impossible d'ouvrir la source vecteur : {spec.uri}"
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
            if not result or not self.output_file.is_file():
                msg = self.error_message or "Échec de l'extraction hors-ligne."
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

            layer_title = f"{entry.name} [Local]"
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
                        f"Couche extraite hors-ligne : {self.output_file}",
                        level=Qgis.MessageLevel.Success,
                        duration=5,
                    )
            else:
                if iface is not None and iface.messageBar() is not None:
                    iface.messageBar().pushMessage(
                        "Geostrata",
                        f"Le fichier extrait est invalide : {self.output_file}",
                        level=Qgis.MessageLevel.Warning,
                        duration=6,
                    )

    task = OfflineExtractorTask()
    QgsApplication.taskManager().addTask(task)
    return task
