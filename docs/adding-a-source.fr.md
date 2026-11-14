# Guide d'Ajout d'une Source (`adding-a-source.fr.md`)

🌐 **Langue** : [English](adding-a-source.md) | **Français**

Ce guide détaille les règles d'écriture des fichiers YAML dans `packages/registry/sources/`, l'utilisation de `/vsicurl/`, la création de fichiers VRT et la syntaxe des recettes Overpass.

---

## 1. Organisation des Dossiers

Un fichier par fournisseur de données, rangé par portée. Un fichier reste court (un fournisseur a rarement plus de 20 couches) : une contribution est un petit diff facile à relire, et deux contributeurs modifient rarement le même fichier.

- `packages/registry/sources/global/` : Données mondiales généralistes (fonds de carte, MNT mondial, recettes Overpass).
- `packages/registry/sources/countries/<iso2>/<fournisseur>.yaml` : Données nationales ou locales (ex. `countries/fr/france.yaml`, `countries/ch/swisstopo.yaml`).
- `packages/registry/sources/thematic/<theme>/<fournisseur>.yaml` : Données thématiques multi-pays (ex. `thematic/hazards/gdacs.yaml`, `thematic/population/meta-hrsl.yaml`).
- `packages/registry/sources/_generated/` : Écrit par `npm run ingest` (OSM Editor Layer Index, geoBoundaries). Ignoré par git et régénéré en CI : ne modifiez et ne commitez jamais ces fichiers, modifiez le script d'ingestion dans `src/ingest/`.

Un fichier contient une source unique, une liste de sources, ou la forme recommandée avec des valeurs par défaut partagées :

```yaml
defaults:                 # champs communs à toutes les sources du fichier
  category: hazard
  license: Public Domain (USGS)
  attribution: U.S. Geological Survey
  tags: [usgs, earthquake, seisme]   # ajoutés aux tags propres à chaque source
sources:
  - id: global-usgs-earthquakes-week
    name: USGS - Earthquakes M4.5+ (past 7 days)
    type: geojson
    url: https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson
    tags: [alerts]
```

### Une entrée pour plusieurs pays (`for_each_country`)

Quand un fournisseur publie la même couche pays par pays, décrivez-la une seule fois. L'entrée est développée à la compilation en une couche par pays listé ; `country_iso` et l'emprise sont renseignés automatiquement.

```yaml
- id: "{iso2}-hot-roads"            # l'id doit contenir {iso2} ou {iso3}
  for_each_country: [HTI, COD, UKR]  # codes ISO-3166-1 alpha-3, ou "all"
  name: "HOT OSM Export - Roads ({country})"
  i18n:
    fr:
      name: "Export OSM HOT - Routes ({country_fr})"
  type: archive
  url: https://example.org/{ISO3}/roads/hotosm_{iso3}_roads_lines_gpkg.zip
  archive_target: hotosm_{iso3}_roads_lines_gpkg.gpkg
  tags: [roads, routes, "{country}", "{country_fr}"]
```

| Variable | Exemple pour Haïti |
| :--- | :--- |
| `{iso2}` / `{ISO2}` | `ht` / `HT` |
| `{iso3}` / `{ISO3}` | `hti` / `HTI` |
| `{country}` / `{country_fr}` | `Haiti` / `Haïti` |

Les codes, noms et emprises des pays viennent de `assets/polygons/country-index.json` (à régénérer avec `npm run refresh:countries`). Ne listez que les pays pour lesquels le fournisseur publie réellement le fichier.

---

## 2. Schéma YAML Complet

