# Geostrata Technical Architecture

🌐 **Language**: **English** | [Français](architecture.fr.md)

This document describes the end-to-end data pipeline (YAML -> TypeScript Compiler -> SQLite Database -> PyQGIS Plugin) and the exact schema of `catalog.sqlite`.

---

## 1. End-to-End Data Pipeline

```text
[YAML Sources + QML Styles + Country WKT Polygons + i18n Synonyms]
                             │
                             ▼ (GitHub Actions CI / npm run build)
          [TypeScript Compiler: src/build.ts]
          - Strict Zod validation (src/schema.ts)
          - Country bbox/WKT polygon injection (when country_iso is set)
          - Bounding-box area calculation in deg² (area_deg2)
          - Raw XML embedding of .qml symbology files
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
```
A final `VACUUM;` compacts all pages before Gzip compression (`catalog.sqlite.gz`).

### 2.1. Primary Table: `sources`

```sql
CREATE TABLE sources (
	rowid INTEGER PRIMARY KEY AUTOINCREMENT,
	id TEXT NOT NULL UNIQUE,
	name TEXT NOT NULL,
	description TEXT,
	type TEXT NOT NULL,
	category TEXT NOT NULL,
	url TEXT NOT NULL,
	crs TEXT NOT NULL DEFAULT 'EPSG:4326',
	country_iso TEXT,
	min_x REAL NOT NULL,
	min_y REAL NOT NULL,
	max_x REAL NOT NULL,
	max_y REAL NOT NULL,
	area_deg2 REAL NOT NULL,
	geom_wkt TEXT NOT NULL,
	tags_json TEXT NOT NULL,
	search_text TEXT NOT NULL DEFAULT '',
	license TEXT NOT NULL,
	attribution TEXT NOT NULL,
	style_qml TEXT,
	min_zoom INTEGER,
	max_zoom INTEGER,
	archive_target TEXT,
	health_status TEXT NOT NULL DEFAULT 'ok'
);
```

- **`area_deg2`**: Bounding-box area in square degrees `(max_x - min_x) * (max_y - min_y)`. A metropolitan dataset (e.g., Greater Lyon, `~0.148 deg²`) has a much smaller area than a national dataset (e.g., France, `~100 deg²`) or a global dataset (`64800 deg²`), ensuring natural local-first sorting via `ORDER BY s.area_deg2 ASC`.
- **`style_qml`**: Full XML content of the `.qml` style embedded directly in the column so PyQGIS can apply cartographic symbology immediately without extra network requests.
- **`search_text`**: Normalized, diacritic-stripped token string used as an automatic fallback if the host SQLite runtime was compiled without `ENABLE_FTS5`.

### 2.2. Spatial Index: `sources_rtree` (SQLite R-Tree)

```sql
CREATE VIRTUAL TABLE sources_rtree USING rtree(
	id,
	minX, maxX,
	minY, maxY
);
```
- Column order required by the SQLite R-Tree extension: `(id, minX, maxX, minY, maxY)` where `id` matches `sources.rowid`.

### 2.3. Full-Text Index: `sources_fts` (FTS5)

```sql
CREATE VIRTUAL TABLE sources_fts USING fts5(
	name,
	description,
	category,
	enriched_tags,
	country_names,
	tokenize='unicode61 remove_diacritics 2'
);
```
The `unicode61 remove_diacritics 2` tokenizer matches unaccented queries (`hopital`) against accented titles (`Hôpital`, `hôpitaux`) across English, French, Spanish, and Arabic synonyms.

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
- Optional columns are detected with `PRAGMA table_info(sources)` and default when absent (older or third-party catalogs): `i18n_json` (`{}`), `priority` (`0`), `data_date` (`NULL`), `health_checked_at` (`NULL`).

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
