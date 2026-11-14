"""Unit test suite for Geostrata PyQGIS core modules (stdlib only)."""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import string
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

PLUGIN_PARENT_DIR = Path(__file__).resolve().parent.parent
REGISTRY_DIST_DIR = (
    PLUGIN_PARENT_DIR.parent / "registry" / "dist"
)
sys.path.insert(0, str(PLUGIN_PARENT_DIR))

from geostrata import i18n  # noqa: E402
from geostrata.core.db_client import (  # noqa: E402
    CatalogDatabaseClient,
    CatalogSourceRecord,
    compute_locality,
    compute_rank_score,
    compute_text_relevance,
    global_group_leads,
    match_quality,
    parse_search_query,
)
from geostrata.core.entry_details import (  # noqa: E402
    build_details_html,
    coverage_label,
    describe_entry,
    link_status_label,
)
from geostrata.core.overpass_task import (  # noqa: E402
    build_overpass_json_ql,
    clamp_overpass_bbox,
    convert_overpass_json_to_geojson,
    normalize_overpass_endpoint,
    resolve_priority_overpass_urls,
)
from geostrata.core.uri_builder import (  # noqa: E402
    build_overpass_ql,
    build_provider_uri,
)


PLUGIN_PACKAGE_DIR = PLUGIN_PARENT_DIR / "geostrata"
WORLD_BBOX = (-180.0, -90.0, 180.0, 90.0)
DRC_BBOX = (12.2, -13.5, 31.3, 5.4)
GOMA_BBOX = (29.1, -1.75, 29.35, -1.6)


def _bbox_wkt(bbox: Tuple[float, float, float, float]) -> str:
    min_x, min_y, max_x, max_y = bbox
    return (
        f"POLYGON(({min_x} {min_y}, {max_x} {min_y}, {max_x} {max_y}, "
        f"{min_x} {max_y}, {min_x} {min_y}))"
    )


def _normalize_like_build_ts(text: str) -> str:
    """Python port of stripDiacritics() in packages/registry/src/build.ts."""
    import unicodedata

    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if not "\u0300" <= ch <= "\u036f")
    return re.sub(r"[^a-z0-9\u0600-\u06ff]+", " ", stripped.lower()).strip()


def build_temp_catalog(
    path: Path,
    entries: List[Dict[str, Any]],
    with_optional_columns: bool = True,
) -> Path:
    """Create a small catalog.sqlite with the same tables as packages/registry/src/build.ts.

    ``with_optional_columns=False`` reproduces an older / third-party catalog without
    `priority`, `data_date`, `health_checked_at` and `i18n_json`.
    """
    optional_sql = (
        """,
            i18n_json TEXT NOT NULL DEFAULT '{}',
            priority INTEGER NOT NULL DEFAULT 0,
            data_date TEXT,
            health_checked_at TEXT"""
        if with_optional_columns
        else ""
    )
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            f"""
            CREATE TABLE sources (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT,
                type TEXT NOT NULL,
                category TEXT NOT NULL,
                url TEXT NOT NULL,
                crs TEXT NOT NULL DEFAULT 'EPSG:4326',
                country_iso TEXT,
                min_x REAL NOT NULL,
                min_y REAL NOT NULL,
                max_x REAL NOT NULL,
                max_y REAL NOT NULL,
                area_deg2 REAL NOT NULL,
                geom_wkt TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                search_text TEXT NOT NULL DEFAULT '',
                license TEXT NOT NULL,
                attribution TEXT NOT NULL,
                style_qml TEXT,
                min_zoom INTEGER,
                max_zoom INTEGER,
                archive_target TEXT,
                health_status TEXT NOT NULL DEFAULT 'ok'{optional_sql}
            );
            CREATE VIRTUAL TABLE sources_rtree USING rtree(id, minX, maxX, minY, maxY);
            """
        )
        # Some Python builds (e.g. the one bundled with QGIS 4 on macOS) lack FTS5: the
        # client then uses the `search_text LIKE` fallback, which these tests also cover.
        has_fts5 = True
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE sources_fts USING fts5("
                "name, description, category, enriched_tags, country_names, "
                "tokenize='unicode61 remove_diacritics 2')"
            )
        except sqlite3.OperationalError:
            has_fts5 = False
        for entry in entries:
            bbox = entry.get("bbox", WORLD_BBOX)
            min_x, min_y, max_x, max_y = bbox
            tags = list(entry.get("tags", []))
            synonyms = list(entry.get("synonyms", []))
            entry_i18n = entry.get("i18n", {})
            i18n_parts: List[str] = []
            for loc in entry_i18n.values():
                i18n_parts.extend(v for v in (loc.get("name"), loc.get("description")) if v)
            description = entry.get("description", "")
            category = entry.get("category", "admin")
            source_type = entry.get("type", "xyz")
            enriched = " ".join(tags + synonyms)
            countries = entry.get("country_names", "")
            search_text = " " + _normalize_like_build_ts(
                " ".join(
                    [entry["id"], entry["name"], description, *i18n_parts,
                     category, source_type, enriched, countries]
                )
            ) + " "
            columns: Dict[str, Any] = {
                "id": entry["id"],
                "name": entry["name"],
                "description": description,
                "type": source_type,
                "category": category,
                "url": entry.get("url", "https://example.org/{z}/{x}/{y}.png"),
                "crs": "EPSG:4326",
                "country_iso": entry.get("country_iso"),
                "min_x": min_x,
                "min_y": min_y,
                "max_x": max_x,
                "max_y": max_y,
                "area_deg2": (max_x - min_x) * (max_y - min_y),
                "geom_wkt": entry.get("geom_wkt", _bbox_wkt(bbox)),
                "tags_json": json.dumps(tags),
                "search_text": search_text,
                "license": "ODbL",
                "attribution": entry.get("attribution", "© Test"),
                "health_status": entry.get("health_status", "ok"),
            }
            if with_optional_columns:
                columns["i18n_json"] = json.dumps(entry_i18n)
                columns["priority"] = entry.get("priority", 0)
                columns["data_date"] = entry.get("data_date")
                columns["health_checked_at"] = entry.get("health_checked_at")
            placeholders = ", ".join("?" for _ in columns)
            cursor = conn.execute(
                f"INSERT INTO sources ({', '.join(columns)}) VALUES ({placeholders})",
                list(columns.values()),
            )
            row_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO sources_rtree (id, minX, maxX, minY, maxY) VALUES (?, ?, ?, ?, ?)",
                (row_id, min_x, max_x, min_y, max_y),
            )
            if has_fts5:
                conn.execute(
                    "INSERT INTO sources_fts (rowid, name, description, category, enriched_tags, country_names) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (row_id, entry["name"], description, category,
                     " ".join([enriched, *i18n_parts]).strip(), countries),
                )
        conn.commit()
    finally:
        conn.close()
    return path


