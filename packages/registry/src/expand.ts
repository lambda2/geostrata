import {
	type BoundingBox,
	type CatalogEntry,
	CatalogEntrySchema,
	CatalogFileSchema,
} from './schema.js'

export interface CountryIndexEntry {
	iso2: string
	name_en: string
	name_fr: string
	bbox: BoundingBox
}

/** Keyed by ISO-3166-1 alpha-3 code, see assets/polygons/country-index.json. */
export type CountryIndex = Record<string, CountryIndexEntry>

type RawEntry = Record<string, unknown>

const TEMPLATE_KEY = 'for_each_country'

function applyFileDefaults(defaults: RawEntry, entry: RawEntry): RawEntry {
	const merged: RawEntry = { ...defaults, ...entry }
	// Tags add up instead of being replaced, so a file can carry its provider keywords once.
	if (Array.isArray(defaults.tags) || Array.isArray(entry.tags)) {
		const defaultTags = Array.isArray(defaults.tags) ? defaults.tags : []
		const entryTags = Array.isArray(entry.tags) ? entry.tags : []
		merged.tags = Array.from(new Set([...defaultTags, ...entryTags]))
	}
	return merged
}

function substitutePlaceholders(value: unknown, replacements: Record<string, string>): unknown {
	if (typeof value === 'string') {
		// Only the country placeholders are touched: {z}/{x}/{y}, {{bbox}} and {time} stay as they are.
		return value.replace(/\{(iso2|ISO2|iso3|ISO3|country|country_fr)\}/g, function replace(_match, key: string) {
			return replacements[key]
		})
	}
	if (Array.isArray(value)) {
		return value.map(function mapItem(item) {
			return substitutePlaceholders(item, replacements)
		})
	}
	if (value && typeof value === 'object') {
		return Object.fromEntries(
			Object.entries(value).map(function mapField([key, fieldValue]) {
				return [key, substitutePlaceholders(fieldValue, replacements)]
			}),
		)
	}
	return value
}

function resolveTemplateCountries(rawList: unknown, countryIndex: CountryIndex, entryId: string): Array<string> {
	if (rawList === 'all') {
		return Object.keys(countryIndex)
	}
	if (!Array.isArray(rawList) || rawList.length === 0) {
		throw new Error(
			`"${entryId}": ${TEMPLATE_KEY} must be "all" or a non-empty list of ISO-3166-1 alpha-3 codes`,
		)
	}
	return rawList.map(function checkCode(code) {
		const iso3 = String(code).toUpperCase()
		if (!countryIndex[iso3]) {
			throw new Error(
				`"${entryId}": unknown country code "${code}" in ${TEMPLATE_KEY} (expected ISO-3166-1 alpha-3, see assets/polygons/country-index.json)`,
			)
		}
		return iso3
	})
}

/**
 * Turns one templated entry into one entry per country. Available placeholders, in any
 * string field: {iso2} {ISO2} {iso3} {ISO3} {country} {country_fr}. `country_iso` is filled in.
 */
function expandCountryTemplate(entry: RawEntry, countryIndex: CountryIndex): Array<RawEntry> {
	const { [TEMPLATE_KEY]: rawList, ...template } = entry
	if (rawList === undefined) {
		return [entry]
	}
	const entryId = String(template.id ?? '?')
	if (!/\{(iso2|iso3)\}/.test(entryId)) {
		throw new Error(
			`"${entryId}": a ${TEMPLATE_KEY} entry needs {iso2} or {iso3} in its id so every country gets a unique id`,
		)
	}
	return resolveTemplateCountries(rawList, countryIndex, entryId).map(function expandForCountry(iso3) {
		const country = countryIndex[iso3]
		const expanded = substitutePlaceholders(template, {
			iso2: country.iso2.toLowerCase(),
			ISO2: country.iso2,
			iso3: iso3.toLowerCase(),
			ISO3: iso3,
			country: country.name_en,
			country_fr: country.name_fr,
		}) as RawEntry
		return { country_iso: country.iso2, ...expanded }
	})
}

export function expandCatalogFileContent(rawContent: unknown, countryIndex: CountryIndex): Array<CatalogEntry> {
	const parsedFile = CatalogFileSchema.parse(rawContent)
	const isWrappedFile =
		!Array.isArray(parsedFile) && Array.isArray((parsedFile as RawEntry).sources)
	const defaults: RawEntry = isWrappedFile
		? (((parsedFile as RawEntry).defaults as RawEntry | undefined) ?? {})
		: {}
	const rawEntries: Array<RawEntry> = isWrappedFile
		? ((parsedFile as RawEntry).sources as Array<RawEntry>)
		: Array.isArray(parsedFile)
			? parsedFile
			: [parsedFile]

	const entries: Array<CatalogEntry> = []
	for (const rawEntry of rawEntries) {
		const withDefaults = applyFileDefaults(defaults, rawEntry)
		for (const expanded of expandCountryTemplate(withDefaults, countryIndex)) {
			const result = CatalogEntrySchema.safeParse(expanded)
			if (!result.success) {
				const issues = result.error.issues
					.map(function formatIssue(issue) {
						return `${issue.path.join('.') || '(entry)'}: ${issue.message}`
					})
					.join('; ')
				throw new Error(`"${String(expanded.id ?? '?')}": ${issues}`)
			}
			entries.push(result.data)
		}
	}
	return entries
}
