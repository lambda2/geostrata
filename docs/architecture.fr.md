# Architecture Technique de Geostrata

🌐 **Langue** : [English](architecture.md) | **Français**

Ce document décrit le flux de données de bout en bout (YAML -> Compilateur TypeScript -> Base SQLite -> Plugin PyQGIS) ainsi que le schéma exact de `catalog.sqlite`.

---

## 1. Flux de Données Global

```text
[Fichiers YAML + Styles QML + Contours des pays + Synonymes i18n]
                            │
                            ▼ (CI GitHub Actions / npm run build)
         [Compilateur TypeScript : src/build.ts]
         - Validation stricte via Zod (src/schema.ts)
         - Injection du contour du pays (si country_iso présent)
         - Calcul de l'aire de l'emprise en degrés² (area_deg2)
         - Styles .qml, emprises et synonymes stockés une seule fois
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
PRAGMA user_version = 2;   -- version de la structure, voir ci-dessous
```
Un `VACUUM;` final compacte les pages avant la compression Gzip (`catalog.sqlite.gz`).

### 2.1. `sources` : une vue sur `sources_data` et trois tables de référence

Les lecteurs interrogent **`sources`**, qui contient une ligne par couche et les colonnes listées ci-dessous. Depuis la structure 2 (`PRAGMA user_version = 2`), c'est une **vue** : les lignes sont dans `sources_data`, et les textes auparavant répétés dans chaque ligne ne sont stockés qu'une fois.

