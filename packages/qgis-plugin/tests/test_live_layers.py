"""Live integration test: load real catalog layers in a headless QGIS.

Every selected entry of ``catalog.sqlite`` is built exactly as the plugin does
(``geostrata.core.uri_builder.create_qgis_layer``) and then exercised one step
further than ``isValid()``:

- xyz / tms / vectortile: one real tile inside the entry footprint is fetched
  and its HTTP status and content are checked;
- wfs / geojson / fgb / vector archives: the first feature is read;
- wms / wmts / cog / vrt / raster archives: the extent and one small block are read;
- overpass: the query is built for every entry, a real query is run for a few.

Outcomes:

- ``ok``           the layer loads and returns data;
- ``broken``       404/410/4xx, invalid layer on a reachable server, wrong
                   content, no data. Fails the run (exit code 1);
- ``unreachable``  timeout, DNS/connection error, 5xx, 401/403/408/429.
                   Reported, never fails the run;
- ``allowlisted``  broken but listed in ``live_layers_allowlist.txt``;
- ``skipped``      cannot be probed (unknown URL placeholder, unusual tile grid).

Run (no pytest needed, any Python that can ``import qgis``)::

    QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/python \
        packages/qgis-plugin/tests/test_live_layers.py --changed origin/main --smoke

The parent process only orchestrates: it never imports ``qgis``, so ``--list``
works with a plain ``python3``. Layers are loaded in worker subprocesses (one
QGIS each), which gives real parallelism and a hard per-layer timeout.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import hashlib
import json
import math
import os
import queue
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_PARENT_DIR = TESTS_DIR.parent
REPO_ROOT = PLUGIN_PARENT_DIR.parent.parent
DEFAULT_CATALOG = PLUGIN_PARENT_DIR.parent / "registry" / "dist" / "catalog.sqlite"
DEFAULT_ALLOWLIST = TESTS_DIR / "live_layers_allowlist.txt"
SOURCES_DIR_REL = "packages/registry/sources"
# Per-country templates of the registry (see packages/registry/src/expand.ts).
TEMPLATE_KEY = "for_each_country"
COUNTRY_INDEX_PATH = REPO_ROOT / "packages" / "registry" / "assets" / "polygons" / "country-index.json"

RESULT_MARK = "@@GEOSTRATA-LIVE@@ "
RESULT_SCHEMA_VERSION = 1

TILE_TYPES = ("xyz", "tms", "vectortile")
# Large machine-generated families: one provider, hundreds of near-identical entries.
FAMILY_MARKERS = ("-eli-", "-geoboundaries-")
FAMILY_MIN_SIZE = 20

STATUS_OK = "ok"
STATUS_BROKEN = "broken"
STATUS_UNREACHABLE = "unreachable"
STATUS_SKIPPED = "skipped"
STATUS_ALLOWLISTED = "allowlisted"

# Same split as packages/registry/src/healthcheck.ts: these answers say
# "not now / not you", not "this resource is gone".
TRANSIENT_HTTP_STATUSES = {401, 403, 408, 425, 429}

EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_USAGE = 2
EXIT_TOO_MANY_UNREACHABLE = 3

# Small populated area (Lyon centre) used for the real Overpass query.
OVERPASS_TEST_BBOX = (4.825, 45.755, 4.845, 45.770)

# Upper bound of tile requests for one layer; a good tile stops the search.
MAX_TILE_PROBES = 9


# --------------------------------------------------------------------------
# Pure helpers (no QGIS import): selection, URL handling, classification
# --------------------------------------------------------------------------


def stable_rank(seed: str, source_id: str) -> str:
    """Deterministic pseudo-random ordering key."""
    return hashlib.sha1(f"{seed}|{source_id}".encode("utf-8")).hexdigest()


def build_family_index(source_ids: Iterable[str]) -> Dict[str, str]:
    """Map id -> family name for machine-generated families.

    A family is one provider published as many near-identical entries: the
    known markers (``-eli-``, ``-geoboundaries-``) and any id suffix shared by
    at least FAMILY_MIN_SIZE entries once the leading country code is removed
    (``xx-hotosm-roads`` for every country, from a ``for_each_country`` template).
    """
    ids = list(source_ids)
    suffix_counts: Dict[str, int] = {}
    for source_id in ids:
        suffix = source_id.split("-", 1)[1] if "-" in source_id else ""
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
    index: Dict[str, str] = {}
    for source_id in ids:
        marker = next((m for m in FAMILY_MARKERS if m in source_id), None)
        if marker:
            index[source_id] = marker.strip("-")
            continue
        suffix = source_id.split("-", 1)[1] if "-" in source_id else ""
        if suffix and suffix_counts[suffix] >= FAMILY_MIN_SIZE:
            index[source_id] = suffix
    return index


def load_catalog_rows(catalog_path: Path) -> List[Dict[str, Any]]:
    """Read every column of every source. No fixed column list: the schema grows."""
    uri = f"{catalog_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute("SELECT * FROM sources ORDER BY id ASC")
        return [{key: row[key] for key in row.keys()} for row in cursor.fetchall()]
    finally:
        conn.close()


def split_csv(values: Optional[Sequence[str]]) -> List[str]:
    items: List[str] = []
    for value in values or []:
        items.extend(part.strip() for part in value.split(",") if part.strip())
    return items


def run_git(args: Sequence[str], repo_root: Path) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def parse_diff_range(range_spec: str, repo_root: Path) -> Tuple[str, Optional[str]]:
    """Return (base_rev, head_rev). head_rev None means the working tree."""
    if "..." in range_spec:
        left, right = range_spec.split("...", 1)
        right = right or "HEAD"
        merge_base = run_git(["merge-base", left, right], repo_root)
        if not merge_base:
            raise ValueError(f"cannot compute merge-base for '{range_spec}'")
        return merge_base.strip(), right
    if ".." in range_spec:
        left, right = range_spec.split("..", 1)
        return left, (right or "HEAD")
    return range_spec, None


def _entries_from_yaml_text(text: Optional[str]) -> Tuple[Dict[str, str], bool]:
    """Map entry id -> canonical JSON of the entry (file defaults merged in).

    Supports the three file shapes of the registry: a single entry, a list of
    entries, and ``{defaults, sources}``. Returns (entries, parsed_ok).
    """
    if text is None:
        return {}, True
    try:
        import yaml  # PyYAML ships with QGIS and with ubuntu-latest runners.

        parsed = yaml.safe_load(text)
    except Exception:
        ids = re.findall(r"^\s*(?:-\s+)?id:\s*['\"]?([^\s'\"#]+)", text, flags=re.M)
        return {source_id: "" for source_id in ids}, False

    defaults: Dict[str, Any] = {}
    if isinstance(parsed, dict) and isinstance(parsed.get("sources"), list):
        raw_entries = parsed["sources"]
        if isinstance(parsed.get("defaults"), dict):
            defaults = parsed["defaults"]
    elif isinstance(parsed, list):
        raw_entries = parsed
    elif isinstance(parsed, dict):
        raw_entries = [parsed]
    else:
        raw_entries = []

    entries: Dict[str, str] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        merged = {**defaults, **raw_entry}  # The id itself may come from the defaults.
        if "id" not in merged:
            continue
        canonical = json.dumps(merged, sort_keys=True, default=str)
        for pattern in _expand_template_id(str(merged["id"]), merged.get(TEMPLATE_KEY)):
            entries[pattern] = entries.get(pattern, "") + canonical
    return entries, True


def _load_country_index() -> Dict[str, Any]:
    try:
        return json.loads(COUNTRY_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _expand_template_id(yaml_id: str, countries: Any) -> List[str]:
    """Ids (or globs) a possibly templated YAML id stands for in the catalog."""
    if not re.search(r"\{[A-Za-z0-9_]+\}", yaml_id):
        return [yaml_id]
    index = _load_country_index()
    if not isinstance(countries, list) or not index:
        return [yaml_id_to_glob(yaml_id)]  # "all" or no index: every country.
    expanded: List[str] = []
    for code in countries:
        iso3 = str(code).upper()
        iso2 = str((index.get(iso3) or {}).get("iso2") or "")
        if not iso2:
            return [yaml_id_to_glob(yaml_id)]
        replaced = (
            yaml_id.replace("{iso2}", iso2.lower())
            .replace("{ISO2}", iso2.upper())
            .replace("{iso3}", iso3.lower())
            .replace("{ISO3}", iso3)
        )
        expanded.append(yaml_id_to_glob(replaced))
    return expanded


def yaml_id_to_glob(yaml_id: str) -> str:
    """A templated id (``{iso2}-foo``) expands to many catalog ids."""
    return re.sub(r"\{[A-Za-z0-9_]+\}", "*", yaml_id)


def changed_id_patterns(
    range_spec: str, repo_root: Path
) -> Tuple[List[str], List[str]]:
    """Map YAML files changed in a git range to id globs. Returns (globs, notes)."""
    base_rev, head_rev = parse_diff_range(range_spec, repo_root)
    diff_args = ["diff", "--name-only", "--diff-filter=ACMR", base_rev]
    if head_rev:
        diff_args.append(head_rev)
    diff_args += ["--", SOURCES_DIR_REL]
    diff_out = run_git(diff_args, repo_root)
    if diff_out is None:
        raise ValueError(
            f"git diff failed for '{range_spec}' (unknown revision or shallow clone?)"
        )
    changed_files = [line.strip() for line in diff_out.splitlines() if line.strip()]
    if head_rev is None:
        untracked = run_git(
            ["ls-files", "--others", "--exclude-standard", "--", SOURCES_DIR_REL],
            repo_root,
        )
        changed_files += [
            line.strip() for line in (untracked or "").splitlines() if line.strip()
        ]

    patterns: List[str] = []
    notes: List[str] = []
    for rel_path in sorted(set(changed_files)):
        if not rel_path.lower().endswith((".yaml", ".yml")):
            continue
        old_text = run_git(["show", f"{base_rev}:{rel_path}"], repo_root)
        if head_rev:
            new_text = run_git(["show", f"{head_rev}:{rel_path}"], repo_root)
        else:
            file_path = repo_root / rel_path
            new_text = (
                file_path.read_text(encoding="utf-8") if file_path.is_file() else None
            )
        old_entries, old_ok = _entries_from_yaml_text(old_text)
        new_entries, new_ok = _entries_from_yaml_text(new_text)
        if not (old_ok and new_ok):
            notes.append(f"{rel_path}: YAML not parsed, every id of the file selected")
            file_patterns = [yaml_id_to_glob(key) for key in new_entries]
        else:
            file_patterns = [
                key for key, value in new_entries.items() if old_entries.get(key) != value
            ]
        notes.append(f"{rel_path}: {len(file_patterns)} changed entr(y/ies)")
        patterns.extend(file_patterns)
    return patterns, notes


def pick_smoke_ids(
    rows: Sequence[Dict[str, Any]], per_type: int, seed: str, families: Dict[str, str]
) -> List[str]:
    """A few deterministic entries per source type: exercises every URI builder branch."""
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_type.setdefault(str(row["type"]).lower(), []).append(row)
    picked: List[str] = []
    for source_type in sorted(by_type):
        candidates = sorted(
            by_type[source_type],
            key=lambda row: (
                str(row["id"]) in families,
                str(row.get("health_status") or "ok") == "dead",
                stable_rank(seed, str(row["id"])),
            ),
        )
        picked.extend(str(row["id"]) for row in candidates[:per_type])
    return picked


def select_rows(
    rows: Sequence[Dict[str, Any]],
    args: argparse.Namespace,
    repo_root: Path,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply the selection flags (union of selectors, then sampling and excludes)."""
    notes: List[str] = []
    all_ids = [str(row["id"]) for row in rows]
    families = build_family_index(all_ids)
    types = {value.lower() for value in split_csv(args.type)}
    id_globs = split_csv(args.id)
    if args.ids_file:
        for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                id_globs.append(line)

    has_selector = bool(
        types
        or id_globs
        or args.ids_file
        or args.changed
        or args.families
        or args.smoke is not None
    )
    selected: Set[str] = set()
    explicit: Set[str] = set()

    if args.all or not has_selector:
        selected.update(all_ids)
    if types:
        selected.update(
            str(row["id"]) for row in rows if str(row["type"]).lower() in types
        )
    if args.families:
        selected.update(families)
    for glob in id_globs:
        matches = fnmatch.filter(all_ids, glob)
        if not matches:
            notes.append(f"--id '{glob}': no catalog entry matches")
        selected.update(matches)
        if not re.search(r"[*?\[]", glob):
            explicit.add(glob)  # A literal id is never sampled away.
    if args.changed:
        globs, change_notes = changed_id_patterns(args.changed, repo_root)
        notes.extend(change_notes)
        matched = 0
        for glob in globs:
            matches = fnmatch.filter(all_ids, glob)
            if not matches:
                notes.append(f"changed id '{glob}' is not in the compiled catalog")
            matched += len(matches)
            selected.update(matches)
        notes.append(f"--changed {args.changed}: {matched} catalog entr(y/ies)")
    if args.smoke is not None:
        smoke_ids = pick_smoke_ids(rows, args.smoke, args.sample_seed, families)
        notes.append(f"--smoke: {len(smoke_ids)} entr(y/ies)")
        selected.update(smoke_ids)
        explicit.update(smoke_ids)

    if args.sample_families and args.sample_families > 0:
        for family in sorted({families[i] for i in selected if i in families}):
            members = sorted(
                (i for i in selected if families.get(i) == family and i not in explicit),
                key=lambda source_id: stable_rank(args.sample_seed, source_id),
            )
            dropped = members[args.sample_families :]
            if dropped:
                selected.difference_update(dropped)
                notes.append(
                    f"family '{family}': sampled {args.sample_families} of {len(members)}"
                )

    for glob in split_csv(args.exclude):
        selected.difference_update(fnmatch.filter(all_ids, glob))

    return [row for row in rows if str(row["id"]) in selected], notes


