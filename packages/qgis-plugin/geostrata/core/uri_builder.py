"""URI builder and layer factory for all Geostrata source types."""

from __future__ import annotations

import datetime
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..diagnostics import note_ignored
from .db_client import CatalogSourceRecord

OVERPASS_INTERPRETER_URL = "https://overpass-api.de/api/interpreter"


@dataclass(frozen=True)
class ResolvedLayerSpec:
    """Resolved PyQGIS layer parameters ready for instantiation."""

    uri: str
    provider: str
    layer_kind: str  # 'raster' | 'vector' | 'vectortile'
    display_name: str


def _wfs_geometry_probe_option() -> str:
    """Ask QGIS 4 to read one feature before declaring the layer geometry type.

    DescribeFeatureType often announces 2D geometries while the features carry
    Z values (IGN BD TOPO), which QGIS 4 reports as "Received feature geometry
    has Z values but the layer type does not". QGIS 3 drops Z silently and does
    not know this option, so it is only added from QGIS 4.
    """
    try:
        from qgis.core import Qgis

        if int(Qgis.QGIS_VERSION_INT) >= 40000:
            return " forceInitialGetFeature='true'"
    except Exception as error:
        note_ignored(error)
    return ""


def _quote_uri_value(value: str) -> str:
    """Escape a layer or style name for a QGIS provider URI.

    QGIS 3.x does not decode %3A back to ':' in these values, so workspace
    prefixed names (GeoServer "workspace:layer") must keep their colon.
    """
    return urllib.parse.quote(value, safe=":")


TIME_PLACEHOLDER = "{time}"

# Types read by OGR from one remote file that cannot be read partially: the plugin
# downloads them once into its file cache (core/file_cache.py) instead of streaming.
DOWNLOADED_FILE_TYPES = ("archive", "geojson")

_OGR_GEOMETRY_TYPE_NAMES = {"point": "Point", "line": "LineString", "polygon": "Polygon"}

# OGC parameters the QGIS wms provider writes itself (or that the plugin maps to a
# provider URI key). Every other KVP parameter of the catalog URL (TIME, ELEVATION,
# DIM_*, map=, CQL_FILTER, access keys...) is kept in the service URL.
_OWS_MANAGED_PARAMS = frozenset(
    {
        "SERVICE", "REQUEST", "VERSION", "LAYERS", "LAYER", "STYLES", "STYLE",
        "FORMAT", "TILEMATRIXSET", "TILEMATRIX", "TILEROW", "TILECOL", "CRS", "SRS",
        "BBOX", "WIDTH", "HEIGHT", "TRANSPARENT",
    }
)


def resolve_time_value(
    time_spec: Optional[Dict[str, Any]],
    today: Optional[datetime.date] = None,
) -> str:
    """Render the date a `{time}` placeholder stands for.

    ``today (UTC) + offset_days`` (negative = past), truncated to the first day of the month
    when ``step`` is "month", written with ``format`` (tokens YYYY, MM, DD).
    """
    spec = time_spec or {}
    try:
        offset_days = int(spec.get("offset_days") or 0)
    except (TypeError, ValueError):
        offset_days = 0
    # "Today" is the UTC date, as in the catalog health check: daily products are
    # published by UTC day, whatever the time zone of the computer.
    reference = today or datetime.datetime.now(datetime.timezone.utc).date()
    day = reference + datetime.timedelta(days=offset_days)
    if str(spec.get("step") or "day").lower() == "month":
        day = day.replace(day=1)
    rendered = str(spec.get("format") or "YYYY-MM-DD")
    return (
        rendered.replace("YYYY", f"{day.year:04d}")
        .replace("MM", f"{day.month:02d}")
        .replace("DD", f"{day.day:02d}")
    )


def entry_time_value(
    entry: CatalogSourceRecord,
    today: Optional[datetime.date] = None,
) -> Optional[str]:
    """Return the resolved date of a time-templated entry, None for an ordinary entry."""
    templated = TIME_PLACEHOLDER in entry.url or TIME_PLACEHOLDER in (
        entry.archive_target or ""
    )
    if not templated:
        return None
    return resolve_time_value(entry.time, today)


