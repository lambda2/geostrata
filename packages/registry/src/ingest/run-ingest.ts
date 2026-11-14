import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { stringify as stringifyYaml } from 'yaml'
import { type CatalogEntry } from '../schema.js'
import {
	type EliGeoJsonCollection,
	convertEliFeatureToCatalogEntry,
} from './eli.js'
import {
	type FieldMapsLayerDescriptor,
	buildFieldMapsCatalogEntry,
} from './fieldmaps.js'

const ELI_GEOJSON_URL =
	'https://osmlab.github.io/editor-layer-index/imagery.geojson'

const HUMANITARIAN_COUNTRIES: Array<FieldMapsLayerDescriptor> = [
	{ admLevel: 1, iso2: 'TR', iso3: 'TUR', countryNameEn: 'Turkey', countryNameFr: 'Turquie' },
	{ admLevel: 2, iso2: 'TR', iso3: 'TUR', countryNameEn: 'Turkey', countryNameFr: 'Turquie' },
	{ admLevel: 1, iso2: 'UA', iso3: 'UKR', countryNameEn: 'Ukraine', countryNameFr: 'Ukraine' },
	{ admLevel: 2, iso2: 'UA', iso3: 'UKR', countryNameEn: 'Ukraine', countryNameFr: 'Ukraine' },
	{ admLevel: 1, iso2: 'MA', iso3: 'MAR', countryNameEn: 'Morocco', countryNameFr: 'Maroc' },
	{ admLevel: 1, iso2: 'SN', iso3: 'SEN', countryNameEn: 'Senegal', countryNameFr: 'Sénégal' },
	{ admLevel: 1, iso2: 'CI', iso3: 'CIV', countryNameEn: 'Ivory Coast', countryNameFr: 'Côte d Ivoire' },
	{ admLevel: 1, iso2: 'CD', iso3: 'COD', countryNameEn: 'DR Congo', countryNameFr: 'RD Congo' },
	{ admLevel: 2, iso2: 'CD', iso3: 'COD', countryNameEn: 'DR Congo', countryNameFr: 'RD Congo' },
	{ admLevel: 1, iso2: 'LR', iso3: 'LBR', countryNameEn: 'Liberia', countryNameFr: 'Liberia' },
	{ admLevel: 2, iso2: 'LR', iso3: 'LBR', countryNameEn: 'Liberia', countryNameFr: 'Liberia' },
	{ admLevel: 1, iso2: 'HT', iso3: 'HTI', countryNameEn: 'Haiti', countryNameFr: 'Haïti' },
	{ admLevel: 2, iso2: 'HT', iso3: 'HTI', countryNameEn: 'Haiti', countryNameFr: 'Haïti' },
	{ admLevel: 1, iso2: 'LB', iso3: 'LBN', countryNameEn: 'Lebanon', countryNameFr: 'Liban' },
	{ admLevel: 2, iso2: 'LB', iso3: 'LBN', countryNameEn: 'Lebanon', countryNameFr: 'Liban' },
	{ admLevel: 1, iso2: 'SY', iso3: 'SYR', countryNameEn: 'Syria', countryNameFr: 'Syrie' },
	{ admLevel: 2, iso2: 'SY', iso3: 'SYR', countryNameEn: 'Syria', countryNameFr: 'Syrie' },
	{ admLevel: 1, iso2: 'MG', iso3: 'MDG', countryNameEn: 'Madagascar', countryNameFr: 'Madagascar' },
	{ admLevel: 2, iso2: 'MG', iso3: 'MDG', countryNameEn: 'Madagascar', countryNameFr: 'Madagascar' },
]

function getPackageRootDir(): string {
	const currentFilePath = fileURLToPath(import.meta.url)
	const ingestDir = path.dirname(currentFilePath)
	return path.resolve(ingestDir, '..', '..')
}

async function ingestEditorLayerIndex(thematicDir: string): Promise<number> {
	const outputPath = path.join(thematicDir, 'eli-imagery.yaml')
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

export async function runIngest(): Promise<void> {
	const packageRoot = getPackageRootDir()
	const thematicDir = path.join(packageRoot, 'sources', 'thematic')
	fs.mkdirSync(thematicDir, { recursive: true })

	const entries: Array<CatalogEntry> = []
	for (const descriptor of HUMANITARIAN_COUNTRIES) {
		entries.push(buildFieldMapsCatalogEntry(descriptor))
	}

	const outputPath = path.join(thematicDir, 'fieldmaps-cod.yaml')
	const yamlContent = stringifyYaml(entries)
	fs.writeFileSync(outputPath, yamlContent, 'utf-8')
	console.log(
		`Generated ${entries.length} ingested FieldMaps/HDX entries in ${outputPath}`,
	)

	await ingestEditorLayerIndex(thematicDir)
}

runIngest()
