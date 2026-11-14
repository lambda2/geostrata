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
          - Stage 1 (< 1 ms): SQLite R-Tree bbox + FTS5 full-text match
          - Stage 2 (< 2 ms): Exact QgsGeometry.fromWkt() intersection
          - Ranking: area_deg2 ASC (Local sources -> Global sources)
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

## 3. Search Engine Query (R-Tree + FTS5)

For a search query `:fts_query` and a map canvas bounding box `(canvas_min_x, canvas_min_y, canvas_max_x, canvas_max_y)` in `EPSG:4326`:

```sql
SELECT
	s.id,
	s.name,
	s.type,
	s.category,
	s.url,
	s.crs,
	s.area_deg2,
	s.geom_wkt,
	s.style_qml,
	s.attribution,
	s.license
FROM sources s
INNER JOIN sources_rtree r ON s.rowid = r.id
INNER JOIN sources_fts f ON s.rowid = f.rowid
WHERE f.sources_fts MATCH :fts_query
  AND r.maxX >= :canvas_min_x
  AND r.minX <= :canvas_max_x
  AND r.maxY >= :canvas_min_y
  AND r.minY <= :canvas_max_y
  AND s.health_status != 'dead'
ORDER BY s.area_deg2 ASC, f.rank ASC
LIMIT 50;
```

If the user appends `!global` to their query, the `sources_rtree` bounding-box filter is bypassed.

---

## 4. `manifest.json` Format

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
