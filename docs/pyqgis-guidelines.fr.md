# Directives de Développement PyQGIS (`pyqgis-guidelines.fr.md`)

🌐 **Langue** : [English](pyqgis-guidelines.md) | **Français**

Ce document fixe les règles strictes de conception et de codage pour `packages/qgis-plugin/geostrata/`.

---

## 1. Zéro Dépendance Externe (`pip`)

Le plugin doit fonctionner immédiatement après installation sur Windows, macOS et Linux dans n'importe quelle version standard de QGIS (3.28 LTR à 4.x / Qt5 & Qt6) :
- **Modules Python autorisés** : Bibliothèque standard uniquement (`sqlite3`, `urllib.parse`, `urllib.request`, `gzip`, `json`, `pathlib`, `hashlib`, `shutil`, `tempfile`, `typing`, `dataclasses`).
- **Modules QGIS / Qt autorisés** : `qgis.core`, `qgis.gui`, `qgis.utils`, `qgis.processing`, `qgis.PyQt.QtCore`, `qgis.PyQt.QtGui`, `qgis.PyQt.QtWidgets`, `qgis.PyQt.QtNetwork`, `qgis.PyQt.QtXml`.
- **Interdit** : `requests`, `pydantic`, `pyyaml`, `shapely`, `geopandas`, `sqlalchemy`.

---

## 2. Thread-Safety & Exécution Non-Bloquante

L'interface graphique de QGIS ne doit **jamais** geler :

1. **Dans `QgsLocatorFilter.fetchResults(string, context, feedback)`** :
   - `fetchResults` est exécuté dans un thread d'arrière-plan géré par QGIS.
   - **Règle SQLite** : Toujours ouvrir une connexion SQLite dédiée au thread courant (`sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, check_same_thread=False)`) et la fermer en fin de recherche.
   - **Accès au canevas** : Ne jamais appeler `iface.mapCanvas()` directement depuis le thread secondaire de `fetchResults`. Capturer l'emprise du canevas (`QgsRectangle`) et son CRS dans `prepare(string, context)` (qui s'exécute sur le thread principal avant `fetchResults`) et la reprojeter en `EPSG:4326` via `QgsCoordinateTransform`.
   - Vérifier régulièrement `feedback.isCanceled()` dans la boucle de résultats.
   - **Classement et filtres** : `CatalogDatabaseClient.search_sources()` renvoie des résultats déjà classés (voir `architecture.fr.md`, section 3). Lui passer l'option ELI, la catégorie et le test géométrique exact (`accept=`) plutôt que de filtrer sa sortie, afin que la limite s'applique en dernier.

2. **Téléchargement et Synchronisation (`sync_manager.py`)** :
   - Utiliser une sous-classe de `QgsTask` pour télécharger `manifest.json` et `catalog.sqlite.gz`.
   - Vérifier le hash SHA-256 avant de remplacer atomiquement le fichier `catalog.sqlite` en cache.

3. **Extraction Hors-Ligne (`Maj + Entrée` -> `extractor_task.py`)** :
   - Exécuter le découpage dans une `QgsTask` en appelant `processing.run("native:extractbyextent", ...)` pour les vecteurs ou `processing.run("gdal:cliprasterbyextent", ...)` pour les rasters.
   - Ne jamais instancier ou ajouter une couche au `QgsProject.instance()` depuis `QgsTask.run()`. Charger la couche dans `QgsTask.finished(result)` sur le thread principal.

4. **Sources de type fichier (`loader_task.py`, `file_cache.py`)** :
   - Ne jamais ouvrir une archive ZIP ou une URL GeoJSON sur le thread principal. `ui/actions.load_entry()` envoie les entrées retenues par `uri_builder.needs_local_copy()` à `launch_file_load_task()` ; FlatGeobuf, COG et VRT restent lus en flux (voir `architecture.fr.md`, section 4.3).
   - Tout le travail réseau et disque se fait dans `QgsTask.run()` via `FileCache.fetch()`, qui interroge `isCanceled()` entre deux blocs. `file_cache.py` est en Python pur et doit le rester (il est testé unitairement avec un serveur HTTP local).
   - Une couche créée dans `run()` appartient au thread de travail : appeler `layer.moveToThread(QgsApplication.instance().thread())` en dernière étape de `run()`, et n'appliquer le style et l'ajout au projet que dans `finished()`.
   - Capturer l'état du thread principal (réglages du cache, `open_layer_sources()`) avant de lancer la tâche et conserver la tâche dans `_ACTIVE_FILE_LOAD_TASKS` jusqu'à `finished()`.
   - Tout autre code ayant besoin d'un tel fichier (la tâche d'extraction hors ligne) appelle `fetch_entry_file()` depuis son propre thread de travail, jamais le réseau directement.

