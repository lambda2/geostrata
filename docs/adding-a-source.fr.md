# Guide d'Ajout d'une Source (`adding-a-source.fr.md`)

🌐 **Langue** : [English](adding-a-source.md) | **Français**

Ce guide détaille les règles d'écriture des fichiers YAML dans `packages/registry/sources/`, l'utilisation de `/vsicurl/`, la création de fichiers VRT et la syntaxe des recettes Overpass.

---

## 1. Organisation des Dossiers

Chaque fichier YAML peut contenir une source unique (objet YAML) ou une liste de sources (`Array<CatalogEntry>`).
- `packages/registry/sources/global/` : Données mondiales (`bbox: [-180, -90, 180, 90]` par défaut).
- `packages/registry/sources/countries/<iso2>/` : Données nationales ou locales (ex. `countries/fr/france.yaml`, `countries/ch/swisstopo.yaml`).
- `packages/registry/sources/thematic/` : Données thématiques multi-pays (HDX, FieldMaps, OSM Editor Layer Index).

---

## 2. Schéma YAML Complet

| Champ | Type | Requis | Description |
| :--- | :--- | :--- | :--- |
| `id` | `string` | Oui | Identifiant unique en kebab-case (`^[a-z0-9-]+$`), ex: `fr-ign-orthophotos`. |
| `name` | `string` | Oui | Nom lisible affiché dans QGIS (`Ctrl+K`). |
| `description` | `string` | Non | Description détaillée des données (indexée dans FTS5). |
| `type` | `SourceType` | Oui | `'xyz' \| 'tms' \| 'wms' \| 'wmts' \| 'wfs' \| 'vectortile' \| 'cog' \| 'vrt' \| 'fgb' \| 'geojson' \| 'archive' \| 'overpass'` |
| `category` | `SourceCategory` | Oui | `'basemap' \| 'elevation' \| 'hydrography' \| 'transport' \| 'admin' \| 'buildings' \| 'landuse' \| 'imagery' \| 'utilities' \| 'crisis'` |
| `url` | `string` | Oui | URL du flux, chemin `/vsicurl/https://...`, ou requête/expression Overpass. |
| `crs` | `string` | Non | Code EPSG (défaut : `'EPSG:4326'`). |
| `country_iso` | `string` | Non | Code ISO-3166-1 alpha-2 sur 2 lettres majuscules (`'FR'`). Injecte automatiquement le polygone WKT et la bbox du pays si non spécifiés. |
| `bbox` | `[number, number, number, number]` | Non* | `[minX, minY, maxX, maxY]` en WGS84 (`EPSG:4326`). Défaut `[-180, -90, 180, 90]` si `country_iso` absent. |
| `geom_wkt` | `string` | Non | Géométrie exacte en WKT (`EPSG:4326`). Calculée automatiquement depuis `bbox` ou `assets/polygons/countries.json` si absente. |
| `tags` | `Array<string>` | Oui | Mots-clés métiers ou géographiques (automatiquement enrichis par `synonyms.json`). |
| `license` | `string` | Oui | Nom court de la licence (ex: `ODbL`, `Licence Ouverte 2.0`, `CC-BY-4.0`). |
| `attribution` | `string` | Oui | Texte de copyright injecté dans les métadonnées de la couche QGIS. |
| `style_file` | `string` | Non | Fichier `.qml` dans `assets/styles/` (ex: `hydrography.qml`). Par défaut, utilise `<category>.qml` s'il existe. |
| `min_zoom` | `number` | Non | Niveau de zoom minimal (entier >= 0). |
| `max_zoom` | `number` | Non | Niveau de zoom maximal (entier <= 24). |
| `archive_target` | `string` | Non | Chemin interne du fichier `.gpkg` ou `.shp` lorsque `type: archive` (via `/vsizip//vsicurl/`). |

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
  url: /vsicurl/https://data.fieldmaps.io/adm0/osm/intl/adm0_polygons.fgb
  ```

---

## 4. Recettes Overpass (`type: overpass`)

Pour les données OpenStreetMap à la demande, utilisez `type: overpass`.
Le champ `url` peut contenir :
- Soit une expression de filtres OSM concise (ex: `amenity=drinking_water|natural=spring|man_made=water_well`) que `overpass_task.py` transforme en requête Overpass `[out:json]` bornée par l'emprise avec bascule multi-miroirs.
- Soit une requête Overpass QL complète incluant le template `{{bbox}}` (`minLat,minLon,maxLat,maxLon`).
