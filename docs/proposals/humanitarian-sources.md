# Humanitarian / disaster-response source candidates for Geostrata

Research date: 2026-11-14. All checks were run from the owner's Mac with `curl` and the GDAL 3.13.3 bundled in `/Applications/QGIS-final-4_2_3.app` (`gdalinfo`, `ogrinfo`). Nothing was loaded in the QGIS GUI itself.

## How to read the status column

- **VERIFIED**: the endpoint answered and fits an existing `type`. The evidence says exactly what was checked:
  - "OGR open" / "GDAL open" = `ogrinfo` / `gdalinfo` opened the exact path given (strongest evidence).
  - "GetMap PNG" = a WMS GetMap for the named layer returned an image (size given; a very small PNG may be an empty tile).
  - "tile 200" = one XYZ tile fetched with the right content-type.
  - "range + zip listing" = HTTP 206 on a range request and the zip central directory was read remotely to confirm the inner path; the layer itself was not opened.
  - "HTTP only" = status/content-type only.
- **UNVERIFIED**: could not reach it, or reached it but could not confirm a loadable layer.
- **NOT-DIRECTLY-LOADABLE**: needs a key, a download and conversion, or a capability the schema lacks. Where the endpoint itself answered, the note says so.

Licenses marked "(confirm)" are from memory, not re-read on the provider's site during this session. Check them before publishing an entry.

Not already in the registry: everything below was cross-checked against `sources/global/*.yaml`, `sources/thematic/humanitarian.yaml`, `fieldmaps-cod.yaml` and the global ELI ids.

**Side finding on an existing entry**: `global-esa-worldcover-2021` (`https://services.terrascope.be/wms/v2`) failed on 4 attempts over about 15 minutes with `HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR` (WMS GetCapabilities, GetMap and the WMTS endpoint). It may be a temporary outage, but worth re-testing.

---

## 1. Population and demographics

Category: needs new category `population` (closest existing fit: `landuse`).

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-meta-hrsl-population` | High Resolution Settlement Layer, general population, 30 m / Meta Data for Good + CIESIN | vrt | `/vsicurl/https://dataforgood-fb-data.s3.amazonaws.com/hrsl-cogs/hrsl_general/hrsl_general-latest.vrt` | Global (about 160 countries) / CC-BY-4.0 (confirm) / © Meta and CIESIN, Columbia University | None | **VERIFIED** GDAL open: VRT, 1295354 x 458338 px, 1 arc-second, EPSG:4326, Float64 |
| `global-ghsl-pop-2020-1km` | GHS-POP R2023A, epoch 2020, 30 arc-second / EC JRC | cog (via `/vsizip/`) | `/vsizip//vsicurl/https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2020_GLOBE_R2023A_4326_30ss/V1-0/GHS_POP_E2020_GLOBE_R2023A_4326_30ss_V1_0.zip/GHS_POP_E2020_GLOBE_R2023A_4326_30ss_V1_0.tif` | Global / CC-BY-4.0 / © European Union, GHSL | 482 MB zip; `archive` type is documented for gpkg/shp only, so this would be typed `cog` with a vsizip path | **VERIFIED** GDAL open: GeoTIFF 43202 x 21384, LZW, 256 px blocks, 5 overview levels (external `.ovr` in the zip). Rendering speed not measured |
| `global-gibs-gpw-population-density-2020` | GPWv4 population density 2020 / NASA SEDAC via GIBS | xyz | `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GPW_Population_Density_2020/default/default/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png` (max_zoom 7) | Global / CC-BY-4.0 (confirm) / NASA SEDAC, CIESIN | Pre-styled picture, not values | **VERIFIED** tile 200 image/png (3/3/4) |
| `xx-worldpop-ppp-2020-constrained` | WorldPop constrained population 100 m, per country / WorldPop | cog | `https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/BSGM/{ISO3}/{iso3}_ppp_2020_constrained.tif` | Per country / CC-BY-4.0 / WorldPop, University of Southampton | Server ignores Range requests | **NOT-DIRECTLY-LOADABLE** file exists (HTI: 200 image/tiff) but GDAL reports "Range downloading not supported by this server". Same result for the 1 km global mosaic `ppp_2020_1km_Aggregated.tif` (870 MB) |
| `global-worldpop-arcgis-100m` | WorldPop total population 100 m ImageServer / WorldPop + Esri | (ArcGIS ImageServer) | `https://worldpop.arcgis.com/arcgis/rest/services/WorldPop_Total_Population_100m/ImageServer` (also `_1km`, `Population_Density_100m`, `Population_Cohorts_1km`) | Global / CC-BY-4.0 / WorldPop | No WMS (WMSServer returns 400) | **NOT-DIRECTLY-LOADABLE** `exportImage` returned a PNG, but needs an ArcGIS ImageServer type |
| `global-kontur-population-h3` | Kontur Population, H3 hexagons 400 m / Kontur | (gpkg.gz) | `https://geodata-eu-central-1-kontur-public.s3.amazonaws.com/kontur_datasets/kontur_population_20231101.gpkg.gz` | Global / CC-BY-4.0 / © Kontur | 2.4 GB gzip, no random access | **NOT-DIRECTLY-LOADABLE** URL answers 206; must be downloaded and unzipped |
| `africa-jrc-ghsl-builtup-2020` | GHSL built-up surface 2020 / EC JRC GeoServer | wms | `https://geospatial.jrc.ec.europa.eu/geoserver/wms`, layer `africa_platform:ghsl_BuiltUpSurface_2020_30ss` | Africa (layer extent not checked) / CC-BY-4.0 / © European Union | None known | **UNVERIFIED** layer name present in GetCapabilities; GetMap not tested |

## 2. Settlements and building footprints

Category: `buildings`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `xx-hotosm-buildings` | HOT OSM export, buildings, per country / HOT | archive | `https://s3.dualstack.us-east-1.amazonaws.com/production-raw-data-api/ISO3/{ISO3}/buildings/polygons/hotosm_{iso3}_buildings_polygons_gpkg.zip`, target `hotosm_{iso3}_buildings_polygons_gpkg.gpkg` | Per country / ODbL-1.0 / © OpenStreetMap contributors, HOT | Large (HTI 97 MB) | **VERIFIED** HTTP only for HTI (206). Inner path inferred from the roads export, which was opened (see section 7) |
| `global-overture-buildings` | Overture Maps buildings / Overture Maps Foundation | (PMTiles) | `https://tiles.overturemaps.org/2026-10-21.0/buildings.pmtiles` | Global / ODbL-1.0 and CDLA-Permissive (per source) / © OpenStreetMap, Overture Maps Foundation | 180 GB single file; URL changes each monthly release (latest id read from `https://stac.overturemaps.org/catalog.json`) | **NOT-DIRECTLY-LOADABLE** range 206; sibling `places.pmtiles` opened with the GDAL PMTiles driver. Needs a `pmtiles` type |
| `global-vida-google-ms-buildings` | Google + Microsoft open buildings, merged / VIDA on Source Cooperative | (PMTiles) | `https://data.source.coop/vida/google-microsoft-open-buildings/pmtiles/go_ms_building_footprints.pmtiles` (with OSM: `.../google-microsoft-osm-open-buildings/pmtiles/goog_msft_osm.pmtiles`) | Global / CC-BY-4.0 and ODbL-1.0 (confirm) / Google, Microsoft, VIDA | 208 GB and 254 GB | **NOT-DIRECTLY-LOADABLE** both answer 206; not opened. Needs a `pmtiles` type |
| `global-ms-building-footprints` | Global ML Building Footprints / Microsoft | (index CSV) | `https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv` | Global / ODbL-1.0 / © Microsoft | Index of quadkey `.csv.gz` files | **NOT-DIRECTLY-LOADABLE** index answers 206; data needs download and conversion |
| `global-google-open-buildings` | Open Buildings v3 / Google Research | (CSV by S2 cell) | Portal `https://sites.research.google/gr/open-buildings/` | Africa, South and SE Asia, Latin America / CC-BY-4.0 and ODbL-1.0 | Download only | **UNVERIFIED** no direct endpoint found (a guessed `tiles.geojson` URL returned 404) |
| `xx-grid3-settlement-extents` | GRID3 settlement extents / GRID3, CIESIN | (ArcGIS FeatureServer) | `https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/GlobalMap_SettlementExtents/FeatureServer/0` (per country: `GRID3_{ISO3}_Settlement_Extents_v3_0`) | Sub-Saharan Africa / CC-BY-4.0 (confirm) / GRID3 | 1000 features per request | **NOT-DIRECTLY-LOADABLE** a `query?f=geojson` request opened in OGR, but paging makes a plain `geojson` entry incomplete. Needs an ArcGIS FeatureServer type. The same server lists 190 services (DRC health facilities, health zones, schools, roads, Nigeria infrastructure) |

