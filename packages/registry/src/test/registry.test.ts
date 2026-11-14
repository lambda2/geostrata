import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { after, before, describe, test } from 'node:test'
import Database from 'better-sqlite3'
import { CATALOG_LAYOUT_VERSION, buildCatalog } from '../build.js'
import { type CountryIndex, expandCatalogFileContent } from '../expand.js'
import {
	classifyHttpStatus,
	extractArcgisErrorCode,
	extractTestableHttpUrl,
	resolveSourcesTableName,
	selectHealthRows,
} from '../healthcheck.js'
import { CatalogEntrySchema } from '../schema.js'
import { formatTimeValue, resolveTimePlaceholder } from '../time.js'

const COUNTRY_INDEX: CountryIndex = {
	HTI: { iso2: 'HT', name_en: 'Haiti', name_fr: 'Haïti', bbox: [-74.5, 18, -71.6, 20.1] },
	COD: {
		iso2: 'CD',
		name_en: 'Democratic Republic of the Congo',
		name_fr: 'République démocratique du Congo',
		bbox: [12.2, -13.5, 31.3, 5.4],
	},
}

const BASE_ENTRY = {
	id: 'test-layer',
	name: 'Test layer',
	type: 'geojson',
	category: 'hazard',
	url: 'https://example.org/data.geojson',
	tags: ['test'],
	license: 'CC0',
	attribution: 'Example',
}

function listIssues(entry: Record<string, unknown>): Array<string> {
	const result = CatalogEntrySchema.safeParse(entry)
	return result.success
		? []
		: result.error.issues.map(function formatIssue(issue) {
				return `${issue.path.join('.')}: ${issue.message}`
			})
}

describe('country templates', function countryTemplateSuite() {
	test('one entry per listed country, with the placeholders filled in', function expandsTemplate() {
		const entries = expandCatalogFileContent(
			[
				{
					...BASE_ENTRY,
					id: '{iso2}-hot-roads',
					for_each_country: ['HTI', 'cod'],
					name: 'Roads ({country})',
					i18n: { fr: { name: 'Routes ({country_fr})' } },
					type: 'archive',
					url: 'https://example.org/{ISO3}/hotosm_{iso3}_roads.zip',
					archive_target: 'hotosm_{iso3}_roads.gpkg',
					tags: ['roads', '{country}', '{ISO2}'],
				},
			],
			COUNTRY_INDEX,
		)
		assert.deepEqual(
			entries.map(function pickId(entry) {
				return entry.id
			}),
			['ht-hot-roads', 'cd-hot-roads'],
		)
		const haiti = entries[0]
		assert.equal(haiti.country_iso, 'HT')
		assert.equal(haiti.name, 'Roads (Haiti)')
		assert.equal(haiti.i18n?.fr.name, 'Routes (Haïti)')
		assert.equal(haiti.url, 'https://example.org/HTI/hotosm_hti_roads.zip')
		assert.equal(haiti.archive_target, 'hotosm_hti_roads.gpkg')
		assert.deepEqual(haiti.tags, ['roads', 'Haiti', 'HT'])
	})

	test('{time}, {z}/{x}/{y} and {{bbox}} are left alone', function keepsOtherPlaceholders() {
		const [dated, tiles] = expandCatalogFileContent(
			[
				{
					...BASE_ENTRY,
					id: '{iso3}-daily',
					for_each_country: ['HTI'],
					type: 'archive',
					url: 'https://example.org/{iso3}/{time}/rain_{time}.zip',
					archive_target: 'rain_{iso3}_{time}.tif',
					time: { offset_days: -1 },
				},
				{
					...BASE_ENTRY,
					id: '{iso2}-tiles',
					for_each_country: ['HTI'],
					type: 'xyz',
					url: 'https://example.org/{iso2}/{z}/{x}/{y}.png?bbox={{bbox}}',
				},
			],
			COUNTRY_INDEX,
		)
		assert.equal(dated.url, 'https://example.org/hti/{time}/rain_{time}.zip')
		assert.equal(dated.archive_target, 'rain_hti_{time}.tif')
		assert.deepEqual(dated.time, { offset_days: -1, step: 'day', format: 'YYYY-MM-DD' })
		assert.equal(tiles.url, 'https://example.org/ht/{z}/{x}/{y}.png?bbox={{bbox}}')
	})

	test('"all" expands to every country of the index', function expandsAll() {
		const entries = expandCatalogFileContent(
			{ ...BASE_ENTRY, id: 'x-{iso3}', for_each_country: 'all' },
			COUNTRY_INDEX,
		)
		assert.equal(entries.length, Object.keys(COUNTRY_INDEX).length)
	})

	test('an unknown country or an id without placeholder is rejected', function rejectsBadTemplates() {
		assert.throws(function unknownCountry() {
			expandCatalogFileContent(
				{ ...BASE_ENTRY, id: '{iso2}-x', for_each_country: ['XXX'] },
				COUNTRY_INDEX,
			)
		}, /unknown country code "XXX"/)
		assert.throws(function missingPlaceholder() {
			expandCatalogFileContent(
				{ ...BASE_ENTRY, id: 'same-id', for_each_country: ['HTI'] },
				COUNTRY_INDEX,
			)
		}, /needs \{iso2\} or \{iso3\} in its id/)
	})
})

