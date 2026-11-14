import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import zlib from 'node:zlib'
import Database from 'better-sqlite3'
import { parse as parseYaml } from 'yaml'
import { type CountryIndex, expandCatalogFileContent } from './expand.js'
import {
	type BoundingBox,
	type CatalogEntry,
	type SourceCategory,
	type TimeSettings,
} from './schema.js'

interface LanguageSynonyms {
	fr: Array<string>
	en: Array<string>
	es: Array<string>
	ar: Array<string>
}

interface CountrySynonymEntry {
	names: Array<string>
}

interface SynonymsDictionary {
	categories: Partial<Record<SourceCategory, LanguageSynonyms>>
	countries: Record<string, CountrySynonymEntry>
}

interface CountryGeometryEntry {
	bbox: BoundingBox
	geom_wkt: string
	/** Area of the outline in deg²; the bounding-box area when absent. */
	area_deg2?: number
	/** R-Tree boxes of the outline; the bounding box when absent. */
	boxes?: Array<BoundingBox>
}

/** Keyed by ISO-3166-1 alpha-2 code. */
type CountryPolygonsDictionary = Record<string, CountryGeometryEntry>

/** assets/polygons/country-outlines.json, keyed by ISO-3166-1 alpha-3 code (see ingest/country-index.ts). */
type CountryOutlinesFile = Record<
	string,
	{ bbox: BoundingBox; boxes?: Array<BoundingBox>; area_deg2: number; geom_wkt: string }
>

interface CompiledCatalogRow {
	id: string
	name: string
	description: string
	i18n_json: string
	type: string
	category: SourceCategory
	url: string
	crs: string
	country_iso: string | null
	min_x: number
	min_y: number
	max_x: number
	max_y: number
	area_deg2: number
	geom_wkt: string
	tags_json: string
	enriched_tags: string
	country_names: string
	search_text: string
	license: string
	attribution: string
	style_qml: string | null
	min_zoom: number | null
	max_zoom: number | null
	archive_target: string | null
	layer: string | null
	geometry_type: string | null
	time_json: string | null
	priority: number
	data_date: string | null
	health_status: string
	health_checked_at: string | null
	/** Boxes indexed in the R-Tree: the bounding box, or the boxes of a country in several pieces. */
	rtree_boxes: Array<BoundingBox>
	/** Pieces of `enriched_tags` and `search_text` owned by the row, see splitSharedTexts(). */
	fts_tags: string
	fts_i18n: string
	search_local: string
	/** Pieces shared with other rows, stored once in `search_terms`. */
	synonym_terms: SharedSearchTerms
	country_terms: SharedSearchTerms
}

interface HealthReport {
	checked_at: string
	results: Record<string, { status: string; detail?: string }>
}

export interface BuildOptions {
	/** Directory of the YAML sources (default: <package>/sources). */
	sourcesDir?: string
	/** Directory of styles, synonyms and country polygons (default: <package>/assets). */
	assetsDir?: string
	/** Output directory (default: $GEOSTRATA_DIST_DIR, else <package>/dist). */
	distDir?: string
	/** Skips the console summary. */
	quiet?: boolean
}

export interface BuildResult {
	sourceCount: number
	sqlitePath: string
	gzipPath: string
	manifestPath: string
	webCatalogPath: string
}

/** Stored in `PRAGMA user_version`: 2 = `sources` is a view over `sources_data` and its lookup tables. */
export const CATALOG_LAYOUT_VERSION = 2

/** Output of `npm run ingest`, ignored by git and rebuilt in CI. */
const GENERATED_DIR_NAME = '_generated'

function getPackageRootDir(): string {
	const currentFilePath = fileURLToPath(import.meta.url)
	const srcDir = path.dirname(currentFilePath)
	return path.resolve(srcDir, '..')
}