5. **Aperçu sur le canevas (`ui/coverage_preview.py`)** :
   - Utiliser le `CoveragePreview` partagé pour mettre une emprise en évidence ; ne jamais créer un autre `QgsRubberBand`. Il ne doit être appelé que depuis le thread principal (`triggerResultFromAction`, slots du panneau) et est libéré dans `unload()`.

---

## 3. Chaînes Visibles par l'Utilisateur (`i18n/`)

- Écrire chaque chaîne visible en **anglais** et l'entourer de `tr()` : `tr("Layer loaded: {name}", name=title)`. Utiliser des paramètres nommés, jamais de f-string ni de concaténation, et `tr_n(singulier, pluriel, nombre)` pour les chaînes comptées.
- Les constantes de module sont marquées avec `N_("...")` et traduites à l'affichage avec `tr(constante)`.
- Ajouter le texte français dans `geostrata/i18n/fr.py` dans le même changement ; `tests/test_core.py` échoue sur une traduction manquante, une clé inutilisée ou des paramètres différents.
- Ne pas utiliser `QCoreApplication.translate()` ni de fichiers `.ts`/`.qm` : le plugin est livré sans étape de compilation.
- Les modules de `core/` doivent rester importables sans QGIS (`i18n` est en Python pur), pour que la suite de tests tourne avec un simple `python3`.

---

## 4. Construction des URIs par Type de Flux (`uri_builder.py`)

| `type` | Provider QGIS | Classe PyQGIS | Format de l'URI |
| :--- | :--- | :--- | :--- |
| `xyz` / `tms` | `wms` | `QgsRasterLayer` | `type=xyz&url=<encoded_url>&zmax=<max_zoom>&zmin=<min_zoom>&crs=EPSG:3857` |
| `wms` / `wmts` | `wms` | `QgsRasterLayer` | Paramètres `contextualWMSLegend=0&crs=...&dpiMode=7&format=image/png&layers=...&styles=&tileMatrixSet=...&url=...` |
| `wfs` | `WFS` | `QgsVectorLayer` | `pagingEnabled='true' preferCoordinatesForWfsT11='false' restrictToRequestBBOX='1' srsname='<crs>' typename='<typename>' url='<url>' version='auto'` |
| `wms` avec KVP supplémentaires (`TIME=...`) | `wms` | `QgsRasterLayer` | `IgnoreGetMapUrl=1&...&url=<point d'accès>?TIME%3D<valeur>` (les paramètres restent dans l'URL, encodés une fois de plus) |
| `wmts` avec une dimension | `wms` | `QgsRasterLayer` | ajoute `tileDimensions=TIME%3D<valeur>` |
| `cog` / `vrt` | `gdal` | `QgsRasterLayer` | `/vsicurl/https://...` |
| `fgb` | `ogr` | `QgsVectorLayer` | `/vsicurl/https://...[\|layername=<couche>][\|geometrytype=Point/LineString/Polygon]` |
| `geojson` | `ogr` | `QgsVectorLayer` | `<fichier en cache>[\|geometrytype=...]` (`/vsicurl/https://...` sur le chemin synchrone) |
| `archive` | `ogr` / `gdal` | `QgsVectorLayer` / `QgsRasterLayer` | `<cible extraite en cache>[\|layername=<couche>][\|geometrytype=...]` (`/vsizip//vsicurl/<url>/<cible>` sur le chemin synchrone) |
| `arcgisfeatureserver` | `arcgisfeatureserver` | `QgsVectorLayer` | `url='<point d'accès de la couche>'` |
| `arcgismapserver` | `arcgismapserver` | `QgsRasterLayer` | `format='PNG32' layer='<id de sous-couche ou vide>' url='<racine MapServer ou ImageServer>'` |
| `vectortile` | `vectortile` | `QgsVectorTileLayer` | `type=xyz&url=<url>&zmax=<max_zoom>&zmin=<min_zoom>` |
| `overpass` | `ogr` | `QgsVectorLayer` | Requête asynchrone multi-miroirs `[out:json]` convertie en `.geojson` local via `overpass_task.py` (points, lignes ou polygones, voir `architecture.fr.md`, section 4.4). |

`{time}` dans `url` / `archive_target` est résolu par `resolved_entry_url()` / `resolved_archive_target()` avant tout ce qui précède (date UTC) ; utiliser `entry_display_name()` pour le titre de la couche afin que la date soit visible.

---

## 5. Application du Style QML et de l'Attribution

Dès qu'une couche `layer` est valide (`layer.isValid()`) :
1. **Attribution & Licence** :
   ```python
   meta = layer.metadata()
   meta.setRights([entry.attribution])
   meta.setLicenses([entry.license])
   layer.setMetadata(meta)
   layer.setAttribution(entry.attribution)
   ```
2. **Style XML embarqué (`style_qml`)** :
   ```python
   if entry.style_qml:
       doc = QDomDocument()
       if doc.setContent(entry.style_qml):
           layer.importNamedStyle(doc)
           layer.triggerRepaint()
   ```
