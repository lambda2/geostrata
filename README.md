# Geostrata

**Geostrata** est un moteur de recherche spatialisé, 100 % statique et sans serveur de sources de données géographiques (vecteurs, rasters, MNT/DEM, COG, FlatGeobuf, WFS, WMTS, requêtes Overpass OSM, Editor Layer Index), couplé à un plugin PyQGIS natif intégré directement dans la barre de recherche universelle de QGIS (`Ctrl+K`).

---

## Pourquoi Geostrata ?

Dans l'écosystème QGIS, l'accès rapide à la donnée géographique est fragmenté :
- **QuickMapServices (QMS)** dépend d'un backend centralisé et se limite essentiellement aux fonds de carte rasters (TMS/XYZ/WMS), sans couvrir la donnée brute exploitable (vecteurs, MNT/DEM, données humanitaires).
- **Les flux métiers et humanitaires** (WFS locaux, Cloud-Optimized GeoTIFF, FlatGeobuf, limites administratives FieldMaps/HDX, requêtes Overpass OSM) sont dispersés sur des dizaines de portails.
- **Aucun moteur de recherche spatialisé unifié** ne permettait de taper un mot-clé (`gst eau`, `gst dem`, `gst hopital`, `gst communes`) et d'obtenir en moins de 3 millisecondes toutes les couches locales et mondiales qui intersectent l'emprise actuelle du canevas QGIS.

---

## Fonctionnalités Clés

- **Recherche instantanée (`Ctrl+K`)** : Intégration native via `QgsLocatorFilter` (préfixe `gst`) avec indexation plein texte multilingue (**SQLite FTS5** : FR, EN, ES, AR) et filtrage spatial en deux étapes (**R-Tree SQLite** + géométrie exacte **WKT PyQGIS**).
- **Tri par pertinence géographique** : Les sources locales (ex. WFS métropolitain ou national) apparaissent toujours avant les sources mondiales (`area_deg2 ASC`). Le mot-clé `!global` permet de désactiver le filtre spatial.
- **Streaming ou Extraction hors-ligne** :
  - `Entrée` : Charge le flux en direct (`/vsicurl/`, WFS avec `restrictToRequestBBOX='1'`, WMTS, XYZ, ou requête Overpass multi-miroirs) avec application automatique du style `.qml` et de l'attribution.
  - `Maj + Entrée` : Découpe et extrait la couche sur l'emprise du canevas en tâche de fond (`QgsTask`) vers un fichier local `.gpkg` ou `.tif`.
- **OSM Editor Layer Index (ELI) & Icônes personnalisables** : Accès optionnel aux couches mondiales et régionales d'orthophotos ELI, pack de 12 icônes SVG thématiques et support d'icônes personnalisées par couche ou par catégorie.
- **100 % Statique & Multi-catalogue** : Aucune dépendance Python externe (`pip`). Compatible QGIS 3 (Qt5) et QGIS 4 (Qt6). Possibilité d'attacher simultanément le catalogue public et des catalogues privés (`ATTACH DATABASE`).

---

## Architecture du Monorepo

```text
geostrata/
├── docs/                           # Documentation d'architecture et guides techniques
├── packages/
│   ├── registry/                   # [TypeScript] Sources YAML, VRT, Styles QML & Compilateur SQLite
│   │   ├── sources/                # Fichiers YAML (global/, countries/, thematic/)
│   │   ├── assets/                 # Styles QML, VRT globaux, polygones WKT, synonymes i18n
│   │   ├── src/                    # Validation Zod et compilateur SQLite (FTS5 + R-Tree)
│   │   └── dist/                   # Artefacts compilés (catalog.sqlite, catalog.sqlite.gz, manifest.json)
│   ├── qgis-plugin/                # [Python] Plugin PyQGIS natif (zéro dépendance externe)
│   └── website/                    # [HTML/CSS/JS] Site web, explorateur interactif & documentation
└── CONTRIBUTING.md
```

---

## Démarrage Rapide

```bash
cd packages/registry
npm install
npm run ingest
npm run build
```

Les fichiers `catalog.sqlite`, `catalog.sqlite.gz`, `catalog.json` et `manifest.json` sont générés dans `packages/registry/dist/`.

---

## Contribuer

Ajouter une nouvelle source de données ne nécessite que quelques lignes de YAML dans `packages/registry/sources/`. Consultez [CONTRIBUTING.md](CONTRIBUTING.md) et [docs/adding-a-source.md](docs/adding-a-source.md) pour les détails.

---

## Licence

Distribué sous licence **GNU General Public License v2.0 or later (GPL-2.0-or-later)** conformément aux exigences de l'écosystème des plugins QGIS. Les métadonnées YAML du catalogue sont librement réutilisables.
