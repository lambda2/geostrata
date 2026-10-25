"""Unit test suite for Geostrata PyQGIS core modules (stdlib only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_PARENT_DIR = Path(__file__).resolve().parent.parent
REGISTRY_DIST_DIR = (
    PLUGIN_PARENT_DIR.parent / "registry" / "dist"
)
sys.path.insert(0, str(PLUGIN_PARENT_DIR))

from geostrata.core.db_client import (  # noqa: E402
    CatalogDatabaseClient,
    CatalogSourceRecord,
    parse_search_query,
)
from geostrata.core.overpass_task import (  # noqa: E402
    build_overpass_json_ql,
    clamp_overpass_bbox,
    convert_overpass_json_to_geojson,
)
from geostrata.core.uri_builder import (  # noqa: E402
    build_overpass_ql,
    build_provider_uri,
)


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
        result_ids = [r.id for r in results]
        self.assertIn("fr-ign-bdtopo-cours-eau", result_ids)
        self.assertIn("global-overpass-drinking-water", result_ids)
        # Verify strict Local -> National -> Global area_deg2 ordering
        areas = [r.area_deg2 for r in results]
        self.assertEqual(areas, sorted(areas))

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


if __name__ == "__main__":
    unittest.main()
