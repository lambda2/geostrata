"""Resilient asynchronous Overpass API fetcher with priority-ordered OSM Wiki mirrors and GeoJSON conversion."""

from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..i18n import tr
from .net import build_direct_opener
from .db_client import CatalogSourceRecord
from .extractor_task import resolve_extraction_output_dir
from .uri_builder import apply_layer_metadata_and_style

SETTINGS_OVERPASS_SERVERS_KEY = "geostrata/overpass_servers_json"

# Default Overpass API servers from https://wiki.openstreetmap.org/wiki/Overpass_API
# Ordered by priority: fast regional instances first (used when canvas bbox falls within their coverage),
# followed by global instances, then optional API-key instances.
DEFAULT_OVERPASS_SERVERS: List[Dict[str, Any]] = [
    {
        "name": "Overpass Europe (dresel.systems)",
        "url": "https://ovp.dresel.systems/api/interpreter",
        "enabled": True,
        "region": "Europe",
        "bbox": [-25.0, 34.0, 45.0, 72.0],
    },
    {
        "name": "Swiss Overpass (overpass.osm.ch)",
        "url": "https://overpass.osm.ch/api/interpreter",
        "enabled": True,
        "region": "Switzerland",
        "bbox": [5.9, 45.8, 10.5, 47.9],
    },
    {
        "name": "Britain & Ireland (atownsend.org.uk)",
        "url": "https://overpass.atownsend.org.uk/api/interpreter",
        "enabled": True,
        "region": "UK & Ireland",
        "bbox": [-11.0, 49.8, 2.0, 61.0],
    },
    {
        "name": "Overpass North America (dresel.systems)",
        "url": "https://ovp.dresel.systems/api-na/interpreter",
        "enabled": True,
        "region": "North America",
        "bbox": [-170.0, 14.0, -50.0, 75.0],
    },
    {
        "name": "MapRVA Virginia (overpass.maprva.org)",
        "url": "https://overpass.maprva.org/api/interpreter",
        "enabled": True,
        "region": "US-VA",
        "bbox": [-83.7, 36.5, -75.2, 39.5],
    },
    {
        "name": "Ethiopia OpenPlaceGuide",
        "url": "https://ethiopia.overpass.openplaceguide.org/api/interpreter",
        "enabled": True,
        "region": "Ethiopia",
        "bbox": [32.9, 3.4, 48.0, 14.9],
    },
    {
        "name": "Private.coffee Overpass (Global)",
        "url": "https://overpass.private.coffee/api/interpreter",
        "enabled": True,
        "region": "Global",
        "bbox": None,
    },
    {
        "name": "Main Overpass API FOSSGIS (Global)",
        "url": "https://overpass-api.de/api/interpreter",
        "enabled": True,
        "region": "Global",
        "bbox": None,
    },
    {
        "name": "VK Maps Overpass (Global)",
        "url": "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        "enabled": True,
        "region": "Global",
        "bbox": None,
    },
    {
        "name": "Overpass.ch (API key required)",
        "url": "https://api.overpass.ch/v1/YOUR_API_KEY/interpreter",
        "enabled": False,
        "region": "Global (API Key)",
        "bbox": None,
    },
    {
        "name": "NextGIS Overpass (API key required)",
        "url": "https://overpass.nextgis.com/YOUR_API_KEY/api/interpreter",
        "enabled": False,
        "region": "Global (API Key)",
        "bbox": None,
    },
    {
        "name": "FairwayMapper (API key required)",
        "url": "https://api.fairwaymapper.com/k/YOUR_API_KEY/api/interpreter",
        "enabled": False,
        "region": "Global (API Key)",
        "bbox": None,
    },
    {
        "name": "Geofabrik Overpass (API key required)",
        "url": "https://overpass.geofabrik.de/YOUR_API_KEY/api/interpreter",
        "enabled": False,
        "region": "Global (API Key)",
        "bbox": None,
    },
]

OVERPASS_MIRRORS: Tuple[str, ...] = tuple(
    str(s["url"]) for s in DEFAULT_OVERPASS_SERVERS if s.get("enabled")
)