## 3. Hazards and live events

Category: `crisis`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-gdacs-events` | GDACS current disaster alerts (EQ, TC, FL, VO, DR, WF) / GDACS, EC JRC + UN OCHA | geojson | `https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP` (variants: `/latest`; `/SEARCH?eventlist=TC`; `/SEARCH?alertlevel=orange;red`) | Global / free reuse with attribution (confirm exact terms) / GDACS | Live feed; `/MAP` returns 400 | **VERIFIED** OGR open: Point layer for `EVENTS4APP` and `latest`. The two `SEARCH` variants HTTP 200 only |
| `global-usgs-earthquakes-m45-week` | Earthquakes M4.5+, past 7 days / USGS | geojson | `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson` | Global / public domain / USGS Earthquake Hazards Program | Live feed | **VERIFIED** OGR open: 3D Point layer |
| `global-usgs-earthquakes-m25-week` | Earthquakes M2.5+, past 7 days / USGS | geojson | `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson` | Global / public domain / USGS | Live feed | **VERIFIED** HTTP only (200 JSON) |
| `global-usgs-earthquakes-significant-month` | Significant earthquakes, past 30 days / USGS | geojson | `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson` (also `4.5_month`, `all_day`) | Global / public domain / USGS | Live feed | **VERIFIED** HTTP only (all three 200/206 JSON) |
| `global-emsc-earthquakes` | Latest earthquakes / EMSC SeismicPortal | geojson | `https://www.seismicportal.eu/fdsnws/event/1/query?limit=200&format=json` | Global, denser in Euro-Med / license to confirm / EMSC-CSEM | Live; `limit` parameter | **VERIFIED** OGR open: 3D Point layer |
| `global-nasa-eonet-open-events` | EONET open natural events (wildfires, storms, volcanoes, ice) / NASA | geojson | `https://eonet.gsfc.nasa.gov/api/v3/events/geojson?status=open&limit=50` | Global / public domain / NASA EONET | Live; without `limit` the response is 8.7 MB | **VERIFIED** OGR open: Point layer |
| `global-firms-viirs-noaa20-24h` | Active fires VIIRS NOAA-20, last 24 h / NASA FIRMS | archive | `https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/shapes/zips/J1_VIIRS_C2_Global_24h.zip`, target `J1_VIIRS_C2_Global_24h.shp` | Global / NASA open data / NASA FIRMS | No key for these static files | **VERIFIED** OGR open through `/vsizip//vsicurl/`: Point layer |
| `global-firms-modis-24h` | Active fires MODIS, last 24 h / NASA FIRMS | archive | `https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/shapes/zips/MODIS_C6_1_Global_24h.zip`, target `MODIS_C6_1_Global_24h.shp` (7-day: `..._7d.zip`) | Global / NASA open data / NASA FIRMS | None | **VERIFIED** range + zip listing for 24 h; 7-day file HTTP 206 only |
| `global-firms-viirs-noaa21-24h` | Active fires VIIRS NOAA-21, last 24 h / NASA FIRMS | archive | `https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/shapes/zips/J2_VIIRS_C2_Global_24h.zip` | Global / NASA open data / NASA FIRMS | None | **VERIFIED** HTTP only (206, 2.6 MB); inner name assumed `J2_VIIRS_C2_Global_24h.shp`. Note: the Suomi-NPP 24 h and 7 d zips were about 400 bytes (empty) today, so do not add that one |
| `global-gwis-active-fires` | Active fire hotspots, VIIRS / Copernicus GWIS (EFFIS) | wms | `https://maps.effis.emergency.copernicus.eu/gwis`, layer `viirs.hs` (also `modis.hs`, `all.hs`; fire weather index `ecmwf.fwi`) | Global / Copernicus free and open / © European Union, Copernicus EMS GWIS | Time-enabled | **VERIFIED** GetMap PNG 16 KB for `viirs.hs`; other layer names from GetCapabilities only |
| `global-gibs-thermal-anomalies-noaa20` | Fires and thermal anomalies 375 m / NASA GIBS | wms | `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi`, layer `VIIRS_NOAA20_Thermal_Anomalies_375m_All` | Global / NASA open data / NASA EOSDIS GIBS | Time-enabled (defaults to latest) | **VERIFIED** GetMap PNG 12 KB. The WMTS tile path for this layer returned 400, so use WMS |
| `global-glofas-flood-hazard-100y` | Flood hazard, 100-year return period / Copernicus GloFAS | wms | `https://ows.globalfloods.eu/glofas-ows/ows.py`, layer `FloodHazard100y` (also `reportingPoints`, `RapidFloodMapping`, `RapidImpactAssessment`, `MajorRiverBasins`, `AccRainEGE`) | Global / Copernicus licence / © European Union, Copernicus EMS GloFAS | Forecast layers are time-enabled | **VERIFIED** GetMap PNG 5 KB for `FloodHazard100y`; other names from GetCapabilities only |
| `global-gfm-observed-flood-extent` | Global Flood Monitoring, Sentinel-1 observed flood extent / Copernicus EMS GFM | wms | `https://geoserver.gfm.eodc.eu/geoserver/gfm/wms`, layer `observed_flood_extent` (also `observed_water_extent`, `reference_water_mask`, `affected_population`, `affected_landcover`) | Global / Copernicus licence / © European Union, Copernicus EMS GFM | Needs a TIME value to show data | **VERIFIED** GetCapabilities parsed and GetMap returned a PNG, but only 1.3 KB (likely empty without TIME) |
| `global-gibs-modis-flood-2day` | MODIS near-real-time flood, 2-day composite / NASA LANCE via GIBS | xyz | `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Combined_Flood_2-Day/default/default/GoogleMapsCompatible_Level9/{z}/{y}/{x}.png` (also `VIIRS_Combined_Flood_2-Day`, `_1-Day`, `_3-Day` per GetCapabilities) | Global / NASA open data / NASA LANCE, GIBS | `default` date = latest; a date can replace it | **VERIFIED** tile 200 image/png |
| `global-jrc-flood-hazard-rp100` | River flood hazard depth, 100-year / EC JRC CEMS-GloFAS | cog (tiles) | `https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/RP100/ID{n}_{lat}_{lon}_RP100_depth.tif` | Global in 10-degree tiles / CC-BY-4.0 / © European Union | Needs a VRT mosaic like the Copernicus DEM one | **NOT-DIRECTLY-LOADABLE** one tile GDAL open (11999 px, DEFLATE, 256 px blocks, no overviews); a global layer needs a VRT built in `assets/vrt/` |
| `global-ibtracs-active-tracks` | Tropical cyclone tracks, active storms / NOAA NCEI IBTrACS | archive | `https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/shapefile/IBTrACS.ACTIVE.list.v04r01.lines.zip`, target `IBTrACS.ACTIVE.list.v04r01.lines.shp` | Global / public domain / NOAA NCEI IBTrACS | None | **VERIFIED** OGR open: Line String layer |
| `global-ibtracs-last3years-tracks` | Tropical cyclone tracks, last 3 years / NOAA NCEI IBTrACS | archive | Same folder, `IBTrACS.last3years.list.v04r01.lines.zip`, target `IBTrACS.last3years.list.v04r01.lines.shp` (also `.points.zip`, `since1980`, per basin) | Global / public domain / NOAA NCEI | None | **VERIFIED** range + zip listing |
| `na-nhc-tropical-weather` | NHC tropical weather outlook, forecast tracks and cones / NOAA NHC | wms | `https://mapservices.weather.noaa.gov/tropical/services/tropical/NHC_tropical_weather/MapServer/WMSServer`, numeric layer names (REST ids: 1 = two-day current location, 3 = seven-day potential development region, then per-storm slots AT1...) | Atlantic, East and Central Pacific only / public domain / NOAA NHC | 400 layers; numeric names are awkward | **VERIFIED** GetCapabilities parsed; GetMap `layers=1` returned a PNG (758 bytes, probably empty); REST layer 3 `query?f=geojson` opened in OGR |
| `global-gvp-holocene-volcanoes` | Holocene volcanoes / Smithsonian Global Volcanism Program | wfs | `https://webservices.volcano.si.edu/geoserver/GVP-VOTW/ows`, typename `GVP-VOTW:Smithsonian_VOTW_Holocene_Volcanoes` (also `Smithsonian_VOTW_Holocene_Eruptions`, `Smithsonian_VOTW_Pleistocene_Volcanoes`) | Global / Smithsonian terms, citation required (confirm) / Global Volcanism Program, Smithsonian Institution | None | **VERIFIED** OGR WFS open for the Holocene volcanoes typename |
| `global-ncei-tsunami-events` | Historical tsunami events / NOAA NCEI | geojson | `https://gis.ngdc.noaa.gov/arcgis/rest/services/web_mercator/hazards/MapServer/0/query?where=1%3D1&outFields=*&f=geojson` (layer 5 = significant earthquakes, 6 = significant eruptions, 12 = plate boundaries) | Global / public domain / NOAA NCEI | ArcGIS record cap not checked; WMSServer returns 400 | **VERIFIED** HTTP only: 200 application/geo+json with `resultRecordCount=2` |
| `global-tectonic-plates-pb2002` | Tectonic plate boundaries (Bird 2003) / H. Ahlenius | geojson | `https://raw.githubusercontent.com/fraxen/tectonicplates/master/GeoJSON/PB2002_boundaries.json` | Global / ODC PDDL (confirm in repo) / Peter Bird 2003, Hugo Ahlenius | None | **VERIFIED** OGR open: Line String layer |
| `global-gibs-landslide-hazard-2000` | Landslide hazard distribution / NASA SEDAC via GIBS | wms | `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi`, layer `NDH_Landslide_Hazard_Distribution_2000` (flood equivalents: `NDH_Flood_Hazard_Frequency_Distribution_1985-2003`, `NDH_Flood_Mortality_Risks_Distribution_2000`) | Global / CC-BY-4.0 (confirm) / NASA SEDAC, CIESIN | Old (2000) | **VERIFIED** GetMap PNG 6 KB |
| `global-copernicus-gdo-drought` | Global Drought Observatory indicators / Copernicus EMS | wms | `https://drought.emergency.copernicus.eu/api/wms` | Global / Copernicus licence | Unknown | **UNVERIFIED** 502 on three variants of the GetCapabilities request |
| `global-nasa-lhasa-landslide` | LHASA landslide nowcast / NASA GSFC | (ArcGIS) | `https://maps.nccs.nasa.gov/...` | Global / NASA open data | Unknown | **UNVERIFIED** host timed out |
| `global-cems-rapid-mapping` | Copernicus EMS rapid mapping activations / Copernicus EMS | (JSON API) | `https://mapping.emergency.copernicus.eu/activations/api/activations/` | Global, per activation / Copernicus licence | Products are per activation and AOI | **NOT-DIRECTLY-LOADABLE** API answers (1069 activations, WKT centroid field) but is not GeoJSON; products need per-event discovery |
| `global-unosat-products` | UNOSAT damage and flood analyses / UNITAR-UNOSAT | (per-event files) | Via HDX organisation `unosat` | Per event / CC-BY-IGO style (confirm) | Per-event shapefiles and GDBs | **NOT-DIRECTLY-LOADABLE** HDX search answers; no stable layer endpoint |
| `global-flood-database` | Global Flood Database v1 (913 events) / Cloud to Street, DFO | (per-event GeoTIFF) | Bucket `https://storage.googleapis.com/gfd_v1_4/` | Global, 2000-2018 / CC-BY-4.0 (confirm) | One file per event | **NOT-DIRECTLY-LOADABLE** bucket listing answers 200; needs an index |

