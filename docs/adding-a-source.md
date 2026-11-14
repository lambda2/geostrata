# Guide to Adding a Data Source (`adding-a-source.md`)

🌐 **Language**: **English** | [Français](adding-a-source.fr.md)

This guide explains how to author YAML files in `packages/registry/sources/`, use GDAL `/vsicurl/` virtual file systems, reference VRT files, and write OSM Overpass recipes.

---

## 1. Directory Organization

One file per data provider, grouped by scope. A file stays short (a provider rarely has more than 20 layers), so a contribution is a small, reviewable diff and two contributors seldom touch the same file.

- `packages/registry/sources/global/`: Worldwide general-purpose datasets (basemaps, global DEM, Overpass recipes).
- `packages/registry/sources/countries/<iso2>/<provider>.yaml`: National or regional datasets (e.g., `countries/fr/france.yaml`, `countries/ch/swisstopo.yaml`).
- `packages/registry/sources/thematic/<theme>/<provider>.yaml`: Multi-country thematic datasets (e.g., `thematic/hazards/gdacs.yaml`, `thematic/population/meta-hrsl.yaml`).
- `packages/registry/sources/_generated/`: Written by `npm run ingest` (OSM Editor Layer Index, geoBoundaries). Ignored by git and rebuilt in CI: never edit or commit these files, change the ingest script in `src/ingest/` instead.

A file contains a single entry, a list of entries, or the recommended form with shared defaults:

```yaml
defaults:                 # fields common to every entry of the file
  category: hazard
  license: Public Domain (USGS)
  attribution: U.S. Geological Survey
  tags: [usgs, earthquake, seisme]   # added to each entry's own tags
sources:
  - id: global-usgs-earthquakes-week
    name: USGS - Earthquakes M4.5+ (past 7 days)
    type: geojson
    url: https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson
    tags: [alerts]
```

### One entry for many countries (`for_each_country`)

When a provider publishes the same layer country by country, describe it once. The entry is expanded at build time into one layer per listed country, with `country_iso` and the coverage filled in automatically.

```yaml
- id: "{iso2}-hot-roads"            # the id must contain {iso2} or {iso3}
  for_each_country: [HTI, COD, UKR]  # ISO-3166-1 alpha-3 codes, or "all"
  name: "HOT OSM Export - Roads ({country})"
  i18n:
    fr:
      name: "Export OSM HOT - Routes ({country_fr})"
  type: archive
  url: https://example.org/{ISO3}/roads/hotosm_{iso3}_roads_lines_gpkg.zip
  archive_target: hotosm_{iso3}_roads_lines_gpkg.gpkg
  tags: [roads, routes, "{country}", "{country_fr}"]
```

| Placeholder | Example for Haiti |
| :--- | :--- |
| `{iso2}` / `{ISO2}` | `ht` / `HT` |
| `{iso3}` / `{ISO3}` | `hti` / `HTI` |
| `{country}` / `{country_fr}` | `Haiti` / `Haïti` |

Country codes, names and bounding boxes come from `assets/polygons/country-index.json` (regenerate it with `npm run refresh:countries`). List only the countries for which the provider really publishes the file.

---

## 2. Full YAML Schema

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `id` | `string` | Yes | Unique kebab-case identifier (`^[a-z0-9-]+$`), e.g. `fr-ign-orthophotos`. |
| `name` | `string` | Yes | Default English title displayed in QGIS (`Ctrl+K`). |
| `description` | `string` | No | Default English dataset description (indexed in FTS5). |
| `i18n` | `Record<string, { name?: string, description?: string }>` | No | Localized translations keyed by 2-letter language code (e.g. `i18n.fr.name`, `i18n.fr.description`). All translated strings are also indexed in FTS5. |
| `type` | `SourceType` | Yes | `'xyz' \| 'tms' \| 'wms' \| 'wmts' \| 'wfs' \| 'vectortile' \| 'cog' \| 'vrt' \| 'fgb' \| 'geojson' \| 'archive' \| 'overpass'` |
| `category` | `SourceCategory` | Yes | `'basemap' \| 'elevation' \| 'hydrography' \| 'transport' \| 'admin' \| 'buildings' \| 'landuse' \| 'imagery' \| 'utilities' \| 'crisis' \| 'population' \| 'health' \| 'hazard' \| 'weather'` |
| `url` | `string` | Yes | Stream URL, `/vsicurl/https://...` path, or Overpass QL / tag filter expression. |
| `crs` | `string` | No | EPSG code (default: `'EPSG:4326'`). |
| `country_iso` | `string` | No | 2-letter uppercase ISO-3166-1 alpha-2 code (`'FR'`). Automatically injects the country's WKT polygon and bounding box if omitted. |
| `bbox` | `[number, number, number, number]` | No* | `[minX, minY, maxX, maxY]` in WGS84 (`EPSG:4326`). Defaults to `[-180, -90, 180, 90]` if `country_iso` is omitted. |
| `geom_wkt` | `string` | No | Exact WKT geometry (`EPSG:4326`). Automatically computed from `bbox`, the curated outline in `assets/polygons/countries.json`, or the country bounding box in `assets/polygons/country-index.json` if omitted. |
| `tags` | `Array<string>` | Yes | Domain or geographic keywords (automatically expanded via `synonyms.json`). |
| `license` | `string` | Yes | Short license name (e.g., `ODbL`, `Etalab 2.0`, `CC-BY-4.0`). |
| `attribution` | `string` | Yes | Copyright string injected into QGIS layer metadata. |
| `style_file` | `string` | No | `.qml` file inside `assets/styles/` (e.g., `hydrography.qml`). Defaults to `<category>.qml` if available. |
| `min_zoom` | `number` | No | Minimum zoom level (integer >= 0). |
| `max_zoom` | `number` | No | Maximum zoom level (integer <= 24). |
| `archive_target` | `string` | No | Internal path to `.gpkg` or `.shp` when `type: archive` (via `/vsizip//vsicurl/`). |
| `priority` | `number` | No | Integer from `-10` to `10` (default `0`). Editorial boost: among equally relevant results, higher values are listed first. Keep `0` unless the layer is a reference an emergency mapper wants first. |
| `data_date` | `string` | No | Free-text vintage or refresh rate shown before loading (e.g. `2021`, `updated daily`). |
| `for_each_country` | `Array<string> \| 'all'` | No | ISO alpha-3 codes: expands the entry into one layer per country (see section 1). |

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
  url: /vsicurl/https://example.org/data/boundaries.fgb
  ```

---

## 4. Overpass Recipes (`type: overpass`)

For live OpenStreetMap vector extraction, use `type: overpass`.
The `url` field can contain:
- Either a concise OSM tag filter expression (e.g. `amenity=drinking_water|natural=spring|man_made=water_well`) that the plugin's `overpass_task.py` converts into a bbox-bounded `[out:json]` query across multiple Overpass mirrors.
- Or a full Overpass QL query containing the `{{bbox}}` placeholder (`minLat,minLon,maxLat,maxLon`).