def load_allowlist(path: Optional[Path]) -> List[Tuple[str, str]]:
    """Lines are ``<id or glob> <reason>``. A reason is mandatory."""
    if path is None or not path.is_file():
        return []
    entries: List[Tuple[str, str]] = []
    for line_no, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            raise ValueError(f"{path}:{line_no}: missing reason after '{parts[0]}'")
        entries.append((parts[0], parts[1].strip()))
    return entries


def strip_vsi_prefixes(url: str) -> str:
    previous = None
    current = url.strip()
    while previous != current:
        previous = current
        current = re.sub(r"^/vsi[a-z_]+/+", "", current)
    return current


def probe_url_for(source_type: str, raw_url: str) -> Optional[str]:
    """Plain HTTP URL telling whether the remote side of a failed layer still exists."""
    url = raw_url.strip()
    if "contextualWMSLegend=" in url or url.startswith("crs="):
        # Already a QGIS wms provider URI: the `url=` parameter is requested as is.
        match = re.search(r"(?:^|&)url=([^&]+)", url)
        if not match:
            return None
        return urllib.parse.unquote(match.group(1))
    if "restrictToRequestBBOX=" in url:
        match = re.search(r"url='([^']+)'", url)
        if not match:
            return None
        url = match.group(1)

    url = strip_vsi_prefixes(url)
    if not url.lower().startswith(("http://", "https://")):
        return None
    ogc_service = {"wms": "WMS", "wmts": "WMTS", "wfs": "WFS"}.get(source_type)
    if ogc_service:
        base_url = url.split("?", 1)[0]
        if base_url.lower().endswith(".xml"):
            return base_url
        return f"{base_url}?SERVICE={ogc_service}&REQUEST=GetCapabilities"
    return url