function collectYamlFiles(dirPath: string): Array<string> {
	const collectedFiles: Array<string> = []
	const directoryEntries = fs.existsSync(dirPath)
		? fs.readdirSync(dirPath, { withFileTypes: true })
		: []

	for (const dirEntry of directoryEntries) {
		const fullPath = path.join(dirPath, dirEntry.name)
		if (dirEntry.isDirectory()) {
			const nestedFiles = collectYamlFiles(fullPath)
			for (const nestedFile of nestedFiles) {
				collectedFiles.push(nestedFile)
			}
		} else if (
			dirEntry.isFile() &&
			(dirEntry.name.endsWith('.yaml') || dirEntry.name.endsWith('.yml'))
		) {
			collectedFiles.push(fullPath)
		}
	}

	return collectedFiles.sort()
}

function createBoundingBoxWkt(bbox: BoundingBox): string {
	const [minX, minY, maxX, maxY] = bbox
	return `POLYGON((${minX} ${minY}, ${maxX} ${minY}, ${maxX} ${maxY}, ${minX} ${maxY}, ${minX} ${minY}))`
}

function loadSynonymsDictionary(assetsDir: string): SynonymsDictionary {
	const synonymsPath = path.join(assetsDir, 'i18n', 'synonyms.json')
	const rawJson = fs.readFileSync(synonymsPath, 'utf-8')
	return JSON.parse(rawJson) as SynonymsDictionary
}

function loadCountryIndex(assetsDir: string): CountryIndex {
	const indexPath = path.join(assetsDir, 'polygons', 'country-index.json')
	return JSON.parse(fs.readFileSync(indexPath, 'utf-8')) as CountryIndex
}

function loadCountryOutlines(assetsDir: string): CountryOutlinesFile {
	const outlinesPath = path.join(assetsDir, 'polygons', 'country-outlines.json')
	return fs.existsSync(outlinesPath)
		? (JSON.parse(fs.readFileSync(outlinesPath, 'utf-8')) as CountryOutlinesFile)
		: {}
}

/**
 * Footprint of every country, keyed by alpha-2 code: the generated outline of
 * country-outlines.json, else the bounding box of the country index.
 */
function loadCountryGeometries(
	assetsDir: string,
	countryIndex: CountryIndex,
): CountryPolygonsDictionary {
	const outlines = loadCountryOutlines(assetsDir)
	const geometries: CountryPolygonsDictionary = {}
	for (const [iso3, country] of Object.entries(countryIndex)) {
		const outline = outlines[iso3]
		geometries[country.iso2] = outline
			? {
					bbox: outline.bbox,
					geom_wkt: outline.geom_wkt,
					area_deg2: outline.area_deg2,
					boxes: outline.boxes,
				}
			: { bbox: country.bbox, geom_wkt: createBoundingBoxWkt(country.bbox) }
	}
	return geometries
}

/** Written by `npm run healthcheck`; absent on a first build, in which case every source is assumed ok. */
function loadHealthReport(distDir: string): HealthReport | null {
	const healthPath = path.join(distDir, 'health.json')
	if (!fs.existsSync(healthPath)) {
		return null
	}
	try {
		return JSON.parse(fs.readFileSync(healthPath, 'utf-8')) as HealthReport
	} catch {
		return null
	}
}

function loadStyleQmlContent(
	assetsDir: string,
	category: SourceCategory,
	customStyleFile: string | undefined,
): string | null {
	const fileName = customStyleFile ? customStyleFile : `${category}.qml`
	const styleFilePath = path.join(assetsDir, 'styles', fileName)
	return fs.existsSync(styleFilePath)
		? fs.readFileSync(styleFilePath, 'utf-8').trim()
		: null
}

/** Synonyms of the entry's category in every language, minus the words already in its tags. */
function buildSynonymTokens(
	entry: CatalogEntry,
	synonyms: SynonymsDictionary,
	tagTokens: Array<string>,
): Array<string> {
	const categorySynonyms = synonyms.categories[entry.category]
	if (!categorySynonyms) {
		return []
	}
	const knownTokens = new Set(tagTokens)
	const synonymTokens: Array<string> = []
	for (const token of [
		...categorySynonyms.fr,
		...categorySynonyms.en,
		...categorySynonyms.es,
		...categorySynonyms.ar,
	]) {
		if (!knownTokens.has(token)) {
			knownTokens.add(token)
			synonymTokens.push(token)
		}
	}
	return synonymTokens
}