describe('file defaults', function defaultsSuite() {
	test('defaults fill the missing fields, the entry wins, tags add up', function mergesDefaults() {
		const entries = expandCatalogFileContent(
			{
				defaults: {
					category: 'hazard',
					license: 'Public Domain',
					attribution: 'USGS',
					tags: ['usgs', 'earthquake'],
					priority: 2,
				},
				sources: [
					{
						id: 'quakes-week',
						name: 'Earthquakes (7 days)',
						type: 'geojson',
						url: 'https://example.org/week.geojson',
						tags: ['alerts', 'usgs'],
					},
					{
						id: 'quakes-faults',
						name: 'Faults',
						type: 'fgb',
						url: '/vsicurl/https://example.org/faults.fgb',
						license: 'CC-BY-4.0',
						priority: 0,
					},
				],
			},
			COUNTRY_INDEX,
		)
		assert.equal(entries[0].category, 'hazard')
		assert.equal(entries[0].license, 'Public Domain')
		assert.deepEqual(entries[0].tags, ['usgs', 'earthquake', 'alerts'])
		assert.equal(entries[0].priority, 2)
		assert.equal(entries[0].crs, 'EPSG:4326')
		assert.equal(entries[1].license, 'CC-BY-4.0')
		assert.deepEqual(entries[1].tags, ['usgs', 'earthquake'])
		assert.equal(entries[1].priority, 0)
	})

	test('a validation error names the entry and the field', function reportsEntry() {
		assert.throws(function invalidEntry() {
			expandCatalogFileContent(
				{ defaults: { license: 'CC0' }, sources: [{ ...BASE_ENTRY, id: 'Bad Id' }] },
				COUNTRY_INDEX,
			)
		}, /"Bad Id": id: /)
	})
})

