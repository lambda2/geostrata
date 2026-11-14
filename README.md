# Geostrata

🌐 **Language**: **English** | [Français](README.fr.md)

**Geostrata** is a 100% static, serverless spatial data catalog engine (vectors, rasters, DEM/DTM, Cloud-Optimized GeoTIFF, FlatGeobuf, WFS, WMTS, OSM Overpass recipes, and OSM Editor Layer Index) paired with a zero-dependency native PyQGIS 3 & 4 plugin integrated directly into the QGIS Universal Locator Bar (`Ctrl+K`).

🔗 **Live Catalog & Documentation**: [https://lambda2.github.io/geostrata/](https://lambda2.github.io/geostrata/)  
📦 **QGIS Plugin Repository XML**: `https://lambda2.github.io/geostrata/plugins.xml`

---

## Why Geostrata?

Finding and loading geospatial datasets in QGIS is traditionally fragmented:
- **QuickMapServices (QMS)** relies on a centralized backend and primarily focuses on raster basemaps (TMS/XYZ/WMS), leaving out raw analytical datasets (vectors, elevation models, humanitarian boundaries).
- **Thematic and national open-data streams** (local WFS, Cloud-Optimized GeoTIFFs, FlatGeobuf, UN OCHA COD / geoBoundaries administrative boundaries, live OSM Overpass queries) are scattered across dozens of portals.
- **No unified spatial search engine** previously allowed typing a keyword (`gst water`, `gst dem`, `gst hospital`, `gst cadastre`) to retrieve in under 3 milliseconds every local, national, and global dataset intersecting your active QGIS map canvas.

---

## Key Features

- **Instant Universal Search (`Ctrl+K`)**: Native `QgsLocatorFilter` integration (prefix `gst`) powered by multilingual full-text search (**SQLite FTS5**: EN, FR, ES, AR) and two-stage spatial filtering (**SQLite R-Tree** + exact **PyQGIS WKT** geometry intersection).
- **Spatial Relevance Ranking**: Local and national datasets (e.g., municipal WFS or national LiDAR DEM) automatically rank above global datasets (`area_deg2 ASC`). Append `!global` to bypass the map canvas spatial filter.
- **Dual Mode — Streaming or Offline Extraction**:
  - `Enter`: Streams the layer directly (`/vsicurl/`, WFS with `restrictToRequestBBOX='1'`, WMTS, XYZ, or multi-mirror Overpass query) and automatically applies embedded `.qml` cartographic styling and attribution metadata.
  - `Shift + Enter`: Clips and extracts the dataset to your current map canvas extent in a background task (`QgsTask`) as a local `.gpkg`, `.tif`, or `.geojson` file.
- **OSM Editor Layer Index (ELI) & Customizable Icons**: Optional toggle for 170+ regional and global imagery layers from the OSM Editor Layer Index, 12 bundled thematic SVG icons, and support for user-defined custom `.svg`/`.png` icons per category or layer ID.
- **Preview Before Loading**: The Geostrata panel and the locator show each layer's description, license, data date and link status, and outline its coverage on the map before anything is loaded.
- **English & French Interface**: The plugin follows the QGIS language (English by default), with a setting to override it.
- **100% Static & Multi-Catalog**: Zero external Python dependencies (`pip`). Runs natively on Windows, macOS, and Linux across QGIS 3 (Qt5) and QGIS 4 (Qt6). Supports querying public and private team/NGO catalogs simultaneously via SQLite `ATTACH DATABASE`.

---

## Monorepo Architecture

```text
geostrata/
├── docs/                           # Technical architecture & developer guides (EN / FR)
├── packages/
│   ├── registry/                   # [TypeScript] YAML sources, VRT, QML styles & SQLite compiler
│   │   ├── sources/                # YAML definitions (global/, countries/, thematic/)
│   │   ├── assets/                 # QML styles, global VRTs, WKT polygons, i18n synonyms
│   │   ├── src/                    # Zod schema validation & SQLite compiler (FTS5 + R-Tree)
│   │   └── dist/                   # Compiled artifacts (catalog.sqlite, catalog.sqlite.gz, manifest.json)
│   ├── qgis-plugin/                # [Python] Native PyQGIS 3 & 4 plugin (zero pip dependencies)
│   └── website/                    # [HTML/CSS/JS] Interactive catalog explorer & documentation site
├── CONTRIBUTING.md                 # Contribution guide (EN)
└── CONTRIBUTING.fr.md              # Guide de contribution (FR)
```

---

## Quick Start (Catalog Compiler)

```bash
cd packages/registry
npm install
npm run ingest
npm run build
```

Compiled artifacts (`catalog.sqlite`, `catalog.sqlite.gz`, `catalog.json`, and `manifest.json`) are emitted to `packages/registry/dist/`.

To package and deploy the plugin directly to your local QGIS 3 / QGIS 4 profile:

```bash
cd packages/registry
npm run deploy:qgis
```

---

## Documentation

| Document | English (Default) | Français |
| :--- | :--- | :--- |
| **Contributing Guide** | [CONTRIBUTING.md](CONTRIBUTING.md) | [CONTRIBUTING.fr.md](CONTRIBUTING.fr.md) |
| **Technical Architecture** | [docs/architecture.md](docs/architecture.md) | [docs/architecture.fr.md](docs/architecture.fr.md) |
| **Adding a Data Source** | [docs/adding-a-source.md](docs/adding-a-source.md) | [docs/adding-a-source.fr.md](docs/adding-a-source.fr.md) |
| **PyQGIS Plugin Guidelines** | [docs/pyqgis-guidelines.md](docs/pyqgis-guidelines.md) | [docs/pyqgis-guidelines.fr.md](docs/pyqgis-guidelines.fr.md) |

---

## License

Distributed under the **GNU General Public License v2.0 or later (GPL-2.0-or-later)** in compliance with the QGIS plugin ecosystem. Catalog YAML metadata definitions are freely reusable.
