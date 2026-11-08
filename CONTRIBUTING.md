# Contribuer à Geostrata

Merci de contribuer à **Geostrata** ! Ce projet est conçu pour que l'ajout d'une source de données géographique (flux WFS, WMTS, COG, VRT, FlatGeobuf, XYZ ou recette Overpass) soit aussi simple que l'édition d'un fichier YAML.

---

## 1. Ajouter une source de données (YAML)

Toutes les sources se trouvent dans `packages/registry/sources/` :
- `sources/global/` : Données couvrant le monde entier (OSM, DEM globaux, requêtes Overpass).
- `sources/countries/<code-iso-2>/` : Données nationales ou locales classées par code pays ISO-3166-1 alpha-2 en minuscules (ex. `fr/`, `ch/`, `be/`).
- `sources/thematic/` : Données thématiques ou humanitaires multi-pays (HDX, FieldMaps, OSM Editor Layer Index).

### Exemple de fichier YAML

```yaml
id: fr-grandlyon-bornes-fontaines
name: Métropole de Lyon - Bornes fontaines et points d'eau
description: Réseau des bornes fontaines d'eau potable sur le territoire du Grand Lyon.
type: wfs
category: hydrography
url: https://data.grandlyon.com/geoserver/metropole-de-lyon/ows?SERVICE=WFS&VERSION=2.0.0&TYPENAMES=metropole-de-lyon:adr_voie_lieu.adrbornefontaine_latest
crs: EPSG:2154
country_iso: FR
bbox: [4.69, 45.55, 5.09, 45.92]
tags:
  - eau potable
  - fontaine
  - lyon
  - grand lyon
license: Licence Ouverte / Etalab 2.0
attribution: © Métropole de Lyon
```

Pour la documentation exhaustive des champs, des préfixes `/vsicurl/` et des recettes Overpass, consultez [docs/adding-a-source.md](docs/adding-a-source.md).

---

## 2. Tester et compiler le catalogue localement

1. Installez les dépendances dans `packages/registry` :
   ```bash
   cd packages/registry
   npm install
   ```
2. Lancez la compilation et la validation Zod :
   ```bash
   npm run build
   ```

Le compilateur vérifie la conformité de chaque fichier YAML, calcule l'aire en degrés carrés (`area_deg2`), embarque le style `.qml` correspondant, enrichit les tags avec le dictionnaire multilingue (`assets/i18n/synonyms.json`), et génère `dist/catalog.sqlite`.

---

## 3. Conventions de Code

### TypeScript (`packages/registry`)
- **Formatage Prettier strict** : `{"semi": false, "useTabs": true, "singleQuote": true}` (tabulations, pas de point-virgule, guillemets simples).
- **Déclarations de fonctions** : Utiliser `function maFonction() {}` plutôt que des fonctions fléchées assignées à des constantes.
- **Typage** : Aucun générique à une seule lettre (`T`, `K`). Toujours écrire `Array<CatalogEntry>` plutôt que `CatalogEntry[]`.
- **Conditions** : Préférer l'opérateur ternaire explicite `condition ? 'value' : null` au lieu de `condition && 'value'`.

### Python / PyQGIS (`packages/qgis-plugin`)
- **Zéro dépendance externe (`pip`)** : Utiliser uniquement la bibliothèque standard Python et l'API native PyQGIS (`qgis.core`, `qgis.gui`, `qgis.processing`).
- **Non-bloquant** : Tout appel réseau ou traitement lourd doit s'exécuter dans une `QgsTask` ou via `QgsNetworkAccessManager`.
