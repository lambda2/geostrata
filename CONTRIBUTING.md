# Contributing to Geostrata

🌐 **Language**: **English** | [Français](CONTRIBUTING.fr.md)

Thank you for contributing to **Geostrata**! This project is designed so that adding a geospatial data source (WFS, WMTS, COG, VRT, FlatGeobuf, XYZ, or Overpass recipe) is as simple as editing a declarative YAML file.

---

## 1. Adding a Data Source (YAML)

All data sources live under `packages/registry/sources/`:
- `sources/global/`: Datasets covering the entire world (OSM, global DEMs, Overpass recipes).
- `sources/countries/<iso-2-code>/`: National or regional datasets organized by lowercase ISO-3166-1 alpha-2 country code (e.g., `fr/`, `ch/`, `be/`).
- `sources/thematic/<theme>/<provider>.yaml`: Multi-country thematic or humanitarian datasets, one file per provider (hazards, population, health, hydrology...).
- `sources/_generated/`: Written by `npm run ingest` (OSM Editor Layer Index, geoBoundaries), ignored by git. Never edit by hand.

Shared fields go in a `defaults:` block at the top of a file, and a layer published country by country is written once with `for_each_country`: see [Adding a Data Source](docs/adding-a-source.md).

### Example YAML Entry

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
  - drinking water
  - fountain
  - lyon
  - grand lyon
license: Licence Ouverte / Etalab 2.0
attribution: © Métropole de Lyon
```

For full field documentation, `/vsicurl/` streaming prefixes, and Overpass recipes, see [docs/adding-a-source.md](docs/adding-a-source.md).

---

## 2. Testing and Compiling Locally

1. Install dependencies in `packages/registry`:
   ```bash
   cd packages/registry
   npm install
   ```
2. Run Zod schema validation and SQLite compilation:
   ```bash
   npm run build
   ```

The compiler validates every YAML file, computes bounding-box area in square degrees (`area_deg2`), embeds the corresponding `.qml` style, enriches tags with the multilingual synonym dictionary (`assets/i18n/synonyms.json`), and emits `dist/catalog.sqlite`.

### Live layer test (real QGIS, real network)

`npm run build` only proves the YAML is well formed. `packages/qgis-plugin/tests/test_live_layers.py` loads the layers in a headless QGIS exactly as the plugin does and reads real data from each (one tile, one feature or one raster block). Run it with the Python bundled with QGIS, after `npm run build`:

```bash
# macOS; on Linux use `python3` where `import qgis` works
QGIS_PY=/Applications/QGIS.app/Contents/MacOS/python
T=packages/qgis-plugin/tests/test_live_layers.py

QT_QPA_PLATFORM=offscreen $QGIS_PY $T --changed origin/main --smoke   # what CI runs on a pull request
QT_QPA_PLATFORM=offscreen $QGIS_PY $T --id 'fr-ign-*'                 # by id (glob)
QT_QPA_PLATFORM=offscreen $QGIS_PY $T --type wmts,wfs                 # by source type
QT_QPA_PLATFORM=offscreen $QGIS_PY $T --all --sample-families 20      # everything, generated families sampled
packages/qgis-plugin/tests/run_live_in_docker.sh 3.44-noble --smoke   # same QGIS image as CI
```

- **broken** (404, layer invalid on a reachable server, wrong content, no data) fails the run; **unreachable** (timeout, 5xx, 403/429) is only reported.
- `--json out.json --markdown out.md` write the reports; `--help` lists every option.
- A layer that cannot be fixed goes in `packages/qgis-plugin/tests/live_layers_allowlist.txt` with a reason.
- CI runs this on every pull request (changed entries + two entries per source type) and on the whole catalog every Monday; broken layers end up in the `catalog-health` issue.

---

## 3. Coding Conventions

### TypeScript (`packages/registry`)
- **Strict Prettier Formatting**: `{"semi": false, "useTabs": true, "singleQuote": true}` (tabs, no semicolons, single quotes).
- **Function Declarations**: Use `function myFunction() {}` rather than arrow functions assigned to constants.
- **Typing**: Avoid single-letter generic parameters (`T`, `K`). Always write `Array<CatalogEntry>` instead of `CatalogEntry[]`.
- **Conditionals**: Prefer explicit ternary expressions `condition ? 'value' : null` over `condition && 'value'`.

### Python / PyQGIS (`packages/qgis-plugin`)
- **Zero External Dependencies (`pip`)**: Use only the Python standard library and native PyQGIS APIs (`qgis.core`, `qgis.gui`, `qgis.processing`).
- **Non-Blocking Execution**: Any network request or heavy spatial processing must run inside a `QgsTask` or `QgsNetworkAccessManager`.
