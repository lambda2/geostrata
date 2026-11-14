"""Local cache of downloaded source files (ZIP archives, GeoJSON documents).

Why: a ZIP has no random access and a GeoJSON must be parsed entirely, so reading them
through /vsicurl/ blocks for as long as the whole file takes to come down (and again on
every later seek for a zipped GeoPackage). The plugin therefore downloads such files once,
in a background task, and opens the local copy.

Layout (one directory per remote file, named after the URL and the archive target)::

    <cache root>/
        <readable-slug>-<sha256(url|target)[:16]>/
            meta.json                 validators (ETag / Last-Modified / size), dates, payload path
            data-<timestamp>/...      the file QGIS opens (extracted target, or the ZIP itself)
            download.part[.json]      interrupted download, resumed with a Range request

Behaviour:

* **Freshness**: a copy checked less than ``revalidate_after`` seconds ago is used as is.
  Otherwise one conditional GET (If-None-Match / If-Modified-Since) is sent: ``304`` keeps
  the copy, ``200`` with new validators replaces it. When the server cannot be reached the
  existing copy is returned with ``stale_reason`` set (offline use).
* **Interruptions**: network errors are retried, resuming from the bytes already on disk.
  A cancelled or failed download leaves ``download.part``; the next attempt resumes it when
  the server still serves the same version (If-Range), else starts again.
* **Replacement** never happens in place: the new payload is written to a new ``data-*``
  directory and ``meta.json`` is switched at the end, so a failed refresh keeps the old copy
  and a layer already open in QGIS keeps its file.
* **Size policy**: least recently used entries are removed when the cache exceeds its limit
  (10 GB by default), never the entry being loaded nor a file used by an open layer.

Pure Python (standard library only, no QGIS import at module level): see tests/test_core.py.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..i18n import tr

SETTINGS_CACHE_DIR_KEY = "geostrata/file_cache_dir"
SETTINGS_CACHE_MAX_GB_KEY = "geostrata/file_cache_max_gb"

DEFAULT_MAX_CACHE_GB = 10
BYTES_PER_GB = 1024**3

# How long a cached copy is used without asking the server whether it changed.
# Archives are large periodic exports: one check a day. GeoJSON documents are small and
# may be live feeds (earthquakes, alerts): re-checked after five minutes.
REVALIDATE_AFTER_SEC: Dict[str, int] = {"archive": 24 * 3600, "geojson": 5 * 60}

USER_AGENT = "Geostrata-QGIS-Plugin/1.1 (+https://github.com/lambda2/geostrata)"
CHUNK_SIZE = 256 * 1024
MAX_DOWNLOAD_ATTEMPTS = 5
STALE_PART_AFTER_SEC = 7 * 24 * 3600
FREE_SPACE_MARGIN = 200 * 1024 * 1024
_TRANSIENT_HTTP_CODES = (408, 425, 429, 500, 502, 503, 504)

PART_NAME = "download.part"
META_NAME = "meta.json"
# Name of an entry directory: <slug>-<16 hex digits>. Eviction and clearing only ever
# delete directories with such a name that also hold cache files, so pointing the cache
# at a folder containing other data cannot destroy it.
_ENTRY_DIR_RE = re.compile(r"^.+-[0-9a-f]{16}$")

# progress(phase, done_bytes, total_bytes or None); phase is "download" or "extract".
ProgressCallback = Callable[[str, int, Optional[int]], None]


class DownloadCancelled(Exception):
    """Raised when ``is_canceled()`` turned true during a download or an extraction."""


class DownloadError(Exception):
    """A download or extraction failed; the message is translated and user-readable."""


class _TransientError(Exception):
    """Network hiccup worth another attempt (resuming from the partial file)."""


@dataclass(frozen=True)
class CachedCopy:
    """A file of the cache ready to be opened by GDAL/OGR."""

    open_path: str  # GDAL path: the file itself, or /vsizip/<zip> for a ZIP without target
    file_path: Path
    from_cache: bool  # True when nothing was downloaded for this request
    fetched_at: str  # ISO date (UTC) of the download
    size_bytes: int
    downloaded_bytes: int = 0
    # Set when the server could not be asked for a newer version (offline, HTTP error).
    stale_reason: Optional[str] = None


def format_size(num_bytes: float) -> str:
    """Return a short translated size such as '1.4 GB', '212 MB' or '87 kB'."""
    value = max(0.0, float(num_bytes))
    if value >= BYTES_PER_GB:
        return tr("{value} GB", value=f"{value / BYTES_PER_GB:.1f}")
    if value >= 1024**2:
        return tr("{value} MB", value=f"{value / 1024**2:.0f}")
    return tr("{value} kB", value=f"{value / 1024:.0f}")


def default_cache_dir() -> Path:
    """Return the per-user cache directory, outside the QGIS profile.

    The profile is roamed/backed up on some systems and belongs to one QGIS version;
    the OS cache location (~/Library/Caches, ~/.cache, %LOCALAPPDATA%/cache) is the
    place for large re-downloadable files, and is shared by QGIS 3 and QGIS 4.
    """
    try:
        from qgis.PyQt.QtCore import QStandardPaths

        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.GenericCacheLocation
        )
        if base:
            return Path(base) / "geostrata" / "files"
    except Exception:
        pass
    return Path.home() / ".cache" / "geostrata" / "files"


def configured_cache_dir() -> Path:
    """Return the cache directory chosen in the settings, else the default one."""
    try:
        from qgis.core import QgsSettings

        raw = str(QgsSettings().value(SETTINGS_CACHE_DIR_KEY, "") or "").strip()
        if raw:
            return Path(raw)
    except Exception:
        pass
    return default_cache_dir()


def configured_max_bytes() -> int:
    """Return the cache size limit of the settings in bytes (default 10 GB)."""
    gigabytes = float(DEFAULT_MAX_CACHE_GB)
    try:
        from qgis.core import QgsSettings

        gigabytes = float(
            QgsSettings().value(SETTINGS_CACHE_MAX_GB_KEY, DEFAULT_MAX_CACHE_GB)
        )
    except Exception:
        pass
    return int(max(1.0, gigabytes) * BYTES_PER_GB)


def configured_file_cache() -> "FileCache":
    """Build the cache described by the settings. Call it on the main thread."""
    return FileCache(configured_cache_dir(), configured_max_bytes())


# One lock per cache entry: two tasks asking for the same file download it once.
_KEY_LOCKS: Dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(entry_dir: Path) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        return _KEY_LOCKS.setdefault(str(entry_dir), threading.Lock())


def _is_locked(entry_dir: Path) -> bool:
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(str(entry_dir))
    return lock is not None and lock.locked()


def _check_canceled(is_canceled: Optional[Callable[[], bool]]) -> None:
    if is_canceled is not None and is_canceled():
        raise DownloadCancelled()


def _sleep_unless_canceled(seconds: float, is_canceled: Optional[Callable[[], bool]]) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _check_canceled(is_canceled)
        time.sleep(0.1)


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _is_in_use(path: Path, in_use: Iterable[str]) -> bool:
    """True when a layer source string refers to a file below `path`."""
    needles = {str(path), path.as_posix()}
    return any(needle in source for source in in_use for needle in needles)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp_path, path)


def _slug(text: str, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    return cleaned[:max_len] or "file"


def _resume_validator(info: Dict[str, Any]) -> Optional[str]:
    """Return a validator usable in If-Range (strong ETag, else Last-Modified)."""
    etag = str(info.get("etag") or "")
    if etag and not etag.startswith("W/"):
        return etag
    return str(info.get("last_modified") or "") or None


def _same_version(cached: Dict[str, Any], info: Dict[str, Any]) -> bool:
    """Compare the validators of the cached copy with those of a fresh response."""
    if cached.get("etag") and info.get("etag"):
        return str(cached["etag"]) == str(info["etag"])
    if cached.get("last_modified") and info.get("last_modified"):
        if str(cached["last_modified"]) != str(info["last_modified"]):
            return False
        new_length = info.get("content_length")
        return new_length is None or new_length == cached.get("content_length")
    return False


def _ensure_free_space(directory: Path, needed: int) -> None:
    try:
        free = shutil.disk_usage(str(directory)).free
    except OSError:
        return
    if needed + FREE_SPACE_MARGIN > free:
        raise DownloadError(
            tr(
                "not enough free disk space ({needed} needed, {free} available)",
                needed=format_size(needed),
                free=format_size(free),
            )
        )


def download_to_file(
    url: str,
    part_path: Path,
    cached: Optional[Dict[str, Any]] = None,
    *,
    accept_gzip: bool = False,
    progress: Optional[ProgressCallback] = None,
    is_canceled: Optional[Callable[[], bool]] = None,
    timeout: float = 30.0,
    on_size_known: Optional[Callable[[Optional[int]], None]] = None,
) -> Optional[Dict[str, Any]]:
    """Download `url` into `part_path`, resuming a previous partial file when possible.

    Returns None when the server reports that the `cached` version (a meta dict with
    ``etag`` / ``last_modified`` / ``content_length``) is still current; otherwise the
    validators of the downloaded version, `part_path` then holding the complete body.

    Raises DownloadCancelled (the partial file is kept) or DownloadError.
    """
    part_meta_path = part_path.with_name(part_path.name + ".json")
    # ProxyHandler({}) avoids the macOS _scproxy lookup, which is not thread-safe.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error = ""

    for attempt in range(MAX_DOWNLOAD_ATTEMPTS):
        _check_canceled(is_canceled)
        if attempt:
            _sleep_unless_canceled(min(8.0, 2.0 ** (attempt - 1)), is_canceled)

        offset = 0
        part_meta = _read_json(part_meta_path) if part_path.is_file() else None
        resume_validator = _resume_validator(part_meta) if part_meta else None
        if part_meta and resume_validator and part_meta.get("url") == url:
            offset = part_path.stat().st_size
        # A fully downloaded file whose installation was interrupted (cancelled
        # extraction): reuse it when the server confirms it is still current.
        complete_part = (
            part_meta
            if offset and part_meta and part_meta.get("complete")
            and offset == part_meta.get("content_length")
            else None
        )
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        conditional = complete_part or (None if offset else cached)
        if complete_part:
            offset = 0
        if offset:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = str(resume_validator)
        else:
            if accept_gzip:
                headers["Accept-Encoding"] = "gzip"
            if conditional and conditional.get("etag"):
                headers["If-None-Match"] = str(conditional["etag"])
            elif conditional and conditional.get("last_modified"):
                headers["If-Modified-Since"] = str(conditional["last_modified"])

        try:
            response = opener.open(
                urllib.request.Request(url, headers=headers), timeout=timeout
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return dict(complete_part) if complete_part else None
            if exc.code == 416 and offset:
                # The partial file does not match the remote file any more.
                part_path.unlink()
                last_error = "HTTP 416"
                continue
            if exc.code in _TRANSIENT_HTTP_CODES:
                last_error = f"HTTP {exc.code}"
                continue
            raise DownloadError(
                tr("the server answered HTTP {code}", code=exc.code)
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            last_error = str(getattr(exc, "reason", exc))
            continue

        try:
            with response:
                info: Dict[str, Any] = {
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                }
                gzip_encoded = (
                    str(response.headers.get("Content-Encoding") or "").lower() == "gzip"
                )
                total: Optional[int] = None
                content_range = str(response.headers.get("Content-Range") or "")
                range_match = re.match(r"bytes\s+(\d+)-\d+/(\d+|\*)", content_range)
                if (
                    offset
                    and response.status == 206
                    and range_match
                    and int(range_match.group(1)) == offset
                ):
                    if range_match.group(2) != "*":
                        total = int(range_match.group(2))
                    info["etag"] = info["etag"] or (part_meta or {}).get("etag")
                    info["last_modified"] = info["last_modified"] or (part_meta or {}).get(
                        "last_modified"
                    )
                else:
                    # Full body: a new download, or the server ignored / refused the Range.
                    offset = 0
                    raw_length = response.headers.get("Content-Length")
                    if raw_length and not gzip_encoded:
                        try:
                            total = int(raw_length)
                        except ValueError:
                            total = None
                    info["content_length"] = total
                    if complete_part and _same_version(complete_part, info):
                        return dict(complete_part)
                    if cached and _same_version(cached, info):
                        # The server ignored the conditional headers but still serves
                        # the cached version: no need to read the body.
                        _remove_part(part_path)
                        return None
                    _write_json(
                        part_meta_path,
                        {
                            "url": url,
                            "etag": None if gzip_encoded else info["etag"],
                            "last_modified": None if gzip_encoded else info["last_modified"],
                            "total": total,
                        },
                    )

                if total is not None:
                    _ensure_free_space(part_path.parent, total - offset)
                if on_size_known is not None:
                    on_size_known(total)

                decompressor = zlib.decompressobj(31) if gzip_encoded else None
                written = offset
                last_report = 0.0
                reader = getattr(response, "read1", None) or response.read
                with part_path.open("ab" if offset else "wb") as handle:
                    while True:
                        _check_canceled(is_canceled)
                        chunk = reader(CHUNK_SIZE)
                        if not chunk:
                            break
                        if decompressor is not None:
                            chunk = decompressor.decompress(chunk)
                        handle.write(chunk)
                        written += len(chunk)
                        now = time.monotonic()
                        if progress is not None and now - last_report >= 0.2:
                            last_report = now
                            progress("download", written, total)
                    if decompressor is not None:
                        handle.write(decompressor.flush())
                        written = handle.tell()

                if total is not None and written != total:
                    raise _TransientError(
                        f"connection closed after {written} of {total} bytes"
                    )
                if progress is not None:
                    progress("download", written, total if total is not None else written)
                info["content_length"] = written
                _write_json(part_meta_path, {"url": url, "complete": True, **info})
                return info
        except (DownloadCancelled, DownloadError):
            raise
        except Exception as exc:
            # Connection reset, timeout, truncated body...: retry from the partial file.
            last_error = str(exc) or exc.__class__.__name__
            continue

    raise DownloadError(
        tr(
            "the download was interrupted {count} times ({error})",
            count=MAX_DOWNLOAD_ATTEMPTS,
            error=last_error,
        )
    )


def _remove_part(part_path: Path) -> None:
    for path in (part_path, part_path.with_name(part_path.name + ".json")):
        try:
            path.unlink()
        except OSError:
            pass


def _zip_members_for_target(
    infos: List[zipfile.ZipInfo],
    target: str,
) -> Tuple[str, List[zipfile.ZipInfo]]:
    """Pick the members to extract for `target`: the file, its sidecars, or a dataset folder."""
    files = [info for info in infos if not info.filename.endswith("/")]
    wanted = target.strip("/")
    by_name = {info.filename: info for info in files}

    resolved = wanted if wanted in by_name else ""
    if not resolved:
        lowered = [name for name in by_name if name.lower() == wanted.lower()]
        if len(lowered) == 1:
            resolved = lowered[0]
    if not resolved:
        # Dataset stored as a folder (File Geodatabase "x.gdb/...").
        folder = [info for info in files if info.filename.startswith(wanted + "/")]
        if folder:
            return wanted, folder
        same_base = [
            name for name in by_name if PurePosixPath(name).name == PurePosixPath(wanted).name
        ]
        if len(same_base) == 1:
            resolved = same_base[0]
    if not resolved:
        listing = ", ".join(sorted(by_name)[:8])
        raise DownloadError(
            tr(
                "'{target}' was not found in the archive (it contains: {files})",
                target=wanted,
                files=listing,
            )
        )

    if resolved.lower().endswith(".vrt"):
        return resolved, files  # a VRT refers to other files of the archive
    # Sidecar files share the stem: roads.shp -> roads.dbf, roads.shx, roads.prj, roads.cpg;
    # dem.tif -> dem.tfw, dem.tif.aux.xml, dem.tif.ovr.
    stem = resolved.rsplit(".", 1)[0] if "." in PurePosixPath(resolved).name else resolved
    members = [
        info
        for info in files
        if info.filename == resolved or info.filename.startswith(stem + ".")
    ]
    return resolved, members


def extract_zip_target(
    zip_path: Path,
    target: str,
    dest_dir: Path,
    progress: Optional[ProgressCallback] = None,
    is_canceled: Optional[Callable[[], bool]] = None,
) -> str:
    """Extract `target` (and its sidecar files) from a ZIP; return its path relative to `dest_dir`."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            resolved, members = _zip_members_for_target(archive.infolist(), target)
            total = sum(info.file_size for info in members)
            _ensure_free_space(dest_dir, total)
            done = 0
            last_report = 0.0
            for info in members:
                relative = PurePosixPath(info.filename)
                if relative.is_absolute() or ".." in relative.parts or ":" in info.filename:
                    raise DownloadError(
                        tr("the archive contains an unsafe path: {path}", path=info.filename)
                    )
                out_path = dest_dir.joinpath(*relative.parts)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, out_path.open("wb") as handle:
                    while True:
                        _check_canceled(is_canceled)
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if progress is not None and now - last_report >= 0.2:
                            last_report = now
                            progress("extract", done, total)
            if progress is not None:
                progress("extract", total, total)
            return resolved
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise DownloadError(
            tr("the downloaded archive is corrupted ({error})", error=str(exc))
        ) from exc