function stripDiacritics(input: string): string {
	return input
		.normalize('NFD')
		.replace(/[\u0300-\u036f]/g, '')
		.toLowerCase()
		.replace(/[^a-z0-9\u0600-\u06ff]+/g, ' ')
		.trim()
}

function buildCountryNames(
	countryIso: string | undefined,
	synonyms: SynonymsDictionary,
): string {
	if (!countryIso) {
		return ''
	}
	const countryEntry = synonyms.countries[countryIso]
	const namesList: Array<string> = countryEntry
		? [countryIso, ...countryEntry.names]
		: [countryIso]
	return namesList.join(' ')
}

/** One row of `search_terms`: a text as indexed by FTS5 and its normalized form for the LIKE fallback. */
interface SharedSearchTerms {
	fts_text: string
	like_text: string
}

interface SharedTextSplit {
	ftsTags: string
	ftsI18n: string
	searchLocal: string
	synonymTerms: SharedSearchTerms
	countryTerms: SharedSearchTerms
}

function prefixWithSpace(text: string): string {
	return text.length > 0 ? ` ${text}` : ''
}

/**
 * `enriched_tags` (FTS5) and `search_text` (LIKE fallback) both embed the synonyms of the
 * category and the names of the country, identical for hundreds of rows. They are cut into
 * a part owned by the row and two parts stored once in `search_terms`; the `sources` and
 * `sources_fts_content` views glue them back together:
 *
 *   enriched_tags = fts_tags || synonyms.fts_text || fts_i18n
 *   country_names = country.fts_text
 *   search_text   = ' ' || search_local || synonyms.like_text || country.like_text || ' '
 *
 * A row whose texts cannot be cut that way (unusual whitespace in a tag) keeps them whole.
 */
function splitSharedTexts(
	entry: CatalogEntry,
	i18nParts: Array<string>,
	tagTokens: Array<string>,
	synonymTokens: Array<string>,
	countryNames: string,
	enrichedTags: string,
	searchText: string,
): SharedTextSplit {
	const synonymTerms: SharedSearchTerms = {
		fts_text: prefixWithSpace(synonymTokens.join(' ')),
		like_text: prefixWithSpace(stripDiacritics(synonymTokens.join(' '))),
	}
	const countryTerms: SharedSearchTerms = {
		fts_text: countryNames,
		like_text: prefixWithSpace(stripDiacritics(countryNames)),
	}
	const ftsTags = tagTokens.join(' ')
	const ftsI18n = prefixWithSpace(i18nParts.join(' '))
	const searchLocal = stripDiacritics(
		[
			entry.id,
			entry.name,
			entry.description ? entry.description : '',
			...i18nParts,
			entry.category,
			entry.type,
			tagTokens.join(' '),
		].join(' '),
	)
	const isSplitExact =
		ftsTags + synonymTerms.fts_text + ftsI18n === enrichedTags &&
		` ${searchLocal}${synonymTerms.like_text}${countryTerms.like_text} ` === searchText

	if (isSplitExact) {
		return { ftsTags, ftsI18n, searchLocal, synonymTerms, countryTerms }
	}
	return {
		ftsTags: enrichedTags,
		ftsI18n: '',
		searchLocal: searchText.slice(1, -1),
		synonymTerms: { fts_text: '', like_text: '' },
		countryTerms: { fts_text: countryNames, like_text: '' },
	}
}

function computeBoundingBoxArea(bbox: BoundingBox): number {
	const [minX, minY, maxX, maxY] = bbox
	return (maxX - minX) * (maxY - minY)
}