def classify_http(http_status: Optional[int], error_text: str) -> Tuple[str, str]:
    """Map one HTTP outcome to (status, reason)."""
    if http_status is None:
        return STATUS_UNREACHABLE, f"network: {error_text or 'no response'}"[:200]
    if 200 <= http_status < 400:
        return STATUS_OK, f"http_{http_status}"
    if http_status >= 500 or http_status in TRANSIENT_HTTP_STATUSES:
        return STATUS_UNREACHABLE, f"http_{http_status}"
    return STATUS_BROKEN, f"http_{http_status}"


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> Tuple[int, int]:
    lat = max(min(lat, 85.0511), -85.0511)
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def quadkey(x: int, y: int, zoom: int) -> str:
    digits = []
    for level in range(zoom, 0, -1):
        mask = 1 << (level - 1)
        digits.append(str((1 if x & mask else 0) + (2 if y & mask else 0)))
    return "".join(digits)


def fill_tile_template(template: str, zoom: int, x: int, y: int) -> Optional[str]:
    """Substitute the placeholders QGIS understands. None when something is left over."""
    url = template
    url = re.sub(
        r"\{switch:([^}]+)\}", lambda match: match.group(1).split(",")[0], url
    )
    replacements = {
        "{z}": str(zoom),
        "{x}": str(x),
        "{y}": str(y),
        "{-y}": str(2**zoom - 1 - y),
        "{q}": quadkey(x, y, zoom),
        "{s}": "a",
        "{r}": "",
    }
    for placeholder, value in replacements.items():
        url = url.replace(placeholder, value)
    return None if re.search(r"\{[^}]*\}", url) else url


def candidate_tiles(
    bbox: Tuple[float, float, float, float],
    points: Sequence[Tuple[float, float]],
    min_zoom: Optional[int],
    max_zoom: Optional[int],
    default_max_zoom: int,
) -> List[Tuple[int, int, int]]:
    """A handful of (z, x, y) inside the footprint, most plausible first."""
    span = max(bbox[2] - bbox[0], bbox[3] - bbox[1], 1e-6)
    fit_zoom = int(math.floor(math.log2(360.0 / span)))
    low = min_zoom if min_zoom is not None else 0
    high = max_zoom if max_zoom is not None else default_max_zoom
    high = max(low, min(high, 20))

    def clamp(zoom: int) -> int:
        return max(low, min(high, zoom))

    # Footprint-sized zooms first, then street-level ones: many imagery
    # layers only exist at deep zooms and legitimately 404 above them.
    zooms: List[int] = []
    for zoom in (
        clamp(fit_zoom + 2),
        clamp(fit_zoom + 4),
        clamp(12),
        clamp(16),
        clamp(fit_zoom),
    ):
        if zoom not in zooms:
            zooms.append(zoom)

    tiles: List[Tuple[int, int, int]] = []
    for index, (lon, lat) in enumerate(points):
        # Every zoom at the best point, the two most plausible at the others.
        for zoom in zooms if index == 0 else zooms[:2]:
            tile = (zoom, *lonlat_to_tile(lon, lat, zoom))
            if tile not in tiles:
                tiles.append(tile)
    return tiles[:MAX_TILE_PROBES]


def sniff_content(body: bytes) -> str:
    """Rough content family from magic bytes."""
    head = bytes(body[:16])
    if not head:
        return "empty"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8") or head.startswith(b"GIF8"):
        return "image"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image"
    if head.startswith((b"II*\x00", b"MM\x00*", b"BM")) or head[4:12] in (b"ftypavif",):
        return "image"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    stripped = head.lstrip()
    if stripped.startswith(b"<"):
        return "markup"
    if stripped.startswith((b"{", b"[")):
        return "json"
    return "binary"


def tile_content_ok(source_type: str, content_type: str, body: bytes) -> Tuple[bool, str]:
    family = sniff_content(body)
    content_type = (content_type or "").split(";")[0].strip().lower()
    if source_type == "vectortile":
        if family in ("gzip", "binary"):
            return True, family
        return False, f"expected a vector tile, got {family} ({content_type or 'no content-type'})"
    if family == "image":
        return True, family
    if content_type.startswith("image/") and family == "binary":
        return True, content_type
    return False, f"expected an image, got {family} ({content_type or 'no content-type'})"


# --------------------------------------------------------------------------
# Worker: one QGIS per process, one layer per request line on stdin
# --------------------------------------------------------------------------


def _resolved_template(entry: Any) -> str:
    """Tile template with the `{time}` placeholder resolved the way the plugin does it."""
    try:
        from geostrata.core.uri_builder import resolved_entry_url

        return resolved_entry_url(entry)
    except Exception:
        return entry.url.strip()