describe('time, layer, geometry_type and ArcGIS types', function contractSuite() {
	test('{time} without a time block is an error', function placeholderNeedsTime() {
		assert.match(
			listIssues({ ...BASE_ENTRY, url: 'https://example.org/{time}.geojson' }).join('\n'),
			/time: a "time" block is required/,
		)
		assert.match(
			listIssues({
				...BASE_ENTRY,
				type: 'archive',
				url: 'https://example.org/data.zip',
				archive_target: 'day_{time}.shp',
			}).join('\n'),
			/time: a "time" block is required/,
		)
	})

	test('a time block without {time} is an error', function timeNeedsPlaceholder() {
		assert.match(
			listIssues({ ...BASE_ENTRY, time: { offset_days: -1 } }).join('\n'),
			/time: "time" is set but neither url nor archive_target contains \{time\}/,
		)
	})

	test('time defaults and accepted values', function timeDefaults() {
		const url = 'https://example.org/{time}.geojson'
		assert.deepEqual(CatalogEntrySchema.parse({ ...BASE_ENTRY, url, time: {} }).time, {
			offset_days: 0,
			step: 'day',
			format: 'YYYY-MM-DD',
		})
		assert.deepEqual(
			CatalogEntrySchema.parse({
				...BASE_ENTRY,
				url,
				time: { offset_days: -45, step: 'month', format: 'YYYYMM' },
			}).time,
			{ offset_days: -45, step: 'month', format: 'YYYYMM' },
		)
		const archive = CatalogEntrySchema.parse({
			...BASE_ENTRY,
			type: 'archive',
			url: 'https://example.org/data.zip',
			archive_target: 'day_{time}.shp',
			time: {},
		})
		assert.equal(archive.archive_target, 'day_{time}.shp')
	})

	test('invalid time blocks are rejected', function rejectsBadTime() {
		const url = 'https://example.org/{time}.geojson'
		assert.notDeepEqual(listIssues({ ...BASE_ENTRY, url, time: { offset_days: 1.5 } }), [])
		assert.notDeepEqual(listIssues({ ...BASE_ENTRY, url, time: { step: 'week' } }), [])
		assert.notDeepEqual(listIssues({ ...BASE_ENTRY, url, time: { format: 'today' } }), [])
		assert.notDeepEqual(listIssues({ ...BASE_ENTRY, url, time: { format: '{YYYY}' } }), [])
		assert.notDeepEqual(listIssues({ ...BASE_ENTRY, url, time: { offset: -1 } }), [])
	})

	test('the date is today + offset_days in UTC, truncated to the month when asked', function formatsDate() {
		const now = new Date('2026-03-02T23:30:00Z')
		assert.equal(formatTimeValue({ offset_days: 0, step: 'day', format: 'YYYY-MM-DD' }, now), '2026-03-02')
		assert.equal(formatTimeValue({ offset_days: -2, step: 'day', format: 'YYYY/MM/DD' }, now), '2026/02/28')
		assert.equal(formatTimeValue({ offset_days: -2, step: 'month', format: 'YYYY-MM-DD' }, now), '2026-02-01')
		assert.equal(formatTimeValue({ offset_days: 30, step: 'month', format: 'YYYYMM' }, now), '202604')
		assert.equal(
			resolveTimePlaceholder('a/{time}/b_{time}.tif', { offset_days: -1, step: 'day', format: 'YYYYMMDD' }, now),
			'a/20260301/b_20260301.tif',
		)
		assert.equal(resolveTimePlaceholder('a/{time}', null, now), 'a/{time}')
	})

	test('layer is a string, geometry_type is point, line or polygon', function layerAndGeometryType() {
		const entry = CatalogEntrySchema.parse({
			...BASE_ENTRY,
			type: 'arcgismapserver',
			url: 'https://example.org/arcgis/rest/services/X/MapServer',
			layer: 3,
			geometry_type: 'line',
		})
		assert.equal(entry.layer, '3')
		assert.equal(entry.geometry_type, 'line')
		assert.equal(CatalogEntrySchema.parse({ ...BASE_ENTRY, layer: 'roads' }).layer, 'roads')
		assert.equal(
			CatalogEntrySchema.parse({
				...BASE_ENTRY,
				type: 'arcgisfeatureserver',
				url: 'https://example.org/arcgis/rest/services/X/FeatureServer/0',
			}).type,
			'arcgisfeatureserver',
		)
		assert.notDeepEqual(listIssues({ ...BASE_ENTRY, geometry_type: 'multipolygon' }), [])
		assert.notDeepEqual(listIssues({ ...BASE_ENTRY, layer: '' }), [])
		assert.notDeepEqual(listIssues({ ...BASE_ENTRY, type: 'arcgis' }), [])
	})
})