def resolved_entry_url(
    entry: CatalogSourceRecord,
    today: Optional[datetime.date] = None,
) -> str:
    """Return the entry URL with `{time}` replaced by its resolved date."""
    url = entry.url.strip()
    if TIME_PLACEHOLDER in url:
        url = url.replace(TIME_PLACEHOLDER, resolve_time_value(entry.time, today))
    return url


def resolved_archive_target(
    entry: CatalogSourceRecord,
    today: Optional[datetime.date] = None,
) -> str:
    """Return `archive_target` (no leading slash) with `{time}` replaced."""
    target = (entry.archive_target or "").strip().lstrip("/")
    if TIME_PLACEHOLDER in target:
        target = target.replace(TIME_PLACEHOLDER, resolve_time_value(entry.time, today))
    return target


def entry_display_name(
    entry: CatalogSourceRecord,
    today: Optional[datetime.date] = None,
) -> str:
    """Localized layer name, followed by the resolved date for a time-templated entry."""
    name = entry.localized_name()
    time_value = entry_time_value(entry, today)
    return f"{name} ({time_value})" if time_value else name


def remote_file_url(
    entry: CatalogSourceRecord,
    today: Optional[datetime.date] = None,
) -> Optional[str]:
    """Return the plain http(s) URL of a file-based entry, None when it has none.

    GDAL prefixes written by catalog authors (/vsizip/, /vsicurl/) are removed.
    """
    url = resolved_entry_url(entry, today)
    url = re.sub(r"^/vsizip/+", "", url)
    url = re.sub(r"^/vsicurl(_streaming)?/", "", url)
    return url if url.lower().startswith(("http://", "https://")) else None


def needs_local_copy(entry: CatalogSourceRecord) -> bool:
    """True when the entry is loaded from a downloaded copy instead of being streamed.

    * ``archive``: a ZIP has no random access. Through /vsizip//vsicurl/ GDAL re-reads
      the deflate stream from the start for every seek of the GeoPackage/Shapefile
      inside (minutes for 100 MB, never ending for gigabytes).
    * ``geojson``: the driver must download and parse the whole document to open it.

    FlatGeobuf, COG and VRT stay streamed: they are designed for HTTP range requests
    (spatial index / internal tiles and overviews), so only the bytes of the visible
    extent are fetched, whatever the file size. Services (WMS, WFS, XYZ, ArcGIS...) are
    never downloaded.
    """
    return (
        entry.type.lower() in DOWNLOADED_FILE_TYPES
        and remote_file_url(entry) is not None
    )


def _ogr_sublayer_suffix(entry: CatalogSourceRecord) -> str:
    """Return the `|layername=...|geometrytype=...` suffix of an OGR URI ('' if none)."""
    suffix = ""
    if entry.layer:
        suffix += f"|layername={entry.layer}"
    ogr_type = _OGR_GEOMETRY_TYPE_NAMES.get((entry.geometry_type or "").lower())
    if ogr_type:
        suffix += f"|geometrytype={ogr_type}"
    return suffix


def _is_raster_target(target: str) -> bool:
    return target.lower().endswith((".tif", ".tiff", ".vrt", ".img"))


def _split_ows_url(url: str) -> Tuple[str, Dict[str, str], str]:
    """Split a WMS/WMTS URL into (endpoint, uppercase-keyed params, service URL).

    The service URL is the endpoint followed by every parameter QGIS does not manage
    itself, in their original order and spelling, so that e.g. ``TIME=2026-11-13`` is
    sent with each GetMap request.
    """
    parsed = urllib.parse.urlsplit(url)
    base_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    upper_params: Dict[str, str] = {}
    extras: List[Tuple[str, str]] = []
    for key, value in pairs:
        upper_params.setdefault(key.upper(), value)
        if key.upper() not in _OWS_MANAGED_PARAMS:
            extras.append((key, value))
    service_url = base_url
    if extras:
        service_url += "?" + urllib.parse.urlencode(
            extras, quote_via=urllib.parse.quote, safe=":/,"
        )
    return base_url, upper_params, service_url