| Champ | Type | Requis | Description |
| :--- | :--- | :--- | :--- |
| `id` | `string` | Oui | Identifiant unique en kebab-case (`^[a-z0-9-]+$`), ex: `fr-ign-orthophotos`. |
| `name` | `string` | Oui | Titre par défaut en anglais affiché dans QGIS (`Ctrl+K`). |
| `description` | `string` | Non | Description par défaut en anglais (indexée dans FTS5). |
| `i18n` | `Record<string, { name?: string, description?: string }>` | Non | Traductions localisées indexées par code langue ISO-2 (ex: `i18n.fr.name`, `i18n.fr.description`). Toutes les langues sont également indexées dans FTS5. |
| `type` | `SourceType` | Oui | `'xyz' \| 'tms' \| 'wms' \| 'wmts' \| 'wfs' \| 'vectortile' \| 'cog' \| 'vrt' \| 'fgb' \| 'geojson' \| 'archive' \| 'overpass'` |
| `category` | `SourceCategory` | Oui | `'basemap' \| 'elevation' \| 'hydrography' \| 'transport' \| 'admin' \| 'buildings' \| 'landuse' \| 'imagery' \| 'utilities' \| 'crisis' \| 'population' \| 'health' \| 'hazard' \| 'weather'` |
| `url` | `string` | Oui | URL du flux, chemin `/vsicurl/https://...`, ou requête/expression Overpass. |
| `crs` | `string` | Non | Code EPSG (défaut : `'EPSG:4326'`). |
| `country_iso` | `string` | Non | Code ISO-3166-1 alpha-2 sur 2 lettres majuscules (`'FR'`). Injecte automatiquement le polygone WKT et la bbox du pays si non spécifiés. |
| `bbox` | `[number, number, number, number]` | Non* | `[minX, minY, maxX, maxY]` en WGS84 (`EPSG:4326`). Défaut `[-180, -90, 180, 90]` si `country_iso` absent. |
| `geom_wkt` | `string` | Non | Géométrie exacte en WKT (`EPSG:4326`). Calculée automatiquement depuis `bbox`, le contour soigné de `assets/polygons/countries.json`, ou l'emprise du pays dans `assets/polygons/country-index.json` si absente. |
| `tags` | `Array<string>` | Oui | Mots-clés métiers ou géographiques (automatiquement enrichis par `synonyms.json`). |
| `license` | `string` | Oui | Nom court de la licence (ex: `ODbL`, `Licence Ouverte 2.0`, `CC-BY-4.0`). |
| `attribution` | `string` | Oui | Texte de copyright injecté dans les métadonnées de la couche QGIS. |
| `style_file` | `string` | Non | Fichier `.qml` dans `assets/styles/` (ex: `hydrography.qml`). Par défaut, utilise `<category>.qml` s'il existe. |
| `min_zoom` | `number` | Non | Niveau de zoom minimal (entier >= 0). |
| `max_zoom` | `number` | Non | Niveau de zoom maximal (entier <= 24). |
| `archive_target` | `string` | Non | Chemin interne du fichier `.gpkg` ou `.shp` lorsque `type: archive` (via `/vsizip//vsicurl/`). |
| `priority` | `number` | Non | Entier de `-10` à `10` (défaut `0`). Coup de pouce éditorial : à pertinence égale, les valeurs hautes sont listées en premier. Laissez `0` sauf pour une couche de référence qu'un cartographe d'urgence veut voir d'abord. |
| `data_date` | `string` | Non | Millésime ou fréquence de mise à jour en texte libre, affiché avant le chargement (ex. `2021`, `updated daily`). |
| `for_each_country` | `Array<string> \| 'all'` | Non | Codes ISO alpha-3 : développe l'entrée en une couche par pays (voir section 1). |

---

## 3. Utilisation de GDAL `/vsicurl/` (COG, FlatGeobuf, VRT)

GDAL et OGR permettent de lire des fichiers distants par requêtes HTTP `Range` sans télécharger l'intégralité du fichier :

### Cloud-Optimized GeoTIFF (`type: cog`) ou VRT (`type: vrt`)
- Préfixer l'URL HTTPS par `/vsicurl/` :
  ```yaml
  type: vrt
  url: /vsicurl/https://raw.githubusercontent.com/lambda2/geostrata/main/packages/registry/assets/vrt/copernicus-dem-glo30.vrt
  ```

### FlatGeobuf (`type: fgb`)
- Les fichiers `.fgb` possèdent un index spatial R-Tree binaire en en-tête. OGR ne télécharge que les entités intersectant l'emprise demandée :
  ```yaml
  type: fgb
  url: /vsicurl/https://example.org/data/boundaries.fgb
  ```

---

## 4. Recettes Overpass (`type: overpass`)

Pour les données OpenStreetMap à la demande, utilisez `type: overpass`.
Le champ `url` peut contenir :
- Soit une expression de filtres OSM concise (ex: `amenity=drinking_water|natural=spring|man_made=water_well`) que `overpass_task.py` transforme en requête Overpass `[out:json]` bornée par l'emprise avec bascule multi-miroirs.
- Soit une requête Overpass QL complète incluant le template `{{bbox}}` (`minLat,minLon,maxLat,maxLon`).
