"""Thread-safe SQLite client: R-Tree + FTS5 (with LIKE fallback) candidate search, Python
ranking (text relevance + locality + curated priority) and multi-catalog ATTACH.

This module must stay importable without QGIS (see tests/test_core.py)."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..i18n import (  # noqa: F401  (re-exported for existing importers)
    SETTINGS_LANGUAGE_KEY,
    get_preferred_language,
)


# Sources whose bbox area is at least this large go to the "global" locator group.
GLOBAL_GROUP_AREA_DEG2 = 10000.0
# A bbox spanning at least this many degrees is "worldwide": no footprint worth drawing.
WORLDWIDE_MIN_SPAN_X = 350.0
WORLDWIDE_MIN_SPAN_Y = 150.0

ELI_ID_MARKER = "-eli-"


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def _clamp_priority(value: Any) -> int:
    """Coerce a curated priority to an int in [-10, 10] (0 when missing or invalid)."""
    try:
        return max(-10, min(10, int(value)))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class CatalogSourceRecord:
    """Represents a compiled geographic data source from catalog.sqlite."""

    id: str
    name: str
    description: str
    type: str
    category: str
    url: str
    crs: str
    country_iso: Optional[str]
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    area_deg2: float
    geom_wkt: str
    tags: List[str]
    license: str
    attribution: str
    style_qml: Optional[str]
    min_zoom: Optional[int]
    max_zoom: Optional[int]
    archive_target: Optional[str]
    health_status: str
    i18n: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Optional columns of recent catalogs (absent in older / third-party catalogs).
    priority: int = 0
    data_date: Optional[str] = None
    health_checked_at: Optional[str] = None

    @property
    def is_worldwide(self) -> bool:
        """True when the bbox is (nearly) the whole world, i.e. not worth drawing."""
        return (
            self.max_x - self.min_x >= WORLDWIDE_MIN_SPAN_X
            and self.max_y - self.min_y >= WORLDWIDE_MIN_SPAN_Y
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CatalogSourceRecord":
        """Rebuild a record from ``dataclasses.asdict()`` output (missing optional keys tolerated)."""
        raw_i18n = data.get("i18n")
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            description=str(data.get("description") or ""),
            type=str(data["type"]),
            category=str(data["category"]),
            url=str(data["url"]),
            crs=str(data.get("crs") or "EPSG:4326"),
            country_iso=str(data["country_iso"]) if data.get("country_iso") else None,
            min_x=float(data["min_x"]),
            min_y=float(data["min_y"]),
            max_x=float(data["max_x"]),
            max_y=float(data["max_y"]),
            area_deg2=float(data["area_deg2"]),
            geom_wkt=str(data.get("geom_wkt") or ""),
            tags=[str(t) for t in (data.get("tags") or [])],
            license=str(data.get("license") or ""),
            attribution=str(data.get("attribution") or ""),
            style_qml=str(data["style_qml"]) if data.get("style_qml") else None,
            min_zoom=_optional_int(data.get("min_zoom")),
            max_zoom=_optional_int(data.get("max_zoom")),
            archive_target=str(data["archive_target"])
            if data.get("archive_target")
            else None,
            health_status=str(data.get("health_status") or "ok"),
            i18n=dict(raw_i18n) if isinstance(raw_i18n, dict) else {},
            priority=_clamp_priority(data.get("priority")),
            data_date=_optional_text(data.get("data_date")),
            health_checked_at=_optional_text(data.get("health_checked_at")),
        )

    def localized_name(self, lang: Optional[str] = None) -> str:
        """Return the translated layer name for lang (or preferred language), falling back to English name."""
        target = (lang or get_preferred_language()).strip().lower()[:2]
        if target != "en" and self.i18n:
            entry = self.i18n.get(target)
            if isinstance(entry, dict):
                translated = entry.get("name")
                if translated and str(translated).strip():
                    return str(translated).strip()
        return self.name

    def localized_description(self, lang: Optional[str] = None) -> str:
        """Return the translated layer description for lang (or preferred language), falling back to English description."""
        target = (lang or get_preferred_language()).strip().lower()[:2]
        if target != "en" and self.i18n:
            entry = self.i18n.get(target)
            if isinstance(entry, dict):
                translated = entry.get("description")
                if translated and str(translated).strip():
                    return str(translated).strip()
        return self.description


def strip_diacritics(text: str) -> str:
    """Normalize string to lowercase and remove accents/diacritics."""
    normalized = unicodedata.normalize("NFD", text)
    without_marks = "".join(
        ch for ch in normalized if unicodedata.category(ch) != "Mn"
    )
    return without_marks.lower()


# Mirrors stripDiacritics() of packages/registry/src/build.ts (search_text column).
_SEARCH_TEXT_SEPARATORS = re.compile(r"[^a-z0-9\u0600-\u06ff]+")

# Same word boundaries as the FTS5 `unicode61` tokenizer: runs of letters/digits.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str) -> List[str]:
    """Split text into lowercase, diacritic-free word tokens (mirrors FTS5 unicode61)."""
    return _WORD_RE.findall(strip_diacritics(text))


def extract_search_tokens(raw_query: str) -> Tuple[List[str], bool]:
    """Extract normalized search tokens and whether !global was requested."""
    tokens = raw_query.strip().split()
    is_global = False
    cleaned_tokens: List[str] = []

    for token in tokens:
        if token.lower() == "!global":
            is_global = True
            continue
        sanitized = re.sub(r'["\'()*^:;\\]', " ", token).strip()
        for sub_token in sanitized.split():
            normalized = strip_diacritics(sub_token)
            # Drop pure punctuation ("-", "/"): it would make an empty FTS5 phrase.
            if normalized and _WORD_RE.search(normalized):
                cleaned_tokens.append(normalized)

    return cleaned_tokens, is_global


def parse_search_query(raw_query: str) -> Tuple[str, bool]:
    """Extract the !global modifier and build an FTS5 prefix match expression.

    Returns:
        Tuple of (fts_match_expression, is_global_override).
    """
    cleaned_tokens, is_global = extract_search_tokens(raw_query)
    fts_expression = " AND ".join(f'"{tok}"*' for tok in cleaned_tokens)
    return fts_expression, is_global


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
#
#   score = W_TEXT * text_relevance        (0..1, only when the query has words)
#         + W_LOCALITY * locality          (0..1, small footprints first)
#         + W_PRIORITY * priority / 10     (curated `sources.priority`, -10..10)
#         - DEGRADED_PENALTY               (only when health_status == 'degraded')
#
# text_relevance is the mean, over the query words, of the best
# `field_weight * match_quality` found for that word:
#
#   field_weight   name (any language) 1.0 > curated tags / category / type 0.9
#                  > synonyms 0.75 > country names 0.6 > description 0.5 > anything else 0.3
#   match_quality  1.0 for an exact word, else for a prefix match
#                  PREFIX_BASE + PREFIX_SPAN * len(query word) / len(matched word)
#                  (0.3..0.8: "hospital" -> "hospitals" 0.74, "dem" -> "democratic" 0.45)
#
# so an exact word always beats a prefix in the same field, a name/tag hit beats a
# description hit, and `dem` ranks "Copernicus DEM" (exact, name: 1.0) far above
# "... Democratic Republic of the Congo" (prefix, name: 0.45).
#
# locality = 1 - log10(area / LOCAL_AREA) / log10(WORLD_AREA / LOCAL_AREA), clamped to
# 0..1: a city dataset (0.15 deg2) scores 0.83, a country (140 deg2) 0.39, the world 0.
#
# With W_TEXT = 2, W_LOCALITY = 1, W_PRIORITY = 0.5 the text match dominates (a wrong
# word match cannot be rescued by being local or by a +10 priority), locality orders
# equally relevant layers local -> national -> global as before, and priority is a
# curated nudge worth at most half of the full city-to-world locality range.

W_TEXT = 2.0
W_LOCALITY = 1.0
W_PRIORITY = 0.5
DEGRADED_PENALTY = 0.25

FIELD_WEIGHT_NAME = 1.0
FIELD_WEIGHT_TAGS = 0.9
FIELD_WEIGHT_SYNONYMS = 0.75
FIELD_WEIGHT_COUNTRY = 0.6
FIELD_WEIGHT_DESCRIPTION = 0.5
FIELD_WEIGHT_OTHER = 0.3
# A row returned by the FTS5/LIKE filter matched somewhere, even if no field above
# explains it (e.g. a hit in the id): never score such a word at zero.
MATCH_FLOOR = 0.1

PREFIX_BASE = 0.3
PREFIX_SPAN = 0.5

LOCAL_AREA_DEG2 = 0.01
WORLD_AREA_DEG2 = 64800.0


def match_quality(term: str, tokens: Iterable[str]) -> float:
    """Return 1.0 for an exact word match, 0.3..0.8 for a prefix match, 0.0 otherwise."""
    best = 0.0
    for token in tokens:
        if token == term:
            return 1.0
        if token.startswith(term):
            quality = PREFIX_BASE + PREFIX_SPAN * (len(term) / len(token))
            if quality > best:
                best = quality
    return best


def compute_text_relevance(
    terms: Sequence[str],
    weighted_fields: Sequence[Tuple[float, Sequence[str]]],
) -> float:
    """Score how well the query words match an entry, in [0, 1].

    Args:
        terms: normalized query words (see :func:`tokenize`).
        weighted_fields: ``(field_weight, texts)`` pairs sorted by decreasing weight.

    Each word gets the best ``field_weight * match_quality`` over the fields (fields are
    tokenized lazily and skipped once they cannot improve the word's score); the result
    is the mean over the words. Returns 0.0 for an empty query.
    """
    if not terms:
        return 0.0

    token_cache: Dict[int, Set[str]] = {}
    total = 0.0
    for term in terms:
        best = 0.0
        for idx, (weight, texts) in enumerate(weighted_fields):
            if best >= weight:
                break
            tokens = token_cache.get(idx)
            if tokens is None:
                tokens = set(tokenize(" ".join(t for t in texts if t)))
                token_cache[idx] = tokens
            candidate = weight * match_quality(term, tokens)
            if candidate > best:
                best = candidate
        total += best if best > 0.0 else MATCH_FLOOR
    return total / len(terms)


def compute_locality(area_deg2: float) -> float:
    """Map a bbox area in square degrees to [0, 1] on a log scale (1 = city, 0 = world)."""
    area = min(max(float(area_deg2), LOCAL_AREA_DEG2), WORLD_AREA_DEG2)
    span = math.log10(WORLD_AREA_DEG2 / LOCAL_AREA_DEG2)
    return 1.0 - math.log10(area / LOCAL_AREA_DEG2) / span


def compute_rank_score(
    text_relevance: float,
    area_deg2: float,
    priority: int = 0,
    health_status: str = "ok",
) -> float:
    """Combine text relevance, locality and curated priority (see the block comment above)."""
    score = (
        W_TEXT * text_relevance
        + W_LOCALITY * compute_locality(area_deg2)
        + W_PRIORITY * (_clamp_priority(priority) / 10.0)
    )
    if health_status == "degraded":
        score -= DEGRADED_PENALTY
    return score


# The "local & national" group is listed first, as before, unless the best global
# result outranks the best local one by more than this rank-score margin (a clear
# text-relevance gap, e.g. an exact name match against a mere prefix match).
GROUP_LEAD_MARGIN = 0.5


def global_group_leads(
    scored_entries: Sequence[Tuple["CatalogSourceRecord", float]],
) -> bool:
    """Decide which locator group comes first for a ranked result list."""
    best_local: Optional[float] = None
    best_global: Optional[float] = None
    for entry, score in scored_entries:
        if entry.area_deg2 < GLOBAL_GROUP_AREA_DEG2:
            if best_local is None:
                best_local = score
        elif best_global is None:
            best_global = score
        if best_local is not None and best_global is not None:
            break
    if best_local is None or best_global is None:
        return False
    return best_global - best_local > GROUP_LEAD_MARGIN


def _split_synonyms(enriched_tags: str, i18n: Dict[str, Any]) -> str:
    """Remove the translated names/descriptions the compiler appends to `enriched_tags`.

    build.ts stores ``tags + synonyms + i18n names + i18n descriptions`` in that FTS
    column; only the first part deserves tag-level weight.
    """
    parts: List[str] = []
    for loc in i18n.values():
        if isinstance(loc, dict):
            for key in ("name", "description"):
                value = loc.get(key)
                if value:
                    parts.append(str(value))
    suffix = " ".join(parts)
    if suffix and enriched_tags.endswith(suffix):
        return enriched_tags[: -len(suffix)]
    return enriched_tags


def _split_search_text(search_text: str, record: "CatalogSourceRecord") -> Tuple[str, str]:
    """Split the `search_text` fallback column into ``(synonyms, other)``.

    build.ts writes ``id name description i18n... category type`` first, then the
    enriched tags (tags + synonyms) and the country names. When that known head is
    found, the remainder is returned as synonyms; otherwise everything is "other".
    """
    head_parts: List[str] = [record.id, record.name, record.description]
    for loc in record.i18n.values():
        if isinstance(loc, dict):
            for key in ("name", "description"):
                if loc.get(key):
                    head_parts.append(str(loc[key]))
    head_parts.extend([record.category, record.type])
    head = _SEARCH_TEXT_SEPARATORS.sub(" ", strip_diacritics(" ".join(head_parts))).strip()
    body = search_text.strip()
    if head and body.startswith(head + " "):
        return body[len(head) + 1 :], ""
    return "", body


def _i18n_values(i18n: Dict[str, Any], key: str) -> List[str]:
    values: List[str] = []
    for loc in i18n.values():
        if isinstance(loc, dict) and loc.get(key):
            values.append(str(loc[key]))
    return values


def _sqlite_supports_fts5(conn: sqlite3.Connection) -> bool:
    """Return True if the current SQLite library was compiled with FTS5 enabled."""
    try:
        options = {
            row[0] for row in conn.execute("PRAGMA compile_options").fetchall()
        }
        return "ENABLE_FTS5" in options
    except Exception:
        return False


# Columns added to `sources` over time: name -> SQL literal used when the column is absent.
_OPTIONAL_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("i18n_json", "'{}'"),
    ("priority", "0"),
    ("data_date", "NULL"),
    ("health_checked_at", "NULL"),
)


class CatalogDatabaseClient:
    """Read-only SQLite query engine for one or more Geostrata databases."""

    def __init__(self, sqlite_paths: Sequence[Path]) -> None:
        self._sqlite_paths: List[Path] = [
            Path(p) for p in sqlite_paths if Path(p).is_file()
        ]
        # Tests set this to False to exercise the `search_text LIKE` fallback.
        self.allow_fts5: bool = True

    @property
    def sqlite_paths(self) -> List[Path]:
        """Return currently configured valid SQLite file paths."""
        return [p for p in self._sqlite_paths if p.is_file()]

    def set_sqlite_paths(self, sqlite_paths: Sequence[Path]) -> None:
        """Update the list of SQLite catalog paths."""
        self._sqlite_paths = [Path(p) for p in sqlite_paths if Path(p).is_file()]

    def search_sources(
        self,
        raw_query: str,
        canvas_bbox_4326: Optional[Tuple[float, float, float, float]] = None,
        limit: int = 50,
        *,
        include_eli: bool = True,
        category: Optional[str] = None,
        accept: Optional[Callable[[CatalogSourceRecord], bool]] = None,
    ) -> List[CatalogSourceRecord]:
        """Search all catalogs and return the best `limit` sources, best first.

        1. SQL (per catalog): R-Tree bbox filter + FTS5 prefix match (or
           ``search_text LIKE`` when FTS5 is unavailable), without ORDER BY / LIMIT.
        2. Python: every candidate is scored with :func:`compute_rank_score`
           (text relevance + locality + curated priority) and sorted.
        3. Filters are applied *before* the limit, walking the ranked list: ``include_eli``,
           ``category`` and the caller's ``accept`` predicate (the UI passes the exact
           geometry intersection test), so a good result is never cut by an early LIMIT.

        Args:
            raw_query: user text; ``!global`` disables the bbox filter.
            canvas_bbox_4326: (min_x, min_y, max_x, max_y) of the map canvas, or None.
            limit: maximum number of records returned (after filtering).
            include_eli: keep OSM Editor Layer Index entries (ids containing ``-eli-``).
            category: keep only this category when set.
            accept: optional predicate; records for which it returns False are skipped.
        """
        return [
            record
            for record, _ in self.search_sources_scored(
                raw_query,
                canvas_bbox_4326,
                limit,
                include_eli=include_eli,
                category=category,
                accept=accept,
            )
        ]

    def search_sources_scored(
        self,
        raw_query: str,
        canvas_bbox_4326: Optional[Tuple[float, float, float, float]] = None,
        limit: int = 50,
        *,
        include_eli: bool = True,
        category: Optional[str] = None,
        accept: Optional[Callable[[CatalogSourceRecord], bool]] = None,
    ) -> List[Tuple[CatalogSourceRecord, float]]:
        """Same as :meth:`search_sources` but returns ``(record, rank_score)`` pairs."""
        valid_paths = self.sqlite_paths
        if not valid_paths:
            return []

        tokens, is_global = extract_search_tokens(raw_query)
        terms = tokenize(" ".join(tokens))
        bbox = canvas_bbox_4326 if not is_global else None

        primary_uri = f"{valid_paths[0].resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(primary_uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        try:
            use_fts5 = self.allow_fts5 and _sqlite_supports_fts5(conn)
            schemas: List[str] = ["main"]
            for idx, extra_path in enumerate(valid_paths[1:], start=1):
                alias = f"cat_{idx}"
                extra_uri = f"{extra_path.resolve().as_uri()}?mode=ro"
                conn.execute(f"ATTACH DATABASE ? AS {alias}", (extra_uri,))
                schemas.append(alias)

            scored: List[Tuple[float, CatalogSourceRecord]] = []
            seen_ids: Set[str] = set()
            for schema in schemas:
                rows: List[sqlite3.Row] = []
                if use_fts5 and tokens:
                    try:
                        rows = self._query_schema(conn, schema, tokens, terms, bbox, True)
                    except sqlite3.OperationalError:
                        # Catalog without a usable sources_fts table: LIKE fallback.
                        rows = self._query_schema(conn, schema, tokens, terms, bbox, False)
                else:
                    rows = self._query_schema(conn, schema, tokens, terms, bbox, False)

                for row in rows:
                    source_id = str(row["id"])
                    # First configured catalog wins when two catalogs share an id.
                    if source_id in seen_ids:
                        continue
                    seen_ids.add(source_id)
                    if not include_eli and ELI_ID_MARKER in source_id:
                        continue
                    if category and str(row["category"]) != category:
                        continue
                    record = self._row_to_record(row)
                    relevance = (
                        self._row_text_relevance(row, record, terms) if terms else 0.0
                    )
                    score = compute_rank_score(
                        relevance,
                        record.area_deg2,
                        record.priority,
                        record.health_status,
                    )
                    scored.append((score, record))

            scored.sort(key=lambda item: (-item[0], item[1].area_deg2, item[1].name, item[1].id))

            results: List[Tuple[CatalogSourceRecord, float]] = []
            for score, record in scored:
                if len(results) >= limit:
                    break
                if accept is not None and not accept(record):
                    continue
                results.append((record, score))
            return results
        finally:
            conn.close()

    @staticmethod
    def _query_schema(
        conn: sqlite3.Connection,
        schema: str,
        tokens: Sequence[str],
        terms: Sequence[str],
        bbox: Optional[Tuple[float, float, float, float]],
        use_fts5: bool,
    ) -> List[sqlite3.Row]:
        """Return every non-dead source of one catalog matching the words and the bbox."""
        cols = {
            row[1]
            for row in conn.execute(f"PRAGMA {schema}.table_info(sources)").fetchall()
        }
        select_exprs: List[str] = [
            "s.id", "s.name", "s.description", "s.type", "s.category", "s.url", "s.crs",
            "s.country_iso", "s.min_x", "s.min_y", "s.max_x", "s.max_y", "s.area_deg2",
            "s.geom_wkt", "s.tags_json", "s.license", "s.attribution", "s.style_qml",
            "s.min_zoom", "s.max_zoom", "s.archive_target", "s.health_status",
        ]
        for col_name, default_sql in _OPTIONAL_COLUMNS:
            select_exprs.append(
                f"s.{col_name}" if col_name in cols else f"{default_sql} AS {col_name}"
            )

        joins: List[str] = []
        where: List[str] = ["s.health_status != 'dead'"]
        params: List[object] = []

        if tokens and use_fts5:
            joins.append(f"INNER JOIN {schema}.sources_fts f ON s.rowid = f.rowid")
            where.append("f.sources_fts MATCH ?")
            params.append(" AND ".join(f'"{tok}"*' for tok in tokens))
            select_exprs.append("f.enriched_tags AS fts_tags")
            select_exprs.append("f.country_names AS fts_countries")
        elif tokens:
            # search_text is ' word word word ' (normalized by the compiler).
            for term in terms:
                where.append("s.search_text LIKE ?")
                params.append(f"% {term}%")
            select_exprs.append("s.search_text AS search_text")

        if bbox is not None:
            joins.append(f"INNER JOIN {schema}.sources_rtree r ON s.rowid = r.id")
            where.append("r.maxX >= ? AND r.minX <= ? AND r.maxY >= ? AND r.minY <= ?")
            min_x, min_y, max_x, max_y = bbox
            params.extend([min_x, max_x, min_y, max_y])

        sql = (
            f"SELECT {', '.join(select_exprs)} FROM {schema}.sources s "
            f"{' '.join(joins)} WHERE {' AND '.join(where)}"
        )
        return conn.execute(sql, params).fetchall()

    @staticmethod
    def _row_text_relevance(
        row: sqlite3.Row,
        record: CatalogSourceRecord,
        terms: Sequence[str],
    ) -> float:
        """Build the weighted fields of a row and score them against the query words."""
        keys = row.keys()
        synonyms = ""
        countries = record.country_iso or ""
        other = ""
        if "fts_tags" in keys:
            synonyms = _split_synonyms(str(row["fts_tags"] or ""), record.i18n)
            countries = f"{countries} {row['fts_countries'] or ''}"
        if "search_text" in keys:
            synonyms, other = _split_search_text(str(row["search_text"] or ""), record)

        weighted_fields: List[Tuple[float, Sequence[str]]] = [
            (FIELD_WEIGHT_NAME, [record.name] + _i18n_values(record.i18n, "name")),
            (FIELD_WEIGHT_TAGS, list(record.tags) + [record.category, record.type]),
            (FIELD_WEIGHT_SYNONYMS, [synonyms]),
            (FIELD_WEIGHT_COUNTRY, [countries]),
            (
                FIELD_WEIGHT_DESCRIPTION,
                [record.description] + _i18n_values(record.i18n, "description"),
            ),
            (FIELD_WEIGHT_OTHER, [other]),
        ]
        return compute_text_relevance(terms, weighted_fields)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> CatalogSourceRecord:
        """Convert a SQLite row into a CatalogSourceRecord (tolerating bad JSON / NULLs)."""
        try:
            parsed_tags = json.loads(row["tags_json"]) if row["tags_json"] else []
            if not isinstance(parsed_tags, list):
                parsed_tags = []
        except (ValueError, TypeError):
            parsed_tags = []

        try:
            parsed_i18n = json.loads(row["i18n_json"]) if row["i18n_json"] else {}
            if not isinstance(parsed_i18n, dict):
                parsed_i18n = {}
        except (ValueError, TypeError):
            parsed_i18n = {}

        return CatalogSourceRecord(
            id=str(row["id"]),
            name=str(row["name"]),
            description=str(row["description"] or ""),
            type=str(row["type"]),
            category=str(row["category"]),
            url=str(row["url"]),
            crs=str(row["crs"] or "EPSG:4326"),
            country_iso=str(row["country_iso"]) if row["country_iso"] else None,
            min_x=float(row["min_x"]),
            min_y=float(row["min_y"]),
            max_x=float(row["max_x"]),
            max_y=float(row["max_y"]),
            area_deg2=float(row["area_deg2"]),
            geom_wkt=str(row["geom_wkt"] or ""),
            tags=[str(t) for t in parsed_tags],
            license=str(row["license"] or ""),
            attribution=str(row["attribution"] or ""),
            style_qml=str(row["style_qml"]) if row["style_qml"] else None,
            min_zoom=_optional_int(row["min_zoom"]),
            max_zoom=_optional_int(row["max_zoom"]),
            archive_target=str(row["archive_target"]) if row["archive_target"] else None,
            health_status=str(row["health_status"] or "ok"),
            i18n=parsed_i18n,
            priority=_clamp_priority(row["priority"]),
            data_date=_optional_text(row["data_date"]),
            health_checked_at=_optional_text(row["health_checked_at"]),
        )