## 4. Health

Category: needs new category `health` (closest existing fit: `crisis`).

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-map-travel-time-healthcare-motorized` | Motorized travel time to nearest healthcare facility, 2020 / Malaria Atlas Project | wms | `https://data.malariaatlas.org/geoserver/ows`, layer `Accessibility:202001_Global_Motorized_Travel_Time_to_Healthcare` | Global / CC-BY (confirm version) / Malaria Atlas Project; Weiss et al. 2020 | None | **VERIFIED** GetMap PNG 47 KB |
| `global-map-travel-time-healthcare-walking` | Walking-only travel time to healthcare, 2020 / MAP | wms | Same server, layer `Accessibility:202001_Global_Walking_Only_Travel_Time_To_Healthcare` | Global / as above | None | **VERIFIED** layer name in GetCapabilities only |
| `global-map-travel-time-cities-2015` | Travel time to cities, 2015 / MAP | wms | Same server, layer `Accessibility:201501_Global_Travel_Time_to_Cities` (friction: `Accessibility:202001_Global_Motorized_Friction_Surface`) | Global / as above; Weiss et al. 2018 | None | **VERIFIED** layer name in GetCapabilities only |
| `global-map-pf-parasite-rate` | Plasmodium falciparum parasite rate / MAP | wms | Same server, layer `Malaria:202608_Global_Pf_Parasite_Rate` | Malaria-endemic countries / as above | Layer name changes with each yearly release | **VERIFIED** layer name in GetCapabilities only |
| `xx-healthsites-hdx` | Healthsites.io facilities, per country, on HDX / Healthsites.io | geojson | Example HTI: `https://data.humdata.org/dataset/a5b640b2-13aa-4da2-97df-0473dff1aa97/resource/c4f64a86-65bb-44a5-82f8-a5453da3bfab/download/haiti.geojson` | Per country / ODbL-1.0 / © Healthsites.io, OpenStreetMap contributors | URL contains HDX UUIDs, so one lookup per country (`package_show?id={country}-healthsites`) | **VERIFIED** OGR open for Haiti only |
| `global-healthsites-api` | Healthsites.io API v3 / Healthsites.io | (API) | `https://healthsites.io/api/v3/facilities/` | Global / ODbL-1.0 | API key | **NOT-DIRECTLY-LOADABLE** 403 without key |
| `xx-hotosm-health-facilities` | HOT OSM export, health facilities, per country / HOT | archive | `https://s3.dualstack.us-east-1.amazonaws.com/production-raw-data-api/ISO3/{ISO3}/health_facilities/points/hotosm_{iso3}_health_facilities_points_gpkg.zip` | Per country / ODbL-1.0 / © OpenStreetMap contributors, HOT | None | **VERIFIED** HTTP only for HTI (206) |

