# Architecture Technique de Geostrata

🌐 **Langue** : [English](architecture.md) | **Français**

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
         - Étape 1 (~ 1 ms) : R-Tree bbox + préfixes FTS5 (candidats)
         - Étape 2 (~ 1 ms) : Classement = pertinence texte + localité + priorité
         - Étape 3 (< 2 ms) : Intersection exacte QgsGeometry.fromWkt(), puis limite
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

- **`area_deg2`** : Superficie du rectangle englobant en degrés carrés `(max_x - min_x) * (max_y - min_y)`. Une source municipale (ex: Grand Lyon, `~0.148 deg²`) a une valeur très inférieure à une source nationale (ex: France métropolitaine, `~100 deg²`) ou mondiale (`64800 deg²`), garantissant un tri naturel `ORDER BY s.area_deg2 ASC`.
- **`style_qml`** : Contenu XML complet du fichier `.qml` embarqué directement dans la colonne texte afin que PyQGIS puisse appliquer le style immédiatement sans requête réseau supplémentaire.
- **`search_text`** : Chaîne normalisée sans accents utilisée automatiquement en repli si la bibliothèque SQLite hôte est compilée sans `ENABLE_FTS5`.

### 2.2. Index Spatial `sources_rtree` (R-Tree SQLite)

```sql
CREATE VIRTUAL TABLE sources_rtree USING rtree(
	id,
	minX, maxX,
	minY, maxY
);
```
- L'ordre des colonnes imposé par l'extension R-Tree de SQLite est `(id, minX, maxX, minY, maxY)` où `id` correspond à `sources.rowid`.

### 2.3. Index Plein Texte `sources_fts` (FTS5)

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
Le tokenizer `unicode61 remove_diacritics 2` permet de rechercher `hopital` et de trouver `Hôpital` ou `hôpitaux`, ainsi que les termes anglais, espagnols et arabes.

---

## 3. Moteur de Recherche : Candidats (R-Tree + FTS5), puis Classement

Implémenté dans `packages/qgis-plugin/geostrata/core/db_client.py` (Python pur, sans import QGIS) et utilisé par le filtre de la barre de recherche (`gst`) comme par le panneau d'exploration.

### 3.1. Requête des candidats (SQL, par catalogue attaché)

Pour une requête `:fts_query` (chaque mot en préfixe : `"dem"*`) et l'emprise du canevas en `EPSG:4326` :

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

