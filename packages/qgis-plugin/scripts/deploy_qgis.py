"""Deploy Geostrata plugin and SQLite cache directly to local QGIS3/QGIS4 profiles."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path
from typing import List

from package_plugin import package_qgis_plugin


def find_local_qgis_profile_dirs() -> List[Path]:
    """Locate active QGIS3 and QGIS4 profile directories on macOS, Linux, or Windows."""
    home = Path.home()
    system = platform.system()
    roots: List[Path] = []

    if system == "Darwin":
        qgis_base = home / "Library" / "Application Support" / "QGIS"
        roots.extend([qgis_base / "QGIS4" / "profiles", qgis_base / "QGIS3" / "profiles"])
    elif system == "Windows":
        appdata = home / "AppData" / "Roaming" / "QGIS"
        roots.extend([appdata / "QGIS4" / "profiles", appdata / "QGIS3" / "profiles"])
    else:
        local_share = home / ".local" / "share" / "QGIS"
        roots.extend(
            [local_share / "QGIS4" / "profiles", local_share / "QGIS3" / "profiles"]
        )

    profile_dirs: List[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir():
                profile_dirs.append(child)
    return profile_dirs


def deploy_to_qgis_profiles() -> None:
    """Package plugin ZIP and deploy plugin + catalog.sqlite into local QGIS profiles."""
    package_qgis_plugin()

    qgis_pkg_root = Path(__file__).resolve().parent.parent
    plugin_src_dir = qgis_pkg_root / "geostrata"
    registry_dist_dir = qgis_pkg_root.parent / "registry" / "dist"
    sqlite_src = registry_dist_dir / "catalog.sqlite"
    manifest_src = registry_dist_dir / "manifest.json"

    profiles = find_local_qgis_profile_dirs()
    if not profiles:
        print("No local QGIS profile directory found; ZIP archive is ready in dist/.")
        return

    for profile_dir in profiles:
        target_plugin_dir = profile_dir / "python" / "plugins" / "geostrata"
        target_plugin_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_plugin_dir.is_dir():
            shutil.rmtree(target_plugin_dir)
        shutil.copytree(
            plugin_src_dir,
            target_plugin_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )

        cache_dir = profile_dir / "geostrata" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        for existing_sqlite in cache_dir.glob("*.sqlite"):
            if sqlite_src.is_file():
                shutil.copy2(sqlite_src, existing_sqlite)
        for existing_manifest in cache_dir.glob("*.manifest.json"):
            if manifest_src.is_file():
                shutil.copy2(manifest_src, existing_manifest)

        print(f"Deployed Geostrata plugin & SQLite cache to: {profile_dir}")


if __name__ == "__main__":
    deploy_to_qgis_profiles()
