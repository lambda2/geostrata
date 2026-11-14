# Contribuer à Geostrata

🌐 **Langue** : [English](CONTRIBUTING.md) | **Français**

Merci de contribuer à **Geostrata** ! Ce projet est conçu pour que l'ajout d'une source de données géographique (flux WFS, WMTS, COG, VRT, FlatGeobuf, XYZ ou recette Overpass) soit aussi simple que l'édition d'un fichier YAML.

---

## 1. Ajouter une source de données (YAML)

Toutes les sources se trouvent dans `packages/registry/sources/` :
- `sources/global/` : Données couvrant le monde entier (OSM, DEM globaux, requêtes Overpass).
- `sources/countries/<code-iso-2>/` : Données nationales ou locales classées par code pays ISO-3166-1 alpha-2 en minuscules (ex. `fr/`, `ch/`, `be/`).
- `sources/thematic/<theme>/<fournisseur>.yaml` : Données thématiques ou humanitaires multi-pays, un fichier par fournisseur (aléas, population, santé, hydrologie...).
- `sources/_generated/` : Écrit par `npm run ingest` (OSM Editor Layer Index, geoBoundaries), ignoré par git. Ne jamais modifier à la main.

Les champs partagés se placent dans un bloc `defaults:` en tête de fichier, et une couche publiée pays par pays s'écrit une seule fois avec `for_each_country` : voir [Ajouter une source](docs/adding-a-source.fr.md).

### Exemple de fichier YAML

```yaml
id: fr-grandlyon-bornes-fontaines
name: Grand Lyon - Drinking Water Fountains
description: Public drinking water fountains across the Greater Lyon metropolitan area.
i18n:
  fr:
    name: Métropole de Lyon - Bornes-fontaines d'eau potable
    description: Réseau des bornes-fontaines d'eau potable sur le territoire de la Métropole de Lyon (Data Grand Lyon).
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

Pour la documentation exhaustive des champs, des préfixes `/vsicurl/` et des recettes Overpass, consultez [docs/adding-a-source.fr.md](docs/adding-a-source.fr.md).

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

### Test des couches en conditions réelles (vrai QGIS, vrai réseau)

`npm run build` prouve seulement que le YAML est bien formé. `packages/qgis-plugin/tests/test_live_layers.py` charge les couches dans un QGIS sans interface, exactement comme le plugin, et lit de vraies données dans chacune (une tuile, une entité ou un bloc raster). Lancez-le avec le Python fourni par QGIS, après `npm run build` :

```bash
# macOS ; sous Linux, utilisez le `python3` où `import qgis` fonctionne
QGIS_PY=/Applications/QGIS.app/Contents/MacOS/python
T=packages/qgis-plugin/tests/test_live_layers.py

QT_QPA_PLATFORM=offscreen $QGIS_PY $T --changed origin/main --smoke   # ce que la CI lance sur une pull request
QT_QPA_PLATFORM=offscreen $QGIS_PY $T --id 'fr-ign-*'                 # par id (motif glob)
QT_QPA_PLATFORM=offscreen $QGIS_PY $T --type wmts,wfs                 # par type de source
QT_QPA_PLATFORM=offscreen $QGIS_PY $T --all --sample-families 20      # tout, familles générées échantillonnées
packages/qgis-plugin/tests/run_live_in_docker.sh 3.44-noble --smoke   # même image QGIS que la CI
```

- **broken** (404, couche invalide sur un serveur joignable, mauvais contenu, aucune donnée) fait échouer le test ; **unreachable** (délai dépassé, 5xx, 403/429) est seulement signalé.
- `--json out.json --markdown out.md` écrivent les rapports ; `--help` liste toutes les options.
- Une couche impossible à corriger va dans `packages/qgis-plugin/tests/live_layers_allowlist.txt`, avec une raison.
- La CI lance ce test sur chaque pull request (entrées modifiées + deux entrées par type de source) et sur tout le catalogue chaque lundi ; les couches cassées sont listées dans l'issue `catalog-health`.

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
