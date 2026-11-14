import { type BoundingBox, type CatalogEntry,
	type CatalogEntryInput, CatalogEntrySchema } from '../schema.js'

export type GeoBoundariesRelease = 'gbHumanitarian' | 'gbOpen'

/** Subset of the fields returned by https://www.geoboundaries.org/api/current/<release>/<ISO3|ALL>/<ADMn>/ */
export interface GeoBoundariesApiRecord {
	boundaryISO: string
	boundaryName: string
	boundaryType: string
	boundarySource?: string
	boundaryLicense?: string
	boundaryYearRepresented?: string
	gjDownloadURL: string
}

export interface CountryReference {
	iso2: string
	nameEn: string
	nameFr: string
	bbox: BoundingBox
}

function isMeaningful(value: string | undefined): value is string {
	return Boolean(value) && value !== 'nan' && value !== 'Unknown'
}

function shortenLicense(rawLicense: string | undefined): string {
	if (!isMeaningful(rawLicense)) {
		return 'See geoBoundaries metadata'
	}
	const shortForm = rawLicense.match(/\(([^)]+)\)\s*$/)
	return shortForm ? shortForm[1] : rawLicense
}

export function buildGeoBoundariesCatalogEntry(
	record: GeoBoundariesApiRecord,
	release: GeoBoundariesRelease,
	country: CountryReference,
	useCuratedCountryPolygon: boolean,
): CatalogEntry {
	const levelTag = record.boundaryType.toLowerCase()
	const levelLabel = levelTag.toUpperCase()
	const isCod = release === 'gbHumanitarian'
	const originEn = isCod ? 'UN OCHA COD-AB via geoBoundaries' : 'geoBoundaries Open'
	const originFr = isCod ? 'COD-AB UN OCHA via geoBoundaries' : 'geoBoundaries Open'
	const yearSuffix = isMeaningful(record.boundaryYearRepresented)
		? `, ${record.boundaryYearRepresented}`
		: ''
	const source = isMeaningful(record.boundarySource)
		? record.boundarySource
		: 'geoBoundaries'

	const candidate: CatalogEntryInput = {
		id: `${country.iso2.toLowerCase()}-geoboundaries-${levelTag}`,
		name: `Administrative Boundaries ${levelLabel} (${country.nameEn}) - ${originEn}`,
		description: `Administrative boundary polygons at level ${levelLabel} for ${country.nameEn}${yearSuffix}. Source: ${source}.`,
		i18n: {
			fr: {
				name: `Limites Administratives ${levelLabel} (${country.nameFr}) - ${originFr}`,
				description: `Polygones des limites administratives de niveau ${levelLabel} pour ${country.nameFr}${yearSuffix}. Source : ${source}.`,
			},
		},
		type: 'geojson',
		category: 'admin',
		url: `/vsicurl/${record.gjDownloadURL}`,
		crs: 'EPSG:4326',
		country_iso: country.iso2,
		// Countries listed in assets/polygons/countries.json keep their curated outline.
		bbox: useCuratedCountryPolygon ? undefined : country.bbox,
		style_file: 'admin.qml',
		tags: [
			levelTag,
			'geoboundaries',
			...(isCod ? ['cod-ab', 'hdx', 'ocha'] : []),
			'humanitarian',
			'administrative boundaries',
			'humanitaire',
			'limites administratives',
			'frontieres',
			country.nameEn.toLowerCase(),
			country.nameFr.toLowerCase(),
		],
		license: shortenLicense(record.boundaryLicense),
		attribution: `© ${source} / geoBoundaries (wmgeolab)`,
	}

	return CatalogEntrySchema.parse(candidate)
}
