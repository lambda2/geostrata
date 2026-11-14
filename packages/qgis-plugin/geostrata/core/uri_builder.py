"""URI builder and layer factory for all Geostrata source types."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .db_client import CatalogSourceRecord

OVERPASS_INTERPRETER_URL = "https://overpass-api.de/api/interpreter"


@dataclass(frozen=True)
class ResolvedLayerSpec:
    """Resolved PyQGIS layer parameters ready for instantiation."""

    uri: str
    provider: str
    layer_kind: str  # 'raster' | 'vector' | 'vectortile'
    display_name: str


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
) -> ResolvedLayerSpec:
    """Construct the PyQGIS provider URI, provider key, and layer class kind."""
    source_type = entry.type.lower()
    raw_url = entry.url.strip()
    crs = entry.crs or "EPSG:4326"
    bbox = canvas_bbox_4326 or (entry.min_x, entry.min_y, entry.max_x, entry.max_y)
    display_title = entry.localized_name()

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

        base_url, params = _parse_query_params_ci(raw_url)
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
            capabilities_url = base_url
            if not base_url.lower().endswith(".xml"):
                capabilities_url = f"{base_url}?SERVICE=WMTS&REQUEST=GetCapabilities"
            uri = (
                f"contextualWMSLegend=0&crs={crs}&dpiMode=7&featureCount=10"
                f"&format={urllib.parse.quote(img_format, safe='/')}"
                f"&layers={urllib.parse.quote(layer_name)}"
                f"&styles={urllib.parse.quote(style_name)}"
                f"&tileMatrixSet={urllib.parse.quote(tms)}"
                f"&url={urllib.parse.quote(capabilities_url, safe=':/?')}"
            )
        else:
            uri = (
                f"contextualWMSLegend=0&crs={crs}&dpiMode=7&featureCount=10"
                f"&format={urllib.parse.quote(img_format, safe='/')}"
                f"&layers={urllib.parse.quote(layer_name)}"
                f"&styles={urllib.parse.quote(style_name)}"
                f"&url={urllib.parse.quote(base_url, safe=':/')}"
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
        uri = (
            raw_url
            if raw_url.startswith("/vsicurl/")
            else f"/vsicurl/{raw_url}"
        )
        return ResolvedLayerSpec(
            uri=uri,
            provider="ogr",
            layer_kind="vector",
            display_name=display_title,
        )

    if source_type == "archive":
        clean_url = re.sub(r"^/vsizip/+", "", raw_url)
        if not clean_url.startswith("/vsicurl/"):
            clean_url = f"/vsicurl/{clean_url}"
        target = (entry.archive_target or "").lstrip("/")
        uri = f"/vsizip/{clean_url}/{target}" if target else f"/vsizip/{clean_url}"
        is_raster = target.lower().endswith((".tif", ".tiff", ".vrt", ".img"))
        return ResolvedLayerSpec(
            uri=uri,
            provider="gdal" if is_raster else "ogr",
            layer_kind="raster" if is_raster else "vector",
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
        metadata.setTitle(entry.localized_name())
        desc = entry.localized_description()
        if desc:
            metadata.setAbstract(desc)
        metadata.setRights([entry.attribution])
        metadata.setLicenses([entry.license])
        layer.setMetadata(metadata)
    except Exception:
        pass

    try:
        if hasattr(layer, "setAttribution"):
            layer.setAttribution(entry.attribution)
        if hasattr(layer, "serverProperties") and layer.serverProperties() is not None:
            layer.serverProperties().setAttribution(entry.attribution)
    except Exception:
        pass

    if entry.style_qml and _style_matches_layer_geometry(layer, entry.style_qml):
        try:
            from qgis.PyQt.QtXml import QDomDocument

            doc = QDomDocument()
            ok, *_ = doc.setContent(entry.style_qml)
            if ok:
                layer.importNamedStyle(doc)
                layer.triggerRepaint()
        except Exception:
            pass


def create_qgis_layer(
    entry: CatalogSourceRecord,
    canvas_bbox_4326: Optional[Tuple[float, float, float, float]] = None,
) -> Tuple[Any, ResolvedLayerSpec]:
    """Instantiate a QgsMapLayer from a CatalogSourceRecord and apply its style."""
    from qgis.core import QgsRasterLayer, QgsVectorLayer, QgsVectorTileLayer

    spec = build_provider_uri(entry, canvas_bbox_4326)

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
        apply_layer_metadata_and_style(layer, entry)

    return layer, spec
