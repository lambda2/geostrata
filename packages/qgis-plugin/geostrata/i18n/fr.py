"""French translations of the plugin interface: {English source string: French text}.

Keys must match the string literals passed to tr() / tr_n() / N_() exactly, placeholders
({name}, {count}...) included; tests/test_core.py checks both directions.
"""

from __future__ import annotations

from typing import Dict

TRANSLATIONS: Dict[str, str] = {
    # --- core/entry_details.py -------------------------------------------------
    "XYZ tiles (raster)": "Tuiles XYZ (raster)",
    "TMS tiles (raster)": "Tuiles TMS (raster)",
    "WMS service (raster)": "Service WMS (raster)",
    "WMTS tiles (raster)": "Tuiles WMTS (raster)",
    "WFS service (vector)": "Service WFS (vecteur)",
    "Cloud Optimized GeoTIFF (raster)": "Cloud Optimized GeoTIFF (raster)",
    "GDAL virtual raster (VRT)": "Raster virtuel GDAL (VRT)",
    "FlatGeobuf (vector)": "FlatGeobuf (vecteur)",
    "GeoJSON (vector)": "GeoJSON (vecteur)",
    "Vector tiles": "Tuiles vectorielles",
    "OpenStreetMap Overpass query (vector)": "Requête Overpass OpenStreetMap (vecteur)",
    "File inside a remote ZIP archive": "Fichier dans une archive ZIP distante",
    "Regional extent ({west}, {south} to {east}, {north})": (
        "Emprise régionale ({west}, {south} à {east}, {north})"
    ),
    "Worldwide": "Monde entier",
    "Not specified": "Non renseigné",
    "Unreachable": "Inaccessible",
    "{status}, checked on {date}": "{status}, vérifié le {date}",
    "Degraded (slow or failing intermittently)": "Dégradé (lent ou en échec par intermittence)",
    "Provider": "Fournisseur",
    "License": "Licence",
    "Format": "Format",
    "Coverage": "Couverture",
    "Data date": "Date des données",
    "Link status": "État du lien",
    "Working": "Fonctionnel",
    "Not checked yet": "Pas encore vérifié",
    # --- core tasks -------------------------------------------------------------
    "Geostrata: offline extraction ({name})": "Geostrata : extraction hors-ligne ({name})",
    "Offline extraction failed.": "Échec de l'extraction hors-ligne.",
    "[Local]": "[Local]",
    "Could not open the raster source: {uri}": "Impossible d'ouvrir la source raster : {uri}",
    "Could not open the vector source: {uri}": "Impossible d'ouvrir la source vecteur : {uri}",
    "Layer extracted offline: {path}": "Couche extraite hors-ligne : {path}",
    "The extracted file is invalid: {path}": "Le fichier extrait est invalide : {path}",
    "All Overpass servers failed ({errors})": "Tous les serveurs Overpass ont échoué ({errors})",
    "Geostrata Overpass: {name}": "Geostrata Overpass : {name}",
    "Overpass query failed for {name}": "Échec de la requête Overpass pour {name}",
    " (extent reduced to the centre of the map)": " (emprise réduite au centre de la carte)",
    "No OSM feature found in this extent for: {name}": (
        "Aucune entité OSM trouvée sur cette emprise pour : {name}"
    ),
    "{count} OSM features loaded via {host}: {name}{note}": (
        "{count} entités OSM chargées via {host} : {name}{note}"
    ),
    "yes": "oui",
    "no": "non",
    "Geostrata: catalog synchronization": "Geostrata : synchronisation des catalogues",
    "Remote synchronization skipped ({error})": "Synchronisation distante ignorée ({error})",
    # --- plugin.py --------------------------------------------------------------
    "Geostrata — Layer browser": "Geostrata — Explorateur de couches",
    "Open the Geostrata layer browser panel (or press Ctrl+K and type gst)": (
        "Ouvrir le panneau d'exploration de couches Geostrata (ou Ctrl+K puis gst)"
    ),
    "Settings & catalogs…": "Paramètres & catalogues…",
    "Share a layer (generate YAML)…": "Partager une couche (générer le YAML)…",
    "Synchronize the catalog now": "Synchroniser le catalogue maintenant",
    # --- ui/actions.py ----------------------------------------------------------
    "Could not load '{name}' (provider={provider}, uri={uri})": (
        "Impossible de charger « {name} » (provider={provider}, uri={uri})"
    ),
    "{name} covers the whole world: no footprint to show.": (
        "{name} couvre le monde entier : aucune emprise à afficher."
    ),
    "Overpass query started in the background: {name}": (
        "Requête Overpass lancée en tâche de fond : {name}"
    ),
    "Offline extraction started in the background: {name}": (
        "Extraction hors-ligne lancée en tâche de fond : {name}"
    ),
    "The coverage of {name} could not be displayed.": (
        "La couverture de {name} n'a pas pu être affichée."
    ),
    "Dense WFS layer added ({name}): zoom in to 1:50,000 to display features.": (
        "Couche WFS dense ajoutée ({name}) : zoomez au 1:50 000 pour afficher les entités."
    ),
    "Layer loaded: {name} ({attribution})": "Couche chargée : {name} ({attribution})",
    # --- ui/browser_dock.py -----------------------------------------------------
    "All categories": "Toutes les catégories",
    "Basemaps": "Fonds de carte",
    "Imagery & orthophotos": "Imagerie & orthophotos",
    "Elevation, DEM & relief": "Altitude, MNT & relief",
    "Water & hydrography": "Eau & hydrographie",
    "Administrative boundaries & cadastre": "Limites administratives & cadastre",
    "Buildings & facilities": "Bâtiments & équipements",
    "Transport, roads, rail & cycling": "Transport, routes, rail & vélo",
    "Networks, energy & telecom": "Réseaux, énergie & télécom",
    "Land use, forests & geology": "Occupation du sol, forêts & géologie",
    "Crisis, rescue & humanitarian": "Crise, secours & humanitaire",
    "Population & demography": "Population & démographie",
    "Health facilities": "Établissements de santé",
    "Natural hazards & risks": "Aléas & risques naturels",
    "Weather & climate": "Météo & climat",
    "Global": "Monde",
    "Search (e.g. water, cadastre, hospital, lidar, cycling)…": (
        "Rechercher (ex. eau, cadastre, hopital, lidar, velo)…"
    ),
    "Map extent": "Emprise carte",
    "Only list layers that intersect the current QGIS map extent": (
        "N'afficher que les couches qui intersectent l'emprise actuelle de la carte QGIS"
    ),
    "Load": "Charger",
    "Add the layer to QGIS (files are downloaded in the background)": (
        "Ajouter la couche à QGIS (les fichiers sont téléchargés en arrière-plan)"
    ),
    "Extract (.gpkg/.tif)": "Extraire (.gpkg/.tif)",
    "Clip the layer to the current map extent and save it locally": (
        "Découper la couche sur l'emprise actuelle de la carte et l'enregistrer en local"
    ),
    "Zoom to coverage": "Zoomer sur la couverture",
    "Show where the selected layer has data and zoom the map to it": (
        "Montrer où la couche sélectionnée contient des données et y centrer la carte"
    ),
    "{count} layer available": "{count} couche disponible",
    "{count} layers available": "{count} couches disponibles",
    "Select a layer to see its details and coverage.": (
        "Sélectionnez une couche pour voir ses détails et sa couverture."
    ),
    # --- ui/locator_filter.py ---------------------------------------------------
    "Geostrata (local & global layers)": "Geostrata (couches locales & mondiales)",
    "Enter: load · Shift+Enter: extract offline": (
        "Entrée : charger · Maj+Entrée : extraire hors-ligne"
    ),
    "link degraded": "lien dégradé",
    "1. Local & national layers": "1. Couches locales & nationales",
    "2. Global layers": "2. Couches mondiales",
    "2. Local & national layers": "2. Couches locales & nationales",
    "1. Global layers": "1. Couches mondiales",
    "Load layer (Enter)": "Charger la couche (Entrée)",
    "Extract offline to GeoPackage/GeoTIFF (Shift+Enter)": (
        "Extraire hors-ligne en GeoPackage/GeoTIFF (Maj+Entrée)"
    ),
    "Show coverage on map": "Afficher la couverture sur la carte",
    # --- ui/settings_dialog.py --------------------------------------------------
    "Geostrata — Settings, Overpass servers & catalogs": (
        "Geostrata — Paramètres, serveurs Overpass & catalogues"
    ),
    "<b>Overpass API servers (priority order):</b><br>"
    "Geostrata queries the checked servers from top to bottom (regional servers are "
    "skipped automatically when your map is outside their coverage) and falls back to the next one when a server is overloaded.<br>"
    "Double-click a cell to edit the name or the URL (e.g. to replace <code>YOUR_API_KEY</code>).": (
        "<b>Serveurs Overpass API (ordre de priorité) :</b><br>"
        "Geostrata interroge les serveurs cochés de haut en bas (les serveurs régionaux sont "
        "ignorés automatiquement quand votre carte est hors de leur couverture) et bascule sur le suivant en cas de surcharge.<br>"
        "Double-cliquez sur une cellule pour modifier le nom ou l'URL (ex. remplacer <code>YOUR_API_KEY</code>)."
    ),
    "↑ Move up (higher priority)": "↑ Monter (priorité +)",
    "↓ Move down (lower priority)": "↓ Descendre (priorité -)",
    "Add an Overpass server…": "Ajouter un serveur Overpass…",
    "Remove": "Supprimer",
    "Reset (OpenStreetMap wiki list)": "Réinitialiser (liste du wiki OpenStreetMap)",
    "Overpass API servers": "Serveurs Overpass API",
    "Automatic (follow the QGIS language)": "Automatique (selon la langue de QGIS)",
    "Language of the Geostrata interface and of layer names and descriptions. "
    "Layers without a translation stay in English; the locator filter title "
    "follows after restarting QGIS.": (
        "Langue de l'interface Geostrata et des noms et descriptions de couches. "
        "Les couches sans traduction restent en anglais ; le titre du filtre de la barre "
        "de recherche suit après un redémarrage de QGIS."
    ),
    "Language:": "Langue :",
    "Include OSM Editor Layer Index layers (ELI — imagery & maps worldwide)": (
        "Inclure les couches OSM Editor Layer Index (ELI — orthophotos & cartes du monde entier)"
    ),
    "ELI sources:": "Sources ELI :",
    "Geostrata coloured thematic icons (recommended)": (
        "Icônes thématiques colorées Geostrata (recommandé)"
    ),
    "QGIS technical icons (raster / point / line / polygon)": (
        "Icônes techniques QGIS (raster / point / ligne / polygone)"
    ),
    "Open the custom icons folder…": "Ouvrir le dossier d'icônes personnalisées…",
    "Put a <category>.svg or <layer-id>.svg file in this folder to override any icon.": (
        "Placez un fichier <categorie>.svg ou <id-couche>.svg dans ce dossier pour "
        "personnaliser n'importe quelle icône."
    ),
    "Icon style:": "Style des icônes :",
    "<b>Configured catalogs (manifest.json):</b><br>"
    "You can combine the public Geostrata catalog with private team or NGO "
    "catalogs (all queried together through ATTACH DATABASE).": (
        "<b>Catalogues configurés (manifest.json) :</b><br>"
        "Vous pouvez combiner le catalogue public Geostrata avec des catalogues privés "
        "d'équipe ou d'ONG (interrogés simultanément via ATTACH DATABASE)."
    ),
    "Add a manifest.json URL…": "Ajouter une URL manifest.json…",
    "Remove selected": "Supprimer la sélection",
    "Reset to default": "Réinitialiser par défaut",
    "Synchronize now": "Synchroniser maintenant",
    "<b>Local SQLite cache status:</b>": "<b>État du cache SQLite local :</b>",
    "Display & catalogs": "Affichage & catalogues",
    "Save and close": "Enregistrer et fermer",
    "Cancel": "Annuler",
    "Add an Overpass API server": "Ajouter un serveur Overpass API",
    "Overpass server URL (e.g. https://my-server.org/api/interpreter):": (
        "URL du serveur Overpass (ex. https://mon-serveur.org/api/interpreter) :"
    ),
    "Overpass server name": "Nom du serveur Overpass",
    "Server name:": "Nom du serveur :",
    "Add a catalog": "Ajouter un catalogue",
    "URL of the manifest.json file:": "URL du fichier manifest.json :",
    "Synchronization started in the background.": "Synchronisation lancée en arrière-plan.",
    "Enabled": "Actif",
    "Server name": "Nom du serveur",
    "Endpoint URL (/api/interpreter)": "URL de l'endpoint (/api/interpreter)",
    "Cached": "En cache",
    "Sources": "Sources",
    "Build date": "Date de build",
    "Customizing Geostrata icons\n"
    "===========================\n"
    "Put your own .svg or .png files in this folder:\n"
    "- Per category: basemap.svg, imagery.svg, elevation.svg, hydrography.svg,\n"
    "  admin.svg, buildings.svg, transport.svg, utilities.svg, landuse.svg, crisis.svg\n"
    "- Per layer identifier: e.g. fr-grandlyon-bornes-fontaines.svg\n": (
        "Personnalisation des icônes Geostrata\n"
        "=====================================\n"
        "Placez dans ce dossier vos propres fichiers .svg ou .png :\n"
        "- Par catégorie : basemap.svg, imagery.svg, elevation.svg, hydrography.svg,\n"
        "  admin.svg, buildings.svg, transport.svg, utilities.svg, landuse.svg, crisis.svg\n"
        "- Par identifiant de couche : ex. fr-grandlyon-bornes-fontaines.svg\n"
    ),
    # --- ui/share_dialog.py -----------------------------------------------------
    "Geostrata — Share a layer (YAML)": "Geostrata — Partager une couche (YAML)",
    "Select a layer of your QGIS project to generate its Geostrata "
    "YAML entry automatically.": (
        "Sélectionnez une couche de votre projet QGIS pour générer automatiquement "
        "sa fiche YAML Geostrata."
    ),
    "QGIS layer:": "Couche QGIS :",
    "Category:": "Catégorie :",
    "E.g. FR (leave empty if worldwide)": "Ex. FR (laisser vide si mondial)",
    "ISO-2 country code:": "Code pays ISO-2 :",
    "CC BY 4.0": "Licence Ouverte / Etalab 2.0",
    "License:": "Licence :",
    "© Data provider": "© Fournisseur de données",
    "Attribution:": "Attribution :",
    "Copy YAML": "Copier le YAML",
    "Propose on GitHub (pre-filled issue)…": "Proposer sur GitHub (issue pré-remplie)…",
    "Close": "Fermer",
    "# No layer is open in the project.": "# Aucune couche ouverte dans le projet.",
    "YAML entry copied to the clipboard.": "Fiche YAML copiée dans le presse-papiers.",
    # --- 1.1: ArcGIS services, time-templated sources -----------------------------
    "ArcGIS Feature Service (vector)": "Service d'entités ArcGIS (vecteur)",
    "ArcGIS Map/Image Service (raster)": "Service de cartes/d'images ArcGIS (raster)",
    "Date loaded": "Date chargée",
    "Loading": "Chargement",
    "Downloaded in the background, then kept for offline use": (
        "Téléchargé en arrière-plan, puis conservé pour un usage hors ligne"
    ),
    # --- core/file_cache.py, core/loader_task.py: background download and cache -----
    "{value} GB": "{value} Go",
    "{value} MB": "{value} Mo",
    "{value} kB": "{value} ko",
    "the download was interrupted {count} times ({error})": (
        "le téléchargement a été interrompu {count} fois ({error})"
    ),
    "not enough free disk space ({needed} needed, {free} available)": (
        "espace disque insuffisant ({needed} nécessaires, {free} disponibles)"
    ),
    "'{target}' was not found in the archive (it contains: {files})": (
        "« {target} » est introuvable dans l'archive (elle contient : {files})"
    ),
    "the downloaded archive is corrupted ({error})": (
        "l'archive téléchargée est corrompue ({error})"
    ),
    "the server sent no data": "le serveur n'a envoyé aucune donnée",
    "the server answered HTTP {code}": "le serveur a répondu HTTP {code}",
    "the archive contains an unsafe path: {path}": (
        "l'archive contient un chemin dangereux : {path}"
    ),
    "this source has no downloadable URL": "cette source n'a pas d'URL téléchargeable",
    "the downloaded file could not be opened ({uri})": (
        "le fichier téléchargé n'a pas pu être ouvert ({uri})"
    ),
    "unknown error": "erreur inconnue",
    "Geostrata: loading {name}": "Geostrata : chargement de {name}",
    "Downloading {name}: {done} of {total}": "Téléchargement de {name} : {done} sur {total}",
    "Downloading {name}: {done}": "Téléchargement de {name} : {done}",
    "Extracting {name}: {percent} %": "Extraction de {name} : {percent} %",
    "Opening {name}…": "Ouverture de {name}…",
    "{name} is already being downloaded.": "{name} est déjà en cours de téléchargement.",
    "Could not load '{name}': {error}": "Impossible de charger « {name} » : {error}",
    "Download cancelled: {name}. The part already downloaded is kept and the download resumes from there next time.": (
        "Téléchargement annulé : {name}. La partie déjà téléchargée est conservée et le "
        "téléchargement reprendra de là la prochaine fois."
    ),
    "Layer loaded from the local copy of {date}: {name}. The server could not be checked for a newer version ({error}).": (
        "Couche chargée depuis la copie locale du {date} : {name}. Impossible de vérifier "
        "auprès du serveur s'il existe une version plus récente ({error})."
    ),
    "Layer loaded from the local cache: {name} (downloaded on {date})": (
        "Couche chargée depuis le cache local : {name} (téléchargée le {date})"
    ),
    "Layer loaded: {name} ({size} downloaded and kept in the local cache)": (
        "Couche chargée : {name} ({size} téléchargés et conservés dans le cache local)"
    ),
    "Offline extraction cancelled: {name}": "Extraction hors ligne annulée : {name}",
    # --- core/overpass_task.py: companion layers of polygon recipes -------------------
    "points": "points",
    "lines": "lignes",
    "polygons": "polygones",
    # --- ui/settings_dialog.py: downloaded files tab ----------------------------------
    "<b>Downloaded files:</b><br>ZIP archives and GeoJSON files are downloaded once, in the background, and kept in this folder: loading them again is immediate and works offline. Geostrata asks the server whether a file changed (at most once a day for archives) and downloads it again only then. When the folder exceeds the maximum size, the files that were not used for the longest time are removed.": (
        "<b>Fichiers téléchargés :</b><br>Les archives ZIP et les fichiers GeoJSON sont "
        "téléchargés une seule fois, en arrière-plan, et conservés dans ce dossier : les "
        "recharger est immédiat et fonctionne hors ligne. Geostrata demande au serveur si un "
        "fichier a changé (au plus une fois par jour pour les archives) et ne le retélécharge "
        "que dans ce cas. Quand le dossier dépasse la taille maximale, les fichiers inutilisés "
        "depuis le plus longtemps sont supprimés."
    ),
    "Choose…": "Choisir…",
    "Default folder": "Dossier par défaut",
    "Open": "Ouvrir",
    "Folder:": "Dossier :",
    " GB": " Go",
    "Maximum size:": "Taille maximale :",
    "Delete the downloaded files": "Supprimer les fichiers téléchargés",
    "Files used by a layer of the open project are kept. Layers of other projects that point to a deleted file will have to be loaded again.": (
        "Les fichiers utilisés par une couche du projet ouvert sont conservés. Les couches "
        "d'autres projets qui pointent vers un fichier supprimé devront être rechargées."
    ),
    "Disk space used:": "Espace disque utilisé :",
    "Downloaded files": "Fichiers téléchargés",
    "{count} file, {size}": "{count} fichier, {size}",
    "{count} files, {size}": "{count} fichiers, {size}",
    "Folder for downloaded files": "Dossier des fichiers téléchargés",
    "Delete {size} of downloaded files? They will be downloaded again the next time their layer is loaded.": (
        "Supprimer {size} de fichiers téléchargés ? Ils seront téléchargés à nouveau au "
        "prochain chargement de leur couche."
    ),
    "{freed} deleted. {kept} kept: used by a layer of the open project or being downloaded.": (
        "{freed} supprimés. {kept} conservés : utilisés par une couche du projet ouvert "
        "ou en cours de téléchargement."
    ),
}