## 5. Education

Category: needs new category `education` (closest existing fit: `crisis`).

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `xx-hotosm-education-facilities` | HOT OSM export, education facilities, per country / HOT | archive | `.../ISO3/{ISO3}/education_facilities/points/hotosm_{iso3}_education_facilities_points_gpkg.zip` | Per country / ODbL-1.0 / © OpenStreetMap contributors, HOT | None | **VERIFIED** HTTP only for HTI (206) |
| `global-giga-schools` | Giga school locations and connectivity / UNICEF + ITU | (API) | `https://uni-ooi-giga-maps-service.azurewebsites.net/api/v1/schools_location` | About 50 countries / per-country terms | API token | **NOT-DIRECTLY-LOADABLE** 401 |

## 6. WASH

Category: `utilities` (water wells and taps are already there as Overpass recipes).

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-wpdx-water-points` | Water Point Data Exchange (WPdx+) / Global Water Challenge | geojson | `https://data.waterpointdata.org/resource/eqje-vguj.geojson?$limit=1000` | About 50 countries, mostly Africa / CC-BY-4.0 (confirm) / WPdx contributors | Socrata API: default 1000 rows; a useful layer needs a bbox or country filter (`$where=clean_country_name='Haiti'` or `within_box(...)`) | **VERIFIED** OGR open with `$limit=1000`: Point layer. A bbox-templated URL would be the right form |

## 7. Roads, logistics and transport

Category: `transport`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `xx-hotosm-roads` | HOT OSM export, roads, per country / HOT | archive | `https://s3.dualstack.us-east-1.amazonaws.com/production-raw-data-api/ISO3/{ISO3}/roads/lines/hotosm_{iso3}_roads_lines_gpkg.zip`, target `hotosm_{iso3}_roads_lines_gpkg.gpkg` | Per country / ODbL-1.0 / © OpenStreetMap contributors, HOT | None | **VERIFIED** OGR open for HTI through `/vsizip//vsicurl/` (GPKG layer) |
| `xx-hotosm-airports` | HOT OSM export, airports, per country / HOT | archive | `.../ISO3/{ISO3}/airports/points/hotosm_{iso3}_airports_points_gpkg.zip` | Per country / ODbL-1.0 | None | **VERIFIED** HTTP only for HTI (206) |
| `xx-hotosm-sea-ports` | HOT OSM export, sea ports, per country / HOT | archive | `.../ISO3/{ISO3}/sea_ports/points/hotosm_{iso3}_sea_ports_points_gpkg.zip` | Per country / ODbL-1.0 | None | **VERIFIED** HTTP only for HTI (206) |
| `xx-grip4-roads` | GRIP4 global roads inventory, by region / PBL Netherlands | archive | `https://dataportaal.pbl.nl/downloads/GRIP4/GRIP4_Region3_vector_shp.zip`, target `GRIP4_region3.shp` (regions 1 to 7) | Global in 7 regions / open, CC-BY-4.0 (confirm) / Meijer et al. 2018, PBL | 242 MB for region 3; likely slow through vsizip | **VERIFIED** range + zip listing for region 3 only |
| `global-ne-roads` | Natural Earth roads 1:10m / Natural Earth | archive | `https://naciscdn.org/naturalearth/10m/cultural/ne_10m_roads.zip`, target `ne_10m_roads.shp` | Global / public domain / Made with Natural Earth | None | **VERIFIED** HTTP only (206); inner name by Natural Earth convention |
| `global-ne-airports` | Natural Earth airports / Natural Earth | archive | `https://naciscdn.org/naturalearth/10m/cultural/ne_10m_airports.zip`, target `ne_10m_airports.shp` | Global / public domain | None | **VERIFIED** HTTP only (206) |
| `global-ne-ports` | Natural Earth ports / Natural Earth | archive | `https://naciscdn.org/naturalearth/10m/cultural/ne_10m_ports.zip`, target `ne_10m_ports.shp` | Global / public domain | None | **VERIFIED** HTTP only (206) |
| `global-ourairports` | OurAirports airports, runways, frequencies / OurAirports | (CSV) | `https://davidmegginson.github.io/ourairports-data/airports.csv` | Global / public domain | CSV with lat/lon columns | **NOT-DIRECTLY-LOADABLE** file answers 206 (12.7 MB); needs a `csv` type with X/Y field names |
| `global-overture-transportation` | Overture transportation segments / Overture Maps Foundation | (PMTiles) | `https://tiles.overturemaps.org/2026-10-21.0/transportation.pmtiles` | Global / ODbL-1.0 | 134 GB; versioned URL | **NOT-DIRECTLY-LOADABLE** range 206; needs a `pmtiles` type |
| `global-wfp-geonode` | WFP GeoNode logistics layers (ports, airports, roads, warehouses) / WFP | wfs | `https://geonode.wfp.org/geoserver/wfs` | Global / per layer | Unknown | **UNVERIFIED** connection timed out on three attempts (up to 60 s) |
| `global-nga-world-port-index` | World Port Index (Pub 150) / NGA | (CSV) | `https://msi.nga.mil/...` | Global / public domain | Unknown | **UNVERIFIED** timeout, then 503 |

## 8. Displacement

Category: `crisis`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-unhcr-poc-locations` | Locations of persons of concern (camps, settlements, centres) / UNHCR | geojson | `https://gis.unhcr.org/arcgis/rest/services/core_v2/wrl_prp_p_unhcr_PoC/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson` | Global / UNHCR terms (confirm) / © UNHCR | ArcGIS record cap not checked, so the layer may be truncated | **VERIFIED** OGR open: Point layer |
| `global-unhcr-poc-polygons` | Camp and settlement outlines / UNHCR | geojson | `https://gis.unhcr.org/arcgis/rest/services/core_v2/wrl_prp_a_unhcr/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson` | Global / as above | As above | **VERIFIED** HTTP only: 200 application/geo+json. Service list also has `wrl_marker_crossing_p_unhcr` (border crossings), not tested |
| `global-iom-dtm` | Displacement Tracking Matrix API / IOM | (API) | `https://dtmapi.iom.int/v3/...` | About 80 countries / IOM terms | Subscription key | **NOT-DIRECTLY-LOADABLE** 401 |

