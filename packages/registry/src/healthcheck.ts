import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import Database from 'better-sqlite3'

export type HealthStatus = 'ok' | 'degraded' | 'dead'

interface SourceHealthRow {
	id: string
	type: string
	url: string
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

function extractTestableHttpUrl(sourceType: string, rawUrl: string): string | null {
	if (sourceType === 'overpass') {
		return 'https://overpass-api.de/api/status'
	}

	const withoutVsiZip = rawUrl.replace(/^\/vsizip\/+/, '')
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

	const probeUrl = withoutVsiCurl
		.replace(/\{z\}/g, '0')
		.replace(/\{x\}/g, '0')
		.replace(/\{y\}/g, '0')
		.replace(/\{-y\}/g, '0')

	// Remaining placeholders ({switch:a,b}, {apikey}...) cannot be probed blindly.
	return probeUrl.includes('{') ? null : probeUrl
}

function classifyHttpStatus(
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

async function checkSingleEndpoint(row: SourceHealthRow): Promise<HealthCheckResult> {
	const targetUrl = extractTestableHttpUrl(row.type, row.url)
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
		await response.body?.cancel()

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

export async function runHealthcheck(): Promise<void> {
	const packageRoot = getPackageRootDir()
	const sqlitePath = path.join(packageRoot, 'dist', 'catalog.sqlite')

	if (!fs.existsSync(sqlitePath)) {
		throw new Error(`catalog.sqlite not found at ${sqlitePath}. Run "npm run build" first.`)
	}

	const db = new Database(sqlitePath)
	const rows = db
		.prepare('SELECT id, type, url FROM sources ORDER BY id ASC')
		.all() as Array<SourceHealthRow>

	const results: Array<HealthCheckResult> = []
	for (let offset = 0; offset < rows.length; offset += HEALTHCHECK_CONCURRENCY) {
		const batch = rows.slice(offset, offset + HEALTHCHECK_CONCURRENCY)
		results.push(...(await Promise.all(batch.map(checkSingleEndpoint))))
	}

	const updateStmt = db.prepare('UPDATE sources SET health_status = ? WHERE id = ?')
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
		path.join(packageRoot, 'dist', 'health.json'),
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

runHealthcheck()