function compileCatalogEntry(
	entry: CatalogEntry,
	assetsDir: string,
	synonyms: SynonymsDictionary,
	countryPolygons: CountryPolygonsDictionary,
	healthReport: HealthReport | null,
): CompiledCatalogRow {
	// An explicit geom_wkt or bbox always wins over the country outline.
	const countryGeometry =
		entry.country_iso && !entry.bbox && !entry.geom_wkt
			? countryPolygons[entry.country_iso]
			: undefined

	const resolvedBbox: BoundingBox = entry.bbox
		? entry.bbox
		: countryGeometry
			? countryGeometry.bbox
			: [-180, -90, 180, 90]

	const [minX, minY, maxX, maxY] = resolvedBbox
	// The outline area keeps a country that crosses the antimeridian (bbox -180..180) or owns
	// distant islands from being ranked as a continent-wide layer.
	const rawArea =
		countryGeometry?.area_deg2 !== undefined
			? Math.min(countryGeometry.area_deg2, computeBoundingBoxArea(resolvedBbox))
			: computeBoundingBoxArea(resolvedBbox)
	const areaDeg2 = Number(Math.max(0.000001, rawArea).toFixed(6))

	const resolvedWkt = entry.geom_wkt
		? entry.geom_wkt
		: countryGeometry
			? countryGeometry.geom_wkt
			: createBoundingBoxWkt(resolvedBbox)

	const styleQml = loadStyleQmlContent(
		assetsDir,
		entry.category,
		entry.style_file,
	)

	const tagTokens = Array.from(new Set(entry.tags))
	const synonymTokens = buildSynonymTokens(entry, synonyms, tagTokens)
	const enrichedTags = [...tagTokens, ...synonymTokens].join(' ')
	const countryNames = buildCountryNames(entry.country_iso, synonyms)
	const i18nParts: Array<string> = []
	if (entry.i18n) {
		for (const loc of Object.values(entry.i18n)) {
			if (loc.name) {
				i18nParts.push(loc.name)
			}
			if (loc.description) {
				i18nParts.push(loc.description)
			}
		}
	}
	const combinedEnrichedTags = [enrichedTags, ...i18nParts].join(' ').trim()

	const rawSearchable = [
		entry.id,
		entry.name,
		entry.description ? entry.description : '',
		...i18nParts,
		entry.category,
		entry.type,
		enrichedTags,
		countryNames,
	].join(' ')
	const searchText = ` ${stripDiacritics(rawSearchable)} `
	const sharedTexts = splitSharedTexts(
		entry,
		i18nParts,
		tagTokens,
		synonymTokens,
		countryNames,
		combinedEnrichedTags,
		searchText,
	)

	return {
		id: entry.id,
		name: entry.name,
		description: entry.description ? entry.description : '',
		i18n_json: JSON.stringify(entry.i18n ? entry.i18n : {}),
		type: entry.type,
		category: entry.category,
		url: entry.url.trim(),
		crs: entry.crs,
		country_iso: entry.country_iso ? entry.country_iso : null,
		min_x: minX,
		min_y: minY,
		max_x: maxX,
		max_y: maxY,
		area_deg2: areaDeg2,
		geom_wkt: resolvedWkt,
		tags_json: JSON.stringify(entry.tags),
		enriched_tags: combinedEnrichedTags,
		country_names: countryNames,
		search_text: searchText,
		license: entry.license,
		attribution: entry.attribution,
		style_qml: styleQml,
		min_zoom: entry.min_zoom !== undefined ? entry.min_zoom : null,
		max_zoom: entry.max_zoom !== undefined ? entry.max_zoom : null,
		archive_target: entry.archive_target ? entry.archive_target : null,
		layer: entry.layer !== undefined ? entry.layer : null,
		geometry_type: entry.geometry_type ? entry.geometry_type : null,
		time_json: entry.time ? JSON.stringify(entry.time) : null,
		priority: entry.priority,
		data_date: entry.data_date ? entry.data_date : null,
		health_status: healthReport?.results[entry.id]?.status ?? 'ok',
		health_checked_at:
			healthReport && healthReport.results[entry.id]
				? healthReport.checked_at
				: null,
		rtree_boxes:
			countryGeometry?.boxes && countryGeometry.boxes.length > 0
				? countryGeometry.boxes
				: [resolvedBbox],
		fts_tags: sharedTexts.ftsTags,
		fts_i18n: sharedTexts.ftsI18n,
		search_local: sharedTexts.searchLocal,
		synonym_terms: sharedTexts.synonymTerms,
		country_terms: sharedTexts.countryTerms,
	}
}

function computeFileSha256(filePath: string): string {
	const fileBuffer = fs.readFileSync(filePath)
	return crypto.createHash('sha256').update(fileBuffer).digest('hex')
}

