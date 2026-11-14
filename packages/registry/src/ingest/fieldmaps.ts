import { type CatalogEntry, CatalogEntrySchema } from '../schema.js'

export interface FieldMapsLayerDescriptor {
	admLevel: 0 | 1 | 2
	iso2?: string
	iso3?: string
	countryNameEn?: string
	countryNameFr?: string
}

export function buildFieldMapsCatalogEntry(
	descriptor: FieldMapsLayerDescriptor,
): CatalogEntry {
	const levelTag = `adm${descriptor.admLevel}`
	const isGlobal = !descriptor.iso2
	const isoUpper = descriptor.iso2 ? descriptor.iso2.toUpperCase() : undefined
	const isoLower = descriptor.iso2 ? descriptor.iso2.toLowerCase() : 'global'
	const iso3Lower = descriptor.iso3 ? descriptor.iso3.toLowerCase() : isoLower
	const labelScopeEn = descriptor.countryNameEn
		? descriptor.countryNameEn
		: 'Global'
	const labelScopeFr = descriptor.countryNameFr
		? descriptor.countryNameFr
		: descriptor.countryNameEn
			? descriptor.countryNameEn
			: 'Mondial'

	const candidate: CatalogEntry = {
		id: `${isoLower}-fieldmaps-${levelTag}`,
		name: `FieldMaps / HDX - Administrative Boundaries ${levelTag.toUpperCase()} (${labelScopeEn})`,
		description: `Administrative boundary polygons at level ${levelTag.toUpperCase()} (${labelScopeEn}) provided by FieldMaps.io and Humanitarian Data Exchange (UN OCHA HDX).`,
		i18n: {
			fr: {
				name: `FieldMaps / HDX - Limites Administratives ${levelTag.toUpperCase()} (${labelScopeFr})`,
				description: `Polygones des limites administratives de niveau ${levelTag.toUpperCase()} (${labelScopeFr}) fournis par FieldMaps.io et Humanitarian Data Exchange (UN OCHA HDX).`,
			},
		},
		type: 'fgb',
		category: 'admin',
		url: isGlobal
			? `/vsicurl/https://data.fieldmaps.io/${levelTag}/osm/intl/${levelTag}_polygons.fgb`
			: `/vsicurl/https://data.fieldmaps.io/cod/originals/${iso3Lower}.gpkg`,
		crs: 'EPSG:4326',
		country_iso: isoUpper,
		bbox: isGlobal ? [-180, -90, 180, 90] : undefined,
		style_file: 'admin.qml',
		tags: [
			levelTag,
			'fieldmaps',
			'hdx',
			'cod-ab',
			'humanitarian',
			'administrative boundaries',
			'humanitaire',
			'limites administratives',
			'frontieres',
			...(descriptor.countryNameEn
				? [descriptor.countryNameEn.toLowerCase()]
				: []),
			...(descriptor.countryNameFr
				? [descriptor.countryNameFr.toLowerCase()]
				: []),
		],
		license: 'CC-BY-4.0 / ODbL-1.0',
		attribution: '© FieldMaps.io / UN OCHA HDX / OpenStreetMap contributors',
	}

	return CatalogEntrySchema.parse(candidate)
}
