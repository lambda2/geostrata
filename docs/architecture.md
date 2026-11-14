# Geostrata Technical Architecture

🌐 **Language**: **English** | [Français](architecture.fr.md)

This document describes the end-to-end data pipeline (YAML -> TypeScript Compiler -> SQLite Database -> PyQGIS Plugin) and the exact schema of `catalog.sqlite`.

---

## 1. End-to-End Data Pipeline

```text
[YAML Sources + QML Styles + Country Outlines + i18n Synonyms]
                             │
                             ▼ (GitHub Actions CI / npm run build)
          [TypeScript Compiler: src/build.ts]
          - Strict Zod validation (src/schema.ts)
          - Country outline injection (when country_iso is set)
          - Footprint area calculation in deg² (area_deg2)
          - .qml styles, footprints and synonyms stored once each
          - Multilingual synonym expansion (EN, FR, ES, AR)
                             │
                             ▼
     [Static Artifacts: dist/catalog.sqlite.gz + manifest.json]
                             │
                             ▼ (Asynchronous PyQGIS background sync)
             [Local QGIS Cache: catalog.sqlite]
          - Multi-catalog support via ATTACH DATABASE
          - Stage 1 (~ 1 ms): SQLite R-Tree bbox + FTS5 prefix match (candidates)
          - Stage 2 (~ 1 ms): Ranking = text relevance + locality + priority
          - Stage 3 (< 2 ms): Exact QgsGeometry.fromWkt() intersection, then limit
```

---

## 2. SQLite Database Schema (`catalog.sqlite`)

The database is compiled with `better-sqlite3` in read-only optimized mode:
```sql
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
PRAGMA user_version = 2;   -- layout version, see below
```
A final `VACUUM;` compacts all pages before Gzip compression (`catalog.sqlite.gz`).

### 2.1. `sources`: a view over `sources_data` and three lookup tables

Readers query **`sources`**, which has one row per layer and the columns listed below. Since layout 2 (`PRAGMA user_version = 2`) it is a **view**: the rows live in `sources_data`, and the texts that used to be repeated in every row are stored once.

| Object | Kind | Content |
| :--- | :--- | :--- |
| `sources` | view | One row per layer. Same column names as the layout 1 table, in the same order, followed by `layer`, `geometry_type`, `time_json`. |
| `sources_data` | table | The rows. `footprint_id`, `style_id`, `synonym_terms_id` and `country_terms_id` point to the lookup tables; `search_local`, `fts_tags`, `fts_i18n` are the per-row parts of the search texts. |
| `styles` | table | `(id, qml)`: each distinct QML style once (about ten for the whole catalog). |
| `footprints` | table | `(id, wkt)`: each distinct footprint once (a country outline is shared by all the layers of that country). |
| `search_terms` | table | `(id, fts_text, like_text)`: the synonyms of a category and the names of a country, each once, in the form indexed by FTS5 and in the normalized form of the `LIKE` fallback. |
| `sources_fts_content` | view | The text columns indexed by `sources_fts` (see 2.3). |
| `sources_rtree` | view | The boxes of each layer, with the columns of the layout 1 R-Tree table (see 2.2). |
| `footprint_rtree`, `footprint_boxes` | R-Tree, table | The boxes and the layer each one belongs to. |

```sql
CREATE VIEW sources AS
SELECT
	d.rowid AS rowid,            -- joins with sources_rtree.id and sources_fts.rowid
	d.id AS id,
	d.name AS name,
	d.description AS description,
	d.i18n_json AS i18n_json,
	d.type AS type,
	d.category AS category,
	d.url AS url,
	d.crs AS crs,
	d.country_iso AS country_iso,
	d.min_x AS min_x, d.min_y AS min_y, d.max_x AS max_x, d.max_y AS max_y,
	d.area_deg2 AS area_deg2,
	(SELECT f.wkt FROM footprints f WHERE f.id = d.footprint_id) AS geom_wkt,
	d.tags_json AS tags_json,
	' ' || d.search_local
		|| (SELECT t.like_text FROM search_terms t WHERE t.id = d.synonym_terms_id)
		|| (SELECT t.like_text FROM search_terms t WHERE t.id = d.country_terms_id)
		|| ' ' AS search_text,
	d.license AS license,
	d.attribution AS attribution,
	(SELECT y.qml FROM styles y WHERE y.id = d.style_id) AS style_qml,
	d.min_zoom AS min_zoom,
	d.max_zoom AS max_zoom,
	d.archive_target AS archive_target,
	d.priority AS priority,
	d.data_date AS data_date,
	d.health_status AS health_status,
	d.health_checked_at AS health_checked_at,
	d.layer AS layer,
	d.geometry_type AS geometry_type,
	d.time_json AS time_json
FROM sources_data d;
```