/** Reads and validates every YAML file of `sourcesDir`, in a stable order. */
export function loadCatalogEntries(
	sourcesDir: string,
	countryIndex: CountryIndex,
): { entries: Array<CatalogEntry>; fileCount: number } {
	const yamlFiles = collectYamlFiles(sourcesDir)
	if (yamlFiles.length === 0) {
		throw new Error(`No YAML source files found in ${sourcesDir}`)
	}
	const allEntries: Array<CatalogEntry> = []
	const seenIds = new Set<string>()
	for (const yamlFilePath of yamlFiles) {
		const relativeFilePath = path.relative(path.dirname(sourcesDir), yamlFilePath)
		const parsedYaml = parseYaml(fs.readFileSync(yamlFilePath, 'utf-8'))
		let entries: Array<CatalogEntry>
		try {
			entries = expandCatalogFileContent(parsedYaml, countryIndex)
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error)
			throw new Error(`${relativeFilePath}: ${message}`)
		}
		for (const entry of entries) {
			if (seenIds.has(entry.id)) {
				throw new Error(
					`Duplicate source id "${entry.id}" found in ${relativeFilePath}`,
				)
			}
			seenIds.add(entry.id)
			allEntries.push(entry)
		}
	}
	return { entries: allEntries, fileCount: yamlFiles.length }
}

/**
 * Layout 2. The rows live in `sources_data`; texts repeated across rows are stored once:
 * `styles` (QML), `footprints` (WKT) and `search_terms` (category synonyms + country names).
 * `sources` is a view with the columns of the former `sources` table, so a reader written
 * for layout 1 (including its joins on `sources.rowid`) works unchanged. Lookups are scalar
 * subqueries: a column that is not selected costs nothing and the planner only has to
 * order `sources_data`, the R-Tree and the FTS index, as before.
 *
 * `sources_rtree` is a view too, with the columns of the former R-Tree table: a source has
 * one box in `footprint_rtree`, or several when its country is in distant pieces or cut by
 * the antimeridian, and `footprint_boxes` says which source a box belongs to. A reader
 * joining `sources_rtree` therefore gets a source once per box that meets its rectangle.
 */