- Il n'y a **ni `ORDER BY` ni `LIMIT`** en SQL : tous les candidats sont lus (quelques centaines de lignes au plus, environ 1 ms) puis classés en Python.
- Si l'utilisateur inclut le modificateur `!global` dans sa requête, la clause sur `sources_rtree` est omise. Sans aucun mot, la jointure FTS est omise (mode exploration).
- Si SQLite a été compilé sans FTS5 (ou si le catalogue n'a pas de table `sources_fts` utilisable), le filtre devient `s.search_text LIKE '% mot%'` et le même classement s'applique aux champs retrouvés dans `search_text`. Ce n'est pas un cas marginal : le Python fourni avec QGIS 4.2 pour macOS dispose de R-Tree mais pas de FTS5 ; les deux chemins sont donc couverts par les tests unitaires et donnent le même ordre.
- Les colonnes optionnelles sont détectées avec `PRAGMA table_info(sources)` et prennent une valeur par défaut si elles sont absentes (catalogues anciens ou tiers) : `i18n_json` (`{}`), `priority` (`0`), `data_date` (`NULL`), `health_checked_at` (`NULL`).

### 3.2. Formule de classement

```text
score = 2.0 * pertinence_texte      (0..1, seulement si la requête contient des mots)
      + 1.0 * localité              (0..1, petites emprises d'abord)
      + 0.5 * priority / 10         (`sources.priority` éditorialisée, -10..10, 0 par défaut)
      - 0.25                        (seulement si health_status = 'degraded')
```

La **pertinence texte** est la moyenne, sur les mots de la requête, du meilleur `poids_du_champ * qualité_de_correspondance` trouvé pour ce mot :

| Champ | Poids |
| :--- | :--- |
| `name` (nom anglais et noms traduits) | 1.0 |
| `tags` éditorialisés, `category`, `type` | 0.9 |
| Synonymes ajoutés par le compilateur (`enriched_tags`) | 0.75 |
| Noms de pays | 0.6 |
| `description` (anglaise et traduite) | 0.5 |
| Tout le reste (`id`, `search_text` non découpé) | 0.3 |

La `qualité_de_correspondance` vaut `1.0` pour un mot exact et `0.3 + 0.5 * len(mot de la requête) / len(mot trouvé)` pour un préfixe (donc entre 0.3 et 0.8). Un mot exact bat donc toujours un préfixe dans le même champ, et une correspondance dans le nom ou les tags bat une correspondance dans la description. Exemple sur Goma avec `gst dem` : « Copernicus **DEM** GLO-30 » obtient `1.0` (exact, nom) alors que « ... (**Dem**ocratic Republic of the Congo) » obtient `1.0 * (0.3 + 0.5 * 3/10) = 0.45` (préfixe, nom).

La **localité** vaut `1 - log10(area_deg2 / 0.01) / log10(64800 / 0.01)` bornée à `0..1` : environ `0.83` pour un jeu métropolitain (0,15 deg²), `0.39` pour un jeu national (140 deg²), `0` pour un jeu mondial. À pertinence texte égale, les résultats gardent l'ordre précédent Local -> National -> Mondial.

La **priorité** est un coup de pouce éditorial : `+10` vaut la moitié de l'écart de localité entre une ville et le monde, et ne peut jamais rattraper une mauvaise correspondance texte (un nom exact sur une couche mondiale, `2.0`, bat toujours un préfixe sur une couche nationale avec `priority: 10`, `0.9 + 0.33 + 0.5 = 1.73`).

Les égalités sont départagées par `area_deg2`, puis le nom, puis l'identifiant.

### 3.3. Filtres et limite

Les filtres s'appliquent sur la liste classée **avant** la limite (50 dans la barre de recherche, 120 dans le panneau), si bien qu'un bon résultat ne peut plus être coupé par un `LIMIT` précoce : l'option ELI (identifiants `-eli-`), la catégorie du panneau, et l'intersection exacte `QgsGeometry.fromWkt()` avec le canevas (passée par l'interface comme prédicat `accept` de `search_sources()`).

### 3.4. Groupes de la barre de recherche

Les résultats gardent leurs deux groupes, « Couches locales & nationales » (`area_deg2 < 10000`) et « Couches mondiales ». Le groupe local est affiché en premier, comme avant, sauf si le meilleur résultat mondial devance le meilleur résultat local de plus de `0.5` (écart net de pertinence texte, comme dans l'exemple `dem`) : le groupe mondial passe alors devant. L'ordre des groupes est fixé par `QgsLocatorResult.groupScore` (QGIS >= 3.40) et par le préfixe `1.` / `2.` du nom de groupe (versions antérieures).

---

## 4. Interface du Plugin

### 4.1. Langue (anglais / français)

L'anglais est la langue source de l'interface. Les traductions sont de simples dictionnaires Python (`geostrata/i18n/fr.py` : chaîne anglaise -> traduction) consultés par `tr()` / `tr_n()` dans `geostrata/i18n/__init__.py` ; il n'y a ni fichier `.ts`/`.qm` ni étape de compilation `lrelease`. Ajouter une langue revient à ajouter un dictionnaire dans `TRANSLATIONS`. `tests/test_core.py` parcourt tous les appels à `tr()` et échoue si une chaîne n'a pas de traduction française.

Une **seule langue** pilote à la fois l'interface et le contenu du catalogue (noms et descriptions de couches issus de `i18n_json`) :

1. le réglage `geostrata/language` s'il ne vaut pas `auto` (boîte de dialogue Paramètres : Automatique / English / Français) ;
2. sinon la langue de QGIS (`locale/userLocale` si `locale/overrideFlag` est activé, sinon `QgsApplication.locale()`).

Chaque côté se replie sur l'anglais indépendamment : l'interface s'il n'existe pas de dictionnaire pour cette langue, une couche si elle n'a pas de traduction dans cette langue.

### 4.2. Aperçu avant chargement

- **Barre de recherche** : chaque résultat propose les actions « Afficher la couverture sur la carte » et « Zoomer sur la couverture » à côté de « Charger la couche » et « Extraire hors-ligne » ; sa ligne de description indique la date des données et la mention « lien dégradé » le cas échéant.
- **Panneau** : la sélection d'une entrée remplit une zone de détails (description, fournisseur, licence, format, couverture, date des données, état du lien d'après `health_status` / `health_checked_at`) et met sa couverture en évidence ; « Zoomer sur la couverture » y centre aussi la carte.
- L'emprise (`geom_wkt`, EPSG:4326, reprojetée dans le CRS du canevas) est dessinée par un unique `CoveragePreview` partagé (`ui/coverage_preview.py`) sous forme de `QgsRubberBand` temporaire. Elle est remplacée par l'aperçu suivant, retirée après 8 secondes et à la fermeture du panneau, et supprimée du canevas au déchargement du plugin. Les couches mondiales ne sont pas dessinées (un message l'indique).

---

## 5. Format du fichier `manifest.json`

Généré dans `packages/registry/dist/manifest.json` :
```json
{
	"version": "1.0.0",
	"generated_at": "2026-11-14T10:00:00.000Z",
	"source_count": 267,
	"sqlite_file": "catalog.sqlite",
	"gzip_file": "catalog.sqlite.gz",
	"sha256": "<hash-sha256-du-fichier-catalog.sqlite>",
	"gzip_sha256": "<hash-sha256-du-fichier-catalog.sqlite.gz>"
}
```
