import Coordinate from 'jsts/org/locationtech/jts/geom/Coordinate.js'
import GeometryFactory from 'jsts/org/locationtech/jts/geom/GeometryFactory.js'
import PrecisionModel from 'jsts/org/locationtech/jts/geom/PrecisionModel.js'
import BufferOp from 'jsts/org/locationtech/jts/operation/buffer/BufferOp.js'
import OverlayOp from 'jsts/org/locationtech/jts/operation/overlay/OverlayOp.js'
import UnaryUnionOp from 'jsts/org/locationtech/jts/operation/union/UnaryUnionOp.js'
import IsValidOp from 'jsts/org/locationtech/jts/operation/valid/IsValidOp.js'
import GeometryPrecisionReducer from 'jsts/org/locationtech/jts/precision/GeometryPrecisionReducer.js'
import DouglasPeuckerSimplifier from 'jsts/org/locationtech/jts/simplify/DouglasPeuckerSimplifier.js'
import TopologyPreservingSimplifier from 'jsts/org/locationtech/jts/simplify/TopologyPreservingSimplifier.js'

/**
 * Simplified country outlines for the footprint test of the plugin (does the map canvas
 * touch the country?). An outline must never be smaller than the country, or the layers of
 * a coastal town would vanish when the user zooms in, so each country is:
 *
 *   1. stripped of its holes (lakes, enclaves) and lightly simplified,
 *   2. grown by OUTLINE_BUFFER_DEG, which also merges the islands of an archipelago and
 *      keeps every atoll as a small blob instead of dropping it,
 *   3. simplified again by less than what it was grown by,
 *   4. clipped to the world rectangle and rounded to OUTLINE_DECIMALS.
 *
 * Natural Earth cuts the countries that cross the antimeridian (Fiji, Russia, Kiribati,
 * USA...) at +-180, so such a country stays a few polygons on both edges of the map instead
 * of a box around the world.
 */

/** About 11 km at the equator: the outline covers the coast, nearshore waters and border towns. */
export const OUTLINE_BUFFER_DEG = 0.1
/** Vertices of the raw geometry closer than this to the simplified line are dropped before growing. */
const PRE_SIMPLIFY_TOLERANCE_DEG = 0.01
/**
 * Must stay below OUTLINE_BUFFER_DEG - PRE_SIMPLIFY_TOLERANCE_DEG - rounding (0.007),
 * so the final outline still contains the country.
 */
const POST_SIMPLIFY_TOLERANCE_DEG = 0.06
const OUTLINE_DECIMALS = 2
/** Segments per quarter circle of the buffer: 2 turns a lone islet into an octagon. */
const BUFFER_QUADRANT_SEGMENTS = 2
/** Polygons whose boxes are closer than this share one R-Tree box. */
const BOX_MERGE_GAP_DEG = 2

export type GeoJsonRing = Array<Array<number>>

export interface GeoJsonPolygonalGeometry {
	type: 'Polygon' | 'MultiPolygon'
	coordinates: Array<GeoJsonRing> | Array<Array<GeoJsonRing>>
}

type Box = [number, number, number, number]

export interface CountryOutline {
	bbox: Box
	/**
	 * Boxes covering the outline, one per group of neighbouring polygons: the R-Tree entries
	 * of the country. A single box when the country is in one piece; several for distant
	 * territories (Hawaii, Svalbard...) and on each side of the antimeridian.
	 */
	boxes: Array<Box>
	/** Area of the outline in square degrees. */
	area_deg2: number
	/** POLYGON or MULTIPOLYGON in EPSG:4326, without holes. */
	geom_wkt: string
}

/** The methods of a JTS geometry used below (the typings shipped with jsts are loose). */
interface JstsGeometry {
	isEmpty(): boolean
	getEnvelope(): JstsGeometry
	getEnvelopeInternal(): { getMinX(): number; getMinY(): number; getMaxX(): number; getMaxY(): number }
	getNumGeometries(): number
	getGeometryN(index: number): JstsGeometry
	getGeometryType(): string
	getArea(): number
	/** Polygons only. */
	getExteriorRing(): { getCoordinates(): Array<Coordinate> }
}

const geometryFactory = new GeometryFactory()

function listExteriorRings(geometry: GeoJsonPolygonalGeometry): Array<GeoJsonRing> {
	const polygons =
		geometry.type === 'Polygon'
			? [geometry.coordinates as Array<GeoJsonRing>]
			: (geometry.coordinates as Array<Array<GeoJsonRing>>)
	return polygons.map(function takeExteriorRing(polygon) {
		return polygon[0]
	})
}

function createShell(ring: GeoJsonRing): JstsGeometry {
	const coordinates = ring.map(function toCoordinate(position) {
		return new Coordinate(position[0], position[1])
	})
	return geometryFactory.createPolygon(geometryFactory.createLinearRing(coordinates))
}

/**
 * A lightly simplified shell. An islet too small to survive the simplification becomes its
 * bounding box, and a shell the simplification would twist into a knot is kept as it is.
 */