MAX_OVERPASS_AREA_DEG2 = 16.0
MAX_CLAMPED_HALF_SPAN_DEG = 1.5
# Polygon recipes ask for the full geometry of every way and relation, far heavier
# than one centre point each: the extent is clamped to 0.5 x 0.5 degrees (~55 km).
MAX_OVERPASS_POLYGON_AREA_DEG2 = 0.25
MAX_CLAMPED_POLYGON_HALF_SPAN_DEG = 0.25

GEOMETRY_POINT = "point"
GEOMETRY_LINE = "line"
GEOMETRY_POLYGON = "polygon"

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

# Strong references to active QgsTask objects to prevent premature Python GC crashes
_ACTIVE_OVERPASS_TASKS: List[Any] = []


def normalize_overpass_endpoint(url: str) -> str:
    """Normalize an Overpass endpoint URL so it ends with /interpreter when needed."""
    cleaned = url.strip()
    if not cleaned:
        return ""
    if cleaned.endswith("/api/"):
        return cleaned + "interpreter"
    if cleaned.endswith("/api"):
        return cleaned + "/interpreter"
    return cleaned


def get_configured_overpass_servers() -> List[Dict[str, Any]]:
    """Return the user's priority-ordered list of Overpass servers from QgsSettings."""
    try:
        from qgis.core import QgsSettings

        raw = QgsSettings().value(SETTINGS_OVERPASS_SERVERS_KEY, "")
        if raw:
            parsed = json.loads(str(raw))
            if isinstance(parsed, list) and parsed:
                valid: List[Dict[str, Any]] = []
                for item in parsed:
                    if isinstance(item, dict) and item.get("url"):
                        valid.append(
                            {
                                "name": str(item.get("name") or item["url"]),
                                "url": normalize_overpass_endpoint(str(item["url"])),
                                "enabled": bool(item.get("enabled", True)),
                                "region": str(item.get("region") or "Global"),
                                "bbox": item.get("bbox"),
                            }
                        )
                if valid:
                    return valid
    except Exception:
        pass
    return [dict(s) for s in DEFAULT_OVERPASS_SERVERS]


def save_configured_overpass_servers(servers: Sequence[Dict[str, Any]]) -> None:
    """Persist the priority-ordered list of Overpass servers into QgsSettings."""
    try:
        from qgis.core import QgsSettings

        serializable = []
        for s in servers:
            url = normalize_overpass_endpoint(str(s.get("url") or ""))
            if not url:
                continue
            serializable.append(
                {
                    "name": str(s.get("name") or url),
                    "url": url,
                    "enabled": bool(s.get("enabled", True)),
                    "region": str(s.get("region") or "Global"),
                    "bbox": s.get("bbox"),
                }
            )
        QgsSettings().setValue(
            SETTINGS_OVERPASS_SERVERS_KEY,
            json.dumps(serializable, ensure_ascii=False),
        )
    except Exception:
        pass


def _bbox_fits_server_region(
    query_bbox: Optional[Tuple[float, float, float, float]],
    server_bbox: Any,
) -> bool:
    """Return True if the server covers the query_bbox (or has global coverage)."""
    if query_bbox is None or not isinstance(server_bbox, (list, tuple)) or len(server_bbox) != 4:
        return True
    try:
        q_min_x, q_min_y, q_max_x, q_max_y = query_bbox
        s_min_x, s_min_y, s_max_x, s_max_y = (
            float(server_bbox[0]),
            float(server_bbox[1]),
            float(server_bbox[2]),
            float(server_bbox[3]),
        )
        # Check that the center of the query bbox is inside the regional server's bbox
        center_x = (q_min_x + q_max_x) / 2.0
        center_y = (q_min_y + q_max_y) / 2.0
        return (s_min_x <= center_x <= s_max_x) and (s_min_y <= center_y <= s_max_y)
    except Exception:
        return True


def resolve_priority_overpass_urls(
    query_bbox: Optional[Tuple[float, float, float, float]] = None,
) -> List[str]:
    """Return enabled Overpass endpoint URLs in priority order, filtering regional servers by bbox."""
    servers = get_configured_overpass_servers()
    urls: List[str] = []
    for srv in servers:
        if not srv.get("enabled", True):
            continue
        url = normalize_overpass_endpoint(str(srv.get("url") or ""))
        if not url or "YOUR_API_KEY" in url:
            continue
        if not _bbox_fits_server_region(query_bbox, srv.get("bbox")):
            continue
        if url not in urls:
            urls.append(url)
    if not urls:
        urls = [
            "https://ovp.dresel.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass-api.de/api/interpreter",
        ]
    return urls


