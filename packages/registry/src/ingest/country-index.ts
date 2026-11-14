import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * Regenerates assets/polygons/country-index.json from Natural Earth (public domain).
 * The index is committed: it only changes when a country is created or renamed.
 * Run with `npm run refresh:countries`.
 */

const NATURAL_EARTH_COUNTRIES_URL =
	'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson'

interface NaturalEarthCollection {
	features: Array<{
		bbox?: Array<number>
		properties: Record<string, string | number | null>
	}>
}

interface CountryIndexEntry {
	iso2: string
	name_en: string
	name_fr: string
	bbox: [number, number, number, number]
}

function roundCoordinate(value: number): number {
	return Number(value.toFixed(4))
}

async function refreshCountryIndex(): Promise<void> {
	const response = await fetch(NATURAL_EARTH_COUNTRIES_URL, {
		headers: { 'User-Agent': 'Geostrata-Ingest/1.0' },
	})
	if (!response.ok) {
		throw new Error(`HTTP ${response.status} from ${NATURAL_EARTH_COUNTRIES_URL}`)
	}
	const collection = (await response.json()) as NaturalEarthCollection

	const index: Record<string, CountryIndexEntry> = {}
	for (const feature of collection.features) {
		const props = feature.properties
		const iso3 = String(props.ISO_A3_EH ?? '')
		const iso2 = String(props.ISO_A2_EH ?? '')
		const bbox = feature.bbox
		if (!/^[A-Z]{3}$/.test(iso3) || !/^[A-Z]{2}$/.test(iso2) || !bbox) {
			continue
		}
		// Dependencies sharing their sovereign's code (e.g. Australian territories) keep the first, largest feature.
		if (index[iso3]) {
			continue
		}
		index[iso3] = {
			iso2,
			name_en: String(props.NAME_EN ?? props.NAME),
			name_fr: String(props.NAME_FR ?? props.NAME_EN ?? props.NAME),
			bbox: [
				roundCoordinate(Math.max(-180, bbox[0])),
				roundCoordinate(Math.max(-90, bbox[1])),
				roundCoordinate(Math.min(180, bbox[2])),
				roundCoordinate(Math.min(90, bbox[3])),
			],
		}
	}

	const sortedIndex = Object.fromEntries(
		Object.entries(index).sort(function compareIso3(a, b) {
			return a[0].localeCompare(b[0])
		}),
	)
	const srcDir = path.dirname(fileURLToPath(import.meta.url))
	const outputPath = path.resolve(
		srcDir,
		'..',
		'..',
		'assets',
		'polygons',
		'country-index.json',
	)
	const lines = Object.entries(sortedIndex).map(function formatLine([iso3, entry]) {
		return `\t${JSON.stringify(iso3)}: ${JSON.stringify(entry)}`
	})
	fs.writeFileSync(outputPath, `{\n${lines.join(',\n')}\n}\n`, 'utf-8')
	console.log(
		`Wrote ${Object.keys(sortedIndex).length} countries to ${outputPath}`,
	)
}

refreshCountryIndex()