## 9. Conflict

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-acled` | ACLED conflict events / ACLED | (API) | `https://acleddata.com/api/acled/read` | Global / restrictive terms: account required, no redistribution, tiered access | Account and token | **NOT-DIRECTLY-LOADABLE** 403. Do not ship data; at most link the portal |
| `global-ucdp-ged` | UCDP Georeferenced Event Dataset / Uppsala University | (API) | `https://ucdpapi.pcr.uu.se/api/gedevents/25.1` | Global / CC-BY-4.0 | API token | **NOT-DIRECTLY-LOADABLE** 401 |

## 10. Food security

Category: needs new category `food-security` (closest existing fit: `crisis`).

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-fewsnet-livelihood-zones` | FEWS NET livelihood zones / FEWS NET | archive | `https://shapefiles.fews.net/LHZ/FEWS_NET_LH_World.zip`, target `FEWS_NET_LH_World_Livelihood Zone.shp` (note the space) | FEWS NET countries / public domain, US Government (confirm) / FEWS NET | 62 MB | **VERIFIED** range + zip listing |
| `global-fewsnet-ipc-phase` | FEWS NET acute food insecurity classification / FEWS NET Data Warehouse | geojson | `https://fdw.fews.net/api/ipcphase/?format=geojson&country_code={ISO2}&scenario=CS` | FEWS NET countries / public domain (confirm) | Server rejects GDAL's default Accept header | **NOT-DIRECTLY-LOADABLE** curl gets 200 geo+json, but `ogrinfo` gets HTTP 406; the response is also large and unfiltered by date |
| `global-fewsnet-hfic-archive` | FEWS NET historical food insecurity shapefiles / FEWS NET | archive | `https://shapefiles.fews.net/ALL_HFIC.zip` | FEWS NET countries | 666 MB | **UNVERIFIED** range 206 only; contents not listed |
| `global-ipc-api` | IPC / CH classification API / IPC Global Support Unit | (API) | `https://api.ipcinfo.org/population?format=geojson` | About 40 countries | API key | **NOT-DIRECTLY-LOADABLE** 401 |
| `global-wfp-hungermap` | HungerMap LIVE / WFP | (API) | `https://api.hungermapdata.org/v1/foodsecurity/country` | Global | API key | **NOT-DIRECTLY-LOADABLE** 401 |
| `global-fao-asis` | Agricultural Stress Index / FAO GIEWS | wms | `https://io.apps.fao.org/geoserver/wms/ASIS/...` | Global | Unknown | **UNVERIFIED** 502 and 404 on the URLs tried |
| `global-fao-locust-swarms` | Desert locust swarms / FAO Locust Hub | (ArcGIS) | `https://services5.arcgis.com/sjP4Ugu5s0dZWLjd/arcgis/rest/services/Swarms_Public/FeatureServer/0` | Africa, Middle East, SW Asia | Unknown | **UNVERIFIED** the query returned a 103-byte body (likely an error or renamed service) |

## 11. Land cover and vegetation

Category: `landuse`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-gfw-tree-cover-loss` | Tree cover loss (Hansen/UMD) / Global Forest Watch | xyz | `https://tiles.globalforestwatch.org/umd_tree_cover_loss/latest/dynamic/{z}/{x}/{y}.png` | Global / CC-BY-4.0 / Hansen, UMD, Google, USGS, NASA; GFW | None known | **VERIFIED** tile 206 image/png (6/33/31) |
| `global-gfw-tree-cover-density-2000` | Tree cover density 2000, 30 % threshold / Global Forest Watch | xyz | `https://tiles.globalforestwatch.org/umd_tree_cover_density_2000/latest/tcd_30/{z}/{x}/{y}.png` | Global / CC-BY-4.0 | None known | **VERIFIED** tile 206 image/png |
| `global-esa-worldcover-2021-cog` | ESA WorldCover 2021 v200, native COG tiles / ESA | cog (tiles) | `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif` (3-degree tiles, e.g. `N18W075`) | Global / CC-BY-4.0 / © ESA WorldCover project 2021 | Needs a VRT mosaic | **NOT-DIRECTLY-LOADABLE** one tile GDAL open: true COG (LAYOUT=COG, 6 overviews). A VRT in `assets/vrt/` would make it a robust replacement for the Terrascope WMS entry |
| `global-esri-lulc-10m` | Sentinel-2 10 m land use / land cover time series / Impact Observatory, Microsoft, Esri | (ArcGIS ImageServer) | `https://ic.imagery1.arcgis.com/arcgis/rest/services/Sentinel2_10m_LandCover/ImageServer` | Global, 2017 onward / CC-BY-4.0 / Impact Observatory, Microsoft, Esri | No WMS (400), no tile cache (404); time-enabled | **NOT-DIRECTLY-LOADABLE** `exportImage` returned a PNG; needs an ArcGIS ImageServer type |
| `global-dynamic-world` | Dynamic World near-real-time land cover / Google, WRI | (Earth Engine) | Earth Engine only | Global / CC-BY-4.0 | Earth Engine account | **NOT-DIRECTLY-LOADABLE** no public tile endpoint known; not tested |

## 12. Surface water and hydrography

Category: `hydrography`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-jrc-gsw-occurrence` | Global Surface Water occurrence 1984-2021 / EC JRC, Google | xyz | `https://storage.googleapis.com/global-surface-water/tiles2021/occurrence/{z}/{x}/{y}.png` | Global / Copernicus free and open, attribution / Source: EC JRC/Google | Y-axis orientation (xyz vs tms) not confirmed | **VERIFIED** tile 206 image/png (6/33/31) |
| `global-jrc-gsw-seasonality` | Global Surface Water seasonality / EC JRC | xyz | `.../tiles2021/seasonality/{z}/{x}/{y}.png` | As above | As above | **VERIFIED** tile 206 image/png |
| `global-jrc-gsw-recurrence` | Global Surface Water recurrence / EC JRC | xyz | `.../tiles2021/recurrence/{z}/{x}/{y}.png` | As above | As above | **VERIFIED** tile 206 image/png |
| `global-jrc-gsw-change` | Global Surface Water occurrence change intensity / EC JRC | xyz | `.../tiles2021/change/{z}/{x}/{y}.png` | As above | As above. `extent` returned 404 | **VERIFIED** tile 206 image/png |
| `global-jrc-gsw-occurrence-cog` | GSW occurrence, raw 10-degree GeoTIFFs / EC JRC | cog (tiles) | `https://storage.googleapis.com/global-surface-water/downloads2021/occurrence/occurrence_{lon}_{lat}v1_4_2021.tif` | Global / as above | Needs a VRT | **NOT-DIRECTLY-LOADABLE** one tile GDAL open (40000 px, LZW, 256 px blocks, no overviews) |
| `af-hydrorivers` | HydroRIVERS v1.0, Africa / HydroSHEDS, WWF | archive | `https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_af_shp.zip`, target `HydroRIVERS_v10_af_shp/HydroRIVERS_v10_af.shp` (other regions: `ar as au eu gr na sa si`) | Per continent / HydroSHEDS licence (free, attribution) / HydroSHEDS, Lehner and Grill 2013 | 108 MB; rendering speed not measured | **VERIFIED** OGR open through `/vsizip//vsicurl/`: Line String layer (Africa only) |
| `af-hydrobasins` | HydroBASINS levels 1-12, Africa / HydroSHEDS | archive | `https://data.hydrosheds.org/file/hydrobasins/standard/hybas_af_lev01-12_v1c.zip` | Per continent / as above | 549 MB | **UNVERIFIED** range gave 200 once and 206 once; inner paths not listed |
| `global-hydrolakes` | HydroLAKES polygons / HydroSHEDS | archive | `https://data.hydrosheds.org/file/hydrolakes/HydroLAKES_polys_v10_shp.zip` | Global / CC-BY-4.0 | Large | **UNVERIFIED** the range request returned 200 (full body), so random access is doubtful |
| `global-osm-water-polygons` | OSM ocean and sea polygons, split / OSM, osmdata.openstreetmap.de | archive | `https://osmdata.openstreetmap.de/download/water-polygons-split-4326.zip`, target `water-polygons-split-4326/water_polygons.shp` (also `land-polygons-split-4326.zip`, `coastlines-split-4326.zip`) | Global / ODbL-1.0 / © OpenStreetMap contributors | 908 MB; likely too slow to stream | **VERIFIED** range + zip listing; land and coastline zips HTTP 206 only |
| `global-ne-rivers` | Natural Earth rivers and lake centerlines / Natural Earth | archive | `https://naciscdn.org/naturalearth/10m/physical/ne_10m_rivers_lake_centerlines.zip`, target `ne_10m_rivers_lake_centerlines.shp` | Global / public domain | None | **VERIFIED** HTTP only (206) |
| `xx-hotosm-waterways` | HOT OSM export, waterways, per country / HOT | archive | `.../ISO3/{ISO3}/waterways/lines/hotosm_{iso3}_waterways_lines_gpkg.zip` | Per country / ODbL-1.0 | None | **VERIFIED** HTTP only for HTI (206) |

