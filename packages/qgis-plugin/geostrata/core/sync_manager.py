"""Asynchronous catalog synchronization manager (manifest.json + catalog.sqlite.gz)."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from ..i18n import tr

DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/lambda2/geostrata/"
    "main/packages/registry/dist/manifest.json"
)
SETTINGS_MANIFEST_URLS_KEY = "geostrata/manifest_urls"


def _url_slug(manifest_url: str) -> str:
    """Create a deterministic filename prefix for a manifest URL."""
    digest = hashlib.sha256(manifest_url.strip().encode("utf-8")).hexdigest()[:12]
    return f"catalog_{digest}"


def compute_sha256_bytes(data: bytes) -> str:
    """Compute hex SHA-256 of a byte buffer."""
    return hashlib.sha256(data).hexdigest()


def compute_sha256_file(path: Path) -> str:
    """Compute hex SHA-256 of a file on disk."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class SyncManager:
    """Manages local cache and non-blocking synchronization of SQLite catalogs."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        on_sync_completed: Optional[Callable[[List[Path]], None]] = None,
    ) -> None:
        self._cache_dir = cache_dir or self._resolve_default_cache_dir()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._on_sync_completed = on_sync_completed
        self._active_task: Optional[ object ] = None
        self.ensure_bundled_fallback()

    @staticmethod
    def _resolve_default_cache_dir() -> Path:
        try:
            from qgis.core import QgsApplication

            base_dir = Path(QgsApplication.qgisSettingsDirPath())
        except Exception:
            base_dir = Path.home() / ".geostrata"
        return base_dir / "geostrata" / "cache"

    @property
    def cache_dir(self) -> Path:
        """Directory where catalog SQLite and manifest files are cached."""
        return self._cache_dir

    def get_manifest_urls(self) -> List[str]:
        """Read configured manifest.json URLs from QgsSettings."""
        try:
            from qgis.core import QgsSettings

            settings = QgsSettings()
            raw_value = settings.value(SETTINGS_MANIFEST_URLS_KEY, None)
            if isinstance(raw_value, list) and raw_value:
                return [str(u).strip() for u in raw_value if str(u).strip()]
            if isinstance(raw_value, str) and raw_value.strip():
                parsed = json.loads(raw_value)
                if isinstance(parsed, list) and parsed:
                    return [str(u).strip() for u in parsed if str(u).strip()]
        except Exception:
            pass
        return [DEFAULT_MANIFEST_URL]

    def set_manifest_urls(self, urls: Sequence[str]) -> None:
        """Persist manifest.json URLs into QgsSettings."""
        cleaned = [u.strip() for u in urls if u.strip()]
        if not cleaned:
            cleaned = [DEFAULT_MANIFEST_URL]
        try:
            from qgis.core import QgsSettings

            settings = QgsSettings()
            settings.setValue(SETTINGS_MANIFEST_URLS_KEY, json.dumps(cleaned))
        except Exception:
            pass

    def ensure_bundled_fallback(self) -> None:
        """Seed or update the cache with bundled catalog.sqlite if newer or missing."""
        plugin_dir = Path(__file__).resolve().parent.parent
        candidate_paths = [
            plugin_dir / "catalog.sqlite",
            plugin_dir.parent.parent / "registry" / "dist" / "catalog.sqlite",
        ]
        for candidate in candidate_paths:
            if not candidate.is_file():
                continue
            slug = _url_slug(DEFAULT_MANIFEST_URL)
            target_sqlite = self._cache_dir / f"{slug}.sqlite"
            target_manifest = self._cache_dir / f"{slug}.manifest.json"

            should_copy = not target_sqlite.is_file()
            if not should_copy:
                try:
                    if compute_sha256_file(candidate) != compute_sha256_file(target_sqlite):
                        should_copy = True
                except Exception:
                    should_copy = True

            if should_copy:
                shutil.copy2(candidate, target_sqlite)
                candidate_manifest = candidate.parent / "manifest.json"
                if candidate_manifest.is_file():
                    shutil.copy2(candidate_manifest, target_manifest)
            break

    def get_cached_sqlite_paths(self) -> List[Path]:
        """Return existing cached SQLite files in order of configured manifest URLs."""
        paths: List[Path] = []
        for url in self.get_manifest_urls():
            slug = _url_slug(url)
            sqlite_path = self._cache_dir / f"{slug}.sqlite"
            if sqlite_path.is_file():
                paths.append(sqlite_path)

        if not paths:
            for fallback_file in sorted(self._cache_dir.glob("*.sqlite")):
                if fallback_file.is_file():
                    paths.append(fallback_file)
        return paths

    def get_cache_status(self) -> List[Dict[str, str]]:
        """Return diagnostic metadata for each configured manifest URL."""
        statuses: List[Dict[str, str]] = []
        for url in self.get_manifest_urls():
            slug = _url_slug(url)
            sqlite_path = self._cache_dir / f"{slug}.sqlite"
            manifest_path = self._cache_dir / f"{slug}.manifest.json"
            info: Dict[str, str] = {
                "url": url,
                "sqlite_path": str(sqlite_path),
                "cached": tr("yes") if sqlite_path.is_file() else tr("no"),
                "source_count": "-",
                "generated_at": "-",
                "sha256": "-",
            }
            if manifest_path.is_file():
                try:
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    info["source_count"] = str(manifest_data.get("source_count", "-"))
                    info["generated_at"] = str(manifest_data.get("generated_at", "-"))
                    info["sha256"] = str(manifest_data.get("sha256", "-"))[:16]
                except Exception:
                    pass
            statuses.append(info)
        return statuses

    @staticmethod
    def sync_single_manifest_blocking(
        manifest_url: str,
        cache_dir: Path,
        timeout_sec: int = 15,
    ) -> Path:
        """Download and verify a single catalog if its remote SHA-256 changed."""
        slug = _url_slug(manifest_url)
        target_sqlite = cache_dir / f"{slug}.sqlite"
        target_manifest = cache_dir / f"{slug}.manifest.json"

        req = urllib.request.Request(
            manifest_url,
            headers={"User-Agent": "Geostrata-QGIS-Plugin/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            manifest_bytes = response.read()

        manifest_data = json.loads(manifest_bytes.decode("utf-8"))
        remote_sha256 = str(manifest_data.get("sha256", ""))

        if (
            target_sqlite.is_file()
            and remote_sha256
            and compute_sha256_file(target_sqlite) == remote_sha256
        ):
            target_manifest.write_bytes(manifest_bytes)
            return target_sqlite

        gzip_file_name = str(manifest_data.get("gzip_file", "catalog.sqlite.gz"))
        gzip_url = urllib.parse.urljoin(manifest_url, gzip_file_name)

        gz_req = urllib.request.Request(
            gzip_url,
            headers={"User-Agent": "Geostrata-QGIS-Plugin/1.0"},
        )
        with urllib.request.urlopen(gz_req, timeout=timeout_sec) as gz_response:
            compressed_bytes = gz_response.read()

        remote_gzip_sha256 = str(manifest_data.get("gzip_sha256", ""))
        if remote_gzip_sha256:
            actual_gzip_sha256 = compute_sha256_bytes(compressed_bytes)
            if actual_gzip_sha256 != remote_gzip_sha256:
                raise ValueError(
                    f"Gzip SHA-256 mismatch for {gzip_url}: "
                    f"expected {remote_gzip_sha256}, got {actual_gzip_sha256}"
                )

        sqlite_bytes = gzip.decompress(compressed_bytes)
        if remote_sha256:
            actual_sqlite_sha256 = compute_sha256_bytes(sqlite_bytes)
            if actual_sqlite_sha256 != remote_sha256:
                raise ValueError(
                    f"SQLite SHA-256 mismatch for {manifest_url}: "
                    f"expected {remote_sha256}, got {actual_sqlite_sha256}"
                )

        temp_sqlite = cache_dir / f"{slug}.sqlite.tmp"
        temp_sqlite.write_bytes(sqlite_bytes)
        temp_sqlite.replace(target_sqlite)
        target_manifest.write_bytes(manifest_bytes)
        return target_sqlite

    def start_background_sync(self) -> None:
        """Schedule a non-blocking QgsTask to synchronize all configured catalogs."""
        try:
            from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsTask
        except Exception:
            return

        urls = self.get_manifest_urls()
        cache_dir = self._cache_dir
        on_done_callback = self._on_sync_completed
        manager_ref = self

        class CatalogSyncTask(QgsTask):
            """Background QgsTask downloading manifest.json and catalog.sqlite.gz."""

            def __init__(self) -> None:
                super().__init__(
                    tr("Geostrata: catalog synchronization"),
                    QgsTask.CanCancel,
                )
                self.synced_paths: List[Path] = []
                self.errors: List[str] = []

            def run(self) -> bool:
                total = max(1, len(urls))
                for idx, url in enumerate(urls):
                    if self.isCanceled():
                        return False
                    try:
                        path = SyncManager.sync_single_manifest_blocking(url, cache_dir)
                        self.synced_paths.append(path)
                    except Exception as exc:
                        self.errors.append(f"{url}: {exc}")
                    self.setProgress(((idx + 1) / total) * 100.0)
                return True

            def finished(self, result: bool) -> None:
                manager_ref._active_task = None
                for err in self.errors:
                    QgsMessageLog.logMessage(
                        tr("Remote synchronization skipped ({error})", error=err),
                        "Geostrata",
                        Qgis.MessageLevel.Warning,
                    )
                if on_done_callback is not None:
                    on_done_callback(manager_ref.get_cached_sqlite_paths())

        task = CatalogSyncTask()
        self._active_task = task
        QgsApplication.taskManager().addTask(task)