def _wmts_dimension_values(service_url: str) -> str:
    """Return the `tileDimensions` value (``Time=2026-11-13;...``) for WMTS dimension KVPs."""
    query = urllib.parse.urlsplit(service_url).query
    dimensions = [
        f"{key}={value}"
        for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True)
        if key.upper() in ("TIME", "ELEVATION") or key.upper().startswith("DIM_")
    ]
    return ";".join(dimensions)


def _parse_query_params_ci(url: str) -> Tuple[str, Dict[str, str]]:
    """Split a URL into its base endpoint and uppercase-keyed query parameters."""
    parsed = urllib.parse.urlsplit(url)
    base_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )
    raw_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    upper_params: Dict[str, str] = {}
    for key, values in raw_params.items():
        if values:
            upper_params[key.upper()] = values[0]
    return base_url, upper_params


def build_overpass_ql(
    expression_or_ql: str,
    canvas_bbox_4326: Tuple[float, float, float, float],
) -> str:
    """Build a valid Overpass QL query bounded by the canvas bbox (south,west,north,east)."""
    min_x, min_y, max_x, max_y = canvas_bbox_4326
    # Overpass bbox order is (south, west, north, east) -> (min_y, min_x, max_y, max_x)
    overpass_bbox = f"{min_y:.6f},{min_x:.6f},{max_y:.6f},{max_x:.6f}"

    stripped = expression_or_ql.strip()
    if "{{bbox}}" in stripped or "{bbox}" in stripped:
        return stripped.replace("{{bbox}}", overpass_bbox).replace(
            "{bbox}", overpass_bbox
        )

    if stripped.startswith("[out:") or stripped.startswith("("):
        return stripped

    # Shorthand recipe format: key1=val1|key2=val2
    clauses: List[str] = []
    for raw_part in stripped.split("|"):
        part = raw_part.strip()
        if not part:
            continue
        if "=" in part:
            key, val = part.split("=", 1)
            key = key.strip()
            val = val.strip()
            clauses.append(f'nwr["{key}"="{val}"]({overpass_bbox});')
        else:
            clauses.append(f'nwr["{part}"]({overpass_bbox});')

    joined_clauses = "\n  ".join(clauses)
    return f"[out:xml][timeout:25];\n(\n  {joined_clauses}\n);\n(._;>;);\nout body;"


