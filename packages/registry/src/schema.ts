import { z } from 'zod'

export const SourceCategorySchema = z.enum([
	'basemap',
	'elevation',
	'hydrography',
	'transport',
	'admin',
	'buildings',
	'landuse',
	'imagery',
	'utilities',
	'crisis',
])

export type SourceCategory = z.infer<typeof SourceCategorySchema>

export const SourceTypeSchema = z.enum([
	'xyz',
	'tms',
	'wms',
	'wmts',
	'wfs',
	'vectortile',
	'cog',
	'vrt',
	'fgb',
	'geojson',
	'archive',
	'overpass',
])

export type SourceType = z.infer<typeof SourceTypeSchema>

export const BoundingBoxSchema = z
	.tuple([
		z.number().min(-180).max(180),
		z.number().min(-90).max(90),
		z.number().min(-180).max(180),
		z.number().min(-90).max(90),
	])
	.refine(
		function validateBoundingBoxOrder(bbox) {
			return bbox[0] <= bbox[2] && bbox[1] <= bbox[3]
		},
		{
			message: 'bbox must follow [minX, minY, maxX, maxY] with minX <= maxX and minY <= maxY',
		},
	)

export type BoundingBox = z.infer<typeof BoundingBoxSchema>

export const CatalogEntrySchema = z
	.object({
		id: z
			.string()
			.regex(/^[a-z0-9-]+$/, 'id must be a valid lowercase kebab-case slug (^[a-z0-9-]+$)'),
		name: z.string().min(1),
		description: z.string().optional(),
		type: SourceTypeSchema,
		category: SourceCategorySchema,
		url: z.string().min(1),
		crs: z.string().default('EPSG:4326'),
		country_iso: z
			.string()
			.regex(/^[A-Z]{2}$/, 'country_iso must be a 2-letter uppercase ISO-3166-1 alpha-2 code')
			.optional(),
		bbox: BoundingBoxSchema.optional(),
		geom_wkt: z.string().min(1).optional(),
		tags: z.array(z.coerce.string().min(1)).min(1),
		license: z.string().min(1),
		attribution: z.string().min(1),
		style_file: z.string().optional(),
		min_zoom: z.number().int().min(0).max(24).optional(),
		max_zoom: z.number().int().min(0).max(24).optional(),
		archive_target: z.string().min(1).optional(),
	})
	.refine(
		function validateArchiveTarget(entry) {
			return entry.type === 'archive' ? Boolean(entry.archive_target) : true
		},
		{
			message: 'archive_target is required when type is "archive"',
			path: ['archive_target'],
		},
	)

export type CatalogEntry = z.infer<typeof CatalogEntrySchema>

export const CatalogFileSchema = z.union([
	CatalogEntrySchema,
	z.array(CatalogEntrySchema).min(1),
])

export function parseCatalogFileContent(rawContent: unknown): Array<CatalogEntry> {
	const parsed = CatalogFileSchema.parse(rawContent)
	return Array.isArray(parsed) ? parsed : [parsed]
}