const CATALOG_SCHEMA_SQL = `
	CREATE TABLE styles (
		id INTEGER PRIMARY KEY,
		qml TEXT NOT NULL
	);

	CREATE TABLE footprints (
		id INTEGER PRIMARY KEY,
		wkt TEXT NOT NULL
	);

	CREATE TABLE search_terms (
		id INTEGER PRIMARY KEY,
		fts_text TEXT NOT NULL,
		like_text TEXT NOT NULL
	);

	CREATE TABLE sources_data (
		rowid INTEGER PRIMARY KEY,
		id TEXT NOT NULL UNIQUE,
		name TEXT NOT NULL,
		description TEXT,
		i18n_json TEXT NOT NULL DEFAULT '{}',
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
		footprint_id INTEGER NOT NULL REFERENCES footprints(id),
		tags_json TEXT NOT NULL,
		license TEXT NOT NULL,
		attribution TEXT NOT NULL,
		style_id INTEGER REFERENCES styles(id),
		min_zoom INTEGER,
		max_zoom INTEGER,
		archive_target TEXT,
		layer TEXT,
		geometry_type TEXT,
		time_json TEXT,
		priority INTEGER NOT NULL DEFAULT 0,
		data_date TEXT,
		health_status TEXT NOT NULL DEFAULT 'ok',
		health_checked_at TEXT,
		synonym_terms_id INTEGER NOT NULL REFERENCES search_terms(id),
		country_terms_id INTEGER NOT NULL REFERENCES search_terms(id),
		search_local TEXT NOT NULL,
		fts_tags TEXT NOT NULL,
		fts_i18n TEXT NOT NULL DEFAULT ''
	);

	CREATE VIEW sources AS
	SELECT
		d.rowid AS rowid,
		d.id AS id,
		d.name AS name,
		d.description AS description,
		d.i18n_json AS i18n_json,
		d.type AS type,
		d.category AS category,
		d.url AS url,
		d.crs AS crs,
		d.country_iso AS country_iso,
		d.min_x AS min_x,
		d.min_y AS min_y,
		d.max_x AS max_x,
		d.max_y AS max_y,
		d.area_deg2 AS area_deg2,
		(SELECT f.wkt FROM footprints f WHERE f.id = d.footprint_id) AS geom_wkt,
		d.tags_json AS tags_json,
		' ' || d.search_local
			|| (SELECT t.like_text FROM search_terms t WHERE t.id = d.synonym_terms_id)
			|| (SELECT t.like_text FROM search_terms t WHERE t.id = d.country_terms_id)
			|| ' ' AS search_text,
		d.license AS license,
		d.attribution AS attribution,
		(SELECT y.qml FROM styles y WHERE y.id = d.style_id) AS style_qml,
		d.min_zoom AS min_zoom,
		d.max_zoom AS max_zoom,
		d.archive_target AS archive_target,
		d.priority AS priority,
		d.data_date AS data_date,
		d.health_status AS health_status,
		d.health_checked_at AS health_checked_at,
		d.layer AS layer,
		d.geometry_type AS geometry_type,
		d.time_json AS time_json
	FROM sources_data d;

	CREATE VIEW sources_fts_content AS
	SELECT
		d.rowid AS rowid,
		d.name AS name,
		d.description AS description,
		d.category AS category,
		d.fts_tags
			|| (SELECT t.fts_text FROM search_terms t WHERE t.id = d.synonym_terms_id)
			|| d.fts_i18n AS enriched_tags,
		(SELECT t.fts_text FROM search_terms t WHERE t.id = d.country_terms_id) AS country_names
	FROM sources_data d;

	CREATE VIRTUAL TABLE footprint_rtree USING rtree(
		id,
		minX, maxX,
		minY, maxY
	);

	CREATE TABLE footprint_boxes (
		box_id INTEGER PRIMARY KEY,
		source_rowid INTEGER NOT NULL REFERENCES sources_data(rowid)
	);

	CREATE INDEX footprint_boxes_source ON footprint_boxes (source_rowid);

	CREATE VIEW sources_rtree AS
	SELECT
		o.source_rowid AS id,
		b.minX AS minX,
		b.maxX AS maxX,
		b.minY AS minY,
		b.maxY AS maxY
	FROM footprint_rtree b
	INNER JOIN footprint_boxes o ON o.box_id = b.id;

	CREATE VIRTUAL TABLE sources_fts USING fts5(
		name,
		description,
		category,
		enriched_tags,
		country_names,
		content='sources_fts_content',
		content_rowid='rowid',
		tokenize='unicode61 remove_diacritics 2'
	);
`