def build_provider_uri(
    entry: CatalogSourceRecord,
    canvas_bbox_4326: Optional[Tuple[float, float, float, float]] = None,
    local_path: Optional[str] = None,
    today: Optional[datetime.date] = None,
) -> ResolvedLayerSpec:
    """Construct the PyQGIS provider URI, provider key, and layer class kind.

    Args:
        entry: the catalog record.
        canvas_bbox_4326: map extent, only used by Overpass recipes.
        local_path: for file-based entries (see :func:`needs_local_copy`), the GDAL
            path of the downloaded copy to open instead of the remote URL.
        today: reference date of `{time}` templates (defaults to the current date).
    """
    source_type = entry.type.lower()
    raw_url = resolved_entry_url(entry, today)
    crs = entry.crs or "EPSG:4326"
    bbox = canvas_bbox_4326 or (entry.min_x, entry.min_y, entry.max_x, entry.max_y)
    display_title = entry_display_name(entry, today)

    if source_type in ("xyz", "tms"):
        zmin = entry.min_zoom if entry.min_zoom is not None else 0
        zmax = entry.max_zoom if entry.max_zoom is not None else 19
        encoded_url = urllib.parse.quote(raw_url, safe=":/?&=,{}")
        uri = f"type=xyz&url={encoded_url}&zmin={zmin}&zmax={zmax}&crs={crs}"
        return ResolvedLayerSpec(
            uri=uri,
            provider="wms",
            layer_kind="raster",
            display_name=display_title,
        )

    if source_type in ("wms", "wmts"):
        if "contextualWMSLegend=" in raw_url or raw_url.startswith("crs="):
            return ResolvedLayerSpec(
                uri=raw_url,
                provider="wms",
                layer_kind="raster",
                display_name=display_title,
            )

        base_url, params, service_url = _split_ows_url(raw_url)
        layer_name = params.get("LAYER") or params.get("LAYERS") or ""
        is_wmts = source_type == "wmts" or bool(params.get("TILEMATRIXSET"))
        # An empty WMS style means "server default"; "normal" is only a WMTS convention.
        style_name = (
            params.get("STYLE") or params.get("STYLES") or ("normal" if is_wmts else "")
        )
        img_format = params.get("FORMAT") or "image/png"
        tile_matrix_set = params.get("TILEMATRIXSET") or ""

        if source_type == "wmts" or tile_matrix_set:
            tms = tile_matrix_set or "PM"
            # The wms provider reads `url` as the capabilities document: a KVP
            # endpoint needs an explicit GetCapabilities request, a RESTful
            # WMTSCapabilities.xml URL is used as is.
            capabilities_url = service_url
            if not base_url.lower().endswith(".xml"):
                separator = "&" if "?" in service_url else "?"
                capabilities_url = (
                    f"{service_url}{separator}SERVICE=WMTS&REQUEST=GetCapabilities"
                )
            # WMTS dimensions (Time...) are not sent through the capabilities URL: the
            # provider substitutes them in each tile request from `tileDimensions`.
            dimensions = _wmts_dimension_values(service_url)
            dimensions_param = (
                f"&tileDimensions={urllib.parse.quote(dimensions, safe=':;')}"
                if dimensions
                else ""
            )
            uri = (
                f"contextualWMSLegend=0&crs={crs}&dpiMode=7&featureCount=10"
                f"&format={urllib.parse.quote(img_format, safe='/')}"
                f"&layers={_quote_uri_value(layer_name)}"
                f"&styles={_quote_uri_value(style_name)}"
                f"{dimensions_param}"
                f"&tileMatrixSet={urllib.parse.quote(tms)}"
                f"&url={urllib.parse.quote(capabilities_url, safe=':/?')}"
            )
        else:
            # QGIS sends GetMap to the URL advertised in the capabilities, which drops
            # the extra parameters kept in `service_url` (TIME=...). IgnoreGetMapUrl
            # makes it use our URL instead; only set when there is something to keep.
            ignore_advertised = "IgnoreGetMapUrl=1&" if service_url != base_url else ""
            uri = (
                f"{ignore_advertised}"
                f"contextualWMSLegend=0&crs={crs}&dpiMode=7&featureCount=10"
                f"&format={urllib.parse.quote(img_format, safe='/')}"
                f"&layers={_quote_uri_value(layer_name)}"
                f"&styles={_quote_uri_value(style_name)}"
                f"&url={urllib.parse.quote(service_url, safe=':/?')}"
            )
        return ResolvedLayerSpec(
            uri=uri,
            provider="wms",
            layer_kind="raster",
            display_name=display_title,
        )

    if source_type == "wfs":
        if "restrictToRequestBBOX=" in raw_url:
            return ResolvedLayerSpec(
                uri=raw_url,
                provider="WFS",
                layer_kind="vector",
                display_name=display_title,
            )
        base_url, params = _parse_query_params_ci(raw_url)
        typename = (
            params.get("TYPENAMES")
            or params.get("TYPENAME")
            or params.get("LAYERS")
            or ""
        )
        version = params.get("VERSION") or "auto"
        uri = (
            f"maxNumFeatures='20000' pagingEnabled='true' "
            f"preferCoordinatesForWfsT11='false' "
            f"restrictToRequestBBOX='1' srsname='{crs}' "
            f"typename='{typename}' url='{base_url}' version='{version}'"
            f"{_wfs_geometry_probe_option()}"
        )
        return ResolvedLayerSpec(
            uri=uri,
            provider="WFS",
            layer_kind="vector",
            display_name=display_title,
        )

    if source_type == "vectortile":
        zmin = entry.min_zoom if entry.min_zoom is not None else 0
        zmax = entry.max_zoom if entry.max_zoom is not None else 14
        uri = f"type=xyz&url={raw_url}&zmin={zmin}&zmax={zmax}"
        return ResolvedLayerSpec(
            uri=uri,
            provider="vectortile",
            layer_kind="vectortile",
            display_name=display_title,
        )

    if source_type in ("cog", "vrt"):
        uri = (
            raw_url
            if raw_url.startswith("/vsicurl/")
            else f"/vsicurl/{raw_url}"
        )
        return ResolvedLayerSpec(
            uri=uri,
            provider="gdal",
            layer_kind="raster",
            display_name=display_title,
        )

    if source_type in ("fgb", "geojson"):
        if local_path:
            uri = local_path
        elif raw_url.startswith("/vsicurl/"):
            uri = raw_url
        else:
            uri = f"/vsicurl/{raw_url}"
        return ResolvedLayerSpec(
            uri=uri + _ogr_sublayer_suffix(entry),
            provider="ogr",
            layer_kind="vector",
            display_name=display_title,
        )

    if source_type == "archive":
        target = resolved_archive_target(entry, today)
        is_raster = _is_raster_target(target)
        if local_path:
            # Downloaded copy: the extracted target, or /vsizip/<local zip> without target.
            uri = local_path
        else:
            clean_url = re.sub(r"^/vsizip/+", "", raw_url)
            if not clean_url.startswith("/vsicurl/"):
                clean_url = f"/vsicurl/{clean_url}"
            uri = f"/vsizip/{clean_url}/{target}" if target else f"/vsizip/{clean_url}"
        if not is_raster:
            uri += _ogr_sublayer_suffix(entry)
        return ResolvedLayerSpec(
            uri=uri,
            provider="gdal" if is_raster else "ogr",
            layer_kind="raster" if is_raster else "vector",
            display_name=display_title,
        )

    if source_type == "arcgisfeatureserver":
        # `url` is the layer endpoint (.../FeatureServer/0). No `crs`: the layer keeps
        # the CRS of the service and QGIS reprojects it. Asking the server for another
        # CRS breaks the spatial filter on some servers (0 features drawn, seen on
        # gis.unhcr.org with QGIS 4.2).
        return ResolvedLayerSpec(
            uri=f"url='{raw_url.rstrip('/')}'",
            provider="arcgisfeatureserver",
            layer_kind="vector",
            display_name=display_title,
        )

    if source_type == "arcgismapserver":
        # `url` is the MapServer / ImageServer root, `layer` the optional sublayer id
        # (empty = the whole service). The provider uses the CRS of the service.
        sublayer = (entry.layer or "").strip()
        uri = f"format='PNG32' layer='{sublayer}' url='{raw_url.rstrip('/')}'"
        return ResolvedLayerSpec(
            uri=uri,
            provider="arcgismapserver",
            layer_kind="raster",
            display_name=display_title,
        )

    if source_type == "overpass":
        ql_query = build_overpass_ql(raw_url, bbox)
        encoded_ql = urllib.parse.quote(ql_query)
        uri = f"/vsicurl_streaming/{OVERPASS_INTERPRETER_URL}?data={encoded_ql}"
        return ResolvedLayerSpec(
            uri=uri,
            provider="ogr",
            layer_kind="vector",
            display_name=display_title,
        )

    raise ValueError(f"Unsupported Geostrata source type: {entry.type}")