def is_linear_overpass_entry(entry: CatalogSourceRecord) -> bool:
    """Determine if an Overpass CatalogSourceRecord expects LineString geometries."""
    if entry.style_qml and 'symbol type="line"' in entry.style_qml:
        return True
    expr_lower = entry.url.lower()
    return any(token in expr_lower for token in LINEAR_OVERPASS_KEYS)


def resolve_overpass_geometry_mode(entry: CatalogSourceRecord) -> str:
    """Decide which geometry an Overpass recipe returns: 'point', 'line' or 'polygon'.

    1. The catalog `geometry_type` column when the author set it.
    2. Otherwise lines when the embedded style is a line symbol or the recipe uses a
       linear OSM key (waterway, railway, power line...).
    3. Otherwise polygons when the embedded style is a fill symbol.
    4. Otherwise one point per feature (node position, or centre of the way/relation):
       the historical behaviour, cheap for the servers and right for points of interest
       (a hospital is found whether it is mapped as a node or as a building).
    """
    if entry.geometry_type in (GEOMETRY_POINT, GEOMETRY_LINE, GEOMETRY_POLYGON):
        return str(entry.geometry_type)
    if is_linear_overpass_entry(entry):
        return GEOMETRY_LINE
    if entry.style_qml and 'symbol type="fill"' in entry.style_qml:
        return GEOMETRY_POLYGON
    return GEOMETRY_POINT


def clamp_overpass_bbox(
    bbox_4326: Tuple[float, float, float, float],
    geometry_mode: Optional[str] = None,
) -> Tuple[Tuple[float, float, float, float], bool]:
    """Clamp overly large canvas extents around the canvas center to protect Overpass.

    Polygon recipes get a much smaller window than point/line recipes.
    """
    max_area = MAX_OVERPASS_AREA_DEG2
    half_span = MAX_CLAMPED_HALF_SPAN_DEG
    if geometry_mode == GEOMETRY_POLYGON:
        max_area = MAX_OVERPASS_POLYGON_AREA_DEG2
        half_span = MAX_CLAMPED_POLYGON_HALF_SPAN_DEG

    min_x, min_y, max_x, max_y = bbox_4326
    area = max(0.0, (max_x - min_x) * (max_y - min_y))
    if area <= max_area:
        return bbox_4326, False

    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    clamped = (
        max(-180.0, center_x - half_span),
        max(-90.0, center_y - half_span),
        min(180.0, center_x + half_span),
        min(90.0, center_y + half_span),
    )
    return clamped, True


def build_overpass_json_ql(
    expression_or_ql: str,
    bbox_4326: Tuple[float, float, float, float],
    prefer_lines: bool = False,
    geometry_mode: Optional[str] = None,
) -> str:
    """Build an [out:json] Overpass QL query for a shorthand recipe (`key=value|key=value`).

    `geometry_mode` (default: 'line' when `prefer_lines`, else 'point') selects the output:
    ways with `out geom;` for lines, nodes/ways/relations with `out geom;` for polygons,
    nodes/ways/relations with `out center;` for points. Full QL recipes are left as written.
    """
    mode = geometry_mode or (GEOMETRY_LINE if prefer_lines else GEOMETRY_POINT)
    prefer_lines = mode == GEOMETRY_LINE
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
    if mode == GEOMETRY_POLYGON:
        # Full geometry of ways and of relation members; a longer server timeout and an
        # explicit memory cap (256 MB) so an oversized request fails fast and cleanly.
        return (
            f"[out:json][timeout:25][maxsize:268435456];\n(\n  {joined_clauses}\n);\nout geom;"
        )
    return (
        f"[out:json][timeout:15];\n(\n  {joined_clauses}\n);\n{out_directive}"
    )


Ring = List[List[float]]