- **Compatibility**: a reader written for layout 1 (`SELECT ... FROM sources s JOIN sources_rtree r ON s.rowid = r.id JOIN sources_fts f ON s.rowid = f.rowid`, `PRAGMA table_info(sources)`) works unchanged and gets the same values. The lookups are scalar subqueries on an integer primary key: a column that is not selected costs nothing, and the query planner still only orders `sources_data`, the R-Tree and the FTS index. Two things differ: writers must target `sources_data` (`npm run healthcheck` does), and `sources_rtree` can return a layer more than once (see 2.2).
- **`min_x`, `min_y`, `max_x`, `max_y`**: Bounding box of the whole footprint (`-180..180` for a country cut by the antimeridian).
- **`area_deg2`**: Area of the footprint in square degrees. For a layer described by a `bbox` it is the bounding-box area `(max_x - min_x) * (max_y - min_y)`; for a layer that takes the outline of its country it is the area of that outline, so that a country crossing the antimeridian (bounding box `-180..180`) or owning distant islands is not ranked as a continent. A metropolitan dataset (e.g., Greater Lyon, `~0.148 deg²`) has a much smaller area than a national dataset (e.g., France, `~80 deg²`) or a global dataset (`64800 deg²`), ensuring natural local-first sorting.
- **`geom_wkt`**: Footprint in `EPSG:4326`, a `POLYGON` or a `MULTIPOLYGON` without holes, used by the exact intersection test of the plugin. See 2.4.
- **`style_qml`**: Full XML content of the `.qml` style, so PyQGIS can apply cartographic symbology immediately without extra network requests.
- **`search_text`**: Normalized, diacritic-stripped token string used as an automatic fallback if the host SQLite runtime was compiled without `ENABLE_FTS5`.
- **`layer`**, **`geometry_type`**, **`time_json`**: Loading hints, `NULL` when unused. `time_json` is the `time` block of the entry with its defaults applied, e.g. `{"offset_days":-1,"step":"day","format":"YYYY-MM-DD"}`: the plugin replaces `{time}` in `url` and `archive_target` with today's date (UTC) plus `offset_days`, truncated to the first day of the month when `step` is `month`. See [adding-a-source.md](adding-a-source.md).

### 2.2. Spatial Index: `sources_rtree` (SQLite R-Tree)

```sql
CREATE VIRTUAL TABLE footprint_rtree USING rtree(
	id,
	minX, maxX,
	minY, maxY
);

CREATE TABLE footprint_boxes (
	box_id INTEGER PRIMARY KEY,                                  -- footprint_rtree.id
	source_rowid INTEGER NOT NULL REFERENCES sources_data(rowid)
);
CREATE INDEX footprint_boxes_source ON footprint_boxes (source_rowid);

CREATE VIEW sources_rtree AS
SELECT o.source_rowid AS id, b.minX AS minX, b.maxX AS maxX, b.minY AS minY, b.maxY AS maxY
FROM footprint_rtree b
INNER JOIN footprint_boxes o ON o.box_id = b.id;
```
- Readers keep joining **`sources_rtree`** on `sources.rowid = sources_rtree.id` and filtering on `minX, maxX, minY, maxY`, as with the layout 1 R-Tree table. It is now a view: the R-Tree itself is `footprint_rtree` (column order required by the SQLite extension: `id, minX, maxX, minY, maxY`).
- Most layers have one box, the bounding box of their footprint (`min_x .. max_y`). A layer that takes the outline of a country in several distant pieces has **one box per group of pieces**: metropolitan France, French Guiana, the Antilles, Réunion and Mayotte are five boxes, and Fiji, Russia, Kiribati, the USA or New Zealand have a box on each side of the antimeridian instead of one `-180..180` band. Without this, a France or USA layer would be a candidate over half the planet.
- Consequence: a query joining `sources_rtree` returns a layer once **per box that meets the rectangle** (rare: the rectangle must span two groups, e.g. a world-wide canvas). The plugin keeps the first row of each `id`; a reader that needs unique rows adds `DISTINCT` or `GROUP BY s.id`.

### 2.3. Full-Text Index: `sources_fts` (FTS5)