def _emit(payload: Dict[str, Any]) -> None:
    sys.stdout.write(RESULT_MARK + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _scoped(owner: Any, scope: str, name: str) -> Any:
    """Enum access working with both PyQt5 (flat) and PyQt6 (scoped)."""
    scoped_owner = getattr(owner, scope, None)
    if scoped_owner is not None and hasattr(scoped_owner, name):
        return getattr(scoped_owner, name)
    return getattr(owner, name)


class LayerChecker:
    """Runs inside a worker process with an initialised QgsApplication."""

    def __init__(self, http_timeout_s: int) -> None:
        from qgis.core import QgsApplication, QgsNetworkAccessManager
        from qgis.PyQt.QtNetwork import QNetworkRequest

        from geostrata.core import db_client, uri_builder

        self._db_client_module = db_client
        self._record_cls = db_client.CatalogSourceRecord
        self._uri_builder = uri_builder
        self._http_status_attr = _scoped(
            QNetworkRequest, "Attribute", "HttpStatusCodeAttribute"
        )
        self._log: List[str] = []
        QgsNetworkAccessManager.setTimeout(http_timeout_s * 1000)
        try:
            QgsApplication.messageLog().messageReceived[str, str, "Qgis::MessageLevel"].connect(
                self._on_log_message
            )
        except Exception:
            try:
                QgsApplication.messageLog().messageReceived.connect(self._on_log_message)
            except Exception:
                pass

    def _on_log_message(self, message: Any, tag: Any = "", level: Any = None) -> None:
        text = " ".join(str(message).split())
        if text:
            self._log.append(f"[{tag}] {text}"[:300])

    # -- record ------------------------------------------------------------

    def make_record(self, row: Dict[str, Any]) -> Any:
        """Build the plugin's dataclass from a raw row, whatever columns exist."""

        def parse_json(value: Any, fallback: Any) -> Any:
            try:
                parsed = json.loads(value) if value else fallback
            except (TypeError, ValueError):
                return fallback
            return parsed if isinstance(parsed, type(fallback)) else fallback

        derived = {
            "tags": parse_json(row.get("tags_json"), []),
            "i18n": parse_json(row.get("i18n_json"), {}),
        }
        # `time_json` is decoded by the plugin's own parser so `{time}` resolves exactly as in QGIS.
        time_parser = getattr(self._db_client_module, "_optional_time_spec", None)
        if time_parser is not None and row.get("time_json"):
            derived["time"] = time_parser(row.get("time_json"))
        text_defaults = {"description": "", "crs": "EPSG:4326", "health_status": "ok"}
        kwargs: Dict[str, Any] = {}
        for field in dataclasses.fields(self._record_cls):
            if field.name in derived and field.name not in row:
                kwargs[field.name] = derived[field.name]
            elif field.name in row and row[field.name] is not None:
                kwargs[field.name] = row[field.name]
            elif field.name in text_defaults:
                kwargs[field.name] = text_defaults[field.name]
            elif (
                field.default is dataclasses.MISSING
                and field.default_factory is dataclasses.MISSING  # type: ignore[misc]
            ):
                kwargs[field.name] = None
        for name in ("min_x", "min_y", "max_x", "max_y", "area_deg2"):
            if kwargs.get(name) is not None:
                kwargs[name] = float(kwargs[name])
        return self._record_cls(**kwargs)

    # -- network -----------------------------------------------------------

    def http_get(self, url: str, range_header: bool = False) -> Dict[str, Any]:
        from qgis.core import QgsNetworkAccessManager
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest

        request = QNetworkRequest(QUrl(url))
        if range_header:
            request.setRawHeader(b"Range", b"bytes=0-1023")
        reply = QgsNetworkAccessManager.blockingGet(request)
        raw_status = reply.attribute(self._http_status_attr)
        try:
            http_status = int(raw_status) if raw_status is not None else None
        except (TypeError, ValueError):
            http_status = None
        body = bytes(reply.content())
        return {
            "url": url,
            "http_status": http_status,
            "error": "" if http_status and http_status < 400 else str(reply.errorString()),
            "content_type": bytes(reply.rawHeader(b"Content-Type")).decode("latin-1"),
            "body": body,
        }

    def probe_remote(self, entry: Any) -> Tuple[str, str, Optional[int], Optional[str]]:
        """After a failed load: is the remote resource gone, down, or fine?"""
        url = probe_url_for(entry.type.lower(), entry.url)
        if url is None:
            return STATUS_BROKEN, "no probe URL", None, None
        response = self.http_get(url, range_header=True)
        status, reason = classify_http(response["http_status"], response["error"])
        return status, reason, response["http_status"], url

    # -- geometry helpers --------------------------------------------------

    def footprint_points(self, entry: Any) -> List[Tuple[float, float]]:
        """Points inside the footprint polygon, then the bbox centre."""
        from qgis.core import QgsGeometry

        centre = ((entry.min_x + entry.max_x) / 2.0, (entry.min_y + entry.max_y) / 2.0)
        points: List[Tuple[float, float]] = []
        try:
            geometry = QgsGeometry.fromWkt(entry.geom_wkt or "")
            if geometry is not None and not geometry.isEmpty():
                # Largest part first: for "France + overseas" that is the mainland.
                parts = sorted(geometry.asGeometryCollection(), key=lambda part: -part.area())
                for part in [*parts[:1], geometry]:
                    inside = part.pointOnSurface().asPoint()
                    point = (float(inside.x()), float(inside.y()))
                    if point not in points:
                        points.append(point)
        except Exception:
            pass
        if centre not in points:
            points.append(centre)
        return points

    def window_in_layer_crs(self, layer: Any, point: Tuple[float, float], half_deg: float) -> Any:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsProject,
            QgsRectangle,
        )

        lon, lat = point
        rect = QgsRectangle(lon - half_deg, lat - half_deg, lon + half_deg, lat + half_deg)
        layer_crs = layer.crs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if layer_crs.isValid() and layer_crs != wgs84:
            transform = QgsCoordinateTransform(wgs84, layer_crs, QgsProject.instance())
            rect = transform.transformBoundingBox(rect)
        return rect

    # -- checks ------------------------------------------------------------

    def check(self, row: Dict[str, Any], overpass_live: bool) -> Dict[str, Any]:
        started = time.time()
        self._log = []
        result: Dict[str, Any] = {
            "id": str(row.get("id")),
            "type": str(row.get("type") or "").lower(),
            "status": STATUS_BROKEN,
            "reason": "",
            "detail": "",
            "checks": [],
            "http_status": None,
            "checked_url": None,
            "uri": None,
            "provider": None,
        }
        try:
            entry = self.make_record(row)
            source_type = entry.type.lower()
            if source_type == "overpass":
                self._check_overpass(entry, result, overpass_live)
            else:
                bbox = (entry.min_x, entry.min_y, entry.max_x, entry.max_y)
                layer, spec = self._uri_builder.create_qgis_layer(entry, bbox)
                result["uri"] = spec.uri[:600]
                result["provider"] = spec.provider
                result["checks"].append("create_qgis_layer")
                if source_type in TILE_TYPES:
                    self._check_tiles(entry, layer, result)
                elif not layer.isValid():
                    self._explain_invalid(entry, layer, result)
                elif spec.layer_kind == "raster":
                    self._check_raster(entry, layer, result)
                else:
                    self._check_vector(entry, layer, result)
                del layer
        except Exception as exc:  # A crash of the builder is a real defect.
            result["status"] = STATUS_BROKEN
            result["reason"] = "exception"
            result["detail"] = f"{type(exc).__name__}: {exc}"[:400]
        if result["status"] != STATUS_OK and self._log:
            result["qgis_log"] = self._log[-4:]
        result["duration_s"] = round(time.time() - started, 2)
        return result

    def _explain_invalid(self, entry: Any, layer: Any, result: Dict[str, Any]) -> None:
        result["checks"].append("probe_remote")
        status, reason, http_status, url = self.probe_remote(entry)
        result["http_status"] = http_status
        result["checked_url"] = url
        try:
            provider_error = " ".join(str(layer.error().summary()).split())[:200]
        except Exception:
            provider_error = ""
        if status == STATUS_OK:
            # The server answers but QGIS cannot open the layer: wrong URI,
            # wrong layer name, unsupported format. Retried before it is final.
            result["status"] = STATUS_BROKEN
            result["reason"] = "invalid_layer"
            result["detail"] = (
                f"layer.isValid() is False although {url} answers HTTP {http_status}. "
                f"{provider_error}"
            ).strip()
            result["retryable"] = True
        else:
            result["status"] = status
            result["reason"] = reason
            result["detail"] = f"layer invalid; {url} -> {reason}. {provider_error}".strip()
            result["retryable"] = status == STATUS_UNREACHABLE

    def _check_tiles(self, entry: Any, layer: Any, result: Dict[str, Any]) -> None:
        source_type = entry.type.lower()
        if not layer.isValid():
            result["reason"] = "invalid_layer"
            result["detail"] = "tile layer rejected by QGIS (malformed URI)"
            return
        crs = (entry.crs or "").upper()
        if crs not in ("EPSG:3857", "EPSG:900913", "EPSG:3785"):
            result["status"] = STATUS_SKIPPED
            result["reason"] = "non_web_mercator_grid"
            result["detail"] = f"tile probe not implemented for {entry.crs}"
            return
        tiles = candidate_tiles(
            (entry.min_x, entry.min_y, entry.max_x, entry.max_y),
            self.footprint_points(entry),
            entry.min_zoom,
            entry.max_zoom,
            14 if source_type == "vectortile" else 19,
        )
        result["checks"].append("fetch_tile")
        outcomes: List[Tuple[str, str, Optional[int], str]] = []
        for zoom, x, y in tiles:
            url = fill_tile_template(_resolved_template(entry), zoom, x, y)
            if url is None:
                result["status"] = STATUS_SKIPPED
                result["reason"] = "unknown_placeholder"
                result["detail"] = "URL template has a placeholder the test cannot fill"
                return
            response = self.http_get(url)
            status, reason = classify_http(response["http_status"], response["error"])
            if status == STATUS_OK:
                body = response["body"]
                if response["http_status"] == 204 or not body:
                    outcomes.append(("empty", "empty tile", response["http_status"], url))
                    continue
                content_ok, content_note = tile_content_ok(
                    source_type, response["content_type"], body
                )
                if content_ok:
                    result["status"] = STATUS_OK
                    result["reason"] = "tile_ok"
                    result["detail"] = f"z{zoom}/{x}/{y}: {content_note}, {len(body)} bytes"
                    result["http_status"] = response["http_status"]
                    result["checked_url"] = url
                    return
                outcomes.append((STATUS_BROKEN, f"wrong_content: {content_note}", 200, url))
            else:
                outcomes.append((status, reason, response["http_status"], url))
                unreachable_seen = sum(1 for outcome in outcomes if outcome[0] == STATUS_UNREACHABLE)
                if unreachable_seen >= 2:
                    break  # The host is down or refusing us: more tiles will not help.

        # No good tile. Prefer the least damning explanation seen.
        for wanted in ("empty", STATUS_UNREACHABLE, STATUS_BROKEN):
            for status, reason, http_status, url in outcomes:
                if status != wanted:
                    continue
                result["http_status"] = http_status
                result["checked_url"] = url
                if wanted == "empty":
                    result["status"] = STATUS_OK
                    result["reason"] = "empty_tile"
                    result["detail"] = "server answers but the probed tiles are empty"
                    result["warning"] = "only empty tiles (HTTP 204 or zero bytes) inside the footprint"
                else:
                    result["status"] = status
                    result["reason"] = reason
                    result["detail"] = f"{len(outcomes)} tile(s) tried, none usable; e.g. {url}"
                    result["retryable"] = status == STATUS_UNREACHABLE
                return
        result["status"] = STATUS_SKIPPED
        result["reason"] = "no_candidate_tile"

    def _check_vector(self, entry: Any, layer: Any, result: Dict[str, Any]) -> None:
        from qgis.core import QgsFeature, QgsFeatureRequest

        result["checks"].append("first_feature")
        feature = QgsFeature()
        found = False
        if entry.type.lower() == "wfs":
            # The plugin loads WFS restricted to the canvas: ask a small window first.
            for point in self.footprint_points(entry)[:1]:
                request = QgsFeatureRequest().setLimit(1)
                request.setFilterRect(self.window_in_layer_crs(layer, point, 0.05))
                found = layer.getFeatures(request).nextFeature(feature)
        if not found:
            found = layer.getFeatures(QgsFeatureRequest().setLimit(1)).nextFeature(feature)
        if found:
            result["status"] = STATUS_OK
            result["reason"] = "feature_ok"
            result["detail"] = f"{len(feature.fields())} attribute(s)"
            return
        result["checks"].append("probe_remote")
        status, reason, http_status, url = self.probe_remote(entry)
        result["http_status"] = http_status
        result["checked_url"] = url
        if status == STATUS_UNREACHABLE:
            result["status"] = STATUS_UNREACHABLE
            result["reason"] = reason
        else:
            result["status"] = STATUS_BROKEN
            result["reason"] = "no_features" if status == STATUS_OK else reason
        result["detail"] = "layer is valid but returned no feature"
        result["retryable"] = True

    def _check_raster(self, entry: Any, layer: Any, result: Dict[str, Any]) -> None:
        result["checks"].append("extent")
        extent = layer.extent()
        if extent.isEmpty() or not extent.isFinite():
            result["reason"] = "empty_extent"
            result["detail"] = f"raster extent is {extent.toString(2)}"
            result["retryable"] = True
            return
        result["checks"].append("read_block")
        provider = layer.dataProvider()
        last_note = ""
        blank_blocks = 0
        # A small window at each footprint point, then a wide one: an overlay
        # can be legitimately transparent at one spot.
        probes = [(point, 0.01) for point in self.footprint_points(entry)]
        probes += [(point, 0.25) for point in self.footprint_points(entry)[:1]]
        for point, half_deg in probes:
            window = self.window_in_layer_crs(layer, point, half_deg)
            if not window.intersects(extent):
                last_note = "probe window outside the raster extent"
                continue
            block = provider.block(1, window, 64, 64)
            if block is not None and block.isValid() and not block.isEmpty():
                if len(set(bytes(block.data()))) > 1:
                    result["status"] = STATUS_OK
                    result["reason"] = "block_ok"
                    result["detail"] = f"{block.width()}x{block.height()} block read"
                    return
                blank_blocks += 1
                continue
            try:
                last_note = " ".join(str(provider.error().summary()).split())[:200]
            except Exception:
                last_note = ""
        if blank_blocks:
            # QGIS reports success but every pixel is identical: either no data
            # at the probed spots, or an image format QGIS cannot decode. Not a
            # failure (too ambiguous), but listed so a human looks at it once.
            result["status"] = STATUS_OK
            result["reason"] = "blank_block"
            result["detail"] = f"{blank_blocks} block(s) read, all uniform"
            result["warning"] = "raster loads but every probed block is blank: check it renders in QGIS"
            return
        if last_note == "probe window outside the raster extent":
            # Footprint and data extent disagree; the layer itself opened fine.
            result["status"] = STATUS_OK
            result["reason"] = "extent_only"
            result["detail"] = last_note
            return
        result["checks"].append("probe_remote")
        status, reason, http_status, url = self.probe_remote(entry)
        result["http_status"] = http_status
        result["checked_url"] = url
        if status == STATUS_UNREACHABLE:
            result["status"] = STATUS_UNREACHABLE
            result["reason"] = reason
        else:
            result["status"] = STATUS_BROKEN
            result["reason"] = "block_read_failed" if status == STATUS_OK else reason
        result["detail"] = f"raster opened but no block could be read. {last_note}".strip()
        result["retryable"] = True

    def _check_overpass(self, entry: Any, result: Dict[str, Any], live: bool) -> None:
        from geostrata.core import overpass_task

        prefer_lines = overpass_task.is_linear_overpass_entry(entry)
        bbox, _ = overpass_task.clamp_overpass_bbox(OVERPASS_TEST_BBOX)
        query = overpass_task.build_overpass_json_ql(entry.url, bbox, prefer_lines=prefer_lines)
        result["checks"].append("build_query")
        result["provider"] = "overpass"
        result["uri"] = " ".join(query.split())[:600]
        if not query.strip() or "[out:json]" not in query or "{" in query:
            result["reason"] = "bad_query"
            result["detail"] = "Overpass QL is empty, not JSON output, or has an unresolved placeholder"
            return
        if not live:
            result["status"] = STATUS_OK
            result["reason"] = "query_built"
            result["detail"] = "query built only; the shared endpoint is exercised by another entry"
            return

        result["checks"].append("live_query")
        try:
            payload, mirror = overpass_task.fetch_overpass_with_failover(
                query, timeout_sec=20, query_bbox=bbox
            )
        except Exception as exc:
            message = str(exc)
            result["detail"] = message[:400]
            if re.search(r"HTTP Error 400", message):
                result["reason"] = "http_400"  # Overpass rejects the query syntax.
            else:
                result["status"] = STATUS_UNREACHABLE
                result["reason"] = "overpass_mirrors_failed"
                result["retryable"] = True
            return
        result["checked_url"] = mirror
        geojson = overpass_task.convert_overpass_json_to_geojson(payload, prefer_lines=prefer_lines)
        features = geojson.get("features") if isinstance(geojson, dict) else None
        if not isinstance(features, list):
            result["reason"] = "bad_conversion"
            result["detail"] = "Overpass answer could not be converted to GeoJSON"
            return
        if features:
            from qgis.core import QgsVectorLayer

            with tempfile.TemporaryDirectory(prefix="geostrata-live-") as tmp_dir:
                geojson_path = Path(tmp_dir) / "overpass.geojson"
                geojson_path.write_text(json.dumps(geojson), encoding="utf-8")
                layer = QgsVectorLayer(str(geojson_path), "overpass", "ogr")
                valid = layer.isValid()
                del layer
            if not valid:
                result["reason"] = "invalid_layer"
                result["detail"] = "converted Overpass GeoJSON is not a valid OGR layer"
                return
        result["status"] = STATUS_OK
        result["reason"] = "overpass_ok"
        result["detail"] = f"{len(features)} feature(s) from {mirror}"


