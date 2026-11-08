# Architecture Technique de Geostrata

Ce document décrit le flux de données de bout en bout (YAML -> Compilateur TypeScript -> Base SQLite -> Plugin PyQGIS) ainsi que le schéma exact de `catalog.sqlite`.

---

## 1. Flux de Données Global

```text
[Fichiers YAML + Styles QML + Polygones WKT + Synonymes i18n]
                            │
                            ▼ (CI GitHub Actions / npm run build)
         [Compilateur TypeScript : src/build.ts]
         - Validation stricte via Zod (src/schema.ts)
         - Injection bbox/WKT pays (si country_iso présent)
         - Calcul de l'aire en degrés² (area_deg2)
         - Lecture du contenu XML brut des styles .qml
         - Enrichissement multilingue (FR, EN, ES, AR)
                            │
                            ▼
    [Artefacts Statiques : dist/catalog.sqlite.gz + manifest.json]
                            │
                            ▼ (Téléchargement asynchrone PyQGIS)
            [Cache Local QGIS : catalog.sqlite]
         - Multi-catalogue via ATTACH DATABASE
         - Étape 1 (< 1 ms) : R-Tree bbox + FTS5 plein texte
         - Étape 2 (< 2 ms) : Intersection exacte QgsGeometry.fromWkt()
         - Tri : area_deg2 ASC (Sources locales -> Sources globales)
```

---

## 2. Schéma Exact de la Base SQLite (`catalog.sqlite`)

La base est compilée avec `better-sqlite3` en mode lecture seule optimisé :
```sql
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
```
Un `VACUUM;` final compacte les pages avant la compression Gzip (`catalog.sqlite.gz`).

### 2.1. Table principale `sources`

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
	license TEXT NOT NULL,
	attribution TEXT NOT NULL,
	style_qml TEXT,
	min_zoom INTEGER,
	max_zoom INTEGER,
	archive_target TEXT,
	health_status TEXT NOT NULL DEFAULT 'ok'
);
```

- **`area_deg2`** : Superficie du rectangle englobant en degrés carrés `(max_x - min_x) * (max_y - min_y)`. Une source municipale (ex: Grand Lyon, `~0.148 deg²`) a une valeur très inférieure à une source nationale (ex: France métropolitaine, `~100 deg²`) ou mondiale (`64800 deg²`), garantissant un tri naturel `ORDER BY s.area_deg2 ASC`.
- **`style_qml`** : Contenu XML complet du fichier `.qml` embarqué directement dans la colonne texte afin que PyQGIS puisse appliquer le style immédiatement sans requête réseau supplémentaire.
- **`health_status`** : `'ok' | 'degraded' | 'dead'`.

### 2.2. Index Spatial `sources_rtree` (R-Tree SQLite)

```sql
CREATE VIRTUAL TABLE sources_rtree USING rtree(
	id,
	minX, maxX,
	minY, maxY
);
```
- Attention à l'ordre des colonnes imposé par l'extension R-Tree de SQLite : `(id, minX, maxX, minY, maxY)` où `id` correspond exactement à `sources.rowid`.

### 2.3. Index Plein Texte `sources_fts` (FTS5)

```sql
CREATE VIRTUAL TABLE sources_fts USING fts5(
	name,
	description,
	category,
	enriched_tags,
	country_names,
	content='sources',
	content_rowid='rowid',
	tokenize='unicode61 remove_diacritics 2'
);
```
*(Note : `sources_fts` peut être créée en table FTS5 standard liée par `rowid` pour permettre l'indexation directe de `enriched_tags` et `country_names` sans alourdir la table `sources`.)*

Structure retenue pour une interrogation directe et rapide par `rowid` :
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
Lors de l'insertion d'une source avec `rowid = rowId`, on insère dans `sources_fts(rowid, name, description, category, enriched_tags, country_names)`.
Le tokenizer `unicode61 remove_diacritics 2` permet de rechercher `hopital` et de trouver `Hôpital` ou `hôpitaux`, ainsi que les termes arabes, espagnols et anglais.

---

## 3. Requête Type du Moteur de Recherche (R-Tree + FTS5)

Pour une recherche sur le mot-clé `:query` et une emprise de carte `(canvas_min_x, canvas_min_y, canvas_max_x, canvas_max_y)` en `EPSG:4326` :

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

Si l'utilisateur inclut le modificateur `!global` dans sa requête, la clause sur `sources_rtree` est omise.

---

## 4. Format du fichier `manifest.json`

Généré dans `packages/registry/dist/manifest.json` :
```json
{
	"version": "1.0.0",
	"generated_at": "2026-11-13T10:00:00.000Z",
	"source_count": 10,
	"sqlite_file": "catalog.sqlite",
	"gzip_file": "catalog.sqlite.gz",
	"sha256": "<hash-sha256-du-fichier-catalog.sqlite>",
	"gzip_sha256": "<hash-sha256-du-fichier-catalog.sqlite.gz>"
}
```