describe('healthcheck probes', function healthcheckSuite() {
	const now = new Date('2026-03-02T12:00:00Z')

	test('ArcGIS endpoints are probed at <url>?f=json', function arcgisProbe() {
		assert.equal(
			extractTestableHttpUrl('arcgisfeatureserver', 'https://example.org/rest/services/X/FeatureServer/0'),
			'https://example.org/rest/services/X/FeatureServer/0?f=json',
		)
		assert.equal(
			extractTestableHttpUrl('arcgismapserver', 'https://example.org/rest/services/X/MapServer/?token=1'),
			'https://example.org/rest/services/X/MapServer?f=json',
		)
		assert.equal(extractArcgisErrorCode('{"error":{"code":404,"message":"Service not found"}}'), 404)
		assert.equal(extractArcgisErrorCode('{"currentVersion":10.91,"layers":[]}'), null)
		assert.equal(classifyHttpStatus('arcgismapserver', 404, 100), 'dead')
		assert.equal(classifyHttpStatus('arcgismapserver', 200, 100), 'ok')
	})

	test('{time} is resolved before probing, other placeholders are not probed', function timeProbe() {
		assert.equal(
			extractTestableHttpUrl(
				'cog',
				'/vsicurl/https://example.org/{time}/rain.tif',
				{ offset_days: -1, step: 'day', format: 'YYYY/MM/DD' },
				now,
			),
			'https://example.org/2026/03/01/rain.tif',
		)
		assert.equal(extractTestableHttpUrl('cog', '/vsicurl/https://example.org/{time}/rain.tif'), null)
		assert.equal(extractTestableHttpUrl('xyz', 'https://example.org/{z}/{x}/{y}.png'), 'https://example.org/0/0/0.png')
		assert.equal(
			extractTestableHttpUrl('wms', 'https://example.org/wms?layers=a'),
			'https://example.org/wms?SERVICE=WMS&REQUEST=GetCapabilities',
		)
	})
})

const FIXTURE_YAML = `
defaults:
  license: CC0
  attribution: Example
sources:
  - id: world-dem
    name: World DEM
    description: Elevation model
    type: cog
    category: elevation
    url: /vsicurl/https://example.org/dem.tif
    tags: [dem, altitude]
  - id: "{iso2}-hot-roads"
    for_each_country: [TZA, FJI]
    name: "Roads ({country})"
    i18n:
      fr:
        name: "Routes ({country_fr})"
        description: "Réseau routier"
    type: archive
    category: transport
    url: https://example.org/{iso3}_roads.zip
    archive_target: roads.gpkg
    layer: roads_lines
    geometry_type: line
    tags: [roads, routes]
  - id: lyon-water
    name: Lyon drinking water
    type: geojson
    category: hydrography
    url: https://example.org/lyon.geojson
    bbox: [4.7, 45.6, 5.1, 45.9]
    tags: [eau]
  - id: daily-rain
    name: Daily rainfall
    type: cog
    category: weather
    url: /vsicurl/https://example.org/{time}/rain.tif
    time: { offset_days: -2 }
    tags: [rain]
  - id: arcgis-hospitals
    name: Hospitals
    type: arcgismapserver
    category: health
    url: https://example.org/arcgis/rest/services/Health/MapServer
    layer: 4
    tags: [hospital]
`

const VIEW_COLUMNS = [
	'rowid', 'id', 'name', 'description', 'i18n_json', 'type', 'category', 'url', 'crs',
	'country_iso', 'min_x', 'min_y', 'max_x', 'max_y', 'area_deg2', 'geom_wkt', 'tags_json',
	'search_text', 'license', 'attribution', 'style_qml', 'min_zoom', 'max_zoom',
	'archive_target', 'priority', 'data_date', 'health_status', 'health_checked_at',
	'layer', 'geometry_type', 'time_json',
]