def _way_coords(raw_geometry: Any) -> Ring:
    """Read the `geometry` array of an Overpass way (or relation member) as [lon, lat] pairs."""
    if not isinstance(raw_geometry, list):
        return []
    return [
        [float(pt["lon"]), float(pt["lat"])]
        for pt in raw_geometry
        if isinstance(pt, dict) and "lon" in pt and "lat" in pt
    ]


def _is_closed_ring(coords: Ring) -> bool:
    return len(coords) >= 4 and coords[0] == coords[-1]


def _assemble_rings(segments: List[Ring]) -> List[Ring]:
    """Join way segments end to end into closed rings (unclosed leftovers are dropped)."""
    rings: List[Ring] = []
    pending = [list(seg) for seg in segments if len(seg) >= 2]
    while pending:
        current = pending.pop(0)
        while not _is_closed_ring(current):
            tail = current[-1]
            for idx, candidate in enumerate(pending):
                if candidate[0] == tail:
                    current.extend(candidate[1:])
                elif candidate[-1] == tail:
                    current.extend(reversed(candidate[:-1]))
                else:
                    continue
                pending.pop(idx)
                break
            else:
                break  # nothing continues this segment
        if _is_closed_ring(current):
            rings.append(current)
    return rings


def _point_in_ring(point: List[float], ring: Ring) -> bool:
    """Ray-casting point-in-polygon test."""
    x, y = point
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _relation_polygons(elem: Dict[str, Any]) -> List[List[Ring]]:
    """Build the polygons (outer ring + holes) of a multipolygon / boundary relation."""
    outers: List[Ring] = []
    inners: List[Ring] = []
    members = elem.get("members")
    for member in members if isinstance(members, list) else []:
        if not isinstance(member, dict) or member.get("type") != "way":
            continue
        coords = _way_coords(member.get("geometry"))
        if len(coords) < 2:
            continue
        (inners if member.get("role") == "inner" else outers).append(coords)

    polygons: List[List[Ring]] = [[ring] for ring in _assemble_rings(outers)]
    for hole in _assemble_rings(inners):
        for polygon in polygons:
            if _point_in_ring(hole[0], polygon[0]):
                polygon.append(hole)
                break
    return polygons