| Objet | Nature | Contenu |
| :--- | :--- | :--- |
| `sources` | vue | Une ligne par couche. Mêmes noms de colonnes que la table de la structure 1, dans le même ordre, suivis de `layer`, `geometry_type`, `time_json`. |
| `sources_data` | table | Les lignes. `footprint_id`, `style_id`, `synonym_terms_id` et `country_terms_id` pointent vers les tables de référence ; `search_local`, `fts_tags`, `fts_i18n` sont les parties des textes de recherche propres à la ligne. |
| `styles` | table | `(id, qml)` : chaque style QML distinct une seule fois (une dizaine pour tout le catalogue). |
| `footprints` | table | `(id, wkt)` : chaque emprise distincte une seule fois (le contour d'un pays est partagé par toutes les couches de ce pays). |
| `search_terms` | table | `(id, fts_text, like_text)` : les synonymes d'une catégorie et les noms d'un pays, une seule fois chacun, sous la forme indexée par FTS5 et sous la forme normalisée du repli `LIKE`. |
| `sources_fts_content` | vue | Les colonnes de texte indexées par `sources_fts` (voir 2.3). |
| `sources_rtree` | vue | Les rectangles de chaque couche, avec les colonnes de la table R-Tree de la structure 1 (voir 2.2). |
| `footprint_rtree`, `footprint_boxes` | R-Tree, table | Les rectangles et la couche à laquelle chacun appartient. |

```sql
CREATE VIEW sources AS
SELECT
	d.rowid AS rowid,            -- jointure avec sources_rtree.id et sources_fts.rowid
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

- **Compatibilité** : un lecteur écrit pour la structure 1 (`SELECT ... FROM sources s JOIN sources_rtree r ON s.rowid = r.id JOIN sources_fts f ON s.rowid = f.rowid`, `PRAGMA table_info(sources)`) fonctionne sans modification et obtient les mêmes valeurs. Les références sont résolues par des sous-requêtes scalaires sur une clé primaire entière : une colonne non sélectionnée ne coûte rien, et le planificateur n'ordonne toujours que `sources_data`, le R-Tree et l'index FTS. Deux choses changent : les écritures doivent viser `sources_data` (c'est ce que fait `npm run healthcheck`), et `sources_rtree` peut renvoyer une couche plusieurs fois (voir 2.2).
- **`min_x`, `min_y`, `max_x`, `max_y`** : Rectangle englobant de toute l'emprise (`-180..180` pour un pays coupé par l'antiméridien).
- **`area_deg2`** : Superficie de l'emprise en degrés carrés. Pour une couche décrite par une `bbox`, c'est la superficie du rectangle englobant `(max_x - min_x) * (max_y - min_y)` ; pour une couche qui reprend le contour de son pays, c'est la superficie de ce contour, afin qu'un pays à cheval sur l'antiméridien (rectangle englobant `-180..180`) ou possédant des îles lointaines ne soit pas classé comme un continent. Une source municipale (ex: Grand Lyon, `~0.148 deg²`) a une valeur très inférieure à une source nationale (ex: France, `~80 deg²`) ou mondiale (`64800 deg²`), garantissant un tri naturel du local vers le mondial.
- **`geom_wkt`** : Emprise en `EPSG:4326`, un `POLYGON` ou un `MULTIPOLYGON` sans trou, utilisée par le test d'intersection exact du plugin. Voir 2.4.
- **`style_qml`** : Contenu XML complet du fichier `.qml`, afin que PyQGIS puisse appliquer le style immédiatement sans requête réseau supplémentaire.
- **`search_text`** : Chaîne normalisée sans accents utilisée automatiquement en repli si la bibliothèque SQLite hôte est compilée sans `ENABLE_FTS5`.
- **`layer`**, **`geometry_type`**, **`time_json`** : Indications de chargement, `NULL` si inutilisées. `time_json` est le bloc `time` de l'entrée avec ses valeurs par défaut, par exemple `{"offset_days":-1,"step":"day","format":"YYYY-MM-DD"}` : le plugin remplace `{time}` dans `url` et `archive_target` par la date du jour (UTC) plus `offset_days`, ramenée au premier jour du mois quand `step` vaut `month`. Voir [adding-a-source.fr.md](adding-a-source.fr.md).

### 2.2. Index Spatial `sources_rtree` (R-Tree SQLite)

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
- Les lecteurs continuent de joindre **`sources_rtree`** avec `sources.rowid = sources_rtree.id` et de filtrer sur `minX, maxX, minY, maxY`, comme avec la table R-Tree de la structure 1. C'est désormais une vue : le R-Tree lui-même est `footprint_rtree` (ordre des colonnes imposé par l'extension SQLite : `id, minX, maxX, minY, maxY`).
- La plupart des couches ont un seul rectangle, le rectangle englobant de leur emprise (`min_x .. max_y`). Une couche qui reprend le contour d'un pays en plusieurs morceaux éloignés a **un rectangle par groupe de morceaux** : la France métropolitaine, la Guyane, les Antilles, La Réunion et Mayotte font cinq rectangles, et les Fidji, la Russie, les Kiribati, les États-Unis ou la Nouvelle-Zélande ont un rectangle de chaque côté de l'antiméridien au lieu d'une bande `-180..180`. Sans cela, une couche de France ou des États-Unis serait candidate sur la moitié de la planète.
- Conséquence : une requête qui joint `sources_rtree` renvoie une couche une fois **par rectangle qui touche la zone demandée** (rare : la zone doit couvrir deux groupes, par exemple un canevas mondial). Le plugin garde la première ligne de chaque `id` ; un lecteur qui a besoin de lignes uniques ajoute `DISTINCT` ou `GROUP BY s.id`.

### 2.3. Index Plein Texte `sources_fts` (FTS5)

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
Le tokenizer `unicode61 remove_diacritics 2` permet de rechercher `hopital` et de trouver `Hôpital` ou `hôpitaux`, ainsi que les termes anglais, espagnols et arabes.

C'est un index à *contenu externe* : seul l'index inversé est stocké, et les valeurs des colonnes (`f.enriched_tags`, `f.country_names`) sont lues dans la vue `sources_fts_content`, qui les reconstruit à partir de `sources_data` et `search_terms` (`enriched_tags` = les tags de l'entrée + les synonymes de sa catégorie + ses noms et descriptions traduits).

### 2.4. Emprises des pays

Une couche qui a un `country_iso` et ni `bbox` ni `geom_wkt` (toutes les entrées `for_each_country`, la famille geoBoundaries) reprend le contour de son pays dans `assets/polygons/country-outlines.json`. Ce fichier est généré par `npm run refresh:countries` à partir de Natural Earth 1:10m (domaine public), comme `country-index.json` :

- les trous sont supprimés et le pays est élargi de 0,1° (environ 11 km), puis simplifié d'une valeur inférieure : le contour contient toujours le pays, sa côte et ses villes frontalières, et chaque île ou atoll est conservé (fusionné avec ses voisins) ;
- Natural Earth coupe les pays à l'antiméridien : les Fidji ou la Russie sont donc quelques polygones sur les deux bords de la carte, et non une bande autour du monde ;
- les coordonnées ont 2 décimales ; le fichier contient 238 contours pour environ 400 ko, chacun avec ses rectangles R-Tree (`boxes`, voir 2.2), son rectangle englobant et sa superficie.

Avant ces contours, une telle couche utilisait le rectangle englobant de son pays : les couches de Tanzanie ou des Kiribati passaient donc le test d'intersection au-dessus de Goma.

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
- Les colonnes optionnelles sont détectées avec `PRAGMA table_info(sources)` et prennent une valeur par défaut si elles sont absentes (catalogues anciens ou tiers) : `i18n_json` (`{}`), `priority` (`0`), `data_date` (`NULL`), `health_checked_at` (`NULL`), `layer` (`NULL`), `geometry_type` (`NULL`), `time_json` (`NULL`). `PRAGMA table_info` fonctionne aussi quand `sources` est une vue, et les résultats sont dédoublonnés par `id` (une source peut correspondre par plusieurs boîtes du R-Tree).

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

### 4.3. Chargement des fichiers : téléchargement en arrière-plan et cache local

« Charger » n'ouvre plus jamais un fichier distant sur le thread principal. `uri_builder.needs_local_copy()` décide selon le type :

| Type | Mode de chargement | Pourquoi |
| :--- | :--- | :--- |
| `archive` (ZIP) | Téléchargé une fois, cible extraite, ouvert localement | Un ZIP n'a pas d'accès direct : via `/vsizip//vsicurl/`, GDAL relit le flux compressé à chaque déplacement (des minutes pour 100 Mo). |
| `geojson` | Téléchargé une fois, ouvert localement | Le document entier doit être récupéré et analysé avant que la couche existe. |
| `fgb`, `cog`, `vrt` | Lus en flux avec `/vsicurl/`, comme avant | Conçus pour les requêtes HTTP partielles (index spatial, tuiles internes, aperçus) : seule l'emprise visible est lue, quelle que soit la taille. Télécharger un COG de plusieurs gigaoctets serait un mauvais choix par défaut ; Maj+Entrée enregistre l'emprise courante. |
| Services (WMS, WMTS, WFS, XYZ, tuiles vectorielles, ArcGIS) | Requêtes du fournisseur | Rien à télécharger. |

La règle dépend du format, pas de la taille : la taille est inconnue avant la première réponse, et pour un petit fichier le chemin en arrière-plan se termine en bien moins d'une seconde.

- **Tâche** (`core/loader_task.py`) : une `QgsTask` annulable télécharge avec progression (gestionnaire de tâches + élément de la barre de messages avec son bouton Annuler), extrait la cible de l'archive avec ses fichiers annexes, crée la couche **dans le thread de la tâche** (ouvrir un gros GeoJSON, c'est l'analyser), la déplace vers le thread principal avec `moveToThread()`, puis `finished()` applique le style et l'ajoute au projet. Les tâches en cours sont conservées dans `_ACTIVE_FILE_LOAD_TASKS` ; charger deux fois le même fichier réutilise la tâche en cours.
- **Cache** (`core/file_cache.py`, Python pur) : un dossier par couple `(URL, cible d'archive)` contenant `meta.json` (ETag, Last-Modified, taille, dates) et le fichier ouvert par QGIS. Seule la cible extraite est conservée, pas le ZIP.
  - *Emplacement* : le dossier de cache du système (`~/Library/Caches/geostrata/files`, `~/.cache/geostrata/files`, `%LOCALAPPDATA%/cache/geostrata/files`), hors du profil QGIS (qui peut être itinérant ou sauvegardé) et commun à QGIS 3 et 4. Il se change dans Paramètres > Fichiers téléchargés (`geostrata/file_cache_dir`).
  - *Fraîcheur* : une copie est utilisée sans aucune requête pendant 24 h (archives) ou 5 min (GeoJSON, qui peut être un flux en direct) ; ensuite une requête GET conditionnelle tranche (`304` conserve la copie). Si le serveur est injoignable, la copie existante est chargée avec un avertissement (usage hors ligne).
  - *Fichier distant modifié* : la nouvelle version est téléchargée dans un nouveau dossier `data-*` et `meta.json` est basculé à la fin ; un rafraîchissement raté conserve donc l'ancienne copie et une couche ouverte garde son fichier.
  - *Échec en cours de route* : jusqu'à 5 tentatives, en reprenant avec une requête `Range` ; le fichier partiel reste pour la tentative suivante (`If-Range` repart de zéro si le fichier distant a changé).
  - *Annulation* : aucune couche n'est ajoutée, le fichier partiel est conservé une semaine et repris au chargement suivant.
  - *Taille* : 10 Go par défaut (`geostrata/file_cache_max_gb`) ; les entrées les moins récemment utilisées sont supprimées en premier, jamais celle en cours de chargement ni un fichier utilisé par une couche du projet ouvert. « Supprimer les fichiers téléchargés » dans les paramètres le vide. La suppression ne touche que les dossiers créés par le cache.
- **L'extraction hors ligne** (Maj+Entrée) découpe la même copie en cache et la télécharge d'abord si elle manque.
- `create_qgis_layer()` reste le chemin synchrone (utilisé par `tests/test_live_layers.py`) ; il lit en flux sauf si on lui passe `local_path`.

### 4.4. Sources datées, sous-couches, services ArcGIS, géométrie Overpass

- **`{time}`** dans `url` / `archive_target` est remplacé au chargement par `aujourd'hui (UTC) + offset_days`, ramené au premier du mois quand `step` vaut `month`, écrit selon `format` (`time_json`). La date résolue est ajoutée au nom de la couche et affichée comme « Date chargée » dans les détails. Pour le WMS, tout paramètre KVP que QGIS ne gère pas lui-même (`TIME`, `ELEVATION`, `DIM_*`, `map`, clés) reste dans l'URL du service et `IgnoreGetMapUrl=1` fait envoyer GetMap à cette URL plutôt qu'à celle annoncée dans les capacités (qui les perdrait). Pour le WMTS, les paramètres de dimension vont dans `tileDimensions`.
- **`layer`** choisit la couche OGR d'un fichier multi-couches (`|layername=` ; sans lui OGR ouvre la première couche) ou la sous-couche d'un MapServer ArcGIS. **`geometry_type`** (`point` / `line` / `polygon`) ajoute `|geometrytype=` pour les fichiers qui mélangent plusieurs types.
- **ArcGIS** : `arcgisfeatureserver` (`url` = point d'accès de la couche) et `arcgismapserver` (`url` = racine du MapServer ou de l'ImageServer). Aucun `crs` n'est envoyé : la couche garde le CRS du service. Les services d'images publiés sous forme de cache de tuiles LERC (rasters d'altitude ou de population, par exemple) ne sont pas rendus par le fournisseur QGIS.
- **Géométrie Overpass** (`overpass_task.resolve_overpass_geometry_mode`) : la colonne `geometry_type` si elle est renseignée ; sinon des lignes pour un style linéaire ou une clé OSM linéaire, des polygones pour un style de remplissage, sinon un point central par objet (comportement par défaut inchangé). Les recettes polygonales utilisent `out geom`, transforment les chemins fermés et les relations multipolygones (anneaux assemblés, trous affectés) en MultiPolygons, et placent ce qu'OSM cartographie sans surface (nœuds, chemins ouverts) dans une couche de points associée pour ne rien perdre. Leur emprise est limitée à 0,5° x 0,5° (points et lignes : 3° x 3°), avec un délai serveur de 25 s et un `maxsize` de 256 Mo.

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