## 13. Climate and weather

Category: needs new category `weather` (no good existing fit).

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-chirps3-monthly` | CHIRPS v3 monthly rainfall / UCSB Climate Hazards Center | cog | `/vsicurl/https://data.chc.ucsb.edu/products/CHIRPS/v3.0/monthly/global/tifs/chirps-v3.0.{YYYY}.{MM}.tif` (latest seen: `2026.10`) | Global land 60S-60N / CC0 or public domain (confirm) / Climate Hazards Center, UC Santa Barbara | One file per month, so the URL needs a date; server returned 429 under parallel requests | **VERIFIED** GDAL open of `2026.10`: 7200 x 2400, LZW. Not a real COG (strip layout, no overviews) but only 21 MB. The older `CHIRPS-2.0/global_monthly/cogs/` path is 404 |
| `global-gibs-imerg-precipitation` | IMERG precipitation rate / NASA GPM via GIBS | xyz | `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/IMERG_Precipitation_Rate/default/default/GoogleMapsCompatible_Level6/{z}/{y}/{x}.png` (max_zoom 6) | Global / NASA open data / NASA GPM, GIBS | Time-enabled; `default` = latest | **VERIFIED** tile 206 image/png |
| `global-eumetsat-ir108` | Meteosat 0-degree infrared 10.8 imagery / EUMETSAT | wms | `https://view.eumetsat.int/geoserver/wms`, layer `msg_fes:ir108` (Indian Ocean: `msg_iodc:ir108`; lightning: `mtg_fd:li_afa`; rain rate: `msg_fes:h60b`) | Europe, Africa, Indian Ocean / EUMETSAT data policy, attribution / © EUMETSAT | Time-enabled, near real time | **VERIFIED** GetMap PNG 63 KB for `msg_fes:ir108`; other names from GetCapabilities only |

## 14. Satellite imagery and basemaps

Category: `imagery` (ClearMap: `basemap`).

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-eox-s2cloudless-2024` | Sentinel-2 cloudless 2024 / EOX | xyz | `https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg` (years 2017 to 2025 exist as `s2cloudless-{year}_3857`) | Global / CC-BY-NC-SA-4.0 for 2018 onward / Sentinel-2 cloudless by EOX IT Services GmbH (contains modified Copernicus Sentinel data) | Non-commercial | **VERIFIED** tile 200 image/jpeg; layer names read from WMTS GetCapabilities |
| `global-eox-s2cloudless-2016` | Sentinel-2 cloudless 2016 / EOX | xyz | `https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg` | Global / CC-BY-4.0 / as above | None | **VERIFIED** tile 200 image/jpeg |
| `global-eox-terrain-light` | Terrain Light basemap / EOX | xyz | `https://tiles.maps.eox.at/wmts/1.0.0/terrain-light_3857/default/g/{z}/{y}/{x}.jpg` | Global / CC-BY-4.0 (confirm) / EOX, data © OpenStreetMap contributors and others | None | **VERIFIED** tile 200 image/jpeg |
| `global-gibs-viirs-truecolor-daily` | VIIRS Suomi-NPP true colour, daily / NASA GIBS | xyz | `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/default/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg` (max_zoom 9) | Global / NASA open data / NASA EOSDIS GIBS | Daily; replace `default` with `YYYY-MM-DD` for a given day | **VERIFIED** tile 200 image/jpeg; WMS GetMap for the same layer also returned an image |
| `global-gibs-modis-terra-truecolor-daily` | MODIS Terra true colour, daily / NASA GIBS | xyz | `.../best/MODIS_Terra_CorrectedReflectance_TrueColor/default/default/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg` | Global / NASA open data | As above | **VERIFIED** tile 200 image/jpeg |
| `global-gibs-blue-marble` | Blue Marble shaded relief and bathymetry / NASA GIBS | xyz | `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg` (max_zoom 8) | Global / public domain / NASA Earth Observatory | None | **VERIFIED** tile 200 image/jpeg |
| `global-un-clearmap-webtopo` | UN ClearMap WebTopo / UN Geospatial | xyz | `https://geoservices.un.org/arcgis/rest/services/ClearMap_WebTopo/MapServer/tile/{z}/{y}/{x}` | Global, UN boundary depiction / UN terms of use (confirm before adding) / United Nations Geospatial | Terms may restrict non-UN use | **VERIFIED** tile 200 image/png |
| `global-maxar-open-data` | Maxar Open Data Program, post-disaster imagery / Maxar (Vantor) | (STAC) | `https://maxar-opendata.s3.amazonaws.com/events/catalog.json` | Per event / CC-BY-NC-4.0 | STAC catalog of per-event COGs | **NOT-DIRECTLY-LOADABLE** catalog answers 206; needs STAC browsing |
| `global-openaerialmap` | OpenAerialMap / HOT | (API) | `https://api.openaerialmap.org/meta` | Per image / CC-BY-4.0 mostly | Per-image COG or TMS | **NOT-DIRECTLY-LOADABLE** API answers; `tiles.openaerialmap.org` returned 503 and the Kontur mosaic tiler timed out |

## 15. Night lights