function writeCatalogDatabase(sqlitePath: string, rows: Array<CompiledCatalogRow>): void {
	if (fs.existsSync(sqlitePath)) {
		fs.unlinkSync(sqlitePath)
	}

	const db = new Database(sqlitePath)
	db.pragma('journal_mode = OFF')
	db.pragma('synchronous = OFF')
	db.pragma(`user_version = ${CATALOG_LAYOUT_VERSION}`)
	db.exec(CATALOG_SCHEMA_SQL)

	const insertStyleStmt = db.prepare('INSERT INTO styles (qml) VALUES (?)')
	const insertFootprintStmt = db.prepare('INSERT INTO footprints (wkt) VALUES (?)')
	const insertSearchTermsStmt = db.prepare(
		'INSERT INTO search_terms (fts_text, like_text) VALUES (?, ?)',
	)
	const insertSourceStmt = db.prepare(`
		INSERT INTO sources_data (
			id, name, description, i18n_json, type, category, url, crs, country_iso,
			min_x, min_y, max_x, max_y, area_deg2, footprint_id, tags_json,
			license, attribution, style_id, min_zoom, max_zoom,
			archive_target, layer, geometry_type, time_json,
			priority, data_date, health_status, health_checked_at,
			synonym_terms_id, country_terms_id, search_local, fts_tags, fts_i18n
		) VALUES (
			@id, @name, @description, @i18n_json, @type, @category, @url, @crs, @country_iso,
			@min_x, @min_y, @max_x, @max_y, @area_deg2, @footprint_id, @tags_json,
			@license, @attribution, @style_id, @min_zoom, @max_zoom,
			@archive_target, @layer, @geometry_type, @time_json,
			@priority, @data_date, @health_status, @health_checked_at,
			@synonym_terms_id, @country_terms_id, @search_local, @fts_tags, @fts_i18n
		)
	`)
	const insertRtreeStmt = db.prepare(`
		INSERT INTO footprint_rtree (minX, maxX, minY, maxY)
		VALUES (?, ?, ?, ?)
	`)
	const insertBoxOwnerStmt = db.prepare(
		'INSERT INTO footprint_boxes (box_id, source_rowid) VALUES (?, ?)',
	)

	// Each distinct style, footprint and set of shared search terms is inserted once.
	const lookupIds = new Map<string, number>()
	function internText(key: string, insert: () => Database.RunResult): number {
		const knownId = lookupIds.get(key)
		if (knownId !== undefined) {
			return knownId
		}
		const newId = Number(insert().lastInsertRowid)
		lookupIds.set(key, newId)
		return newId
	}

	function internSearchTerms(terms: SharedSearchTerms): number {
		return internText(
			['terms', terms.fts_text, terms.like_text].join('\u0000'),
			function insertSearchTerms() {
				return insertSearchTermsStmt.run(terms.fts_text, terms.like_text)
			},
		)
	}

	const insertAllTransaction = db.transaction(function insertRows(
		allRows: Array<CompiledCatalogRow>,
	) {
		for (const row of allRows) {
			const styleQml = row.style_qml
			const styleId =
				styleQml === null
					? null
					: internText(`style\u0000${styleQml}`, function insertStyle() {
							return insertStyleStmt.run(styleQml)
						})
			const footprintId = internText(
				`footprint\u0000${row.geom_wkt}`,
				function insertFootprint() {
					return insertFootprintStmt.run(row.geom_wkt)
				},
			)
			const synonymTermsId = internSearchTerms(row.synonym_terms)
			const countryTermsId = internSearchTerms(row.country_terms)
			const info = insertSourceStmt.run({
				id: row.id,
				name: row.name,
				description: row.description,
				i18n_json: row.i18n_json,
				type: row.type,
				category: row.category,
				url: row.url,
				crs: row.crs,
				country_iso: row.country_iso,
				min_x: row.min_x,
				min_y: row.min_y,
				max_x: row.max_x,
				max_y: row.max_y,
				area_deg2: row.area_deg2,
				footprint_id: footprintId,
				tags_json: row.tags_json,
				license: row.license,
				attribution: row.attribution,
				style_id: styleId,
				min_zoom: row.min_zoom,
				max_zoom: row.max_zoom,
				archive_target: row.archive_target,
				layer: row.layer,
				geometry_type: row.geometry_type,
				time_json: row.time_json,
				priority: row.priority,
				data_date: row.data_date,
				health_status: row.health_status,
				health_checked_at: row.health_checked_at,
				synonym_terms_id: synonymTermsId,
				country_terms_id: countryTermsId,
				search_local: row.search_local,
				fts_tags: row.fts_tags,
				fts_i18n: row.fts_i18n,
			})
			for (const [minX, minY, maxX, maxY] of row.rtree_boxes) {
				const boxInfo = insertRtreeStmt.run(minX, maxX, minY, maxY)
				insertBoxOwnerStmt.run(boxInfo.lastInsertRowid, info.lastInsertRowid)
			}
		}
	})

	insertAllTransaction(rows)

	// External-content FTS5: the index is built from the view and the texts are not stored twice.
	db.exec("INSERT INTO sources_fts(sources_fts) VALUES('rebuild');")
	db.exec("INSERT INTO sources_fts(sources_fts) VALUES('optimize');")
	db.exec('VACUUM;')
	db.close()
}

