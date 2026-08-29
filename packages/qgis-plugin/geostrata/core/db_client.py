"""Thread-safe SQLite client supporting FTS5 (with fallback) + R-Tree and multi-catalog ATTACH."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


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


def strip_diacritics(text: str) -> str:
    """Normalize string to lowercase and remove accents/diacritics."""
    normalized = unicodedata.normalize("NFD", text)
    without_marks = "".join(
        ch for ch in normalized if unicodedata.category(ch) != "Mn"
    )
    return without_marks.lower()


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
            if sub_token:
                cleaned_tokens.append(strip_diacritics(sub_token))

    return cleaned_tokens, is_global


def parse_search_query(raw_query: str) -> Tuple[str, bool]:
    """Extract the !global modifier and build an FTS5 prefix match expression.

    Returns:
        Tuple of (fts_match_expression, is_global_override).
    """
    cleaned_tokens, is_global = extract_search_tokens(raw_query)
    fts_expression = " AND ".join(f'"{tok}"*' for tok in cleaned_tokens)
    return fts_expression, is_global


def _sqlite_supports_fts5(conn: sqlite3.Connection) -> bool:
    """Return True if the current SQLite library was compiled with FTS5 enabled."""
    try:
        options = {
            row[0] for row in conn.execute("PRAGMA compile_options").fetchall()
        }
        return "ENABLE_FTS5" in options
    except Exception:
        return False


class CatalogDatabaseClient:
    """Read-only SQLite query engine for one or more Geostrata databases."""

    def __init__(self, sqlite_paths: Sequence[Path]) -> None:
        self._sqlite_paths: List[Path] = [
            Path(p) for p in sqlite_paths if Path(p).is_file()
        ]

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
    ) -> List[CatalogSourceRecord]:
        """Execute Step 1 search (SQLite R-Tree + FTS5/search_text) across all catalogs."""
        valid_paths = self.sqlite_paths
        if not valid_paths:
            return []

        tokens, is_global = extract_search_tokens(raw_query)
        fts_expr = " AND ".join(f'"{tok}"*' for tok in tokens)
        apply_bbox = (not is_global) and (canvas_bbox_4326 is not None)

        primary_path = valid_paths[0]
        primary_uri = f"{primary_path.resolve().as_uri()}?mode=ro"

        conn = sqlite3.connect(primary_uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        try:
            has_fts5 = _sqlite_supports_fts5(conn)
            schemas: List[str] = ["main"]
            for idx, extra_path in enumerate(valid_paths[1:], start=1):
                alias = f"cat_{idx}"
                extra_uri = f"{extra_path.resolve().as_uri()}?mode=ro"
                conn.execute(f"ATTACH DATABASE ? AS {alias}", (extra_uri,))
                schemas.append(alias)

            union_queries: List[str] = []
            query_params: List[object] = []

            for schema in schemas:
                where_clauses: List[str] = ["s.health_status != 'dead'"]
                join_clauses: List[str] = [
                    f"INNER JOIN {schema}.sources_rtree r ON s.rowid = r.id"
                ]

                if tokens:
                    if has_fts5:
                        join_clauses.append(
                            f"INNER JOIN {schema}.sources_fts f ON s.rowid = f.rowid"
                        )
                        where_clauses.append("f.sources_fts MATCH ?")
                        query_params.append(fts_expr)
                        rank_expr = "f.rank"
                    else:
                        for tok in tokens:
                            where_clauses.append("s.search_text LIKE ?")
                            query_params.append(f"% {tok}%")
                        rank_expr = "0.0"
                else:
                    rank_expr = "0.0"

                if apply_bbox and canvas_bbox_4326 is not None:
                    min_x, min_y, max_x, max_y = canvas_bbox_4326
                    where_clauses.append(
                        "r.maxX >= ? AND r.minX <= ? AND r.maxY >= ? AND r.minY <= ?"
                    )
                    query_params.extend([min_x, max_x, min_y, max_y])

                joins_sql = " ".join(join_clauses)
                where_sql = " AND ".join(where_clauses)

                subquery = f"""
                    SELECT
                        s.id,
                        s.name,
                        s.description,
                        s.type,
                        s.category,
                        s.url,
                        s.crs,
                        s.country_iso,
                        s.min_x,
                        s.min_y,
                        s.max_x,
                        s.max_y,
                        s.area_deg2,
                        s.geom_wkt,
                        s.tags_json,
                        s.license,
                        s.attribution,
                        s.style_qml,
                        s.min_zoom,
                        s.max_zoom,
                        s.archive_target,
                        s.health_status,
                        {rank_expr} AS fts_rank
                    FROM {schema}.sources s
                    {joins_sql}
                    WHERE {where_sql}
                """
                union_queries.append(subquery)

            full_sql = (
                "SELECT * FROM ("
                + " UNION ALL ".join(union_queries)
                + ") combined ORDER BY combined.area_deg2 ASC, combined.fts_rank ASC LIMIT ?"
            )
            query_params.append(limit)

            cursor = conn.execute(full_sql, query_params)
            rows = cursor.fetchall()

            results_by_id: Dict[str, CatalogSourceRecord] = {}
            for row in rows:
                source_id = str(row["id"])
                if source_id in results_by_id:
                    continue

                raw_tags = row["tags_json"]
                try:
                    parsed_tags = json.loads(raw_tags) if raw_tags else []
                except (ValueError, TypeError):
                    parsed_tags = []

                results_by_id[source_id] = CatalogSourceRecord(
                    id=source_id,
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
                    geom_wkt=str(row["geom_wkt"]),
                    tags=[str(t) for t in parsed_tags],
                    license=str(row["license"]),
                    attribution=str(row["attribution"]),
                    style_qml=str(row["style_qml"]) if row["style_qml"] else None,
                    min_zoom=int(row["min_zoom"])
                    if row["min_zoom"] is not None
                    else None,
                    max_zoom=int(row["max_zoom"])
                    if row["max_zoom"] is not None
                    else None,
                    archive_target=str(row["archive_target"])
                    if row["archive_target"]
                    else None,
                    health_status=str(row["health_status"] or "ok"),
                )

            return list(results_by_id.values())
        finally:
            conn.close()