class FileCache:
    """Disk cache of remote files keyed by (URL, archive target)."""

    def __init__(self, root: Path, max_bytes: int = DEFAULT_MAX_CACHE_GB * BYTES_PER_GB) -> None:
        self.root = Path(root)
        self.max_bytes = int(max_bytes)

    # -- paths and metadata ----------------------------------------------------

    def entry_dir(self, url: str, target: str = "") -> Path:
        """Return the directory of a remote file (not created)."""
        digest = hashlib.sha256(f"{url}|{target}".encode("utf-8")).hexdigest()[:16]
        basename = PurePosixPath(target).name or PurePosixPath(
            urllib.parse.urlsplit(url).path
        ).name
        return self.root / f"{_slug(basename)}-{digest}"

    @staticmethod
    def _read_meta(entry_dir: Path) -> Optional[Dict[str, Any]]:
        meta = _read_json(entry_dir / META_NAME)
        if not meta or not meta.get("payload"):
            return None
        if not (entry_dir / str(meta["payload"])).exists():
            return None
        return meta

    @staticmethod
    def _copy_from_meta(
        entry_dir: Path,
        meta: Dict[str, Any],
        from_cache: bool,
        downloaded_bytes: int = 0,
        stale_reason: Optional[str] = None,
    ) -> CachedCopy:
        file_path = entry_dir / str(meta["payload"])
        return CachedCopy(
            open_path=f"{meta.get('open_prefix') or ''}{file_path.as_posix()}",
            file_path=file_path,
            from_cache=from_cache,
            fetched_at=str(meta.get("fetched_at") or ""),
            size_bytes=int(meta.get("size_bytes") or 0),
            downloaded_bytes=downloaded_bytes,
            stale_reason=stale_reason,
        )

    def lookup(self, url: str, target: str = "") -> Optional[CachedCopy]:
        """Return the cached copy of a remote file without any network access."""
        entry_dir = self.entry_dir(url, target)
        meta = self._read_meta(entry_dir)
        return self._copy_from_meta(entry_dir, meta, True) if meta else None

    # -- main entry point ------------------------------------------------------

    def fetch(
        self,
        url: str,
        target: str = "",
        *,
        kind: str = "archive",
        progress: Optional[ProgressCallback] = None,
        is_canceled: Optional[Callable[[], bool]] = None,
        in_use: Iterable[str] = (),
        revalidate_after: Optional[float] = None,
        timeout: float = 30.0,
    ) -> CachedCopy:
        """Return a local copy of `url` (of `target` inside it for a ZIP), downloading if needed.

        Blocking: call it from a background thread. See the module docstring for the
        freshness, resume and replacement rules.

        Args:
            url: plain http(s) URL.
            target: path of the file inside the ZIP ('' = open the ZIP as a whole).
            kind: "archive" (ZIP) or "geojson".
            progress: ``progress(phase, done, total)`` with phase "download" / "extract".
            is_canceled: polled between chunks; DownloadCancelled is raised when true.
            in_use: data source strings of the layers currently open (never deleted).
            revalidate_after: seconds a copy is trusted without asking the server
                (default: REVALIDATE_AFTER_SEC[kind]).
        """
        in_use = list(in_use)
        entry_dir = self.entry_dir(url, target)
        lock = _key_lock(entry_dir)
        while not lock.acquire(timeout=0.2):
            _check_canceled(is_canceled)
        try:
            return self._fetch_locked(
                url, target, kind, entry_dir, progress, is_canceled, in_use,
                REVALIDATE_AFTER_SEC.get(kind, 0) if revalidate_after is None else revalidate_after,
                timeout,
            )
        finally:
            lock.release()

    def _fetch_locked(
        self,
        url: str,
        target: str,
        kind: str,
        entry_dir: Path,
        progress: Optional[ProgressCallback],
        is_canceled: Optional[Callable[[], bool]],
        in_use: List[str],
        revalidate_after: float,
        timeout: float,
    ) -> CachedCopy:
        entry_dir.mkdir(parents=True, exist_ok=True)
        meta = self._read_meta(entry_dir)
        now = time.time()

        if meta and now - float(meta.get("validated_at") or 0) < revalidate_after:
            return self._touch(entry_dir, meta)

        try:
            return self._download_and_install(
                url, target, kind, entry_dir, meta, progress, is_canceled, in_use, timeout
            )
        except DownloadError as exc:
            if meta:
                # Offline, server error or unusable new file: keep serving the copy we have.
                return self._touch(entry_dir, meta, stale_reason=str(exc))
            raise

    def _download_and_install(
        self,
        url: str,
        target: str,
        kind: str,
        entry_dir: Path,
        meta: Optional[Dict[str, Any]],
        progress: Optional[ProgressCallback],
        is_canceled: Optional[Callable[[], bool]],
        in_use: List[str],
        timeout: float,
    ) -> CachedCopy:
        now = time.time()
        part_path = entry_dir / PART_NAME
        info = download_to_file(
            url,
            part_path,
            meta,
            accept_gzip=(kind == "geojson"),
            progress=progress,
            is_canceled=is_canceled,
            timeout=timeout,
            on_size_known=lambda total: self.evict(
                incoming=total or 0, keep=entry_dir, in_use=in_use
            ),
        )
        if info is None and meta:
            meta["validated_at"] = now
            return self._touch(entry_dir, meta)
        if info is None:
            raise DownloadError(tr("the server sent no data"))

        downloaded = int(info.get("content_length") or 0)
        install_dir = entry_dir / "install.tmp"
        shutil.rmtree(install_dir, ignore_errors=True)
        install_dir.mkdir(parents=True)
        open_prefix = ""
        try:
            if kind == "archive" and target:
                payload_name = extract_zip_target(
                    part_path, target, install_dir, progress, is_canceled
                )
            else:
                if kind == "archive":
                    payload_name = "archive.zip"
                    open_prefix = "/vsizip/"
                else:
                    payload_name = _slug(
                        PurePosixPath(urllib.parse.urlsplit(url).path).name, 80
                    )
                    if not payload_name.lower().endswith((".geojson", ".json")):
                        payload_name += ".geojson"
                os.replace(part_path, install_dir / payload_name)
            data_dir = entry_dir / f"data-{int(now * 1000)}"
            os.replace(install_dir, data_dir)
            _remove_part(part_path)
        except DownloadCancelled:
            # The complete download stays in download.part and is reused next time.
            shutil.rmtree(install_dir, ignore_errors=True)
            raise
        except DownloadError:
            shutil.rmtree(install_dir, ignore_errors=True)
            _remove_part(part_path)  # a corrupted archive must not be resumed
            raise
        except OSError as exc:
            shutil.rmtree(install_dir, ignore_errors=True)
            raise DownloadError(str(exc)) from exc

        new_meta: Dict[str, Any] = {
            "version": 1,
            "url": url,
            "target": target,
            "kind": kind,
            "etag": info.get("etag"),
            "last_modified": info.get("last_modified"),
            "content_length": downloaded,
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "validated_at": now,
            "last_used": now,
            "payload": f"{data_dir.name}/{payload_name}",
            "open_prefix": open_prefix,
            "size_bytes": _dir_size(data_dir),
        }
        _write_json(entry_dir / META_NAME, new_meta)
        self._remove_old_payloads(entry_dir, data_dir.name, in_use)
        self.evict(keep=entry_dir, in_use=in_use)
        return self._copy_from_meta(entry_dir, new_meta, False, downloaded_bytes=downloaded)

    def _touch(
        self,
        entry_dir: Path,
        meta: Dict[str, Any],
        stale_reason: Optional[str] = None,
    ) -> CachedCopy:
        meta["last_used"] = time.time()
        try:
            _write_json(entry_dir / META_NAME, meta)
        except OSError:
            pass
        return self._copy_from_meta(entry_dir, meta, True, stale_reason=stale_reason)

    @staticmethod
    def _remove_old_payloads(entry_dir: Path, current: str, in_use: Iterable[str]) -> None:
        in_use = list(in_use)
        for child in entry_dir.glob("data-*"):
            if child.name != current and not _is_in_use(child, in_use):
                shutil.rmtree(child, ignore_errors=True)

    def discard(self, url: str, target: str = "") -> None:
        """Forget the cached copy of a remote file (e.g. it turned out to be unreadable)."""
        entry_dir = self.entry_dir(url, target)
        if entry_dir in self._entry_dirs() and not _is_locked(entry_dir):
            shutil.rmtree(entry_dir, ignore_errors=True)

    # -- size policy -----------------------------------------------------------

    def _entry_dirs(self) -> List[Path]:
        """Return the directories of the root that are cache entries (see _ENTRY_DIR_RE)."""
        found: List[Path] = []
        try:
            children = list(self.root.iterdir())
        except OSError:
            return found
        for child in children:
            if not child.is_dir() or child.is_symlink() or not _ENTRY_DIR_RE.match(child.name):
                continue
            try:
                names = [item.name for item in child.iterdir()]
            except OSError:
                continue
            if not names or any(
                name in (META_NAME, PART_NAME, PART_NAME + ".json", "install.tmp")
                or name.startswith("data-")
                for name in names
            ):
                found.append(child)
        return found

    def total_size(self) -> int:
        """Return the size of the cache in bytes (partial downloads included)."""
        return sum(_dir_size(child) for child in self._entry_dirs())

    def entry_count(self) -> int:
        """Return the number of cached files."""
        return sum(1 for child in self._entry_dirs() if (child / META_NAME).is_file())

    def evict(
        self,
        incoming: int = 0,
        keep: Optional[Path] = None,
        in_use: Iterable[str] = (),
    ) -> int:
        """Remove least recently used entries until the cache (+ `incoming`) fits its limit.

        Entries being downloaded, the `keep` entry and files used by an open layer are
        never removed, so the cache may temporarily exceed the limit. Partial downloads
        older than a week are dropped. Returns the number of bytes freed.
        """
        in_use = list(in_use)
        now = time.time()
        candidates: List[Tuple[float, int, Path]] = []
        total = incoming
        for child in self._entry_dirs():
            size = _dir_size(child)
            total += size
            if (keep is not None and child == keep) or _is_locked(child):
                continue
            part = child / PART_NAME
            meta = _read_json(child / META_NAME)
            if not meta:
                # Only a partial download (or leftovers): drop it once it is old.
                try:
                    age = now - (part.stat().st_mtime if part.is_file() else child.stat().st_mtime)
                except OSError:
                    age = 0.0
                if age > STALE_PART_AFTER_SEC:
                    shutil.rmtree(child, ignore_errors=True)
                    total -= size
                continue
            if _is_in_use(child, in_use):
                continue
            candidates.append((float(meta.get("last_used") or 0), size, child))

        freed = 0
        for _last_used, size, child in sorted(candidates, key=lambda item: item[0]):
            if total <= self.max_bytes:
                break
            shutil.rmtree(child, ignore_errors=True)
            total -= size
            freed += size
        return freed

    def clear(self, in_use: Iterable[str] = ()) -> Tuple[int, int]:
        """Delete every cached file not used by an open layer nor being downloaded.

        Returns ``(bytes freed, bytes kept)``.
        """
        in_use = list(in_use)
        freed = 0
        for child in self._entry_dirs():
            if _is_locked(child) or _is_in_use(child, in_use):
                continue
            size = _dir_size(child)
            shutil.rmtree(child, ignore_errors=True)
            if not child.exists():
                freed += size
        return freed, self.total_size()
