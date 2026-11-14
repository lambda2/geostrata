"""Resilient asynchronous Overpass API fetcher with multi-mirror failover and GeoJSON conversion."""

from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .db_client import CatalogSourceRecord
from .extractor_task import resolve_extraction_output_dir
from .uri_builder import apply_layer_metadata_and_style

OVERPASS_MIRRORS: Tuple[str, ...] = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

MAX_OVERPASS_AREA_DEG2 = 16.0
MAX_CLAMPED_HALF_SPAN_DEG = 1.5

LINEAR_OVERPASS_KEYS: Tuple[str, ...] = (
    "waterway=",
    "railway=rail",
    "railway=tram",
    "railway=light_rail",
    "railway=subway",
    "highway=cycleway",
    "highway=path",
    "highway=track",
    "power=line",
    "power=minor_line",
)


def is_linear_overpass_entry(entry: CatalogSourceRecord) -> bool:
    """Determine if an Overpass CatalogSourceRecord expects LineString geometries."""
    if entry.style_qml and 'symbol type="line"' in entry.style_qml:
        return True
    expr_lower = entry.url.lower()
    return any(token in expr_lower for token in LINEAR_OVERPASS_KEYS)


def clamp_overpass_bbox(
    bbox_4326: Tuple[float, float, float, float],
) -> Tuple[Tuple[float, float, float, float], bool]:
    """Clamp overly large canvas extents around the canvas center to protect Overpass."""
    min_x, min_y, max_x, max_y = bbox_4326
    area = max(0.0, (max_x - min_x) * (max_y - min_y))
    if area <= MAX_OVERPASS_AREA_DEG2:
        return bbox_4326, False

    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    clamped = (
        max(-180.0, center_x - MAX_CLAMPED_HALF_SPAN_DEG),
        max(-90.0, center_y - MAX_CLAMPED_HALF_SPAN_DEG),
        min(180.0, center_x + MAX_CLAMPED_HALF_SPAN_DEG),
        min(90.0, center_y + MAX_CLAMPED_HALF_SPAN_DEG),
    )
    return clamped, True


def build_overpass_json_ql(
    expression_or_ql: str,
    bbox_4326: Tuple[float, float, float, float],
    prefer_lines: bool = False,
) -> str:
    """Build a fast [out:json] Overpass QL query (`out geom;` for lines, `out center;` for POIs)."""
    min_x, min_y, max_x, max_y = bbox_4326
    overpass_bbox = f"{min_y:.6f},{min_x:.6f},{max_y:.6f},{max_x:.6f}"

    stripped = expression_or_ql.strip()
    if "{{bbox}}" in stripped or "{bbox}" in stripped:
        replaced = stripped.replace("{{bbox}}", overpass_bbox).replace(
            "{bbox}", overpass_bbox
        )
        return re.sub(r"\[out:xml\]", "[out:json]", replaced, flags=re.IGNORECASE)

    if stripped.startswith("[out:") or stripped.startswith("("):
        return stripped

    element_kind = "way" if prefer_lines else "nwr"
    out_directive = "out geom;" if prefer_lines else "out center;"

    clauses: List[str] = []
    for raw_part in stripped.split("|"):
        part = raw_part.strip()
        if not part:
            continue
        if "=" in part:
            key, val = part.split("=", 1)
            clauses.append(
                f'{element_kind}["{key.strip()}"="{val.strip()}"]({overpass_bbox});'
            )
        else:
            clauses.append(f'{element_kind}["{part}"]({overpass_bbox});')

    joined_clauses = "\n  ".join(clauses)
    return (
        f"[out:json][timeout:15];\n(\n  {joined_clauses}\n);\n{out_directive}"
    )