def worker_main(http_timeout_s: int) -> int:
    """Request/response loop: one JSON line in, one marked JSON line out."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(PLUGIN_PARENT_DIR))
    settings_dir = tempfile.mkdtemp(prefix="geostrata-live-settings-")

    from qgis.PyQt.QtCore import QCoreApplication, QSettings

    # Never read or write the developer's real QGIS profile.
    QCoreApplication.setOrganizationName("GeostrataLiveTest")
    QCoreApplication.setApplicationName("live-layers")
    QSettings.setDefaultFormat(_scoped(QSettings, "Format", "IniFormat"))
    QSettings.setPath(
        _scoped(QSettings, "Format", "IniFormat"),
        _scoped(QSettings, "Scope", "UserScope"),
        settings_dir,
    )

    from qgis.core import Qgis, QgsApplication

    app = QgsApplication([], False, settings_dir)
    app.initQgis()
    checker = LayerChecker(http_timeout_s)
    _emit({"hello": {"qgis_version": Qgis.QGIS_VERSION, "python": sys.version.split()[0]}})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        job = json.loads(line)
        _emit({"result": checker.check(job["row"], bool(job.get("overpass_live")))})
    # No exitQgis(): tearing providers down after hundreds of network layers is
    # slow and occasionally crashes; the process is about to end anyway.
    sys.stdout.flush()
    os._exit(0)


# --------------------------------------------------------------------------
# Parent: worker pool, retries, timeouts
# --------------------------------------------------------------------------


def _probe_after_crash(row: Dict[str, Any]) -> Tuple[str, str]:
    """Ask the endpoint of a layer that crashed its worker whether it answers at all."""
    source_type = str(row["type"]).lower()
    if source_type in ("archive", "geojson"):
        # The plugin never streams these: it downloads them with urllib and opens the
        # local copy (uri_builder.needs_local_copy). A crash of the streamed read used
        # here as a cheap check says nothing about what a user gets.
        return STATUS_UNREACHABLE, "crash_on_streamed_read"
    target = probe_url_for(source_type, str(row["url"]))
    if not target or "{" in target:
        return STATUS_BROKEN, "worker_crash"
    request = urllib.request.Request(
        target,
        headers={"User-Agent": "Geostrata-LiveTest/1.0", "Range": "bytes=0-1023"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return classify_http(int(response.status), "")
    except urllib.error.HTTPError as exc:
        return classify_http(int(exc.code), "")
    except Exception as exc:  # DNS failure, timeout, connection reset
        return classify_http(None, str(exc))


class WorkerProcess:
    """One QGIS subprocess, restarted transparently when it hangs or dies."""

    def __init__(self, http_timeout_s: int, startup_timeout_s: int, stderr_path: Optional[Path]) -> None:
        self._http_timeout_s = http_timeout_s
        self._startup_timeout_s = startup_timeout_s
        self._stderr_path = stderr_path
        self._process: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self.hello: Dict[str, Any] = {}

    def _start(self) -> None:
        env = dict(os.environ)
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        env.setdefault("GDAL_HTTP_TIMEOUT", str(self._http_timeout_s))
        env.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "15")
        env["PYTHONUNBUFFERED"] = "1"
        stderr_target: Any = subprocess.DEVNULL
        if self._stderr_path is not None:
            stderr_target = open(self._stderr_path, "ab")
        self._lines = queue.Queue()
        self._process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                "--http-timeout",
                str(self._http_timeout_s),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_target,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        lines = self._lines
        stdout = self._process.stdout

        def pump() -> None:
            assert stdout is not None
            for raw_line in stdout:
                if raw_line.startswith(RESULT_MARK):
                    lines.put(raw_line[len(RESULT_MARK) :])
            lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        first = self._read(self._startup_timeout_s)
        if first is None or "hello" not in first:
            self.kill()
            raise RuntimeError(
                "QGIS worker did not start. Run this script with a Python that can "
                "'import qgis' (see --worker-log for its stderr)."
            )
        self.hello = first["hello"]

    def _read(self, timeout_s: float) -> Optional[Dict[str, Any]]:
        try:
            line = self._lines.get(timeout=timeout_s)
        except queue.Empty:
            return {"__timeout__": True}
        if line is None:
            return None
        return json.loads(line)

    def kill(self) -> None:
        if self._process is not None:
            try:
                self._process.kill()
                self._process.wait(timeout=10)
            except Exception:
                pass
        self._process = None

    def run(self, job: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
        if self._process is None or self._process.poll() is not None:
            self._start()
        assert self._process is not None and self._process.stdin is not None
        row = job["row"]
        started = time.time()
        base = {
            "id": str(row["id"]),
            "type": str(row["type"]).lower(),
            "checks": [],
            "http_status": None,
            "checked_url": None,
            "uri": None,
            "provider": None,
        }
        try:
            self._process.stdin.write(json.dumps(job, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            self.kill()
            return {
                **base,
                "status": STATUS_BROKEN,
                "reason": "worker_crash",
                "detail": "QGIS worker died before the layer could be sent",
                "retryable": True,
            }
        answer = self._read(timeout_s)
        base["duration_s"] = round(time.time() - started, 2)
        if answer is None:
            self.kill()
            # Old GDAL builds (QGIS 3.28) can crash on a streamed archive when the
            # server refuses the request (403 from a CI runner). The plugin itself
            # downloads such files and reports a clean error, so a crash on an
            # endpoint that does not answer right now is "unreachable", not "broken".
            probe_status, probe_reason = _probe_after_crash(row)
            if probe_status == STATUS_UNREACHABLE:
                return {
                    **base,
                    "status": STATUS_UNREACHABLE,
                    "reason": probe_reason,
                    "detail": f"QGIS crashed while loading this layer and the endpoint answers {probe_reason}",
                    "retryable": True,
                }
            return {
                **base,
                "status": STATUS_BROKEN,
                "reason": "worker_crash",
                "detail": "QGIS crashed while loading this layer",
                "retryable": True,
            }
        if answer.get("__timeout__"):
            self.kill()
            return {
                **base,
                "status": STATUS_UNREACHABLE,
                "reason": "timeout",
                "detail": f"no result after {int(timeout_s)} s (worker killed)",
                "retryable": True,
            }
        return answer["result"]


def run_suite(
    rows: Sequence[Dict[str, Any]],
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Check every row with a pool of QGIS workers. Returns (results, hello)."""
    overpass_rows = [row for row in rows if str(row["type"]).lower() == "overpass"]
    live_overpass_ids = {
        str(row["id"])
        for row in sorted(overpass_rows, key=lambda row: stable_rank(args.sample_seed, str(row["id"])))[
            : max(0, args.overpass_live)
        ]
    }
    # Interleave hosts so one provider does not receive a burst of requests.
    ordered = sorted(rows, key=lambda row: stable_rank("order", str(row["id"])))
    jobs: "queue.Queue[Dict[str, Any]]" = queue.Queue()
    for row in ordered:
        jobs.put({"row": row, "overpass_live": str(row["id"]) in live_overpass_ids})

    results: List[Dict[str, Any]] = []
    results_lock = threading.Lock()
    hello: Dict[str, Any] = {}
    fatal: List[str] = []
    total = len(ordered)
    stderr_path = Path(args.worker_log) if args.worker_log else None

    def slot() -> None:
        worker = WorkerProcess(args.http_timeout, args.startup_timeout, stderr_path)
        try:
            while not fatal:
                try:
                    job = jobs.get_nowait()
                except queue.Empty:
                    return
                result: Dict[str, Any] = {}
                attempts = 0
                while True:
                    attempts += 1
                    try:
                        result = worker.run(job, args.timeout)
                    except RuntimeError as exc:
                        fatal.append(str(exc))
                        return
                    if not hello and worker.hello:
                        hello.update(worker.hello)
                    retry = bool(result.pop("retryable", False)) or (
                        result["status"] == STATUS_UNREACHABLE
                    )
                    if result["status"] in (STATUS_OK, STATUS_SKIPPED) or not retry:
                        break
                    if attempts > args.retries:
                        break
                    if result.get("reason") == "timeout" and attempts >= 2:
                        break  # A second full timeout is conclusive enough.
                    time.sleep(args.retry_delay * attempts)
                result["attempts"] = attempts
                with results_lock:
                    results.append(result)
                    done = len(results)
                if not args.quiet:
                    marker = {
                        STATUS_OK: "ok",
                        STATUS_BROKEN: "BROKEN",
                        STATUS_UNREACHABLE: "unreachable",
                        STATUS_SKIPPED: "skipped",
                    }[result["status"]]
                    extra = "" if result["status"] == STATUS_OK else f" [{result['reason']}]"
                    print(
                        f"[{done}/{total}] {marker:<11} {result['type']:<10} {result['id']}{extra}",
                        flush=True,
                    )
        finally:
            worker.kill()

    threads = [
        threading.Thread(target=slot, daemon=True)
        for _ in range(max(1, min(args.jobs, total)))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if fatal:
        raise RuntimeError(fatal[0])
    results.sort(key=lambda item: item["id"])
    return results, hello


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def apply_allowlist(
    results: List[Dict[str, Any]], allowlist: Sequence[Tuple[str, str]]
) -> List[Dict[str, str]]:
    """Downgrade allowlisted failures. Returns allowlist lines that no longer apply."""
    used: Set[str] = set()
    tested_ids = [item["id"] for item in results]
    for item in results:
        for pattern, reason in allowlist:
            if not fnmatch.fnmatchcase(item["id"], pattern):
                continue
            if item["status"] == STATUS_BROKEN:
                item["status"] = STATUS_ALLOWLISTED
                item["allowlist_reason"] = reason
                used.add(pattern)
            elif item["status"] == STATUS_UNREACHABLE:
                used.add(pattern)  # No verdict this time: keep the line.
            break
    return [
        {"pattern": pattern, "reason": reason}
        for pattern, reason in allowlist
        if pattern not in used and fnmatch.filter(tested_ids, pattern)
    ]


def summarize(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    summary = {
        "checked": len(results),
        STATUS_OK: 0,
        STATUS_BROKEN: 0,
        STATUS_UNREACHABLE: 0,
        STATUS_SKIPPED: 0,
        STATUS_ALLOWLISTED: 0,
    }
    for item in results:
        summary[item["status"]] += 1
    return summary


def _md_cell(text: Any, limit: int = 160) -> str:
    cleaned = " ".join(str(text or "").split()).replace("|", "\\|")
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def render_markdown(report: Dict[str, Any], max_rows: int = 80) -> str:
    summary = report["summary"]
    results = report["results"]
    lines: List[str] = []
    verdict = "FAILED" if summary[STATUS_BROKEN] else "passed"
    lines.append(f"### Live layer test: {verdict}")
    lines.append("")
    lines.append(
        f"QGIS {report.get('qgis_version') or '?'} · {summary['checked']} layer(s) checked "
        f"of {report['catalog']['source_count']} in the catalog · {report['duration_s']} s"
    )
    lines.append("")
    lines.append("| ok | broken | unreachable | allowlisted | skipped |")
    lines.append("|---:|---:|---:|---:|---:|")
    lines.append(
        f"| {summary[STATUS_OK]} | {summary[STATUS_BROKEN]} | {summary[STATUS_UNREACHABLE]} "
        f"| {summary[STATUS_ALLOWLISTED]} | {summary[STATUS_SKIPPED]} |"
    )

    def table(title: str, status: str, note: str) -> None:
        items = [item for item in results if item["status"] == status]
        if not items:
            return
        lines.append("")
        lines.append(f"#### {title} ({len(items)})")
        if note:
            lines.append("")
            lines.append(note)
        lines.append("")
        lines.append("| id | type | reason | detail |")
        lines.append("|---|---|---|---|")
        for item in items[:max_rows]:
            detail = item.get("allowlist_reason") or item.get("detail")
            lines.append(
                f"| `{item['id']}` | {item['type']} | {_md_cell(item['reason'], 60)} | {_md_cell(detail)} |"
            )
        if len(items) > max_rows:
            lines.append(f"| … | | | {len(items) - max_rows} more in the JSON report |")

    suspicious = [item for item in results if item["status"] == STATUS_OK and item.get("warning")]

    table("Broken", STATUS_BROKEN, "These fail the run: fix the entry, remove it, or allowlist it with a reason.")
    table(
        "Unreachable right now",
        STATUS_UNREACHABLE,
        "Timeouts, 5xx, 401/403/429, DNS errors. Not a failure; worth a look if it repeats.",
    )
    table("Allowlisted", STATUS_ALLOWLISTED, "Known failures listed in `live_layers_allowlist.txt`.")
    table("Skipped", STATUS_SKIPPED, "")
    if suspicious:
        lines.append("")
        lines.append(f"#### Loaded, but look at these once ({len(suspicious)})")
        lines.append("")
        for item in suspicious[:max_rows]:
            lines.append(f"- `{item['id']}` ({item['type']}): {_md_cell(item['warning'])}")

    if report.get("stale_allowlist"):
        lines.append("")
        lines.append("#### Allowlist lines to remove")
        lines.append("")
        for stale in report["stale_allowlist"]:
            lines.append(f"- `{stale['pattern']}` now passes ({_md_cell(stale['reason'], 100)})")
    if report.get("notes"):
        lines.append("")
        lines.append("<details><summary>Selection</summary>")
        lines.append("")
        for note in report["notes"]:
            lines.append(f"- {_md_cell(note, 200)}")
        lines.append("")
        lines.append("</details>")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load real catalog layers in headless QGIS and report the broken ones.",
    )
    selection = parser.add_argument_group(
        "selection (selectors add up; without any selector every entry is tested)"
    )
    selection.add_argument("--all", action="store_true", help="every catalog entry")
    selection.add_argument("--type", action="append", metavar="T[,T]", help="entries of these source types")
    selection.add_argument("--id", action="append", metavar="GLOB[,GLOB]", help="ids matching a glob, e.g. 'fr-ign-*'")
    selection.add_argument("--ids-file", metavar="PATH", help="file with one id or glob per line")
    selection.add_argument(
        "--changed",
        metavar="RANGE",
        help="entries whose YAML changed in a git range: 'origin/main' (vs working tree), 'A..B' or 'A...B'",
    )
    selection.add_argument(
        "--smoke",
        nargs="?",
        const=2,
        type=int,
        metavar="N",
        help="N deterministic entries per source type (default 2): covers every URI builder branch",
    )
    selection.add_argument(
        "--families",
        action="store_true",
        help="every entry of the generated families (combine with --sample-families)",
    )
    selection.add_argument(
        "--sample-families",
        type=int,
        default=0,
        metavar="N",
        help="keep at most N entries of each generated family (-eli-, -geoboundaries-, "
        "per-country templates such as xx-hotosm-roads); 0 keeps all",
    )
    selection.add_argument("--sample-seed", default="", help="changes which family members and smoke entries are picked")
    selection.add_argument("--exclude", action="append", metavar="GLOB[,GLOB]", help="ids to leave out")
    selection.add_argument("--list", action="store_true", help="print the selected ids and exit (no QGIS needed)")

    execution = parser.add_argument_group("execution")
    execution.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="path to catalog.sqlite")
    execution.add_argument(
        "--extra-entries",
        metavar="JSON",
        help="JSON list of extra rows (same keys as the sources table) tested with the catalog",
    )
    execution.add_argument("--jobs", type=int, default=min(8, (os.cpu_count() or 2) * 2), help="parallel QGIS workers")
    execution.add_argument("--timeout", type=int, default=150, help="seconds allowed per layer attempt")
    execution.add_argument("--http-timeout", type=int, default=30, help="seconds allowed per HTTP request")
    execution.add_argument("--retries", type=int, default=2, help="extra attempts for anything that is not a clear 4xx")
    execution.add_argument("--retry-delay", type=float, default=3.0, help="seconds before the first retry (grows linearly)")
    execution.add_argument("--overpass-live", type=int, default=1, metavar="N", help="Overpass entries that run a real query")
    execution.add_argument("--startup-timeout", type=int, default=120, help=argparse.SUPPRESS)
    execution.add_argument("--worker-log", metavar="PATH", help="append the workers' stderr (GDAL/Qt noise) to this file")
    execution.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)

    reporting = parser.add_argument_group("reporting")
    reporting.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST), help="known-unfixable entries, one '<id> <reason>' per line")
    reporting.add_argument("--no-allowlist", action="store_true", help="ignore the allowlist")
    reporting.add_argument("--json", metavar="PATH", help="write the machine-readable result")
    reporting.add_argument("--markdown", metavar="PATH", help="write the Markdown summary")
    reporting.add_argument(
        "--fail-if-unreachable-above",
        type=float,
        metavar="RATIO",
        help="exit 3 when more than this share (0-1) of the layers is unreachable: the run proved nothing",
    )
    reporting.add_argument("--quiet", action="store_true", help="no per-layer progress line")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.worker:
        return worker_main(args.http_timeout)

    catalog_path = Path(args.catalog)
    if not catalog_path.is_file():
        print(
            f"error: {catalog_path} not found. Run 'npm run build' in packages/registry first.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        rows = load_catalog_rows(catalog_path)
        catalog_count = len(rows)
        if args.extra_entries:
            extra_rows = json.loads(Path(args.extra_entries).read_text(encoding="utf-8"))
            known_ids = {str(row["id"]) for row in rows}
            for extra in extra_rows:
                if str(extra["id"]) in known_ids:
                    raise ValueError(f"--extra-entries: id '{extra['id']}' already in the catalog")
                rows.append(extra)
        selected, notes = select_rows(rows, args, REPO_ROOT)
        allowlist = [] if args.no_allowlist else load_allowlist(Path(args.allowlist))
    except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    if args.list:
        for row in selected:
            print(row["id"])
        return EXIT_OK

    print(
        f"Testing {len(selected)} of {catalog_count} catalog entries with {min(args.jobs, max(1, len(selected)))} QGIS worker(s)",
        flush=True,
    )
    started = time.time()
    hello: Dict[str, Any] = {}
    results: List[Dict[str, Any]] = []
    if selected:
        try:
            results, hello = run_suite(selected, args)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
    stale_allowlist = apply_allowlist(results, allowlist)
    summary = summarize(results)
    report = {
        "schema": RESULT_SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "qgis_version": hello.get("qgis_version"),
        "python_version": hello.get("python"),
        "catalog": {"path": str(catalog_path), "source_count": catalog_count},
        "arguments": [arg for arg in (list(argv) if argv is not None else sys.argv[1:])],
        "notes": notes,
        "duration_s": round(time.time() - started, 1),
        "summary": summary,
        "stale_allowlist": stale_allowlist,
        "results": results,
    }
    markdown = render_markdown(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(markdown, encoding="utf-8")

    print("")
    for item in results:
        if item["status"] in (STATUS_BROKEN, STATUS_UNREACHABLE):
            print(
                f"{item['status'].upper():<12} {item['id']} ({item['type']}): "
                f"{item['reason']} - {item.get('detail', '')}"
            )
    print(
        f"\n{summary['checked']} checked: {summary[STATUS_OK]} ok, {summary[STATUS_BROKEN]} broken, "
        f"{summary[STATUS_UNREACHABLE]} unreachable, {summary[STATUS_ALLOWLISTED]} allowlisted, "
        f"{summary[STATUS_SKIPPED]} skipped in {report['duration_s']} s "
        f"(QGIS {report['qgis_version'] or '?'})"
    )
    for item in results:
        if item["status"] == STATUS_OK and item.get("warning"):
            print(f"warning: {item['id']} ({item['type']}): {item['warning']}")
    for stale in stale_allowlist:
        print(f"allowlist: '{stale['pattern']}' now passes, remove the line")

    if summary[STATUS_BROKEN]:
        return EXIT_BROKEN
    if (
        args.fail_if_unreachable_above is not None
        and summary["checked"]
        and summary[STATUS_UNREACHABLE] / summary["checked"] > args.fail_if_unreachable_above
    ):
        print(
            "error: too many unreachable layers, this run did not prove anything "
            "(network problem on the runner?)",
            file=sys.stderr,
        )
        return EXIT_TOO_MANY_UNREACHABLE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
