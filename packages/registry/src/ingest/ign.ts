import {
	type CatalogEntry,
	type CatalogEntryInput,
	CatalogEntrySchema,
	type SourceCategory,
} from '../schema.js'

export interface IgnWfsLayerDescriptor {
	idSuffix: string
	title: string
	typename: string
	category: SourceCategory
	description: string
	tags: Array<string>
}

export function buildIgnGeoplateformeWfsEntry(
	descriptor: IgnWfsLayerDescriptor,
): CatalogEntry {
	const encodedTypename = encodeURIComponent(descriptor.typename)
	const wfsUrl = `https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&TYPENAMES=${encodedTypename}`

	const candidate: CatalogEntryInput = {
		id: `fr-ign-bdtopo-${descriptor.idSuffix}`,
		name: `IGN Géoplateforme - BD TOPO (${descriptor.title})`,
		description: descriptor.description,
		type: 'wfs',
		category: descriptor.category,
		url: wfsUrl,
		crs: 'EPSG:4326',
		country_iso: 'FR',
		tags: ['ign', 'geoplateforme', 'bdtopo', 'france', ...descriptor.tags],
		license: 'Licence Ouverte / Etalab 2.0',
		attribution: '© IGN - Géoplateforme (BD TOPO)',
	}

	return CatalogEntrySchema.parse(candidate)
}