def convert_overpass_json_to_geojson(
    overpass_payload: Dict[str, Any],
    prefer_lines: bool = False,
) -> Dict[str, Any]:
    """Convert an Overpass JSON response (`elements`) into a homogeneous GeoJSON FeatureCollection."""
    elements = overpass_payload.get("elements")
    if not isinstance(elements, list):
        elements = []

    features: List[Dict[str, Any]] = []
    for elem in elements:
        if not isinstance(elem, dict):
            continue

        elem_type = str(elem.get("type", ""))
        elem_id = elem.get("id")
        tags = elem.get("tags")
        if not isinstance(tags, dict):
            tags = {}

        geometry: Optional[Dict[str, Any]] = None

        if prefer_lines:
            if isinstance(elem.get("geometry"), list) and elem["geometry"]:
                coords = [
                    [float(pt["lon"]), float(pt["lat"])]
                    for pt in elem["geometry"]
                    if isinstance(pt, dict) and "lon" in pt and "lat" in pt
                ]
                if len(coords) >= 2:
                    geometry = {
                        "type": "LineString",
                        "coordinates": coords,
                    }
        else:
            lon: Optional[float] = None
            lat: Optional[float] = None
            if "lon" in elem and "lat" in elem:
                lon = float(elem["lon"])
                lat = float(elem["lat"])
            elif isinstance(elem.get("center"), dict):
                center = elem["center"]
                if "lon" in center and "lat" in center:
                    lon = float(center["lon"])
                    lat = float(center["lat"])

            if lon is not None and lat is not None:
                geometry = {
                    "type": "Point",
                    "coordinates": [lon, lat],
                }
            elif isinstance(elem.get("geometry"), list) and elem["geometry"]:
                coords = [
                    [float(pt["lon"]), float(pt["lat"])]
                    for pt in elem["geometry"]
                    if isinstance(pt, dict) and "lon" in pt and "lat" in pt
                ]
                if len(coords) >= 2:
                    geometry = {
                        "type": "LineString",
                        "coordinates": coords,
                    }

        if geometry is None:
            continue

        properties: Dict[str, Any] = {
            "osm_id": f"{elem_type}/{elem_id}",
            "osm_type": elem_type,
            "name": tags.get("name", ""),
        }
        for tag_key, tag_val in tags.items():
            properties[str(tag_key)] = str(tag_val)

        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": properties,
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def fetch_overpass_with_failover(
    ql_query: str,
    mirrors: Sequence[str] = OVERPASS_MIRRORS,
    timeout_sec: int = 12,
) -> Tuple[Dict[str, Any], str]:
    """Execute Overpass QL POST across mirrors until one succeeds.

    Returns:
        Tuple of (parsed_json_dict, winning_mirror_url).
    """
    encoded_body = urllib.parse.urlencode({"data": ql_query}).encode("utf-8")
    errors: List[str] = []

    for mirror_url in mirrors:
        req = urllib.request.Request(
            mirror_url,
            data=encoded_body,
            method="POST",
            headers={
                "User-Agent": "Geostrata-QGIS-Plugin/1.0",
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                raw_bytes = resp.read()
            payload = json.loads(raw_bytes.decode("utf-8"))
            if isinstance(payload, dict) and "elements" in payload:
                return payload, mirror_url
            errors.append(f"{mirror_url}: réponse JSON inattendue")
        except Exception as exc:
            errors.append(f"{mirror_url}: {exc}")

    raise RuntimeError(
        "Tous les miroirs Overpass ont échoué (" + " ; ".join(errors) + ")"
    )


def launch_overpass_fetch_task(
    iface: Any,
    entry: CatalogSourceRecord,
    canvas_bbox_4326: Tuple[float, float, float, float],
    is_offline_extract: bool = False,
) -> Any:
    """Schedule a non-blocking QgsTask that fetches Overpass JSON and loads a GeoJSON layer."""
    from qgis.core import (
        Qgis,
        QgsApplication,
        QgsMessageLog,
        QgsProject,
        QgsTask,
        QgsVectorLayer,
    )

    prefer_lines = is_linear_overpass_entry(entry)
    clamped_bbox, was_clamped = clamp_overpass_bbox(canvas_bbox_4326)
    ql_query = build_overpass_json_ql(
        entry.url,
        clamped_bbox,
        prefer_lines=prefer_lines,
    )
    out_dir = resolve_extraction_output_dir()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_geojson_path = out_dir / f"{entry.id}_{timestamp}.geojson"

    class OverpassFetchTask(QgsTask):
        """Background task querying Overpass mirrors and writing a local GeoJSON."""

        def __init__(self) -> None:
            super().__init__(
                f"Geostrata Overpass : {entry.localized_name()}",
                QgsTask.CanCancel,
            )
            self.output_file: Path = output_geojson_path
            self.feature_count: int = 0
            self.mirror_used: str = ""
            self.error_message: Optional[str] = None

        def run(self) -> bool:
            if self.isCanceled():
                return False
            try:
                self.setProgress(15.0)
                payload, mirror = fetch_overpass_with_failover(ql_query)
                if self.isCanceled():
                    return False
                self.mirror_used = mirror
                self.setProgress(70.0)

                geojson_dict = convert_overpass_json_to_geojson(
                    payload,
                    prefer_lines=prefer_lines,
                )
                features = geojson_dict.get("features", [])
                self.feature_count = len(features) if isinstance(features, list) else 0

                self.output_file.write_text(
                    json.dumps(geojson_dict, ensure_ascii=False),
                    encoding="utf-8",
                )
                self.setProgress(100.0)
                return True
            except Exception as exc:
                self.error_message = str(exc)
                return False

        def finished(self, result: bool) -> None:
            title_str = entry.localized_name()
            if not result or not self.output_file.is_file():
                msg = (
                    self.error_message
                    or f"Échec de la requête Overpass pour {title_str}"
                )
                QgsMessageLog.logMessage(
                    msg, "Geostrata", Qgis.MessageLevel.Warning
                )
                if iface is not None and iface.messageBar() is not None:
                    iface.messageBar().pushMessage(
                        "Geostrata",
                        msg,
                        level=Qgis.MessageLevel.Warning,
                        duration=7,
                    )
                return

            if self.feature_count == 0:
                if iface is not None and iface.messageBar() is not None:
                    iface.messageBar().pushMessage(
                        "Geostrata",
                        f"Aucune entité OSM trouvée sur cette emprise pour : {title_str}",
                        level=Qgis.MessageLevel.Info,
                        duration=5,
                    )
                return

            suffix = " [Local]" if is_offline_extract else ""
            layer = QgsVectorLayer(
                str(self.output_file),
                f"{title_str}{suffix}",
                "ogr",
            )
            if layer.isValid():
                apply_layer_metadata_and_style(layer, entry)
                QgsProject.instance().addMapLayer(layer)
                clamp_note = (
                    " (emprise réduite au centre de la carte)" if was_clamped else ""
                )
                if iface is not None and iface.messageBar() is not None:
                    iface.messageBar().pushMessage(
                        "Geostrata",
                        f"{self.feature_count} entités OSM chargées : {title_str}{clamp_note}",
                        level=Qgis.MessageLevel.Success,
                        duration=5,
                    )

    task = OverpassFetchTask()
    QgsApplication.taskManager().addTask(task)
    return task
