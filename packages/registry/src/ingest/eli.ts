import { stringify as stringifyYaml } from 'yaml'
import {
	type BoundingBox,
	type CatalogEntry,
	type CatalogEntryInput,
	CatalogEntrySchema,
	type SourceCategory,
	type SourceType,
} from '../schema.js'

export interface EliFeatureProperties {
	id: string
	name: string
	type: string
	url: string
	category?: string
	country_code?: string
	best?: boolean
	description?: string
	license_url?: string
	privacy_policy_url?: string
	attribution?: {
		text?: string
		url?: string
	}
	min_zoom?: number
	max_zoom?: number
}

export interface EliGeoJsonGeometry {
	type: string
	coordinates: Array<unknown>
}

export interface EliGeoJsonFeature {
	type: 'Feature'
	properties: EliFeatureProperties
	geometry?: EliGeoJsonGeometry | null
}

export interface EliGeoJsonCollection {
	type: 'FeatureCollection'
	features: Array<EliGeoJsonFeature>
}

function normalizeSlug(rawId: string): string {
	return rawId
		.toLowerCase()
		.replace(/[^a-z0-9-]+/g, '-')
		.replace(/-+/g, '-')
		.replace(/^-+|-+$/g, '')
}

function mapEliCategory(eliCategory: string | undefined): SourceCategory {
	if (!eliCategory) {
		return 'imagery'
	}
	const lowered = eliCategory.toLowerCase()
	if (lowered.includes('elevation')) {
		return 'elevation'
	}
	if (
		lowered.includes('osmbasedmap') ||
		lowered === 'map' ||
		lowered === 'historicmap'
	) {
		return 'basemap'
	}
	return 'imagery'
}

function normalizeEliUrlAndType(
	eliType: string,
	rawUrl: string,
): { type: SourceType; url: string } | null {
	const loweredType = eliType.toLowerCase()
	const trimmedUrl = rawUrl.trim()

	// Exclude layers requiring private API keys or hardcoded tokens
	if (
		/\{(apikey|api_key|token|access_token|key|username|password)\}/i.test(
			trimmedUrl,
		) ||
		/(access_token=|apikey=|api_key=|pk\.eyJ|sk\.eyJ)/i.test(trimmedUrl)
	) {
		return null
	}

	if (!trimmedUrl.startsWith('http://') && !trimmedUrl.startsWith('https://')) {
		return null
	}

	// Replace JOSM {switch:a,b,c} with the first option
	const withoutSwitch = trimmedUrl.replace(
		/\{switch:([^,}]+)[^}]*\}/gi,
		'$1',
	)
	const withStandardZoom = withoutSwitch.replace(/\{zoom\}/gi, '{z}')

	if (loweredType === 'tms') {
		const isInvertedY = withStandardZoom.includes('{-y}')
		return {
			type: isInvertedY ? 'tms' : 'xyz',
			url: withStandardZoom,
		}
	}

	if (loweredType === 'wmts') {
		return {
			type: 'wmts',
			url: withStandardZoom,
		}
	}

	return null
}

function collectCoordinatesFlat(
	coords: unknown,
	out: Array<[number, number]>,
): void {
	if (!Array.isArray(coords)) {
		return
	}
	if (
		coords.length >= 2 &&
		typeof coords[0] === 'number' &&
		typeof coords[1] === 'number'
	) {
		out.push([coords[0], coords[1]])
		return
	}
	for (const child of coords) {
		collectCoordinatesFlat(child, out)
	}
}

export function computeBboxFromGeometry(
	geometry: EliGeoJsonGeometry | null | undefined,
): BoundingBox | undefined {
	if (!geometry || !geometry.coordinates) {
		return undefined
	}
	const pairs: Array<[number, number]> = []
	collectCoordinatesFlat(geometry.coordinates, pairs)
	if (pairs.length === 0) {
		return undefined
	}

	let minX = 180
	let minY = 90
	let maxX = -180
	let maxY = -90

	for (const [lon, lat] of pairs) {
		if (lon < minX) minX = lon
		if (lon > maxX) maxX = lon
		if (lat < minY) minY = lat
		if (lat > maxY) maxY = lat
	}

	minX = Math.max(-180, Math.min(180, Number(minX.toFixed(4))))
	minY = Math.max(-90, Math.min(90, Number(minY.toFixed(4))))
	maxX = Math.max(-180, Math.min(180, Number(maxX.toFixed(4))))
	maxY = Math.max(-90, Math.min(90, Number(maxY.toFixed(4))))

	return minX <= maxX && minY <= maxY ? [minX, minY, maxX, maxY] : undefined
}

export function convertEliFeatureToCatalogEntry(
	feature: EliGeoJsonFeature,
	fallbackBbox?: BoundingBox,
): CatalogEntry | null {
	const props = feature.properties
	if (!props || !props.url || !props.id || !props.name || !props.type) {
		return null
	}

	const normalized = normalizeEliUrlAndType(props.type, props.url)
	if (!normalized) {
		return null
	}

	const isoCode =
		props.country_code && /^[A-Za-z]{2}$/.test(props.country_code)
			? props.country_code.toUpperCase()
			: undefined

	const prefix = isoCode ? isoCode.toLowerCase() : 'global'
	const slugId = normalizeSlug(`${prefix}-eli-${props.id}`)
	if (!slugId || !/^[a-z0-9-]+$/.test(slugId)) {
		return null
	}

	const category = mapEliCategory(props.category)
	const attributionText =
		props.attribution && props.attribution.text
			? props.attribution.text
			: `© ${props.name}`

	const geomBbox = computeBboxFromGeometry(feature.geometry)
	const resolvedBbox: BoundingBox | undefined = geomBbox
		? geomBbox
		: fallbackBbox
			? fallbackBbox
			: isoCode
				? undefined
				: [-180, -85.05, 180, 85.05]

	const minZoom =
		typeof props.min_zoom === 'number' &&
		props.min_zoom >= 0 &&
		props.min_zoom <= 24
			? Math.floor(props.min_zoom)
			: undefined
	const maxZoom =
		typeof props.max_zoom === 'number' &&
		props.max_zoom >= 0 &&
		props.max_zoom <= 24
			? Math.floor(props.max_zoom)
			: undefined

	const candidate: CatalogEntryInput = {
		id: slugId,
		// Community imagery index: listed after the curated sources of the same relevance.
		priority: -1,
		name: props.name,
		description: props.description
			? props.description
			: `Layer from the OpenStreetMap Editor Layer Index (${props.name}).`,
		i18n: {
			fr: {
				name: props.name,
				description: props.description
					? props.description
					: `Couche issue du registre OpenStreetMap Editor Layer Index (${props.name}).`,
			},
		},
		type: normalized.type,
		category,
		url: normalized.url,
		crs: 'EPSG:3857',
		country_iso: isoCode,
		bbox: resolvedBbox,
		tags: [
			'eli',
			'editor layer index',
			category,
			normalized.type,
			...(isoCode ? [isoCode.toLowerCase()] : ['global']),
		],
		license: props.license_url
			? props.license_url
			: 'OSM Editor Layer Index License',
		attribution: attributionText,
		min_zoom: minZoom,
		max_zoom: maxZoom,
	}

	const validation = CatalogEntrySchema.safeParse(candidate)
	return validation.success ? validation.data : null
}

export function serializeCatalogEntriesToYaml(
	entries: Array<CatalogEntry>,
): string {
	return stringifyYaml(entries)
}