describe('build round-trip', function buildSuite() {
	let workDir = ''
	let db: Database.Database

	before(function buildFixtureCatalog() {
		workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'geostrata-registry-test-'))
		const sourcesDir = path.join(workDir, 'sources')
		fs.mkdirSync(sourcesDir)
		fs.writeFileSync(path.join(sourcesDir, 'fixture.yaml'), FIXTURE_YAML)
		const result = buildCatalog({ sourcesDir, distDir: path.join(workDir, 'dist'), quiet: true })
		assert.equal(result.sourceCount, 6)
		db = new Database(result.sqlitePath)
	})

	after(function removeFixtureCatalog() {
		db?.close()
		fs.rmSync(workDir, { recursive: true, force: true })
	})

	test('`sources_rtree` keeps the columns of the R-Tree table, one box per source at least', function rtreeView() {
		const columns = (db.pragma('table_info(sources_rtree)') as Array<{ name: string }>).map(
			function pickName(column) {
				return column.name
			},
		)
		assert.deepEqual(columns, ['id', 'minX', 'maxX', 'minY', 'maxY'])
		const counts = db
			.prepare('SELECT count(DISTINCT id) AS sources, count(*) AS boxes FROM sources_rtree')
			.get() as { sources: number; boxes: number }
		assert.equal(counts.sources, 6)
		assert.ok(counts.boxes > 6)
		const lyon = db
			.prepare("SELECT r.* FROM sources s INNER JOIN sources_rtree r ON s.rowid = r.id WHERE s.id = 'lyon-water'")
			.all() as Array<Record<string, number>>
		assert.equal(lyon.length, 1)
		assert.ok(lyon[0].minX <= 4.7 && lyon[0].maxX >= 5.1 && lyon[0].minY <= 45.6 && lyon[0].maxY >= 45.9)
	})

	test('`sources` is a view with the layout 1 columns followed by the new ones', function viewColumns() {
		const object = db.prepare("SELECT type FROM sqlite_master WHERE name = 'sources'").get() as {
			type: string
		}
		assert.equal(object.type, 'view')
		const columns = (db.pragma('table_info(sources)') as Array<{ name: string }>).map(
			function pickName(column) {
				return column.name
			},
		)
		assert.deepEqual(columns, VIEW_COLUMNS)
		assert.equal(db.pragma('user_version', { simple: true }), CATALOG_LAYOUT_VERSION)
	})

	test('the new fields reach the view', function newFields() {
		const roads = db.prepare("SELECT * FROM sources WHERE id = 'tz-hot-roads'").get() as Record<string, unknown>
		assert.equal(roads.layer, 'roads_lines')
		assert.equal(roads.geometry_type, 'line')
		assert.equal(roads.time_json, null)
		assert.equal(roads.archive_target, 'roads.gpkg')
		assert.equal(roads.country_iso, 'TZ')
		const rain = db.prepare("SELECT * FROM sources WHERE id = 'daily-rain'").get() as Record<string, unknown>
		assert.equal(rain.url, '/vsicurl/https://example.org/{time}/rain.tif')
		assert.deepEqual(JSON.parse(String(rain.time_json)), {
			offset_days: -2,
			step: 'day',
			format: 'YYYY-MM-DD',
		})
		const arcgis = db.prepare("SELECT * FROM sources WHERE id = 'arcgis-hospitals'").get() as Record<string, unknown>
		assert.equal(arcgis.type, 'arcgismapserver')
		assert.equal(arcgis.layer, '4')
	})

	test('styles and footprints are stored once and read back through the view', function sharedTexts() {
		const count = function countRows(table: string): number {
			return (db.prepare(`SELECT count(*) AS n FROM ${table}`).get() as { n: number }).n
		}
		assert.equal(count('sources_data'), 6)
		// transport.qml is shared by the two road layers; the world box by three layers.
		assert.ok(count('styles') < 6)
		assert.equal(count('footprints'), 4)
		const roads = db
			.prepare("SELECT id, style_qml FROM sources WHERE id LIKE '%-hot-roads' ORDER BY id")
			.all() as Array<{ id: string; style_qml: string }>
		assert.equal(roads.length, 2)
		assert.match(roads[0].style_qml, /<qgis/)
		assert.equal(roads[0].style_qml, roads[1].style_qml)
		const dem = db.prepare("SELECT style_qml, geom_wkt FROM sources WHERE id = 'world-dem'").get() as {
			style_qml: string | null
			geom_wkt: string
		}
		assert.equal(dem.geom_wkt, 'POLYGON((-180 -90, 180 -90, 180 90, -180 90, -180 -90))')
	})

	test('search_text keeps its format: normalized words between spaces', function searchText() {
		const row = db.prepare("SELECT search_text FROM sources WHERE id = 'fj-hot-roads'").get() as {
			search_text: string
		}
		assert.match(row.search_text, /^ fj hot roads roads fiji routes fidji reseau routier transport archive roads routes /)
		assert.match(row.search_text, / fj [^ ].* $/)
		assert.ok(!row.search_text.includes('  '))
		const liked = db
			.prepare("SELECT id FROM sources WHERE search_text LIKE '% reseau%' ORDER BY id")
			.all() as Array<{ id: string }>
		assert.deepEqual(liked, [{ id: 'fj-hot-roads' }, { id: 'tz-hot-roads' }])
	})

	test('the plugin query works: view joined to FTS5 and R-Tree on rowid', function pluginQuery() {
		const rows = db
			.prepare(
				`SELECT s.id, s.style_qml, f.enriched_tags AS fts_tags, f.country_names AS fts_countries
				FROM sources s
				INNER JOIN sources_fts f ON s.rowid = f.rowid
				INNER JOIN sources_rtree r ON s.rowid = r.id
				WHERE s.health_status != 'dead' AND f.sources_fts MATCH ?
					AND r.maxX >= ? AND r.minX <= ? AND r.maxY >= ? AND r.minY <= ?`,
			)
			.all('"rout"*', 39.2, 39.3, -6.9, -6.8) as Array<Record<string, string>>
		// Dar es Salaam: Tanzania matches, Fiji is filtered by the R-Tree.
		assert.deepEqual(
			rows.map(function pickId(row) {
				return row.id
			}),
			['tz-hot-roads'],
		)
		assert.match(rows[0].fts_tags, /^roads routes .* Routes \(Tanzanie\) Réseau routier$/)
		assert.match(rows[0].fts_countries, /^TZ/)
		// The FTS index and the view it reads its content from agree.
		db.exec("INSERT INTO sources_fts(sources_fts, rank) VALUES('integrity-check', 1)")
	})

	test('a country layer gets the outline of the country, not its bounding box', function countryOutline() {
		const outlines = JSON.parse(
			fs.readFileSync(
				path.resolve(import.meta.dirname, '..', '..', 'assets', 'polygons', 'country-outlines.json'),
				'utf-8',
			),
		) as Record<string, { bbox: Array<number>; area_deg2: number; geom_wkt: string }>
		const tanzania = db.prepare("SELECT * FROM sources WHERE id = 'tz-hot-roads'").get() as Record<string, number | string>
		assert.equal(tanzania.geom_wkt, outlines.TZA.geom_wkt)
		assert.deepEqual(
			[tanzania.min_x, tanzania.min_y, tanzania.max_x, tanzania.max_y],
			outlines.TZA.bbox,
		)
		assert.equal(tanzania.area_deg2, outlines.TZA.area_deg2)
		// Fiji crosses the antimeridian: its bbox spans the world, its area does not.
		const fiji = db.prepare("SELECT * FROM sources WHERE id = 'fj-hot-roads'").get() as Record<string, number | string>
		assert.match(String(fiji.geom_wkt), /^MULTIPOLYGON\(/)
		assert.equal(fiji.min_x, -180)
		assert.equal(fiji.max_x, 180)
		assert.ok(Number(fiji.area_deg2) < 20)
		// ... and neither do its R-Tree boxes: one group of islands on each side of the antimeridian.
		const boxes = db
			.prepare('SELECT minX, maxX FROM sources_rtree WHERE id = ? ORDER BY minX')
			.all(fiji.rowid) as Array<{ minX: number; maxX: number }>
		assert.ok(boxes.length >= 2)
		assert.equal(boxes[0].minX, -180)
		assert.equal(boxes[boxes.length - 1].maxX, 180)
		for (const box of boxes) {
			assert.ok(box.maxX - box.minX < 10)
		}
		const findRoads = db.prepare(
			`SELECT DISTINCT s.id FROM sources s INNER JOIN sources_rtree r ON s.rowid = r.id
			WHERE s.id LIKE '%-hot-roads' AND r.maxX >= ? AND r.minX <= ? AND r.maxY >= ? AND r.minY <= ?`,
		)
		// Suva, the other side of the antimeridian, then places that the -180..180 box would catch.
		assert.deepEqual(findRoads.all(178.35, 178.55, -18.2, -18.05), [{ id: 'fj-hot-roads' }])
		assert.deepEqual(findRoads.all(-179.9, -179.5, -17, -16.5), [{ id: 'fj-hot-roads' }])
		assert.deepEqual(findRoads.all(2.2, 2.5, 48.8, 48.95), [])
		// Goma is inside the bounding box of Tanzania: only the exact test on geom_wkt rules it out.
		assert.deepEqual(findRoads.all(29.1, 29.35, -1.75, -1.6), [{ id: 'tz-hot-roads' }])
		assert.deepEqual(findRoads.all(-10, 10, -17, -16.5), [])
		// An explicit bbox wins over everything.
		const lyon = db.prepare("SELECT geom_wkt, area_deg2 FROM sources WHERE id = 'lyon-water'").get() as Record<string, number | string>
		assert.equal(lyon.geom_wkt, 'POLYGON((4.7 45.6, 5.1 45.6, 5.1 45.9, 4.7 45.9, 4.7 45.6))')
		assert.equal(lyon.area_deg2, 0.12)
	})

	test('the healthcheck reads the view and writes the table behind it', function healthcheckLayout() {
		assert.equal(resolveSourcesTableName(db), 'sources_data')
		const rows = selectHealthRows(db)
		assert.equal(rows.length, 6)
		const rain = rows.find(function isRain(row) {
			return row.id === 'daily-rain'
		})
		assert.match(String(rain?.time_json), /"offset_days":-2/)
		db.prepare(`UPDATE ${resolveSourcesTableName(db)} SET health_status = ? WHERE id = ?`).run('dead', 'lyon-water')
		const status = db.prepare("SELECT health_status FROM sources WHERE id = 'lyon-water'").get() as {
			health_status: string
		}
		assert.equal(status.health_status, 'dead')
	})

	test('catalog.json and the manifest are written', function webCatalog() {
		const catalog = JSON.parse(
			fs.readFileSync(path.join(workDir, 'dist', 'catalog.json'), 'utf-8'),
		) as Array<Record<string, unknown>>
		assert.equal(catalog.length, 6)
		const rain = catalog.find(function isRain(item) {
			return item.id === 'daily-rain'
		})
		assert.deepEqual(rain?.time, { offset_days: -2, step: 'day', format: 'YYYY-MM-DD' })
		assert.equal(rain?.layer, null)
		assert.equal(rain?.geometry_type, null)
		const roads = catalog.find(function isRoads(item) {
			return item.id === 'tz-hot-roads'
		})
		assert.equal(roads?.layer, 'roads_lines')
		assert.equal(roads?.geometry_type, 'line')
		assert.equal(roads?.time, null)
		const manifest = JSON.parse(
			fs.readFileSync(path.join(workDir, 'dist', 'manifest.json'), 'utf-8'),
		) as Record<string, unknown>
		assert.equal(manifest.source_count, 6)
		assert.ok(fs.existsSync(path.join(workDir, 'dist', 'catalog.sqlite.gz')))
	})

	test('a duplicate id fails the build', function duplicateId() {
		const sourcesDir = path.join(workDir, 'duplicate-sources')
		fs.mkdirSync(sourcesDir)
		fs.writeFileSync(path.join(sourcesDir, 'a.yaml'), FIXTURE_YAML)
		fs.writeFileSync(path.join(sourcesDir, 'b.yaml'), FIXTURE_YAML)
		assert.throws(function buildDuplicates() {
			buildCatalog({ sourcesDir, distDir: path.join(workDir, 'duplicate-dist'), quiet: true })
		}, /Duplicate source id "world-dem"/)
	})
})
