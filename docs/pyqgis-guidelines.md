# PyQGIS Development Guidelines (`pyqgis-guidelines.md`)

🌐 **Language**: **English** | [Français](pyqgis-guidelines.fr.md)

This document defines the architecture and coding standards for `packages/qgis-plugin/geostrata/`.

---

## 1. Zero External Dependencies (`pip`)

The plugin must work out-of-the-box on Windows, macOS, and Linux across standard QGIS installations (QGIS 3.28 LTR through QGIS 4.x / Qt5 & Qt6):
- **Allowed Python modules**: Python standard library only (`sqlite3`, `urllib.parse`, `urllib.request`, `gzip`, `json`, `pathlib`, `hashlib`, `shutil`, `tempfile`, `typing`, `dataclasses`).
- **Allowed QGIS / Qt modules**: `qgis.core`, `qgis.gui`, `qgis.utils`, `qgis.processing`, `qgis.PyQt.QtCore`, `qgis.PyQt.QtGui`, `qgis.PyQt.QtWidgets`, `qgis.PyQt.QtNetwork`, `qgis.PyQt.QtXml`.
- **Forbidden**: `requests`, `pydantic`, `pyyaml`, `shapely`, `geopandas`, `sqlalchemy`.

---

## 2. Thread Safety & Non-Blocking Execution

The QGIS main UI thread must **never** freeze:

1. **Inside `QgsLocatorFilter.fetchResults(string, context, feedback)`**:
   - `fetchResults` runs on a worker thread managed by QGIS.
   - **SQLite Connections**: Always open a dedicated read-only connection per worker thread (`sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, check_same_thread=False)`) and close it in a `finally:` block.
   - **Canvas Extent Access**: Never call `iface.mapCanvas()` from `fetchResults`. Capture the canvas extent (`QgsRectangle`) and CRS inside `prepare(string, context)` on the main thread and transform it to `EPSG:4326` using `QgsCoordinateTransform`.
   - Periodically check `feedback.isCanceled()` inside loops.
   - **Ranking and filters**: `CatalogDatabaseClient.search_sources()` returns results already ranked (see `architecture.md`, section 3). Pass the ELI toggle, the category and the exact geometry test (`accept=`) to it instead of filtering its output, so the limit is applied last.

2. **Background Catalog Synchronization (`sync_manager.py`)**:
   - Use a `QgsTask` subclass to download `manifest.json` and `catalog.sqlite.gz`.
   - Verify the SHA-256 digest before atomically replacing the cached `catalog.sqlite` file.

3. **Offline Extraction (`Shift + Enter` -> `extractor_task.py`)**:
   - Run clipping inside a `QgsTask` calling `processing.run("native:extractbyextent", ...)` for vectors or `processing.run("gdal:cliprasterbyextent", ...)` for rasters.
   - Only instantiate and add the resulting `.gpkg` or `.tif` layer to `QgsProject.instance()` inside `QgsTask.finished(result)` on the main thread.

4. **Canvas Preview (`ui/coverage_preview.py`)**:
   - Use the shared `CoveragePreview` to highlight a footprint; never create another `QgsRubberBand`. It must only be called from the main thread (`triggerResultFromAction`, dock slots) and is disposed in `unload()`.

---

## 3. User-Visible Strings (`i18n/`)

- Write every user-visible string in **English** and wrap it in `tr()`: `tr("Layer loaded: {name}", name=title)`. Use named placeholders, never f-strings or concatenation, and `tr_n(singular, plural, count)` for counted strings.
- Module-level constants are marked with `N_("...")` and translated where they are displayed with `tr(constant)`.
- Add the French text to `geostrata/i18n/fr.py` in the same change; `tests/test_core.py` fails on a missing translation, an unused key or mismatched placeholders.
- Do not use `QCoreApplication.translate()` or `.ts`/`.qm` files: the plugin ships without a compile step.
- `core/` modules must stay importable without QGIS (`i18n` is pure Python), so the test suite runs with a plain `python3`.

---

## 4. Provider URI Construction (`uri_builder.py`)

| `type` | QGIS Provider | PyQGIS Class | URI Format |
| :--- | :--- | :--- | :--- |
| `xyz` / `tms` | `wms` | `QgsRasterLayer` | `type=xyz&url=<encoded_url>&zmax=<max_zoom>&zmin=<min_zoom>&crs=EPSG:3857` |
| `wms` / `wmts` | `wms` | `QgsRasterLayer` | `contextualWMSLegend=0&crs=...&dpiMode=7&format=image/png&layers=...&styles=&tileMatrixSet=...&url=...` |
| `wfs` | `WFS` | `QgsVectorLayer` | `pagingEnabled='true' preferCoordinatesForWfsT11='false' restrictToRequestBBOX='1' srsname='<crs>' typename='<typename>' url='<url>' version='auto'` |
| `cog` / `vrt` | `gdal` | `QgsRasterLayer` | `/vsicurl/https://...` |
| `fgb` / `geojson` | `ogr` | `QgsVectorLayer` | `/vsicurl/https://...` |
| `vectortile` | `vectortile` | `QgsVectorTileLayer` | `type=xyz&url=<url>&zmax=<max_zoom>&zmin=<min_zoom>` |
| `overpass` | `ogr` | `QgsVectorLayer` | Asynchronous multi-mirror `[out:json]` query converted to a local `.geojson` file via `overpass_task.py`. |

---

## 5. Applying QML Styling and Attribution Metadata

Once a layer is valid (`layer.isValid()`):
1. **Attribution & License**:
   ```python
   meta = layer.metadata()
   meta.setRights([entry.attribution])
   meta.setLicenses([entry.license])
   layer.setMetadata(meta)
   layer.setAttribution(entry.attribution)
   ```
2. **Embedded XML Style (`style_qml`)**:
   ```python
   if entry.style_qml:
       doc = QDomDocument()
       if doc.setContent(entry.style_qml):
           layer.importNamedStyle(doc)
           layer.triggerRepaint()
   ```