function simplifyShell(shell: JstsGeometry): JstsGeometry {
	const simplifier = new DouglasPeuckerSimplifier(shell)
	simplifier.setDistanceTolerance(PRE_SIMPLIFY_TOLERANCE_DEG)
	simplifier.setEnsureValid(false)
	const simplified = simplifier.getResultGeometry() as JstsGeometry
	if (simplified.isEmpty()) {
		return shell.getEnvelope() as JstsGeometry
	}
	return IsValidOp.isValid(simplified) ? simplified : shell
}

function listPolygons(geometry: JstsGeometry): Array<JstsGeometry> {
	const polygons: Array<JstsGeometry> = []
	for (let index = 0; index < geometry.getNumGeometries(); index += 1) {
		const part = geometry.getGeometryN(index) as JstsGeometry
		if (part.getGeometryType() === 'Polygon' && !part.isEmpty()) {
			polygons.push(part)
		}
	}
	return polygons
}

/** Drops the holes, then merges the shells that overlap once their holes are gone. */
function removeHoles(geometry: JstsGeometry): JstsGeometry {
	const shells = listPolygons(geometry).map(function keepShell(polygon) {
		return geometryFactory.createPolygon(polygon.getExteriorRing()) as JstsGeometry
	})
	return shells.length === 1
		? shells[0]
		: (UnaryUnionOp.union(geometryFactory.createGeometryCollection(shells)) as JstsGeometry)
}

function formatNumber(value: number): string {
	// Number() drops "-0" and trailing zeros: 12.50 -> "12.5".
	return String(Number(value.toFixed(OUTLINE_DECIMALS)))
}

function formatPolygonWkt(polygon: JstsGeometry): string {
	const ring = polygon
		.getExteriorRing()
		.getCoordinates()
		.map(function formatCoordinate(coordinate) {
			return `${formatNumber(coordinate.x)} ${formatNumber(coordinate.y)}`
		})
		.join(',')
	return `((${ring}))`
}

function areBoxesClose(a: Box, b: Box): boolean {
	return (
		a[0] - BOX_MERGE_GAP_DEG <= b[2] &&
		b[0] - BOX_MERGE_GAP_DEG <= a[2] &&
		a[1] - BOX_MERGE_GAP_DEG <= b[3] &&
		b[1] - BOX_MERGE_GAP_DEG <= a[3]
	)
}

/** Merges neighbouring boxes until the remaining ones are all far from each other. */
function mergeCloseBoxes(boxes: Array<Box>): Array<Box> {
	const merged: Array<Box> = boxes.map(function copyBox(box) {
		return [...box] as Box
	})
	let hasMerged = true
	while (hasMerged) {
		hasMerged = false
		for (let first = 0; first < merged.length && !hasMerged; first += 1) {
			for (let second = first + 1; second < merged.length; second += 1) {
				if (areBoxesClose(merged[first], merged[second])) {
					const a = merged[first]
					const b = merged[second]
					merged[first] = [
						Math.min(a[0], b[0]),
						Math.min(a[1], b[1]),
						Math.max(a[2], b[2]),
						Math.max(a[3], b[3]),
					]
					merged.splice(second, 1)
					hasMerged = true
					break
				}
			}
		}
	}
	return merged.sort(function compareBoxes(a, b) {
		return a[0] - b[0] || a[1] - b[1]
	})
}

function getBox(geometry: JstsGeometry): Box {
	const envelope = geometry.getEnvelopeInternal()
	return [envelope.getMinX(), envelope.getMinY(), envelope.getMaxX(), envelope.getMaxY()]
}

export function buildCountryOutline(geometry: GeoJsonPolygonalGeometry): CountryOutline {
	const shells = listExteriorRings(geometry).map(function toSimplifiedShell(ring) {
		return simplifyShell(createShell(ring))
	})
	const grown = BufferOp.bufferOp(
		geometryFactory.createGeometryCollection(shells),
		OUTLINE_BUFFER_DEG,
		BUFFER_QUADRANT_SEGMENTS,
	) as JstsGeometry

	const simplified = TopologyPreservingSimplifier.simplify(
		removeHoles(grown),
		POST_SIMPLIFY_TOLERANCE_DEG,
	) as JstsGeometry

	const world = geometryFactory.createPolygon(
		geometryFactory.createLinearRing([
			new Coordinate(-180, -90),
			new Coordinate(180, -90),
			new Coordinate(180, 90),
			new Coordinate(-180, 90),
			new Coordinate(-180, -90),
		]),
	)
	const clipped = OverlayOp.intersection(simplified, world) as JstsGeometry

	const scale = 10 ** OUTLINE_DECIMALS
	const rounded = GeometryPrecisionReducer.reduce(
		clipped,
		new PrecisionModel(scale),
	) as JstsGeometry
	// Rounding can close a narrow strait into a hole or make two parts touch: clean up once more.
	const outline = removeHoles(rounded)
	const polygons = listPolygons(outline)
	if (polygons.length === 0 || !IsValidOp.isValid(outline)) {
		throw new Error('the simplified outline is empty or invalid')
	}

	return {
		bbox: getBox(outline),
		boxes: mergeCloseBoxes(polygons.map(getBox)),
		area_deg2: Number(outline.getArea().toFixed(2)),
		geom_wkt:
			polygons.length === 1
				? `POLYGON${formatPolygonWkt(polygons[0])}`
				: `MULTIPOLYGON(${polygons.map(formatPolygonWkt).join(',')})`,
	}
}
