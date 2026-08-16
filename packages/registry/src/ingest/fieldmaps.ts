import { type CatalogEntry, CatalogEntrySchema } from '../schema.js'

export interface FieldMapsLayerDescriptor {
	admLevel: 0 | 1 | 2
	iso2?: string
	iso3?: string
	countryName?: string
}

export function buildFieldMapsCatalogEntry(
	descriptor: FieldMapsLayerDescriptor,
): CatalogEntry {
	const levelTag = `adm${descriptor.admLevel}`
	const isGlobal = !descriptor.iso2
	const isoUpper = descriptor.iso2 ? descriptor.iso2.toUpperCase() : undefined
	const isoLower = descriptor.iso2 ? descriptor.iso2.toLowerCase() : 'global'
	const iso3Lower = descriptor.iso3 ? descriptor.iso3.toLowerCase() : isoLower
	const labelScope = descriptor.countryName ? descriptor.countryName : 'Mondial'

	const candidate: CatalogEntry = {
		id: `${isoLower}-fieldmaps-${levelTag}`,
		name: `FieldMaps / HDX - Limites Administratives ${levelTag.toUpperCase()} (${labelScope})`,
		description: `Polygones des limites administratives de niveau ${levelTag.toUpperCase()} (${labelScope}) fournis par FieldMaps.io et Humanitarian Data Exchange (UN OCHA HDX).`,
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
			'humanitaire',
			'limites administratives',
			'frontieres',
			...(descriptor.countryName ? [descriptor.countryName.toLowerCase()] : []),
		],
		license: 'CC-BY-4.0 / ODbL-1.0',
		attribution: '© FieldMaps.io / UN OCHA HDX / OpenStreetMap contributors',
	}

	return CatalogEntrySchema.parse(candidate)
}