Category: `imagery`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-gibs-black-marble-2016` | VIIRS Black Marble 2016 composite / NASA GIBS | xyz | `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/default/2016-01-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png` (max_zoom 8) | Global / public domain / NASA Earth Observatory, GIBS | None | **VERIFIED** tile 200 image/png |
| `global-gibs-city-lights-2012` | VIIRS City Lights 2012 / NASA GIBS | xyz | `.../best/VIIRS_CityLights_2012/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg` | Global / public domain | None | **VERIFIED** tile 200 image/jpeg |
| `global-gibs-dnb-radiance-daily` | VIIRS day/night band at-sensor radiance, nightly / NASA GIBS | xyz | `.../best/VIIRS_SNPP_DayNightBand_At_Sensor_Radiance/default/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png` | Global / NASA open data | Daily; useful for outage detection when compared across dates | **VERIFIED** tile 200 image/png |

## 16. Protected areas

Category: `landuse`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-wdpa-protected-areas` | World Database on Protected Areas / UNEP-WCMC, IUCN | xyz | `https://data-gis.unep-wcmc.org/server/rest/services/ProtectedSites/The_World_Database_of_Protected_Areas/MapServer/tile/{z}/{y}/{x}` | Global / WDPA terms: no commercial use without permission, no redistribution / UNEP-WCMC and IUCN (2026), Protected Planet | Raster tiles only; WMSServer returns 400 | **VERIFIED** tiles 200 image/png at z3 and z6. A `query?f=geojson` on layer 1 also opened in OGR (record cap applies) |

## 17. Power and telecoms

Category: `utilities`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-openinframap` | Open Infrastructure Map (power, telecoms, pipelines) / OpenInfraMap | vectortile | `https://openinframap.org/tiles/{z}/{x}/{y}.pbf` (TileJSON: `https://openinframap.org/map.json`) | Global / ODbL-1.0 / © OpenStreetMap contributors, OpenInfraMap | No published usage policy seen; ask before bundling | **VERIFIED** tile 206 application/vnd.mapbox-vector-tile at two locations |
| `global-submarine-cables` | Submarine cables / TeleGeography | geojson | `https://www.submarinecablemap.com/api/v3/cable/cable-geo.json` | Global / CC-BY-SA-4.0 (confirm) / TeleGeography | None | **VERIFIED** OGR open: Multi Line String layer |
| `global-submarine-cable-landing-points` | Cable landing points / TeleGeography | geojson | `https://www.submarinecablemap.com/api/v3/landing-point/landing-point-geo.json` | As above | None | **VERIFIED** HTTP only (206 JSON) |
| `global-ookla-mobile-performance` | Mobile network performance tiles / Ookla Open Data | archive | `https://ookla-open-data.s3.amazonaws.com/shapefiles/performance/type=mobile/year=2024/quarter=1/2024-01-01_performance_mobile_tiles.zip`, target `gps_mobile_tiles.shp` | Global / CC-BY-NC-SA-4.0 / Speedtest by Ookla Global Mobile Network Performance Maps | 140 MB; one URL per quarter; non-commercial | **VERIFIED** range + zip listing for 2024 Q1 only (newer quarters not tested) |
| `global-gridfinder` | Gridfinder predicted electricity grid / Arderne et al., World Bank | fgb (gpkg over vsicurl, as in `fieldmaps-cod.yaml`) | `/vsicurl/https://zenodo.org/records/3628142/files/grid.gpkg` | Global / CC-BY-4.0 / Arderne et al. 2020 | 725 MB; Zenodo rate limits | **VERIFIED** HTTP only (206); not opened with OGR |
| `global-wri-power-plants` | Global Power Plant Database / WRI | (CSV) | `https://raw.githubusercontent.com/wri/global-power-plant-database/master/output_database/global_power_plant_database.csv` | Global / CC-BY-4.0 | CSV | **NOT-DIRECTLY-LOADABLE** file answers 206; needs a `csv` type |
| `global-opencellid` | OpenCelliD cell towers / Unwired Labs | (download) | `https://opencellid.org/ocid/downloads?token=...` | Global / CC-BY-SA-4.0 | Token | **NOT-DIRECTLY-LOADABLE** token required; CSV.gz |

## 18. Boundaries

Category: `admin`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-geoboundaries-cgaz-adm1` | geoBoundaries CGAZ ADM1 / William and Mary geoLab | fgb (gpkg over vsicurl) | `/vsicurl/https://github.com/wmgeolab/geoBoundaries/raw/main/releaseData/CGAZ/geoBoundariesCGAZ_ADM1.gpkg` | Global / CC-BY-4.0 / geoBoundaries, Runfola et al. 2020 | 144 MB via GitHub LFS redirect | **VERIFIED** OGR open: layer `globalADM1` |
| `global-geoboundaries-cgaz-adm2` | geoBoundaries CGAZ ADM2 | fgb (gpkg) | `.../CGAZ/geoBoundariesCGAZ_ADM2.gpkg` | As above | 241 MB | **VERIFIED** HTTP only (206). The ADM0 GeoJSON is 401 MB, so avoid it |
| `global-ne-admin0-countries` | Natural Earth countries 1:10m / Natural Earth | archive | `https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip`, target `ne_10m_admin_0_countries.shp` | Global / public domain / Made with Natural Earth | None | **VERIFIED** OGR open: Polygon layer |
| `global-ne-disputed-areas` | Natural Earth disputed areas | archive | `.../cultural/ne_10m_admin_0_disputed_areas.zip` | Global / public domain | None | **VERIFIED** HTTP only (206) |
| `global-ne-populated-places` | Natural Earth populated places | archive | `.../cultural/ne_10m_populated_places.zip` | Global / public domain | None | **VERIFIED** HTTP only (206) |
| `global-ne-urban-areas` | Natural Earth urban areas | archive | `.../cultural/ne_10m_urban_areas.zip` | Global / public domain | None | **VERIFIED** HTTP only (206) |
| `global-marineregions-eez` | Exclusive Economic Zones v12 / Flanders Marine Institute | wfs | `https://geo.vliz.be/geoserver/MarineRegions/wfs`, typename `MarineRegions:eez` (also `eez_12nm`, `eez_24nm`, `eez_boundaries`, `high_seas`, `iho`) | Global / CC-BY-4.0 / Flanders Marine Institute (2023), Maritime Boundaries Geodatabase | None | **VERIFIED** OGR WFS open: Multi Surface layer |
| `xx-gadm-41` | GADM 4.1 per country, levels 0-4 / GADM | fgb (gpkg) | `/vsicurl/https://geodata.ucdavis.edu/gadm/gadm4.1/gpkg/gadm41_{ISO3}.gpkg` (layers `ADM_ADM_0` to `ADM_ADM_4`) | Per country / free for non-commercial use, redistribution not allowed / © GADM | Non-commercial; host timed out twice before answering | **VERIFIED** OGR open for HTI, 5 layers. Needs a layer-name field in the schema |
| `xx-geoboundaries-api` | geoBoundaries per country and level / geoLab | (API) | `https://www.geoboundaries.org/api/current/gbOpen/{ISO3}/ADM{n}/` returns JSON with a `gjDownloadURL` | Per country / CC-BY-4.0 or per-country | Two-step resolution | **NOT-DIRECTLY-LOADABLE** API answers; the data URL is pinned to a commit hash and must be resolved first |
| `global-ocha-cod-ab-services` | OCHA COD global admin 0-3 feature services / UN OCHA | (ArcGIS FeatureServer) | `https://gis.unocha.org/server/rest/services/COD/GBL_COD_Admin1/FeatureServer` (also `Admin0`, `GLB_COD_Admin2`, `Admin3`) | COD countries / CC-BY-IGO (confirm) | ArcGIS | **UNVERIFIED** only the service listing was read. The older ITOS host `codgis.itos.uga.edu` timed out |

## 19. Bathymetry and terrain

