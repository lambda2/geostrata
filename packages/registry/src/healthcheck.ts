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

function extractTestableHttpUrl(sourceType: string, rawUrl: string): string | null {
	if (sourceType === 'overpass') {
		return 'https://overpass-api.de/api/status'
	}

	const withoutVsiZip = rawUrl.replace(/^\/vsizip\/+/, '')
	const withoutVsiCurl = withoutVsiZip.replace(/^\/vsicurl(_streaming)?\/+/, '')

	if (!withoutVsiCurl.startsWith('http://') && !withoutVsiCurl.startsWith('https://')) {
		return null
	}

	return withoutVsiCurl
		.replace(/\{z\}/g, '0')
		.replace(/\{x\}/g, '0')
		.replace(/\{y\}/g, '0')
		.replace(/\{-y\}/g, '0')
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

	const startTime = Date.now()
	const controller = new AbortController()
	const timeoutHandle = setTimeout(function abortRequest() {
		controller.abort()
	}, 8000)

	try {
		const response = await fetch(targetUrl, {
			method: 'HEAD',
			headers: {
				'User-Agent': 'Geostrata-Healthcheck/1.0',
				Range: 'bytes=0-1023',
			},
			signal: controller.signal,
		})
		const latencyMs = Date.now() - startTime
		const isOk = response.status >= 200 && response.status < 405
		const status: HealthStatus = isOk
			? latencyMs > 4000
				? 'degraded'
				: 'ok'
			: response.status >= 500
				? 'degraded'
				: 'dead'

		return {
			id: row.id,
			status,
			latencyMs,
			detail: `HTTP ${response.status}`,
		}
	} catch (error) {
		const latencyMs = Date.now() - startTime
		const errorMessage = error instanceof Error ? error.message : String(error)
		return {
			id: row.id,
			status: 'degraded',
			latencyMs,
			detail: errorMessage,
		}
	} finally {
		clearTimeout(timeoutHandle)
	}
}

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
	for (const row of rows) {
		const result = await checkSingleEndpoint(row)
		results.push(result)
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

	for (const item of results) {
		console.log(
			`[${item.status.toUpperCase()}] ${item.id} (${item.latencyMs} ms) — ${item.detail}`,
		)
	}
}

runHealthcheck()
