import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import Database from 'better-sqlite3'
import { type TimeSettings } from './schema.js'
import { resolveTimePlaceholder } from './time.js'

export type HealthStatus = 'ok' | 'degraded' | 'dead'

interface SourceHealthRow {
	id: string
	type: string
	url: string
	/** JSON of the entry's `time` block when its url contains `{time}`, else null. */
	time_json: string | null
}

interface HealthCheckResult {
	id: string
	status: HealthStatus
	latencyMs: number
	detail: string
}

function getPackageRootDir(): string {
	const currentFilePath = fileURLToPath(import.meta.url)
	const srcDir = path.dirname(currentFilePath)
	return path.resolve(srcDir, '..')
}

const OGC_SERVICE_BY_TYPE: Record<string, string> = {
	wms: 'WMS',
	wmts: 'WMTS',
	wfs: 'WFS',
}

/** Tile templates legitimately answer 4xx for the 0/0/0 probe tile when their coverage is regional. */
const TILE_TEMPLATE_TYPES = new Set(['xyz', 'tms', 'vectortile'])

/** ArcGIS REST endpoints describe themselves at `<url>?f=json`. */
const ARCGIS_REST_TYPES = new Set(['arcgisfeatureserver', 'arcgismapserver'])

/**
 * URL probed for a source, or null when nothing can be probed over HTTP. `{time}` is resolved
 * as the plugin does at load time; `layer` and `geometry_type` never change the probed URL.
 */
export function extractTestableHttpUrl(
	sourceType: string,
	rawUrl: string,
	time: TimeSettings | null = null,
	now: Date = new Date(),
): string | null {
	if (sourceType === 'overpass') {
		return 'https://overpass-api.de/api/status'
	}

	const datedUrl = resolveTimePlaceholder(rawUrl, time, now)
	const withoutVsiZip = datedUrl.replace(/^\/vsizip\/+/, '')
	const withoutVsiCurl = withoutVsiZip.replace(/^\/vsicurl(_streaming)?\/+/, '')

	if (!withoutVsiCurl.startsWith('http://') && !withoutVsiCurl.startsWith('https://')) {
		return null
	}

	const ogcService = OGC_SERVICE_BY_TYPE[sourceType]
	if (ogcService) {
		const baseUrl = withoutVsiCurl.split('?')[0]
		return baseUrl.toLowerCase().endsWith('.xml')
			? baseUrl
			: `${baseUrl}?SERVICE=${ogcService}&REQUEST=GetCapabilities`
	}

	if (ARCGIS_REST_TYPES.has(sourceType)) {
		return `${withoutVsiCurl.split('?')[0].replace(/\/+$/, '')}?f=json`
	}

	const probeUrl = withoutVsiCurl
		.replace(/\{z\}/g, '0')
		.replace(/\{x\}/g, '0')
		.replace(/\{y\}/g, '0')
		.replace(/\{-y\}/g, '0')

	// Remaining placeholders ({switch:a,b}, {apikey}...) cannot be probed blindly.
	return probeUrl.includes('{') ? null : probeUrl
}

export function classifyHttpStatus(
	sourceType: string,
	httpStatus: number,
	latencyMs: number,
): HealthStatus {
	if (httpStatus >= 200 && httpStatus < 400) {
		return latencyMs > 4000 ? 'degraded' : 'ok'
	}
	if (httpStatus >= 500 || [401, 403, 408, 429].includes(httpStatus)) {
		return 'degraded'
	}
	if (TILE_TEMPLATE_TYPES.has(sourceType)) {
		return 'ok'
	}
	return 'dead'
}

function parseTimeSettings(timeJson: string | null): TimeSettings | null {
	if (!timeJson) {
		return null
	}
	try {
		return JSON.parse(timeJson) as TimeSettings
	} catch {
		return null
	}
}

/**
 * ArcGIS REST answers HTTP 200 with `{"error":{"code":404,...}}` for a missing service:
 * the code of the JSON body, when there is one, is the status to classify.
 */