Category: `elevation`.

| id | Name / provider | Type | Endpoint | Coverage / license / attribution | Constraints | Status and evidence |
|---|---|---|---|---|---|---|
| `global-gebco-latest` | GEBCO latest bathymetry, shaded / GEBCO | wms | `https://wms.gebco.net/mapserv?`, layer `GEBCO_LATEST` (also `GEBCO_LATEST_2`, `GEBCO_LATEST_SUB_ICE_TOPO`, `GEBCO_LATEST_TID`) | Global / public domain / GEBCO Compilation Group | None | **VERIFIED** GetCapabilities parsed; GetMap returned a PNG (531 bytes over land, so ocean rendering not visually confirmed) |
| `global-terrarium-terrain-tiles` | Terrain tiles, Terrarium RGB encoding / Mapzen, AWS Open Data | xyz | `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png` (max_zoom 15) | Global / mixed open sources, attribution list required / Mapzen, SRTM, GMTED, ETOPO1 and others | Looks like noise unless QGIS decodes it (`interpretation=terrariumterrainrgb`) | **VERIFIED** tile 206 image/png. Needs an `interpretation` field to be useful |
| `global-mapterhorn-terrain` | Mapterhorn terrain tiles / Mapterhorn | xyz | `https://tiles.mapterhorn.com/{z}/{x}/{y}.webp` | Global / per-source open licences (confirm) | Terrarium-encoded WebP, 512 px | **VERIFIED** HTTP only: 200 image/webp. QGIS decoding not tested |

## 20. Overpass recipes worth adding

These are tag filters in the syntax documented in `docs/adding-a-source.md`. None was executed against Overpass, so they are not counted in the status totals. Existing recipes already cover hospitals, pharmacies, schools, water points, shelters, helipads, fuel, power and telecom masts.

| id | Category | Filter |
|---|---|---|
| `global-overpass-refugee-sites` | crisis | `amenity=refugee_site\|social_facility=refugee_site` |
| `global-overpass-airfields` | transport | `aeroway=aerodrome\|aeroway=runway\|aeroway=airstrip` |
| `global-overpass-bridges-fords` | transport | `bridge=yes\|ford=yes\|man_made=bridge` |
| `global-overpass-ferry-ports` | transport | `amenity=ferry_terminal\|landuse=harbour\|route=ferry` |
| `global-overpass-border-crossings` | transport | `barrier=border_control` |
| `global-overpass-main-roads` | transport | `highway=motorway\|highway=trunk\|highway=primary\|highway=secondary` |
| `global-overpass-places` | admin | `place=city\|place=town\|place=village\|place=hamlet` |
| `global-overpass-markets-banks` | utilities | `amenity=marketplace\|amenity=bank\|amenity=atm\|amenity=money_transfer` |
| `global-overpass-places-of-worship-community` | crisis | `amenity=place_of_worship\|amenity=community_centre\|amenity=townhall` |
| `global-overpass-dams-levees` | hydrography | `waterway=dam\|man_made=dyke\|man_made=embankment\|waterway=weir` |
| `global-overpass-sanitation` | utilities | `amenity=toilets\|amenity=shower\|amenity=waste_disposal` |
| `global-overpass-ngo-offices` | crisis | `office=ngo\|office=government\|office=diplomatic` |

---

## 21. Valuable data that does not fit the current model

| Gap | Examples (from the tables above) | Capability needed |
|---|---|---|
| Per-country file families | HOT exports (8 themes checked for HTI), GADM, WorldPop, geoBoundaries, healthsites-on-HDX, GRID3 per-country services, HydroSHEDS per continent, GRIP per region | A URL template with `{iso3}` / `{ISO3}` / `{region}` placeholders expanded at build time against a country list, generating one entry per country with `country_iso` set. This is the single highest-yield change: one HOT template would generate roughly 200 countries x 8 themes |
| Time-enabled layers | All GIBS daily layers, GFM flood extent, GloFAS forecasts, GWIS fires, EUMETSAT, CHIRPS monthly, Ookla quarterly, Esri LULC yearly | A `time` block (default value, step, range) and a `{date}` placeholder; for WMS, pass TIME and enable the QGIS temporal controller |
| ArcGIS REST services | WorldPop ImageServer, Esri LULC ImageServer, GRID3, UNHCR, OCHA COD, NOAA NCEI hazards, WDPA vectors, NHC | New types `arcgisfeatureserver` and `arcgismapserver` (QGIS has native providers for both). The `query?f=geojson` workaround used above is capped by `maxRecordCount` (1000 on GRID3) |
| PMTiles | Overture buildings, transportation and places; VIDA Google + Microsoft buildings | A `pmtiles` type (QGIS vector tile provider and GDAL both read it), plus a "latest release" resolver for Overture's versioned URLs |
| CSV with coordinates | OurAirports, WRI power plants, FIRMS CSV, Meta Relative Wealth Index, World Port Index | A `csv` type with `x_field`, `y_field` (OGR open options `X_POSSIBLE_NAMES` / `Y_POSSIBLE_NAMES`) |
| Tiled COG collections without a mosaic | ESA WorldCover (3-degree COGs), JRC flood hazard (10-degree), GSW rasters, GHSL tiles | No schema change: generate VRTs into `assets/vrt/` as already done for Copernicus DEM. WorldCover is the best first target because its tiles are true COGs with overviews |
| Rasters inside zips | GHS-POP global | Either let `archive` + `archive_target` point to a raster, or document `/vsizip//vsicurl/` as a valid `cog` URL |
| Multi-layer containers | GADM gpkg (5 layers), geoBoundaries gpkg | A `layer` field (the FieldMaps COD entries already face this) |
| Layer-name and style for WMS/WFS | Every WMS above | The existing entry packs `LAYERS=` into the URL query. A dedicated `layers` / `typename` / `styles` field would be clearer and would allow several entries per server |
| Encoded elevation tiles | Terrarium, Mapterhorn | An `interpretation` field (`terrariumterrainrgb`, `maptilerterrain`) |
| STAC and per-event catalogs | Maxar Open Data, Copernicus EMS activations, UNOSAT, Global Flood Database, OpenAerialMap, Sentinel-2 archives | A `stac` type that opens a browser, or a separate "event imagery" feature. Not a static-catalog fit |
| Key-gated or registration APIs | ACLED, UCDP, IOM DTM, IPC, WFP HungerMap, healthsites API, OpenCelliD, Giga, FIRMS WMS | An `auth` block (`key_param`, `signup_url`) with the user's key stored in QGIS auth settings. ACLED additionally forbids redistribution, so only a link-out is appropriate |
| Request headers | FEWS NET Data Warehouse (406 on GDAL's default Accept header) | A `headers` field mapped to `GDAL_HTTP_HEADERS` |
| Bbox-filtered APIs | WPdx (Socrata), ArcGIS `query`, EMSC | A `{bbox}` placeholder for `geojson` URLs, as already exists for Overpass |
| Access metadata | EOX 2018+ (NC), Ookla (NC), GADM (NC, no redistribution), WDPA (restricted), ACLED | A structured `access` field (`open`, `non-commercial`, `registration`, `key`) so the plugin can show a warning |
| Categories | Population, health, education, food security, weather/climate, hazards vs. live events | The current 10 categories push about a third of these candidates into `crisis`. Suggested additions: `population`, `health`, `hazard`, `weather`, and possibly `food-security` |