def _coords_center(coords: Ring) -> Optional[List[float]]:
    """Centre of the bounding box of a coordinate list (what `out center` returns)."""
    if not coords:
        return None
    xs = [pt[0] for pt in coords]
    ys = [pt[1] for pt in coords]
    return [(min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0]


def _polygon_mode_geometry(elem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Geometry of one element of a polygon recipe: MultiPolygon for areas, else a Point.

    Closed ways and multipolygon relations become MultiPolygons (one type, so the layer
    is homogeneous). What OSM maps without an area (a node, an unclosed way, a relation
    whose rings cannot be closed inside the extent) is kept as a point so that no feature
    is lost; the caller puts those in a companion point layer.
    """
    elem_type = elem.get("type")
    if elem_type == "node":
        if "lon" in elem and "lat" in elem:
            return {"type": "Point", "coordinates": [float(elem["lon"]), float(elem["lat"])]}
        return None

    if elem_type == "way":
        coords = _way_coords(elem.get("geometry"))
        if _is_closed_ring(coords):
            return {"type": "MultiPolygon", "coordinates": [[coords]]}
        center = _coords_center(coords)
        return {"type": "Point", "coordinates": center} if center else None

    if elem_type == "relation":
        polygons = _relation_polygons(elem)
        if polygons:
            return {"type": "MultiPolygon", "coordinates": polygons}
        all_coords: Ring = []
        members = elem.get("members")
        for member in members if isinstance(members, list) else []:
            if not isinstance(member, dict):
                continue
            if "lon" in member and "lat" in member:
                all_coords.append([float(member["lon"]), float(member["lat"])])
            all_coords.extend(_way_coords(member.get("geometry")))
        center = _coords_center(all_coords)
        return {"type": "Point", "coordinates": center} if center else None

    return None


_GEOMETRY_KIND_BY_GEOJSON_TYPE = {
    "Point": GEOMETRY_POINT,
    "MultiPoint": GEOMETRY_POINT,
    "LineString": GEOMETRY_LINE,
    "MultiLineString": GEOMETRY_LINE,
    "Polygon": GEOMETRY_POLYGON,
    "MultiPolygon": GEOMETRY_POLYGON,
}


def split_features_by_geometry(geojson: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Split a FeatureCollection into one homogeneous collection per geometry kind.

    Returns ``{"polygon": {...}, "point": {...}}`` with only the kinds present, since a
    QGIS vector layer holds a single geometry type.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    features = geojson.get("features")
    for feature in features if isinstance(features, list) else []:
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        kind = _GEOMETRY_KIND_BY_GEOJSON_TYPE.get(
            str(geometry.get("type")) if isinstance(geometry, dict) else ""
        )
        if kind is None:
            continue
        groups.setdefault(kind, {"type": "FeatureCollection", "features": []})[
            "features"
        ].append(feature)
    return groups


def convert_overpass_json_to_geojson(
    overpass_payload: Dict[str, Any],
    prefer_lines: bool = False,
    geometry_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert an Overpass JSON response (`elements`) into a GeoJSON FeatureCollection.

    The collection is homogeneous in 'point' and 'line' modes. In 'polygon' mode it holds
    MultiPolygons plus Points for the features OSM maps without an area (see
    :func:`split_features_by_geometry`).
    """
    mode = geometry_mode or (GEOMETRY_LINE if prefer_lines else GEOMETRY_POINT)
    prefer_lines = mode == GEOMETRY_LINE
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

        if mode == GEOMETRY_POLYGON:
            geometry = _polygon_mode_geometry(elem)
        elif prefer_lines:
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


# HTTP timeouts: a centre-point query answers in a few seconds; full geometries need
# the server-side [timeout:25] plus transfer time.
OVERPASS_HTTP_TIMEOUT_SEC = 8
OVERPASS_GEOMETRY_HTTP_TIMEOUT_SEC = 30


def fetch_overpass_with_failover(
    ql_query: str,
    mirrors: Optional[Sequence[str]] = None,
    timeout_sec: int = OVERPASS_HTTP_TIMEOUT_SEC,
    query_bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Tuple[Dict[str, Any], str]:
    """Execute Overpass QL POST across priority-ordered mirrors until one succeeds.

    Uses a dedicated ProxyHandler({}) opener so worker threads on macOS never
    invoke the non-thread-safe _scproxy C API.
    """
    target_mirrors = (
        list(mirrors)
        if mirrors is not None
        else resolve_priority_overpass_urls(query_bbox)
    )
    encoded_body = urllib.parse.urlencode({"data": ql_query}).encode("utf-8")
    errors: List[str] = []
    # Bypass macOS _scproxy in background worker threads to prevent CFNetwork segfaults
    opener = build_direct_opener()

    for mirror_url in target_mirrors:
        req = urllib.request.Request(
            mirror_url,
            data=encoded_body,
            method="POST",
            headers={
                "User-Agent": "Geostrata-QGIS-Plugin/1.0 (+https://github.com/lambda2/geostrata)",
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "Accept": "application/json",
            },
        )
        try:
            with opener.open(req, timeout=timeout_sec) as resp:
                raw_bytes = resp.read()
            payload = json.loads(raw_bytes.decode("utf-8"))
            if isinstance(payload, dict) and "elements" in payload:
                return payload, mirror_url
            errors.append(f"{mirror_url}: unexpected JSON response")
        except Exception as exc:
            errors.append(f"{mirror_url}: {exc}")

    raise RuntimeError(
        tr("All Overpass servers failed ({errors})", errors=" ; ".join(errors))
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

    geometry_mode = resolve_overpass_geometry_mode(entry)
    clamped_bbox, was_clamped = clamp_overpass_bbox(canvas_bbox_4326, geometry_mode)
    ql_query = build_overpass_json_ql(
        entry.url,
        clamped_bbox,
        geometry_mode=geometry_mode,
    )
    http_timeout = (
        OVERPASS_GEOMETRY_HTTP_TIMEOUT_SEC
        if geometry_mode == GEOMETRY_POLYGON
        else OVERPASS_HTTP_TIMEOUT_SEC
    )
    priority_urls = resolve_priority_overpass_urls(clamped_bbox)
    out_dir = resolve_extraction_output_dir()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_geojson_path = out_dir / f"{entry.id}_{timestamp}.geojson"
    title_str = entry.localized_name()

    class OverpassFetchTask(QgsTask):
        """Background task querying Overpass mirrors and writing a local GeoJSON."""

        def __init__(self) -> None:
            super().__init__(
                tr("Geostrata Overpass: {name}", name=title_str),
                QgsTask.CanCancel,
            )
            self.output_file: Path = output_geojson_path
            # (geometry kind, file) of each layer to add; the first one is the main layer.
            self.output_files: List[Tuple[str, Path]] = []
            self.feature_count: int = 0
            self.mirror_used: str = ""
            self.error_message: Optional[str] = None

        def run(self) -> bool:
            if self.isCanceled():
                return False
            try:
                payload, mirror = fetch_overpass_with_failover(
                    ql_query,
                    mirrors=priority_urls,
                    timeout_sec=http_timeout,
                    query_bbox=clamped_bbox,
                )
                if self.isCanceled():
                    return False
                self.mirror_used = mirror

                geojson_dict = convert_overpass_json_to_geojson(
                    payload,
                    geometry_mode=geometry_mode,
                )
                features = geojson_dict.get("features", [])
                self.feature_count = len(features) if isinstance(features, list) else 0

                # One file per geometry kind (a polygon recipe may also return points);
                # the kind the recipe asks for comes first and keeps the plain file name.
                groups = split_features_by_geometry(geojson_dict)
                for kind in sorted(groups, key=lambda k: (k != geometry_mode, k)):
                    path = (
                        self.output_file
                        if not self.output_files
                        else self.output_file.with_name(
                            f"{self.output_file.stem}_{kind}.geojson"
                        )
                    )
                    path.write_text(
                        json.dumps(groups[kind], ensure_ascii=False),
                        encoding="utf-8",
                    )
                    self.output_files.append((kind, path))
                if not self.output_files:
                    self.output_file.write_text(
                        json.dumps(geojson_dict, ensure_ascii=False),
                        encoding="utf-8",
                    )
                return True
            except Exception as exc:
                self.error_message = str(exc)
                return False

        def finished(self, result: bool) -> None:
            try:
                if self in _ACTIVE_OVERPASS_TASKS:
                    _ACTIVE_OVERPASS_TASKS.remove(self)
            except Exception:
                pass

            if not result or not self.output_file.is_file():
                msg = (
                    self.error_message
                    or tr("Overpass query failed for {name}", name=title_str)
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
                        tr("No OSM feature found in this extent for: {name}", name=title_str),
                        level=Qgis.MessageLevel.Info,
                        duration=5,
                    )
                return

            suffix = f" {tr('[Local]')}" if is_offline_extract else ""
            kind_labels = {
                GEOMETRY_POINT: tr("points"),
                GEOMETRY_LINE: tr("lines"),
                GEOMETRY_POLYGON: tr("polygons"),
            }
            added = 0
            # Main layer first: companion point layers are then drawn above the polygons.
            for index, (kind, path) in enumerate(self.output_files):
                kind_note = f" ({kind_labels.get(kind, kind)})" if index else ""
                layer = QgsVectorLayer(
                    str(path),
                    f"{title_str}{kind_note}{suffix}",
                    "ogr",
                )
                if layer.isValid():
                    apply_layer_metadata_and_style(layer, entry)
                    QgsProject.instance().addMapLayer(layer)
                    added += 1
            if added:
                clamp_note = (
                    tr(" (extent reduced to the centre of the map)") if was_clamped else ""
                )
                host = urllib.parse.urlsplit(self.mirror_used).netloc or self.mirror_used
                if iface is not None and iface.messageBar() is not None:
                    iface.messageBar().pushMessage(
                        "Geostrata",
                        tr(
                            "{count} OSM features loaded via {host}: {name}{note}",
                            count=self.feature_count,
                            host=host,
                            name=title_str,
                            note=clamp_note,
                        ),
                        level=Qgis.MessageLevel.Success,
                        duration=5,
                    )

    task = OverpassFetchTask()
    _ACTIVE_OVERPASS_TASKS.append(task)
    QgsApplication.taskManager().addTask(task)
    return task
