import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
	type CountryOutline,
	type GeoJsonPolygonalGeometry,
	buildCountryOutline,
} from './country-outlines.js'

/**
 * Regenerates, from Natural Earth 1:10m (public domain):
 *   - assets/polygons/country-index.json: codes, names and bounding box of every country,
 *   - assets/polygons/country-outlines.json: its simplified outline (see country-outlines.ts).
 * Both files are committed: they only change when a country is created, renamed or redrawn.
 * Run with `npm run refresh:countries`.
 */

const NATURAL_EARTH_COUNTRIES_URL =
	'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson'

interface NaturalEarthCollection {
	features: Array<{
		bbox?: Array<number>
		properties: Record<string, string | number | null>
		geometry: GeoJsonPolygonalGeometry | null
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
	const outlines: Record<string, CountryOutline> = {}
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
		if (feature.geometry) {
			try {
				outlines[iso3] = buildCountryOutline(feature.geometry)
			} catch (error) {
				// The build falls back to the bounding box of the index for this country.
				const message = error instanceof Error ? error.message : String(error)
				console.warn(`No outline for ${iso3}: ${message}`)
			}
		}
	}

	const polygonsDir = path.resolve(
		path.dirname(fileURLToPath(import.meta.url)),
		'..',
		'..',
		'assets',
		'polygons',
	)
	const indexPath = path.join(polygonsDir, 'country-index.json')
	writeJsonObjectByLine(indexPath, index)
	console.log(`Wrote ${Object.keys(index).length} countries to ${indexPath}`)

	const outlinesPath = path.join(polygonsDir, 'country-outlines.json')
	writeJsonObjectByLine(outlinesPath, outlines)
	console.log(
		`Wrote ${Object.keys(outlines).length} outlines to ${outlinesPath} (${fs.statSync(outlinesPath).size} bytes)`,
	)
}

/** One key per line, sorted, so a refresh gives a readable diff. */
function writeJsonObjectByLine(outputPath: string, content: Record<string, unknown>): void {
	const lines = Object.entries(content)
		.sort(function compareKeys(a, b) {
			return a[0].localeCompare(b[0])
		})
		.map(function formatLine([key, value]) {
			return `\t${JSON.stringify(key)}: ${JSON.stringify(value)}`
		})
	fs.writeFileSync(outputPath, `{\n${lines.join(',\n')}\n}\n`, 'utf-8')
}

refreshCountryIndex()