_QML_SYMBOL_TYPE_BY_GEOMETRY = {0: "marker", 1: "line", 2: "fill"}


def _style_matches_layer_geometry(layer: Any, style_qml: str) -> bool:
    """Reject a category default style whose symbol cannot draw this layer.

    A fill symbol applied to a point layer (or a line symbol to polygons)
    renders nothing at all, which looks like an empty layer to the user.
    """
    try:
        if not hasattr(layer, "geometryType"):
            # Raster and vector-tile layers have no single-symbol QML to apply.
            return 'symbol type="' not in style_qml
        geometry_type = layer.geometryType()
        geometry_code = int(getattr(geometry_type, "value", geometry_type))
        expected = _QML_SYMBOL_TYPE_BY_GEOMETRY.get(geometry_code)
        if expected is None:
            return False
        declared = re.search(r'<symbol[^>]*\btype="(marker|line|fill)"', style_qml)
        return declared is None or declared.group(1) == expected
    except Exception:
        return True


def apply_layer_metadata_and_style(layer: Any, entry: CatalogSourceRecord) -> None:
    """Apply attribution, license metadata, and embedded QML style XML to a QGIS layer."""
    if layer is None or not layer.isValid():
        return

    try:
        metadata = layer.metadata()
        metadata.setTitle(entry_display_name(entry))
        desc = entry.localized_description()
        if desc:
            metadata.setAbstract(desc)
        metadata.setRights([entry.attribution])
        metadata.setLicenses([entry.license])
        layer.setMetadata(metadata)
    except Exception as error:
        note_ignored(error)

    try:
        if hasattr(layer, "setAttribution"):
            layer.setAttribution(entry.attribution)
        if hasattr(layer, "serverProperties") and layer.serverProperties() is not None:
            layer.serverProperties().setAttribution(entry.attribution)
    except Exception as error:
        note_ignored(error)

    if entry.style_qml and _style_matches_layer_geometry(layer, entry.style_qml):
        try:
            from qgis.PyQt.QtXml import QDomDocument

            doc = QDomDocument()
            ok, *_ = doc.setContent(entry.style_qml)
            if ok:
                layer.importNamedStyle(doc)
                layer.triggerRepaint()
        except Exception as error:
            note_ignored(error)


