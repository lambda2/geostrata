import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { stringify as stringifyYaml } from 'yaml'
import { type CountryIndex } from '../expand.js'
import { type CatalogEntry } from '../schema.js'
import {
	type EliGeoJsonCollection,
	convertEliFeatureToCatalogEntry,
} from './eli.js'
import {
	type CountryReference,
	type GeoBoundariesApiRecord,
	type GeoBoundariesRelease,
	buildGeoBoundariesCatalogEntry,
} from './geoboundaries.js'

const ELI_GEOJSON_URL =
	'https://osmlab.github.io/editor-layer-index/imagery.geojson'

const GEOBOUNDARIES_API_URL = 'https://www.geoboundaries.org/api/current'

const ADMIN_LEVELS: Array<string> = ['ADM1', 'ADM2']

/**
 * Every country published in the gbHumanitarian release (UN OCHA COD-AB) is ingested.
 * Countries listed here are also ingested from gbOpen when they have no COD-AB release.
 */
const ALWAYS_INGESTED_ISO3: Array<string> = ['TUR', 'LBN']

function getPackageRootDir(): string {
	const currentFilePath = fileURLToPath(import.meta.url)
	const ingestDir = path.dirname(currentFilePath)
	return path.resolve(ingestDir, '..', '..')
}

async function ingestEditorLayerIndex(generatedDir: string): Promise<number> {
	const outputPath = path.join(generatedDir, 'eli-imagery.yaml')
	try {
		const response = await fetch(ELI_GEOJSON_URL, {
			headers: { 'User-Agent': 'Geostrata-Ingest/1.0' },
		})
		if (!response.ok) {
			throw new Error(`HTTP ${response.status} from ${ELI_GEOJSON_URL}`)
		}
		const collection = (await response.json()) as EliGeoJsonCollection
		const features = Array.isArray(collection.features)
			? collection.features
			: []

		const entries: Array<CatalogEntry> = []
		const seenIds = new Set<string>()

		for (const feature of features) {
			const props = feature.properties
			const isPreferred =
				Boolean(props.best) ||
				props.category === 'elevation' ||
				props.category === 'map' ||
				props.category === 'osmbasedmap'

			if (!isPreferred) {
				continue
			}

			const converted = convertEliFeatureToCatalogEntry(feature)
			if (!converted || seenIds.has(converted.id)) {
				continue
			}
			seenIds.add(converted.id)
			entries.push(converted)
		}

		fs.writeFileSync(outputPath, stringifyYaml(entries), 'utf-8')
		console.log(
			`Generated ${entries.length} Editor Layer Index (ELI) entries in ${outputPath}`,
		)
		return entries.length
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error)
		console.warn(`ELI remote fetch skipped (${message}).`)
		return 0
	}
}

async function fetchJson<T>(url: string): Promise<T> {
	const response = await fetch(url, {
		headers: { 'User-Agent': 'Geostrata-Ingest/1.0' },
	})
	if (!response.ok) {
		throw new Error(`HTTP ${response.status} from ${url}`)
	}
	return (await response.json()) as T
}

function loadCountryReferences(packageRoot: string): Map<string, CountryReference> {
	const indexPath = path.join(
		packageRoot,
		'assets',
		'polygons',
		'country-index.json',
	)
	const countryIndex = JSON.parse(fs.readFileSync(indexPath, 'utf-8')) as CountryIndex
	const references = new Map<string, CountryReference>()
	for (const [iso3, country] of Object.entries(countryIndex)) {
		references.set(iso3, {
			iso2: country.iso2,
			nameEn: country.name_en,
			nameFr: country.name_fr,
			bbox: country.bbox,
		})
	}
	return references
}

function loadCuratedCountryCodes(packageRoot: string): Set<string> {
	const polygonsPath = path.join(
		packageRoot,
		'assets',
		'polygons',
		'countries.json',
	)
	return fs.existsSync(polygonsPath)
		? new Set(Object.keys(JSON.parse(fs.readFileSync(polygonsPath, 'utf-8'))))
		: new Set()
}

async function ingestGeoBoundaries(
	packageRoot: string,
	generatedDir: string,
): Promise<number> {
	const outputPath = path.join(generatedDir, 'geoboundaries-adm.yaml')
	try {
		const countries = loadCountryReferences(packageRoot)
		const curatedCodes = loadCuratedCountryCodes(packageRoot)
		const entries: Array<CatalogEntry> = []

		for (const level of ADMIN_LEVELS) {
			const byIso3 = new Map<
				string,
				{ record: GeoBoundariesApiRecord; release: GeoBoundariesRelease }
			>()
			const openRecords = await fetchJson<Array<GeoBoundariesApiRecord>>(
				`${GEOBOUNDARIES_API_URL}/gbOpen/ALL/${level}/`,
			)
			for (const record of openRecords) {
				if (ALWAYS_INGESTED_ISO3.includes(record.boundaryISO)) {
					byIso3.set(record.boundaryISO, { record, release: 'gbOpen' })
				}
			}
			const codRecords = await fetchJson<Array<GeoBoundariesApiRecord>>(
				`${GEOBOUNDARIES_API_URL}/gbHumanitarian/ALL/${level}/`,
			)
			for (const record of codRecords) {
				byIso3.set(record.boundaryISO, { record, release: 'gbHumanitarian' })
			}

			for (const [iso3, { record, release }] of byIso3) {
				const country = countries.get(iso3)
				if (!country || !record.gjDownloadURL) {
					continue
				}
				entries.push(
					buildGeoBoundariesCatalogEntry(
						record,
						release,
						country,
						curatedCodes.has(country.iso2),
					),
				)
			}
		}

		if (entries.length === 0) {
			throw new Error('geoBoundaries API returned no usable record')
		}

		entries.sort(function compareIds(a, b) {
			return a.id.localeCompare(b.id)
		})
		fs.writeFileSync(outputPath, stringifyYaml(entries), 'utf-8')
		console.log(
			`Generated ${entries.length} geoBoundaries entries in ${outputPath}`,
		)
		return entries.length
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error)
		console.warn(
			`geoBoundaries remote fetch skipped, keeping the previous file if any (${message}).`,
		)
		return 0
	}
}

export async function runIngest(): Promise<void> {
	const packageRoot = getPackageRootDir()
	const generatedDir = path.join(packageRoot, 'sources', '_generated')
	fs.mkdirSync(generatedDir, { recursive: true })

	await ingestGeoBoundaries(packageRoot, generatedDir)
	await ingestEditorLayerIndex(generatedDir)
}

runIngest()