```sql
CREATE VIRTUAL TABLE sources_fts USING fts5(
	name,
	description,
	category,
	enriched_tags,
	country_names,
	content='sources_fts_content',
	content_rowid='rowid',
	tokenize='unicode61 remove_diacritics 2'
);
```
The `unicode61 remove_diacritics 2` tokenizer matches unaccented queries (`hopital`) against accented titles (`Hôpital`, `hôpitaux`) across English, French, Spanish, and Arabic synonyms.

It is an *external content* index: only the inverted index is stored, and the column values (`f.enriched_tags`, `f.country_names`) are read from the `sources_fts_content` view, which rebuilds them from `sources_data` and `search_terms` (`enriched_tags` = the entry's tags + the synonyms of its category + its translated names and descriptions).

### 2.4. Country footprints

A layer with a `country_iso` and neither `bbox` nor `geom_wkt` (every `for_each_country` entry, the geoBoundaries family) takes the outline of its country from `assets/polygons/country-outlines.json`. That file is generated by `npm run refresh:countries` from Natural Earth 1:10m (public domain), like `country-index.json`:

- holes are removed and the country is grown by 0.1° (about 11 km), then simplified by less than that: the outline always contains the country, its coast and its border towns, and every island or atoll is kept (merged with its neighbours);
- Natural Earth cuts the countries at the antimeridian, so Fiji or Russia are a few polygons on both edges of the map, not a band around the world;
- coordinates have 2 decimals; the file holds 238 outlines in about 400 kB, each with its R-Tree boxes (`boxes`, see 2.2), its bounding box and its area.

Before these outlines, such a layer used the bounding box of its country, so Tanzania or Kiribati layers passed the intersection test over Goma.

---

## 3. Search Engine: Candidates (R-Tree + FTS5), then Ranking

Implemented in `packages/qgis-plugin/geostrata/core/db_client.py` (pure Python, no QGIS import) and used by both the locator filter (`gst`) and the browser dock.

### 3.1. Candidate query (SQL, per attached catalog)

For a search query `:fts_query` (every word as a prefix: `"dem"*`) and a map canvas bounding box in `EPSG:4326`:

```sql
SELECT s.*, f.enriched_tags, f.country_names
FROM sources s
INNER JOIN sources_fts f ON s.rowid = f.rowid
INNER JOIN sources_rtree r ON s.rowid = r.id
WHERE f.sources_fts MATCH :fts_query
  AND r.maxX >= :canvas_min_x
  AND r.minX <= :canvas_max_x
  AND r.maxY >= :canvas_min_y
  AND r.minY <= :canvas_max_y
  AND s.health_status != 'dead';
```

- There is **no `ORDER BY` and no `LIMIT`** in SQL: all candidates are fetched (a few hundred rows at most, about 1 ms) and ranked in Python.
- If the user appends `!global` to the query, the `sources_rtree` bounding-box filter is bypassed. Without any word, the FTS join is skipped (browse mode).
- If SQLite was compiled without FTS5 (or the catalog has no usable `sources_fts`), the word filter becomes `s.search_text LIKE '% word%'` and the same ranking is applied to the fields recovered from `search_text`. This is not a corner case: the Python bundled with QGIS 4.2 for macOS has R-Tree but no FTS5, so both paths are covered by the unit tests and give the same order.
- Optional columns are detected with `PRAGMA table_info(sources)` and default when absent (older or third-party catalogs): `i18n_json` (`{}`), `priority` (`0`), `data_date` (`NULL`), `health_checked_at` (`NULL`), `layer` (`NULL`), `geometry_type` (`NULL`), `time_json` (`NULL`). `PRAGMA table_info` also works when `sources` is a view, and results are deduplicated by `id` (a source can match through several R-Tree boxes).

### 3.2. Ranking formula

```text
score = 2.0 * text_relevance        (0..1, only when the query has words)
      + 1.0 * locality              (0..1, small footprints first)
      + 0.5 * priority / 10         (curated `sources.priority`, -10..10, default 0)
      - 0.25                        (only when health_status = 'degraded')
```

**Text relevance** is the mean, over the query words, of the best `field_weight * match_quality` found for that word:

| Field | Weight |
| :--- | :--- |
| `name` (English and translated names) | 1.0 |
| Curated `tags`, `category`, `type` | 0.9 |
| Synonyms added by the compiler (`enriched_tags`) | 0.75 |
| Country names | 0.6 |
| `description` (English and translated) | 0.5 |
| Anything else (`id`, unsplit `search_text`) | 0.3 |

`match_quality` is `1.0` for an exact word and `0.3 + 0.5 * len(query word) / len(matched word)` for a prefix match (so between 0.3 and 0.8). An exact word therefore always beats a prefix in the same field, and a name or tag hit beats a description hit. Example over Goma with `gst dem`: "Copernicus **DEM** GLO-30" scores `1.0` (exact, name) while "... (**Dem**ocratic Republic of the Congo)" scores `1.0 * (0.3 + 0.5 * 3/10) = 0.45` (prefix, name).

**Locality** is `1 - log10(area_deg2 / 0.01) / log10(64800 / 0.01)` clamped to `0..1`: about `0.83` for a metropolitan dataset (0.15 deg²), `0.39` for a national one (140 deg²), `0` for a global one. With equal text relevance, results keep the previous Local -> National -> Global order.

**Priority** is a curated nudge: `+10` is worth half of the full city-to-world locality range and can never rescue a weak text match (an exact name match on a global layer, `2.0`, still beats a prefix match on a national layer with `priority: 10`, `0.9 + 0.33 + 0.5 = 1.73`).

Ties are broken by `area_deg2`, then name, then id.

### 3.3. Filters and limit

Filters run on the ranked list **before** the limit (50 in the locator, 120 in the dock), so a relevant result can no longer be cut by an early `LIMIT`: the ELI toggle (`-eli-` ids), the dock category, and the exact `QgsGeometry.fromWkt()` intersection with the canvas (passed by the UI as the `accept` predicate of `search_sources()`).

### 3.4. Locator groups

Results keep their two groups, "Local & national layers" (`area_deg2 < 10000`) and "Global layers". The local group is listed first, as before, unless the best global result outranks the best local one by more than `0.5` (a clear text-relevance gap, as in the `dem` example): the global group then comes first. Group order is set with `QgsLocatorResult.groupScore` (QGIS >= 3.40) and with the `1.` / `2.` prefix of the group name (older versions).

---

## 4. Plugin Interface

### 4.1. Language (English / French)

English is the source language of the interface. Translations are plain Python dictionaries (`geostrata/i18n/fr.py`: English string -> translation) looked up by `tr()` / `tr_n()` in `geostrata/i18n/__init__.py`; there is no `.ts`/`.qm` file and no `lrelease` build step. Adding a language is one more dictionary registered in `TRANSLATIONS`. `tests/test_core.py` scans every `tr()` call and fails when a string has no French translation.

A **single language** drives both the interface and the catalog content (layer names and descriptions from `i18n_json`):

1. the `geostrata/language` setting when it is not `auto` (Settings dialog: Automatic / English / Français);
2. otherwise the QGIS language (`locale/userLocale` when `locale/overrideFlag` is set, else `QgsApplication.locale()`).

Each side falls back to English independently: the interface when no dictionary exists for that language, a layer when it has no translation for it.

### 4.2. Preview before loading

- **Locator**: each result has the actions "Show coverage on map" and "Zoom to coverage" next to "Load layer" and "Extract offline"; its description line shows the data date and a "link degraded" note when relevant.
- **Dock**: selecting an entry fills a details area (description, provider, license, format, coverage, data date, link status from `health_status` / `health_checked_at`) and highlights its coverage; "Zoom to coverage" also zooms to it.
- The footprint (`geom_wkt`, EPSG:4326, reprojected to the canvas CRS) is drawn by one shared `CoveragePreview` (`ui/coverage_preview.py`) as a temporary `QgsRubberBand`. It is replaced by the next preview, removed after 8 seconds and when the dock closes, and deleted from the canvas on plugin unload. Worldwide layers are not drawn (a message says so).

### 4.3. Loading files: background download and local cache

"Load" never opens a remote file on the main thread any more. `uri_builder.needs_local_copy()` decides per type:

| Type | How it is loaded | Why |
| :--- | :--- | :--- |
| `archive` (ZIP) | Downloaded once, the target extracted, opened locally | A ZIP has no random access: through `/vsizip//vsicurl/` GDAL re-reads the deflate stream for every seek (minutes for 100 MB). |
| `geojson` | Downloaded once, opened locally | The whole document must be fetched and parsed before the layer exists. |
| `fgb`, `cog`, `vrt` | Streamed with `/vsicurl/`, as before | Built for HTTP range requests (spatial index, internal tiles, overviews): only the visible extent is read, whatever the file size. Downloading a multi-gigabyte COG would be the wrong default; Shift+Enter saves the current extent. |
| Services (WMS, WMTS, WFS, XYZ, vector tiles, ArcGIS) | Provider requests | Nothing to download. |

The rule is by format, not by size: the size is unknown before the first response, and for a small file the background path finishes in well under a second.

- **Task** (`core/loader_task.py`): a cancellable `QgsTask` downloads with progress (task manager + a message-bar item with its own Cancel button), extracts the archive target with its sidecar files, creates the layer **in the task thread** (opening a large GeoJSON means parsing it), moves it to the main thread with `moveToThread()`, and `finished()` styles it and adds it to the project. Running tasks are kept in `_ACTIVE_FILE_LOAD_TASKS`; loading the same file twice reuses the running task.
- **Cache** (`core/file_cache.py`, pure Python): one directory per `(URL, archive target)` holding `meta.json` (ETag, Last-Modified, size, dates) and the file QGIS opens. Only the extracted target is kept, not the ZIP.
  - *Location*: the OS cache folder (`~/Library/Caches/geostrata/files`, `~/.cache/geostrata/files`, `%LOCALAPPDATA%/cache/geostrata/files`), outside the QGIS profile (which may be roamed or backed up) and shared by QGIS 3 and 4. It can be changed in Settings > Downloaded files (`geostrata/file_cache_dir`).
  - *Freshness*: a copy is used without any request for 24 h (archives) or 5 min (GeoJSON, which may be a live feed); after that one conditional GET decides (`304` keeps the copy). If the server cannot be reached, the existing copy is loaded with a warning (offline use).
  - *Remote file changed*: the new version is downloaded into a new `data-*` directory and `meta.json` is switched at the end, so a failed refresh keeps the old copy and an open layer keeps its file.
  - *Failure midway*: retried up to 5 times, resuming with a `Range` request; the partial file stays for the next attempt (`If-Range` restarts from zero when the remote file changed).
  - *Cancel*: no layer is added, the partial file is kept for a week and resumed by the next load.
  - *Size*: 10 GB by default (`geostrata/file_cache_max_gb`); least recently used entries are removed first, never the entry being loaded nor a file used by a layer of the open project. "Delete the downloaded files" in the settings empties it. Deletion only touches directories the cache created.
- **Offline extraction** (Shift+Enter) clips from the same cached copy and downloads it first when missing.
- `create_qgis_layer()` remains the synchronous path (used by `tests/test_live_layers.py`); it streams unless given `local_path`.

### 4.4. Dated sources, sub-layers, ArcGIS services, Overpass geometry

- **`{time}`** in `url` / `archive_target` is replaced at load time by `today (UTC) + offset_days`, truncated to the first of the month when `step` is `month`, written with `format` (`time_json`). The resolved date is appended to the layer name and shown as "Date loaded" in the details. For WMS, every KVP parameter QGIS does not manage itself (`TIME`, `ELEVATION`, `DIM_*`, `map`, keys) stays in the service URL and `IgnoreGetMapUrl=1` makes QGIS send GetMap to that URL rather than to the one advertised in the capabilities (which would drop them). For WMTS, dimension parameters go to `tileDimensions`.
- **`layer`** selects the OGR layer of a multi-layer file (`|layername=`; without it OGR opens the first layer) or the sublayer of an ArcGIS MapServer. **`geometry_type`** (`point` / `line` / `polygon`) adds `|geometrytype=` for files mixing several types.
- **ArcGIS**: `arcgisfeatureserver` (`url` = layer endpoint) and `arcgismapserver` (`url` = MapServer or ImageServer root). No `crs` is sent: the layer keeps the CRS of the service. Image services published as a LERC tile cache (e.g. elevation or population rasters) are not rendered by the QGIS provider.
- **Overpass geometry** (`overpass_task.resolve_overpass_geometry_mode`): the `geometry_type` column when set; otherwise lines for a line style or a linear OSM key, polygons for a fill style, else one centre point per feature (unchanged default). Polygon recipes use `out geom`, turn closed ways and multipolygon relations (rings assembled, holes assigned) into MultiPolygons, and put what OSM maps without an area (nodes, open ways) in a companion point layer so nothing is lost. Their extent is clamped to 0.5° x 0.5° (points and lines: 3° x 3°), with a 25 s server timeout and a 256 MB `maxsize`.

---

## 5. `manifest.json` Format

Generated in `packages/registry/dist/manifest.json`:
```json
{
	"version": "1.0.0",
	"generated_at": "2026-11-14T10:00:00.000Z",
	"source_count": 267,
	"sqlite_file": "catalog.sqlite",
	"gzip_file": "catalog.sqlite.gz",
	"sha256": "<sha256-hash-of-catalog.sqlite>",
	"gzip_sha256": "<sha256-hash-of-catalog.sqlite.gz>"
}
```