export function buildCatalog(options: BuildOptions = {}): BuildResult {
	const packageRoot = getPackageRootDir()
	const sourcesDir = options.sourcesDir ?? path.join(packageRoot, 'sources')
	const assetsDir = options.assetsDir ?? path.join(packageRoot, 'assets')
	// GEOSTRATA_DIST_DIR lets parallel builds (tests, CI matrix) write to separate directories.
	const distDir = options.distDir
		? path.resolve(options.distDir)
		: process.env.GEOSTRATA_DIST_DIR
			? path.resolve(process.env.GEOSTRATA_DIST_DIR)
			: path.join(packageRoot, 'dist')

	fs.mkdirSync(distDir, { recursive: true })

	const synonyms = loadSynonymsDictionary(assetsDir)
	const countryIndex = loadCountryIndex(assetsDir)
	const countryPolygons = loadCountryGeometries(assetsDir, countryIndex)
	const healthReport = loadHealthReport(distDir)

	const { entries, fileCount } = loadCatalogEntries(sourcesDir, countryIndex)
	if (!options.quiet && !fs.existsSync(path.join(sourcesDir, GENERATED_DIR_NAME))) {
		console.warn(
			`No sources/${GENERATED_DIR_NAME}/ directory: run "npm run ingest" to add the generated families (OSM Editor Layer Index, geoBoundaries).`,
		)
	}

	const compiledRows = entries.map(function compileEntry(entry) {
		return compileCatalogEntry(entry, assetsDir, synonyms, countryPolygons, healthReport)
	})

	const sqlitePath = path.join(distDir, 'catalog.sqlite')
	writeCatalogDatabase(sqlitePath, compiledRows)

	const sqliteBuffer = fs.readFileSync(sqlitePath)
	const gzippedBuffer = zlib.gzipSync(sqliteBuffer, { level: 9 })
	const gzipPath = path.join(distDir, 'catalog.sqlite.gz')
	fs.writeFileSync(gzipPath, gzippedBuffer)

	const sqliteSha256 = computeFileSha256(sqlitePath)
	const gzipSha256 = computeFileSha256(gzipPath)

	const manifest = {
		version: '1.0.0',
		generated_at: new Date().toISOString(),
		source_count: compiledRows.length,
		sqlite_file: 'catalog.sqlite',
		gzip_file: 'catalog.sqlite.gz',
		sha256: sqliteSha256,
		gzip_sha256: gzipSha256,
	}

	const manifestPath = path.join(distDir, 'manifest.json')
	fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, '\t')}\n`)

	const webCatalogSummary = compiledRows.map(function mapWebCatalogItem(row) {
		return {
			id: row.id,
			name: row.name,
			description: row.description,
			i18n: JSON.parse(row.i18n_json) as Record<
				string,
				{ name?: string; description?: string }
			>,
			type: row.type,
			category: row.category,
			url: row.url,
			crs: row.crs,
			country_iso: row.country_iso,
			bbox: [row.min_x, row.min_y, row.max_x, row.max_y],
			area_deg2: row.area_deg2,
			tags: JSON.parse(row.tags_json) as Array<string>,
			license: row.license,
			attribution: row.attribution,
			layer: row.layer,
			geometry_type: row.geometry_type,
			time: row.time_json ? (JSON.parse(row.time_json) as TimeSettings) : null,
			priority: row.priority,
			data_date: row.data_date,
			health_status: row.health_status,
			health_checked_at: row.health_checked_at,
		}
	})
	const webCatalogPath = path.join(distDir, 'catalog.json')
	fs.writeFileSync(webCatalogPath, JSON.stringify(webCatalogSummary))

	if (!options.quiet) {
		console.log(
			`Built Geostrata catalog: ${compiledRows.length} sources from ${fileCount} YAML files.`,
		)
		console.log(`  SQLite:   ${sqlitePath} (${sqliteBuffer.length} bytes)`)
		console.log(`  Gzip:     ${gzipPath} (${gzippedBuffer.length} bytes)`)
		console.log(`  Manifest: ${manifestPath} (sha256: ${sqliteSha256})`)
	}

	return {
		sourceCount: compiledRows.length,
		sqlitePath,
		gzipPath,
		manifestPath,
		webCatalogPath,
	}
}

/** True when this module is the script being run (`tsx src/build.ts`), false when imported. */
function isMainModule(): boolean {
	return Boolean(process.argv[1]) && import.meta.url === pathToFileURL(process.argv[1]).href
}

if (isMainModule()) {
	buildCatalog()
}
