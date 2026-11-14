# Guide to Adding a Data Source (`adding-a-source.md`)

🌐 **Language**: **English** | [Français](adding-a-source.fr.md)

This guide explains how to author YAML files in `packages/registry/sources/`, use GDAL `/vsicurl/` virtual file systems, reference VRT files, and write OSM Overpass recipes.

---

## 1. Directory Organization

Each YAML file may contain either a single source object or an array of sources (`Array<CatalogEntry>`).
- `packages/registry/sources/global/`: Worldwide datasets (`bbox: [-180, -90, 180, 90]` by default).
- `packages/registry/sources/countries/<iso2>/`: National or regional datasets (e.g., `countries/fr/france.yaml`, `countries/ch/swisstopo.yaml`).
- `packages/registry/sources/thematic/`: Multi-country thematic datasets (HDX, FieldMaps, OSM Editor Layer Index).

---

## 2. Full YAML Schema

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `id` | `string` | Yes | Unique kebab-case identifier (`^[a-z0-9-]+$`), e.g. `fr-ign-orthophotos`. |
| `name` | `string` | Yes | Human-readable title displayed in QGIS (`Ctrl+K`). |
| `description` | `string` | No | Detailed dataset description (indexed in FTS5). |
| `type` | `SourceType` | Yes | `'xyz' \| 'tms' \| 'wms' \| 'wmts' \| 'wfs' \| 'vectortile' \| 'cog' \| 'vrt' \| 'fgb' \| 'geojson' \| 'archive' \| 'overpass'` |
| `category` | `SourceCategory` | Yes | `'basemap' \| 'elevation' \| 'hydrography' \| 'transport' \| 'admin' \| 'buildings' \| 'landuse' \| 'imagery' \| 'utilities' \| 'crisis'` |
| `url` | `string` | Yes | Stream URL, `/vsicurl/https://...` path, or Overpass QL / tag filter expression. |
| `crs` | `string` | No | EPSG code (default: `'EPSG:4326'`). |
| `country_iso` | `string` | No | 2-letter uppercase ISO-3166-1 alpha-2 code (`'FR'`). Automatically injects the country's WKT polygon and bounding box if omitted. |
| `bbox` | `[number, number, number, number]` | No* | `[minX, minY, maxX, maxY]` in WGS84 (`EPSG:4326`). Defaults to `[-180, -90, 180, 90]` if `country_iso` is omitted. |
| `geom_wkt` | `string` | No | Exact WKT geometry (`EPSG:4326`). Automatically computed from `bbox` or `assets/polygons/countries.json` if omitted. |
| `tags` | `Array<string>` | Yes | Domain or geographic keywords (automatically expanded via `synonyms.json`). |
| `license` | `string` | Yes | Short license name (e.g., `ODbL`, `Etalab 2.0`, `CC-BY-4.0`). |
| `attribution` | `string` | Yes | Copyright string injected into QGIS layer metadata. |
| `style_file` | `string` | No | `.qml` file inside `assets/styles/` (e.g., `hydrography.qml`). Defaults to `<category>.qml` if available. |
| `min_zoom` | `number` | No | Minimum zoom level (integer >= 0). |
| `max_zoom` | `number` | No | Maximum zoom level (integer <= 24). |
| `archive_target` | `string` | No | Internal path to `.gpkg` or `.shp` when `type: archive` (via `/vsizip//vsicurl/`). |

---

## 3. Using GDAL `/vsicurl/` (COG, FlatGeobuf, VRT)

GDAL and OGR stream remote cloud-native files using HTTP `Range` requests without downloading the entire file:

### Cloud-Optimized GeoTIFF (`type: cog`) or VRT (`type: vrt`)
- Prefix the HTTPS URL with `/vsicurl/`:
  ```yaml
  type: vrt
  url: /vsicurl/https://raw.githubusercontent.com/lambda2/geostrata/main/packages/registry/assets/vrt/copernicus-dem-glo30.vrt
  ```

### FlatGeobuf (`type: fgb`)
- `.fgb` files include a binary R-Tree spatial index in their header so OGR fetches only features intersecting the active extent:
  ```yaml
  type: fgb
  url: /vsicurl/https://data.fieldmaps.io/adm0/osm/intl/adm0_polygons.fgb
  ```

---

## 4. Overpass Recipes (`type: overpass`)

For live OpenStreetMap vector extraction, use `type: overpass`.
The `url` field can contain:
- Either a concise OSM tag filter expression (e.g. `amenity=drinking_water|natural=spring|man_made=water_well`) that the plugin's `overpass_task.py` converts into a bbox-bounded `[out:json]` query across multiple Overpass mirrors.
- Or a full Overpass QL query containing the `{{bbox}}` placeholder (`minLat,minLon,maxLat,maxLon`).