# Reproduces the real catalog around Goma: `dem` prefix-matches "Democratic".
DEM_CASE_ENTRIES: List[Dict[str, Any]] = [
    {
        "id": "cd-geoboundaries-adm1",
        "name": "Administrative Boundaries ADM1 (Democratic Republic of the Congo)",
        "description": "Administrative boundary polygons for Democratic Republic of the Congo.",
        "category": "admin",
        "type": "fgb",
        "country_iso": "CD",
        "bbox": DRC_BBOX,
        "tags": ["adm1", "administrative boundaries", "democratic republic of the congo"],
        "country_names": "CD République démocratique du Congo RDC DRC Congo Kinshasa",
    },
    {
        "id": "cd-roads-with-dem-in-description",
        "name": "Road Network (Democratic Republic of the Congo)",
        "description": "Road centerlines; slopes were computed from the SRTM DEM.",
        "category": "transport",
        "type": "fgb",
        "country_iso": "CD",
        "bbox": DRC_BBOX,
        "tags": ["roads"],
    },
    {
        "id": "global-esri-world-hillshade",
        "name": "Esri World Hillshade (Global Shaded Relief)",
        "description": "Multi-directional global terrain hillshade.",
        "category": "elevation",
        "bbox": (-180.0, -85.05, 180.0, 85.05),
        "tags": ["esri", "hillshade", "relief"],
        "synonyms": ["altimetrie", "dem", "dsm", "dtm"],
    },
    {
        "id": "global-copernicus-dem-glo30",
        "name": "Copernicus DEM GLO-30 (30m)",
        "description": "Global 30-meter Digital Surface Model.",
        "category": "elevation",
        "type": "cog",
        "tags": ["copernicus", "dem", "glo-30", "elevation"],
        "data_date": "2021",
        "health_checked_at": "2026-11-10T04:00:00Z",
    },
]


def _dummy_record(source_type: str, url: str, **overrides: object) -> CatalogSourceRecord:
    base = {
        "id": f"test-{source_type}",
        "name": f"Test {source_type}",
        "description": "Test description",
        "type": source_type,
        "category": "hydrography",
        "url": url,
        "crs": "EPSG:4326",
        "country_iso": None,
        "min_x": -180.0,
        "min_y": -90.0,
        "max_x": 180.0,
        "max_y": 90.0,
        "area_deg2": 64800.0,
        "geom_wkt": "POLYGON((-180 -90, 180 -90, 180 90, -180 90, -180 -90))",
        "tags": ["test"],
        "license": "ODbL",
        "attribution": "© Test",
        "style_qml": None,
        "min_zoom": 0,
        "max_zoom": 18,
        "archive_target": None,
        "health_status": "ok",
    }
    base.update(overrides)
    return CatalogSourceRecord(**base)  # type: ignore[arg-type]