export function extractArcgisErrorCode(body: string): number | null {
	const match = /^\s*\{\s*"error"\s*:\s*\{[^}]*?"code"\s*:\s*(\d+)/.exec(body)
	return match ? Number(match[1]) : null
}

async function checkSingleEndpoint(row: SourceHealthRow): Promise<HealthCheckResult> {
	const targetUrl = extractTestableHttpUrl(row.type, row.url, parseTimeSettings(row.time_json))
	if (!targetUrl) {
		return {
			id: row.id,
			status: 'ok',
			latencyMs: 0,
			detail: 'non-http template',
		}
	}

	// Sources sharing one endpoint (all Overpass recipes) are probed once.
	const cachedProbe = probeCache.get(targetUrl)
	const probe = cachedProbe
		? cachedProbe
		: probeEndpoint(row.type, targetUrl)
	probeCache.set(targetUrl, probe)
	return { id: row.id, ...(await probe) }
}

const probeCache = new Map<string, Promise<Omit<HealthCheckResult, 'id'>>>()

async function probeEndpoint(
	sourceType: string,
	targetUrl: string,
): Promise<Omit<HealthCheckResult, 'id'>> {
	const startTime = Date.now()
	const controller = new AbortController()
	const timeoutHandle = setTimeout(function abortRequest() {
		controller.abort()
	}, 15000)

	try {
		// A ranged GET behaves like the real GDAL/QGIS access; many servers reject HEAD.
		const response = await fetch(targetUrl, {
			method: 'GET',
			headers: {
				'User-Agent': 'Geostrata-Healthcheck/1.0',
				Range: 'bytes=0-1023',
			},
			signal: controller.signal,
		})
		const latencyMs = Date.now() - startTime
		if (ARCGIS_REST_TYPES.has(sourceType) && response.ok) {
			const arcgisErrorCode = extractArcgisErrorCode(await response.text())
			if (arcgisErrorCode !== null) {
				return {
					// 498 / 499 are the ArcGIS "invalid token" / "token required" codes.
					status: classifyHttpStatus(
						sourceType,
						arcgisErrorCode === 498 || arcgisErrorCode === 499 ? 403 : arcgisErrorCode,
						latencyMs,
					),
					latencyMs,
					detail: `HTTP ${response.status}, ArcGIS error ${arcgisErrorCode}`,
				}
			}
		} else {
			await response.body?.cancel()
		}

		return {
			status: classifyHttpStatus(sourceType, response.status, latencyMs),
			latencyMs,
			detail: `HTTP ${response.status}`,
		}
	} catch (error) {
		const latencyMs = Date.now() - startTime
		const errorMessage = error instanceof Error ? error.message : String(error)
		return {
			status: 'degraded',
			latencyMs,
			detail: errorMessage,
		}
	} finally {
		clearTimeout(timeoutHandle)
	}
}

const HEALTHCHECK_CONCURRENCY = 8

/** `sources_data` in layout 2 (where `sources` is a view), `sources` in a layout 1 catalog. */
export function resolveSourcesTableName(db: Database.Database): string {
	const sourcesObject = db
		.prepare("SELECT type FROM sqlite_master WHERE name = 'sources'")
		.get() as { type: string } | undefined
	return sourcesObject?.type === 'view' ? 'sources_data' : 'sources'
}

export function selectHealthRows(db: Database.Database): Array<SourceHealthRow> {
	const columns = db.pragma('table_info(sources)') as Array<{ name: string }>
	const hasTimeColumn = columns.some(function isTimeColumn(column) {
		return column.name === 'time_json'
	})
	return db
		.prepare(
			`SELECT id, type, url, ${hasTimeColumn ? 'time_json' : 'NULL AS time_json'} FROM sources ORDER BY id ASC`,
		)
		.all() as Array<SourceHealthRow>
}

export async function runHealthcheck(): Promise<void> {
	const packageRoot = getPackageRootDir()
	// Same override as the build, so a scratch build can be checked without touching dist/.
	const distDir = process.env.GEOSTRATA_DIST_DIR
		? path.resolve(process.env.GEOSTRATA_DIST_DIR)
		: path.join(packageRoot, 'dist')
	const sqlitePath = path.join(distDir, 'catalog.sqlite')

	if (!fs.existsSync(sqlitePath)) {
		throw new Error(`catalog.sqlite not found at ${sqlitePath}. Run "npm run build" first.`)
	}

	const db = new Database(sqlitePath)
	const rows = selectHealthRows(db)

	const results: Array<HealthCheckResult> = []
	for (let offset = 0; offset < rows.length; offset += HEALTHCHECK_CONCURRENCY) {
		const batch = rows.slice(offset, offset + HEALTHCHECK_CONCURRENCY)
		results.push(...(await Promise.all(batch.map(checkSingleEndpoint))))
	}

	// `sources` is a view since layout 2: the statuses are written to the table behind it.
	const updateStmt = db.prepare(
		`UPDATE ${resolveSourcesTableName(db)} SET health_status = ? WHERE id = ?`,
	)
	const updateTransaction = db.transaction(function applyResults(
		items: Array<HealthCheckResult>,
	) {
		for (const item of items) {
			updateStmt.run(item.status, item.id)
		}
	})

	updateTransaction(results)
	db.close()

	// Persisted so the next `npm run build` bakes the statuses into catalog.sqlite and catalog.json.
	const healthReport = {
		checked_at: new Date().toISOString(),
		results: Object.fromEntries(
			results.map(function toReportEntry(item) {
				return [item.id, { status: item.status, detail: item.detail }]
			}),
		),
	}
	fs.writeFileSync(
		path.join(distDir, 'health.json'),
		`${JSON.stringify(healthReport, null, '\t')}\n`,
	)

	for (const item of results) {
		console.log(
			`[${item.status.toUpperCase()}] ${item.id} (${item.latencyMs} ms) — ${item.detail}`,
		)
	}

	const deadItems = results.filter(function isDead(item) {
		return item.status === 'dead'
	})
	const degradedCount = results.filter(function isDegraded(item) {
		return item.status === 'degraded'
	}).length
	console.log(
		`\nHealthcheck: ${results.length} sources, ${deadItems.length} dead, ${degradedCount} degraded.`,
	)
	if (deadItems.length > 0) {
		// Fail the scheduled workflow so dead links are noticed instead of scrolling by in a green log.
		process.exitCode = 1
	}
}

if (Boolean(process.argv[1]) && import.meta.url === pathToFileURL(process.argv[1]).href) {
	runHealthcheck()
}
