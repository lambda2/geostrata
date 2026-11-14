import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import zlib from 'node:zlib'
import Database from 'better-sqlite3'
import { parse as parseYaml } from 'yaml'
import {
	type BoundingBox,
	type CatalogEntry,
	type SourceCategory,
	parseCatalogFileContent,
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
	categories: Record<SourceCategory, LanguageSynonyms>
	countries: Record<string, CountrySynonymEntry>
}

interface CountryGeometryEntry {
	bbox: BoundingBox
	geom_wkt: string
}

type CountryPolygonsDictionary = Record<string, CountryGeometryEntry>

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
	health_status: string
}

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

function loadCountryPolygons(assetsDir: string): CountryPolygonsDictionary {
	const polygonsPath = path.join(assetsDir, 'polygons', 'countries.json')
	const rawJson = fs.existsSync(polygonsPath)
		? fs.readFileSync(polygonsPath, 'utf-8')
		: '{}'
	return JSON.parse(rawJson) as CountryPolygonsDictionary
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

function buildEnrichedTags(
	entry: CatalogEntry,
	synonyms: SynonymsDictionary,
): string {
	const categorySynonyms = synonyms.categories[entry.category]
	const combinedTokens: Array<string> = [...entry.tags]

	if (categorySynonyms) {
		combinedTokens.push(
			...categorySynonyms.fr,
			...categorySynonyms.en,
			...categorySynonyms.es,
			...categorySynonyms.ar,
		)
	}

	const deduplicatedTokens = Array.from(new Set(combinedTokens))
	return deduplicatedTokens.join(' ')
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

function compileCatalogEntry(
	entry: CatalogEntry,
	assetsDir: string,
	synonyms: SynonymsDictionary,
	countryPolygons: CountryPolygonsDictionary,
): CompiledCatalogRow {
	const countryGeometry = entry.country_iso
		? countryPolygons[entry.country_iso]
		: undefined

	const resolvedBbox: BoundingBox = entry.bbox
		? entry.bbox
		: countryGeometry
			? countryGeometry.bbox
			: [-180, -90, 180, 90]

	const [minX, minY, maxX, maxY] = resolvedBbox
	const rawArea = (maxX - minX) * (maxY - minY)
	const areaDeg2 = Number(Math.max(0.000001, rawArea).toFixed(6))

	const resolvedWkt = entry.geom_wkt
		? entry.geom_wkt
		: !entry.bbox && countryGeometry
			? countryGeometry.geom_wkt
			: createBoundingBoxWkt(resolvedBbox)

	const styleQml = loadStyleQmlContent(
		assetsDir,
		entry.category,
		entry.style_file,
	)

	const enrichedTags = buildEnrichedTags(entry, synonyms)
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
		health_status: 'ok',
	}
}

function computeFileSha256(filePath: string): string {
	const fileBuffer = fs.readFileSync(filePath)
	return crypto.createHash('sha256').update(fileBuffer).digest('hex')
}

export function buildCatalog(): void {
	const packageRoot = getPackageRootDir()
	const sourcesDir = path.join(packageRoot, 'sources')
	const assetsDir = path.join(packageRoot, 'assets')
	const distDir = path.join(packageRoot, 'dist')

	fs.mkdirSync(distDir, { recursive: true })

	const yamlFiles = collectYamlFiles(sourcesDir)
	if (yamlFiles.length === 0) {
		throw new Error(`No YAML source files found in ${sourcesDir}`)
	}

	const synonyms = loadSynonymsDictionary(assetsDir)
	const countryPolygons = loadCountryPolygons(assetsDir)

	const compiledRows: Array<CompiledCatalogRow> = []
	const seenIds = new Set<string>()

	for (const yamlFilePath of yamlFiles) {
		const relativeFilePath = path.relative(packageRoot, yamlFilePath)
		const rawYamlText = fs.readFileSync(yamlFilePath, 'utf-8')
		const parsedYaml = parseYaml(rawYamlText)
		const entries = parseCatalogFileContent(parsedYaml)

		for (const entry of entries) {
			if (seenIds.has(entry.id)) {
				throw new Error(
					`Duplicate source id "${entry.id}" found in ${relativeFilePath}`,
				)
			}
			seenIds.add(entry.id)
			const compiledRow = compileCatalogEntry(
				entry,
				assetsDir,
				synonyms,
				countryPolygons,
			)
			compiledRows.push(compiledRow)
		}
	}

	const sqlitePath = path.join(distDir, 'catalog.sqlite')
	if (fs.existsSync(sqlitePath)) {
		fs.unlinkSync(sqlitePath)
	}

	const db = new Database(sqlitePath)
	db.pragma('journal_mode = OFF')
	db.pragma('synchronous = OFF')

	db.exec(`
		CREATE TABLE sources (
			rowid INTEGER PRIMARY KEY AUTOINCREMENT,
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
			geom_wkt TEXT NOT NULL,
			tags_json TEXT NOT NULL,
			search_text TEXT NOT NULL DEFAULT '',
			license TEXT NOT NULL,
			attribution TEXT NOT NULL,
			style_qml TEXT,
			min_zoom INTEGER,
			max_zoom INTEGER,
			archive_target TEXT,
			health_status TEXT NOT NULL DEFAULT 'ok'
		);

		CREATE VIRTUAL TABLE sources_rtree USING rtree(
			id,
			minX, maxX,
			minY, maxY
		);

		CREATE VIRTUAL TABLE sources_fts USING fts5(
			name,
			description,
			category,
			enriched_tags,
			country_names,
			tokenize='unicode61 remove_diacritics 2'
		);
	`)

	const insertSourceStmt = db.prepare(`
		INSERT INTO sources (
			id, name, description, i18n_json, type, category, url, crs, country_iso,
			min_x, min_y, max_x, max_y, area_deg2, geom_wkt, tags_json, search_text,
			license, attribution, style_qml, min_zoom, max_zoom,
			archive_target, health_status
		) VALUES (
			@id, @name, @description, @i18n_json, @type, @category, @url, @crs, @country_iso,
			@min_x, @min_y, @max_x, @max_y, @area_deg2, @geom_wkt, @tags_json, @search_text,
			@license, @attribution, @style_qml, @min_zoom, @max_zoom,
			@archive_target, @health_status
		)
	`)

	const insertRtreeStmt = db.prepare(`
		INSERT INTO sources_rtree (id, minX, maxX, minY, maxY)
		VALUES (?, ?, ?, ?, ?)
	`)

	const insertFtsStmt = db.prepare(`
		INSERT INTO sources_fts (rowid, name, description, category, enriched_tags, country_names)
		VALUES (?, ?, ?, ?, ?, ?)
	`)

	const insertAllTransaction = db.transaction(function insertRows(
		rows: Array<CompiledCatalogRow>,
	) {
		for (const row of rows) {
			const info = insertSourceStmt.run(row)
			const rowId = Number(info.lastInsertRowid)
			insertRtreeStmt.run(rowId, row.min_x, row.max_x, row.min_y, row.max_y)
			insertFtsStmt.run(
				rowId,
				row.name,
				row.description,
				row.category,
				row.enriched_tags,
				row.country_names,
			)
		}
	})

	insertAllTransaction(compiledRows)

	db.exec('INSERT INTO sources_fts(sources_fts) VALUES(\'optimize\');')
	db.exec('VACUUM;')
	db.close()

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
		}
	})
	const webCatalogPath = path.join(distDir, 'catalog.json')
	fs.writeFileSync(webCatalogPath, JSON.stringify(webCatalogSummary))

	console.log(
		`Built Geostrata catalog: ${compiledRows.length} sources from ${yamlFiles.length} YAML files.`,
	)
	console.log(`  SQLite:   ${sqlitePath} (${sqliteBuffer.length} bytes)`)
	console.log(`  Gzip:     ${gzipPath} (${gzippedBuffer.length} bytes)`)
	console.log(`  Manifest: ${manifestPath} (sha256: ${sqliteSha256})`)
}

buildCatalog()