def instantiate_layer(spec: ResolvedLayerSpec) -> Any:
    """Create the QgsMapLayer of a resolved spec, without metadata or style.

    Safe to call from a background QgsTask: the caller must then move the layer to
    the main thread (``layer.moveToThread(QgsApplication.instance().thread())``)
    before handing it over, and apply style / add it to the project on the main thread.
    """
    from qgis.core import QgsRasterLayer, QgsVectorLayer, QgsVectorTileLayer

    if spec.layer_kind == "raster":
        return QgsRasterLayer(spec.uri, spec.display_name, spec.provider)
    if spec.layer_kind == "vectortile":
        return QgsVectorTileLayer(spec.uri, spec.display_name)
    return QgsVectorLayer(spec.uri, spec.display_name, spec.provider)


def _assign_declared_crs_if_missing(layer: Any, entry: CatalogSourceRecord) -> None:
    """Give a file without CRS information (shapefile lacking its .prj) the CRS declared in the catalog."""
    try:
        if layer.crs().isValid() or not entry.crs:
            return
        from qgis.core import QgsCoordinateReferenceSystem

        declared = QgsCoordinateReferenceSystem(entry.crs)
        if declared.isValid():
            layer.setCrs(declared)
    except Exception as error:
        note_ignored(error)


def create_qgis_layer(
    entry: CatalogSourceRecord,
    canvas_bbox_4326: Optional[Tuple[float, float, float, float]] = None,
    local_path: Optional[str] = None,
) -> Tuple[Any, ResolvedLayerSpec]:
    """Instantiate a QgsMapLayer from a CatalogSourceRecord and apply its style.

    Synchronous: the provider opens its source in the calling thread. Without
    `local_path`, file-based entries are streamed (/vsicurl/), which blocks for as
    long as GDAL needs to read them; the interface uses core/loader_task.py instead
    and only calls this with the path of a downloaded copy.
    """
    from qgis.core import QgsRasterLayer, QgsVectorLayer, QgsVectorTileLayer

    spec = build_provider_uri(entry, canvas_bbox_4326, local_path=local_path)

    if spec.layer_kind == "raster":
        layer = QgsRasterLayer(spec.uri, spec.display_name, spec.provider)
    elif spec.layer_kind == "vectortile":
        layer = QgsVectorTileLayer(spec.uri, spec.display_name)
    else:
        layer = QgsVectorLayer(spec.uri, spec.display_name, spec.provider)
        if not layer.isValid() and entry.type.lower() == "geojson":
            # Live feeds (earthquakes, alerts) are generated on the fly: no
            # Content-Length, no Range support, so /vsicurl/ refuses them.
            # The GeoJSON driver can still download such a URL in one request.
            plain_url = re.sub(r"^/vsicurl/", "", spec.uri)
            if plain_url.startswith(("http://", "https://")):
                fallback = QgsVectorLayer(plain_url, spec.display_name, spec.provider)
                if fallback.isValid():
                    layer = fallback
                    spec = ResolvedLayerSpec(
                        uri=plain_url,
                        provider=spec.provider,
                        layer_kind=spec.layer_kind,
                        display_name=spec.display_name,
                    )

    if layer.isValid():
        _assign_declared_crs_if_missing(layer, entry)
        apply_layer_metadata_and_style(layer, entry)

    return layer, spec