class TestGeostrataCore(unittest.TestCase):
    """Tests for query parsing, SQLite R-Tree/FTS5 search, and URI builder."""

    def setUp(self) -> None:
        # Tests must not depend on the developer's LANG / QGIS locale.
        i18n.set_language_override("en")

    def tearDown(self) -> None:
        i18n.set_language_override(None)

    def test_parse_search_query(self) -> None:
        fts_expr, is_global = parse_search_query("eau potable !global")
        self.assertTrue(is_global)
        self.assertEqual(fts_expr, '"eau"* AND "potable"*')

    def test_sqlite_lyon_water_ranking(self) -> None:
        sqlite_path = REGISTRY_DIST_DIR / "catalog.sqlite"
        self.assertTrue(sqlite_path.is_file(), f"Missing {sqlite_path}")

        client = CatalogDatabaseClient([sqlite_path])
        lyon_bbox = (4.83, 45.76, 4.84, 45.77)
        results = client.search_sources("eau", canvas_bbox_4326=lyon_bbox)

        self.assertGreaterEqual(len(results), 4)
        self.assertEqual(results[0].id, "fr-grandlyon-bornes-fontaines")
        self.assertIn("Drinking Water Fountains", results[0].localized_name("en"))
        self.assertIn("Bornes fontaines", results[0].localized_name("fr"))
        result_ids = [r.id for r in results]
        self.assertIn("fr-ign-bdtopo-cours-eau", result_ids)
        self.assertIn("global-overpass-drinking-water", result_ids)
        # Equally relevant layers stay ordered Local -> National -> Global
        self.assertLess(
            result_ids.index("fr-grandlyon-bornes-fontaines"),
            result_ids.index("fr-ign-bdtopo-cours-eau"),
        )
        self.assertLess(
            result_ids.index("fr-ign-bdtopo-cours-eau"),
            result_ids.index("global-overpass-drinking-water"),
        )

    def test_global_modifier_bypasses_bbox(self) -> None:
        sqlite_path = REGISTRY_DIST_DIR / "catalog.sqlite"
        client = CatalogDatabaseClient([sqlite_path])
        tokyo_bbox = (139.65, 35.65, 139.80, 35.75)

        without_global = client.search_sources("lyon", canvas_bbox_4326=tokyo_bbox)
        with_global = client.search_sources(
            "lyon !global", canvas_bbox_4326=tokyo_bbox
        )
        self.assertEqual(len(without_global), 0)
        self.assertGreaterEqual(len(with_global), 4)
        self.assertIn(
            "fr-grandlyon-bornes-fontaines",
            [r.id for r in with_global],
        )

    def test_uri_builder_all_12_types(self) -> None:
        bbox = (4.80, 45.70, 4.90, 45.80)

        xyz_spec = build_provider_uri(
            _dummy_record("xyz", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
            bbox,
        )
        self.assertEqual(xyz_spec.provider, "wms")
        self.assertEqual(xyz_spec.layer_kind, "raster")
        self.assertIn("type=xyz", xyz_spec.uri)

        tms_spec = build_provider_uri(
            _dummy_record("tms", "https://example.org/{z}/{x}/{-y}.png"),
            bbox,
        )
        self.assertEqual(tms_spec.provider, "wms")

        wms_spec = build_provider_uri(
            _dummy_record(
                "wms",
                "https://example.org/wms?SERVICE=WMS&LAYERS=ortho&FORMAT=image/jpeg",
            ),
            bbox,
        )
        self.assertEqual(wms_spec.provider, "wms")
        self.assertIn("layers=ortho", wms_spec.uri)

        wmts_spec = build_provider_uri(
            _dummy_record(
                "wmts",
                "https://data.geopf.fr/wmts?SERVICE=WMTS&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&TILEMATRIXSET=PM&FORMAT=image/jpeg",
            ),
            bbox,
        )
        self.assertEqual(wmts_spec.provider, "wms")
        self.assertIn("tileMatrixSet=PM", wmts_spec.uri)
        self.assertIn(
            "url=https://data.geopf.fr/wmts?SERVICE%3DWMTS%26REQUEST%3DGetCapabilities",
            wmts_spec.uri,
        )

        rest_wmts_spec = build_provider_uri(
            _dummy_record(
                "wmts",
                "https://example.org/wmts/1.0.0/WMTSCapabilities.xml?LAYER=topo&TILEMATRIXSET=3857",
            ),
            bbox,
        )
        self.assertTrue(
            rest_wmts_spec.uri.endswith(
                "url=https://example.org/wmts/1.0.0/WMTSCapabilities.xml"
            )
        )

        wfs_spec = build_provider_uri(
            _dummy_record(
                "wfs",
                "https://data.grandlyon.com/geoserver/ows?SERVICE=WFS&VERSION=2.0.0&TYPENAMES=ns:layer",
                crs="EPSG:2154",
            ),
            bbox,
        )
        self.assertEqual(wfs_spec.provider, "WFS")
        self.assertIn("restrictToRequestBBOX='1'", wfs_spec.uri)
        self.assertIn("typename='ns:layer'", wfs_spec.uri)

        vt_spec = build_provider_uri(
            _dummy_record("vectortile", "https://example.org/tiles/{z}/{x}/{y}.pbf"),
            bbox,
        )
        self.assertEqual(vt_spec.provider, "vectortile")
        self.assertEqual(vt_spec.layer_kind, "vectortile")

        cog_spec = build_provider_uri(
            _dummy_record("cog", "https://example.org/dem.tif"),
            bbox,
        )
        self.assertEqual(cog_spec.provider, "gdal")
        self.assertTrue(cog_spec.uri.startswith("/vsicurl/"))

        vrt_spec = build_provider_uri(
            _dummy_record("vrt", "/vsicurl/https://example.org/dem.vrt"),
            bbox,
        )
        self.assertEqual(vrt_spec.provider, "gdal")
        self.assertTrue(vrt_spec.uri.startswith("/vsicurl/"))

        fgb_spec = build_provider_uri(
            _dummy_record("fgb", "https://example.org/adm0.fgb"),
            bbox,
        )
        self.assertEqual(fgb_spec.provider, "ogr")
        self.assertTrue(fgb_spec.uri.startswith("/vsicurl/"))

        geojson_spec = build_provider_uri(
            _dummy_record("geojson", "https://example.org/data.geojson"),
            bbox,
        )
        self.assertEqual(geojson_spec.provider, "ogr")

        archive_spec = build_provider_uri(
            _dummy_record(
                "archive",
                "https://example.org/bundle.zip",
                archive_target="data/roads.gpkg",
            ),
            bbox,
        )
        self.assertEqual(archive_spec.provider, "ogr")
        self.assertEqual(
            archive_spec.uri,
            "/vsizip//vsicurl/https://example.org/bundle.zip/data/roads.gpkg",
        )

        overpass_spec = build_provider_uri(
            _dummy_record(
                "overpass",
                "amenity=drinking_water|natural=spring",
            ),
            bbox,
        )
        self.assertEqual(overpass_spec.provider, "ogr")
        self.assertIn("overpass-api.de", overpass_spec.uri)

        ql = build_overpass_ql("amenity=hospital", (4.8, 45.7, 4.9, 45.8))
        self.assertIn("45.700000,4.800000,45.800000,4.900000", ql)

    def test_overpass_json_and_geojson_conversion(self) -> None:
        clamped, was_clamped = clamp_overpass_bbox((-10.0, 40.0, 15.0, 52.0))
        self.assertTrue(was_clamped)
        self.assertLess((clamped[2] - clamped[0]) * (clamped[3] - clamped[1]), 16.0)

        json_ql = build_overpass_json_ql(
            "amenity=hospital|amenity=clinic",
            (4.8, 45.7, 4.9, 45.8),
        )
        self.assertIn("[out:json]", json_ql)
        self.assertIn("out center;", json_ql)

        fake_overpass_response = {
            "elements": [
                {
                    "type": "node",
                    "id": 101,
                    "lat": 45.76,
                    "lon": 4.83,
                    "tags": {"amenity": "hospital", "name": "Hôpital Édouard Herriot"},
                },
                {
                    "type": "way",
                    "id": 202,
                    "center": {"lat": 45.75, "lon": 4.85},
                    "tags": {"amenity": "clinic", "name": "Clinique du Parc"},
                },
            ]
        }
        geojson = convert_overpass_json_to_geojson(fake_overpass_response)
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertEqual(len(geojson["features"]), 2)
        self.assertEqual(
            geojson["features"][0]["properties"]["name"],
            "Hôpital Édouard Herriot",
        )
        self.assertEqual(
            geojson["features"][1]["geometry"]["coordinates"],
            [4.85, 45.75],
        )

        # Verify priority-ordered Overpass servers from OSM Wiki
        self.assertEqual(
            normalize_overpass_endpoint("https://overpass.atownsend.org.uk/api/"),
            "https://overpass.atownsend.org.uk/api/interpreter",
        )
        lyon_urls = resolve_priority_overpass_urls((4.80, 45.70, 4.90, 45.80))
        self.assertEqual(
            lyon_urls[0],
            "https://ovp.dresel.systems/api/interpreter",
        )
        bern_urls = resolve_priority_overpass_urls((7.40, 46.90, 7.50, 47.00))
        self.assertIn("https://overpass.osm.ch/api/interpreter", bern_urls)
        tokyo_urls = resolve_priority_overpass_urls((139.65, 35.65, 139.80, 35.75))
        self.assertNotIn("https://ovp.dresel.systems/api/interpreter", tokyo_urls)
        self.assertIn("https://overpass.private.coffee/api/interpreter", tokyo_urls)


class TestSearchRanking(unittest.TestCase):
    """Ranking = text relevance + locality + curated priority, on small temp catalogs."""

    def setUp(self) -> None:
        i18n.set_language_override("en")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        i18n.set_language_override(None)
        self._tmp.cleanup()

    def _client(
        self,
        entries: List[Dict[str, Any]],
        name: str = "catalog.sqlite",
        **kwargs: Any,
    ) -> CatalogDatabaseClient:
        return CatalogDatabaseClient(
            [build_temp_catalog(self.tmp_dir / name, entries, **kwargs)]
        )

    def test_match_quality_and_text_relevance(self) -> None:
        self.assertEqual(match_quality("dem", ["copernicus", "dem"]), 1.0)
        prefix = match_quality("dem", ["democratic"])
        self.assertGreater(prefix, 0.0)
        self.assertLess(prefix, 0.5)
        # The closer the query is to the whole word, the better the prefix match.
        self.assertGreater(match_quality("hospital", ["hospitals"]), prefix)
        self.assertEqual(match_quality("dem", ["academy"]), 0.0)

        fields_name = [(1.0, ["Copernicus DEM"]), (0.5, ["a description"])]
        fields_desc = [(1.0, ["Something else"]), (0.5, ["built from a DEM"])]
        fields_prefix = [(1.0, ["Democratic Republic"]), (0.5, [""])]
        exact_name = compute_text_relevance(["dem"], fields_name)
        exact_desc = compute_text_relevance(["dem"], fields_desc)
        prefix_name = compute_text_relevance(["dem"], fields_prefix)
        self.assertEqual(exact_name, 1.0)
        self.assertGreater(exact_name, exact_desc)  # name beats description
        self.assertGreater(exact_name, prefix_name)  # exact beats prefix
        self.assertEqual(compute_text_relevance([], fields_name), 0.0)

    def test_locality_and_priority_scores(self) -> None:
        self.assertGreater(compute_locality(0.15), compute_locality(140.0))
        self.assertGreater(compute_locality(140.0), compute_locality(64800.0))
        self.assertEqual(compute_locality(64800.0), 0.0)
        self.assertEqual(compute_locality(0.000001), 1.0)

        base = compute_rank_score(1.0, 140.0, 0)
        self.assertGreater(compute_rank_score(1.0, 140.0, 5), base)
        self.assertLess(compute_rank_score(1.0, 140.0, -5), base)
        self.assertLess(compute_rank_score(1.0, 140.0, 0, "degraded"), base)
        # Out-of-range priorities are clamped to [-10, 10].
        self.assertEqual(
            compute_rank_score(1.0, 140.0, 99), compute_rank_score(1.0, 140.0, 10)
        )
        # A +10 priority cannot make a weak prefix match beat an exact name match.
        self.assertGreater(
            compute_rank_score(1.0, 64800.0, 0), compute_rank_score(0.45, 360.0, 10)
        )

    def test_dem_ranks_above_democratic(self) -> None:
        client = self._client(DEM_CASE_ENTRIES)
        for allow_fts5 in (True, False):
            client.allow_fts5 = allow_fts5
            with self.subTest(fts5=allow_fts5):
                ids = [r.id for r in client.search_sources("dem", GOMA_BBOX)]
                self.assertEqual(len(ids), 4)
                self.assertEqual(ids[0], "global-copernicus-dem-glo30")
                # Exact synonym hit on a global layer still beats "Democratic".
                self.assertEqual(ids[1], "global-esri-world-hillshade")
                # Exact word in a description beats a prefix hit in a name.
                self.assertLess(
                    ids.index("cd-roads-with-dem-in-description"),
                    ids.index("cd-geoboundaries-adm1"),
                )
                self.assertEqual(ids[-1], "cd-geoboundaries-adm1")

                # The full word still finds the national layer first (more local).
                ids_full = [r.id for r in client.search_sources("democratic", GOMA_BBOX)]
                self.assertEqual(ids_full[0], "cd-geoboundaries-adm1")

    def test_locator_group_order_follows_relevance(self) -> None:
        client = self._client(DEM_CASE_ENTRIES)
        # `dem`: the best global hit is far better than the best national one.
        self.assertTrue(global_group_leads(client.search_sources_scored("dem", GOMA_BBOX)))
        # `democratic`, `congo` or no query: local & national layers lead, as before.
        for query in ("democratic", "congo", ""):
            with self.subTest(query=query):
                scored = client.search_sources_scored(query, GOMA_BBOX)
                self.assertTrue(scored)
                self.assertFalse(global_group_leads(scored))
        only_global = client.search_sources_scored("copernicus", GOMA_BBOX)
        self.assertFalse(global_group_leads(only_global))
        self.assertFalse(global_group_leads([]))

    def test_priority_orders_equally_relevant_sources(self) -> None:
        def osm(source_id: str, priority: int, **extra: Any) -> Dict[str, Any]:
            entry: Dict[str, Any] = {
                "id": source_id,
                "name": f"Basemap {source_id}",
                "category": "basemap",
                "tags": ["basemap"],
                "priority": priority,
            }
            entry.update(extra)
            return entry

        client = self._client(
            [
                osm("global-b-demoted", -8),
                osm("global-c-neutral", 0),
                osm("global-a-promoted", 6),
                osm("global-d-degraded", 0, health_status="degraded"),
                osm("global-e-dead", 10, health_status="dead"),
            ]
        )
        for allow_fts5 in (True, False):
            client.allow_fts5 = allow_fts5
            for query in ("basemap", ""):
                with self.subTest(fts5=allow_fts5, query=query):
                    records = client.search_sources(query, GOMA_BBOX)
                    self.assertEqual(
                        [r.id for r in records],
                        [
                            "global-a-promoted",
                            "global-c-neutral",
                            "global-d-degraded",
                            "global-b-demoted",
                        ],
                    )
                    self.assertEqual(records[0].priority, 6)

    def test_locality_still_orders_equal_matches(self) -> None:
        entries = [
            {"id": "global-water", "name": "Water points", "tags": ["water"]},
            {
                "id": "cd-water",
                "name": "Water points",
                "tags": ["water"],
                "country_iso": "CD",
                "bbox": DRC_BBOX,
            },
            {
                "id": "goma-water",
                "name": "Water points",
                "tags": ["water"],
                "country_iso": "CD",
                "bbox": (29.0, -1.8, 29.4, -1.5),
            },
            {
                "id": "tokyo-water",
                "name": "Water points",
                "tags": ["water"],
                "bbox": (139.6, 35.6, 139.9, 35.8),
            },
        ]
        client = self._client(entries)
        ids = [r.id for r in client.search_sources("water", GOMA_BBOX)]
        self.assertEqual(ids, ["goma-water", "cd-water", "global-water"])
        ids_global = [r.id for r in client.search_sources("water !global", GOMA_BBOX)]
        self.assertIn("tokyo-water", ids_global)

    def test_limit_is_applied_after_filters(self) -> None:
        entries: List[Dict[str, Any]] = [
            {
                "id": f"xx-eli-ortho-{idx:02d}",
                "name": f"Ortho {idx:02d}",
                "category": "imagery",
                "tags": ["ortho"],
                "bbox": (29.0, -2.0, 29.5, -1.5),
            }
            for idx in range(60)
        ]
        entries.append(
            {"id": "global-ortho", "name": "World ortho", "category": "imagery", "tags": ["ortho"]}
        )
        entries.append(
            {"id": "global-ortho-admin", "name": "Ortho index", "category": "admin", "tags": ["ortho"]}
        )
        client = self._client(entries)

        # 60 more local ELI entries rank first: an early LIMIT 50 would hide the others.
        self.assertEqual(len(client.search_sources("ortho", GOMA_BBOX, limit=50)), 50)
        without_eli = client.search_sources("ortho", GOMA_BBOX, limit=50, include_eli=False)
        self.assertEqual({r.id for r in without_eli}, {"global-ortho", "global-ortho-admin"})

        only_imagery = client.search_sources(
            "ortho", GOMA_BBOX, limit=50, include_eli=False, category="imagery"
        )
        self.assertEqual([r.id for r in only_imagery], ["global-ortho"])

        # The `accept` predicate (exact geometry test in the UI) also runs before the limit.
        accepted = client.search_sources(
            "ortho", GOMA_BBOX, limit=1, accept=lambda r: r.id == "global-ortho"
        )
        self.assertEqual([r.id for r in accepted], ["global-ortho"])

    def test_catalog_without_optional_columns(self) -> None:
        client = self._client(DEM_CASE_ENTRIES, name="old.sqlite", with_optional_columns=False)
        records = client.search_sources("dem", GOMA_BBOX)
        self.assertEqual(records[0].id, "global-copernicus-dem-glo30")
        self.assertEqual(records[0].priority, 0)
        self.assertIsNone(records[0].data_date)
        self.assertIsNone(records[0].health_checked_at)
        self.assertEqual(records[0].i18n, {})

    def test_optional_columns_and_multi_catalog(self) -> None:
        new_path = build_temp_catalog(self.tmp_dir / "new.sqlite", DEM_CASE_ENTRIES)
        old_path = build_temp_catalog(
            self.tmp_dir / "old.sqlite",
            [
                {"id": "global-copernicus-dem-glo30", "name": "Duplicate DEM", "tags": ["dem"]},
                {"id": "private-dem", "name": "Team DEM", "tags": ["dem"], "bbox": DRC_BBOX},
            ],
            with_optional_columns=False,
        )
        client = CatalogDatabaseClient([new_path, old_path])
        records = client.search_sources("dem", GOMA_BBOX)
        by_id = {r.id: r for r in records}
        self.assertEqual(len(by_id), len(records))  # no duplicate id
        self.assertEqual(records[0].id, "private-dem")  # exact name hit + more local
        copernicus = by_id["global-copernicus-dem-glo30"]
        self.assertEqual(copernicus.name, "Copernicus DEM GLO-30 (30m)")  # first catalog wins
        self.assertEqual(copernicus.data_date, "2021")
        self.assertEqual(copernicus.health_checked_at, "2026-11-10T04:00:00Z")

    def test_record_dict_round_trip_and_defaults(self) -> None:
        from dataclasses import asdict

        record = _dummy_record("cog", "https://example.org/dem.tif", priority=4, data_date="2021")
        self.assertEqual(CatalogSourceRecord.from_dict(json.loads(json.dumps(asdict(record)))), record)

        legacy = asdict(record)
        for key in ("priority", "data_date", "health_checked_at", "i18n"):
            legacy.pop(key)
        rebuilt = CatalogSourceRecord.from_dict(legacy)
        self.assertEqual(rebuilt.priority, 0)
        self.assertIsNone(rebuilt.data_date)
        self.assertIsNone(rebuilt.health_checked_at)

    def test_real_catalog_dem_over_goma(self) -> None:
        sqlite_path = REGISTRY_DIST_DIR / "catalog.sqlite"
        if not sqlite_path.is_file():
            self.skipTest(f"Missing {sqlite_path}")
        client = CatalogDatabaseClient([sqlite_path])
        ids = [r.id for r in client.search_sources("dem", GOMA_BBOX)]
        if "global-copernicus-dem-glo30" not in ids:
            self.skipTest("Copernicus DEM entry not in this catalog build")
        self.assertEqual(ids[0], "global-copernicus-dem-glo30")


class TestEntryDetails(unittest.TestCase):
    """Preview facts shown before loading a layer (coverage, data date, link status)."""

    def setUp(self) -> None:
        i18n.set_language_override("en")

    def tearDown(self) -> None:
        i18n.set_language_override(None)

    def test_coverage_and_link_status(self) -> None:
        world = _dummy_record("xyz", "https://example.org/{z}/{x}/{y}.png")
        self.assertTrue(world.is_worldwide)
        self.assertEqual(coverage_label(world), "Worldwide")
        self.assertEqual(link_status_label(world), "Not checked yet")

        national = _dummy_record(
            "wfs",
            "https://example.org/wfs",
            country_iso="CD",
            min_x=12.2, min_y=-13.5, max_x=31.3, max_y=5.4, area_deg2=361.0,
            health_status="degraded",
            health_checked_at="2026-11-10T04:00:00Z",
            data_date="updated daily",
        )
        self.assertFalse(national.is_worldwide)
        self.assertEqual(coverage_label(national), "CD")
        self.assertEqual(
            coverage_label(national, lambda iso: "Congo - Kinshasa"),
            "Congo - Kinshasa (CD)",
        )
        self.assertIn("Degraded", link_status_label(national))
        self.assertIn("2026-11-10", link_status_label(national))

        rows = dict(describe_entry(national))
        self.assertEqual(rows["Data date"], "updated daily")
        self.assertEqual(rows["Format"], "WFS service (vector)")
        self.assertEqual(dict(describe_entry(world))["Data date"], "Not specified")

        checked = _dummy_record("cog", "https://example.org/a.tif", health_checked_at="2026-11-10")
        self.assertEqual(link_status_label(checked), "Working, checked on 2026-11-10")

        regional = _dummy_record(
            "cog", "https://example.org/a.tif",
            min_x=5.0, min_y=45.0, max_x=11.0, max_y=48.0, area_deg2=18.0,
        )
        self.assertIn("5.00", coverage_label(regional))

    def test_details_html_is_escaped_and_translated(self) -> None:
        record = _dummy_record(
            "xyz",
            "https://example.org/{z}/{x}/{y}.png",
            name="Rivers <b>& lakes",
            attribution="© A & B",
        )
        html_en = build_details_html(record)
        self.assertIn("Rivers &lt;b&gt;&amp; lakes", html_en)
        self.assertIn("© A &amp; B", html_en)
        self.assertIn("Link status", html_en)

        i18n.set_language_override("fr")
        html_fr = build_details_html(record)
        self.assertIn("État du lien", html_fr)
        self.assertIn("Monde entier", html_fr)


def _collect_translatable_strings() -> Tuple[Dict[str, List[str]], List[str]]:
    """Statically collect the literals passed to tr() / tr_n() / N_() in the plugin.

    Returns ({source string: ["file:line", ...]}, problems). A non-literal argument is
    only allowed for tr(), where it must be a constant declared with N_().
    """
    found: Dict[str, List[str]] = {}
    problems: List[str] = []
    for py_file in sorted(PLUGIN_PACKAGE_DIR.rglob("*.py")):
        if "i18n" in py_file.relative_to(PLUGIN_PACKAGE_DIR).parts:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in ("tr", "tr_n", "N_"):
                continue
            where = f"{py_file.relative_to(PLUGIN_PACKAGE_DIR)}:{node.lineno}"
            text_args = node.args[:2] if node.func.id == "tr_n" else node.args[:1]
            if not text_args:
                problems.append(f"{where}: {node.func.id}() without a source string")
            for arg in text_args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(arg.value, []).append(where)
                elif isinstance(arg, ast.JoinedStr) or node.func.id != "tr":
                    problems.append(
                        f"{where}: {node.func.id}() needs a plain string literal "
                        "(no f-string / expression); use placeholders: tr('... {name}', name=x)"
                    )
    return found, problems


def _placeholders(text: str) -> Set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class TestTranslations(unittest.TestCase):
    """English is the source language; every tr() string needs a French translation."""

    def tearDown(self) -> None:
        i18n.set_language_override(None)

    def test_every_tr_string_has_a_french_translation(self) -> None:
        found, problems = _collect_translatable_strings()
        self.assertEqual(problems, [])
        self.assertGreater(len(found), 50, "tr() scanner found suspiciously few strings")

        french = i18n.TRANSLATIONS["fr"]
        missing = [
            f"{text!r} (used in {', '.join(places)})"
            for text, places in found.items()
            if not french.get(text, "").strip()
        ]
        self.assertEqual(missing, [], "Add these strings to geostrata/i18n/fr.py")

    def test_translations_are_consistent_for_every_language(self) -> None:
        found, _ = _collect_translatable_strings()
        for lang, catalog in i18n.TRANSLATIONS.items():
            with self.subTest(lang=lang):
                stale = sorted(set(catalog) - set(found))
                self.assertEqual(stale, [], f"Unused keys in i18n/{lang}.py")
                for source, translated in catalog.items():
                    self.assertEqual(
                        _placeholders(source),
                        _placeholders(translated),
                        f"Placeholder mismatch in i18n/{lang}.py for {source!r}",
                    )

    def test_tr_selects_language_and_falls_back_to_english(self) -> None:
        i18n.set_language_override("en")
        self.assertEqual(i18n.tr("Load"), "Load")
        self.assertEqual(i18n.get_ui_language(), "en")

        i18n.set_language_override("fr_FR")
        self.assertEqual(i18n.get_ui_language(), "fr")
        self.assertEqual(i18n.tr("Load"), "Charger")
        self.assertEqual(
            i18n.tr("Layer loaded: {name} ({attribution})", name="X", attribution="Y"),
            "Couche chargée : X (Y)",
        )
        self.assertEqual(i18n.tr("Not a translated string"), "Not a translated string")
        self.assertEqual(
            i18n.tr_n("{count} layer available", "{count} layers available", 0),
            "0 couche disponible",
        )
        self.assertEqual(
            i18n.tr_n("{count} layer available", "{count} layers available", 3),
            "3 couches disponibles",
        )

        # A language without a dictionary: English interface, but the catalog content
        # language is kept (layer names use the entry's own i18n block when present).
        i18n.set_language_override("es")
        self.assertEqual(i18n.get_ui_language(), "en")
        self.assertEqual(i18n.get_preferred_language(), "es")
        self.assertEqual(i18n.tr("Load"), "Load")
        self.assertEqual(
            i18n.tr_n("{count} layer available", "{count} layers available", 0),
            "0 layers available",
        )
        record = _dummy_record(
            "xyz",
            "https://example.org/{z}/{x}/{y}.png",
            i18n={"es": {"name": "Prueba"}, "fr": {"name": "Essai"}},
        )
        self.assertEqual(record.localized_name(), "Prueba")
        i18n.set_language_override("fr")
        self.assertEqual(record.localized_name(), "Essai")
        i18n.set_language_override("de")
        self.assertEqual(record.localized_name(), "Test xyz")


if __name__ == "__main__":
    unittest.main()
