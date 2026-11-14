#!/usr/bin/env python3
"""
Assemble le site web statique complet dans `_site/` pour GitHub Pages ou prévisualisation locale :
- Site vitrine + explorateur interactif + documentation (`packages/website/*`)
- Icônes thématiques SVG (`packages/qgis-plugin/geostrata/assets/icons/*.svg`)
- Catalogue compilé (`catalog.sqlite`, `catalog.sqlite.gz`, `manifest.json`, `catalog.json`)
- Dépôt de plugin QGIS (`geostrata.zip`, `plugins.xml`)
"""

import shutil
from pathlib import Path


def build_static_site() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    website_dir = repo_root / "packages" / "website"
    icons_src_dir = repo_root / "packages" / "qgis-plugin" / "geostrata" / "assets" / "icons"
    registry_dist = repo_root / "packages" / "registry" / "dist"
    plugin_dist = repo_root / "packages" / "qgis-plugin" / "dist"
    site_dir = repo_root / "_site"

    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copier les fichiers du site web
    for asset_name in ("index.html", "styles.css", "app.js"):
        src = website_dir / asset_name
        if src.exists():
            shutil.copy2(src, site_dir / asset_name)

    # 2. Copier les icônes SVG thématiques (dans _site/icons/ et dans packages/website/icons/)
    site_icons_dir = site_dir / "icons"
    site_icons_dir.mkdir(parents=True, exist_ok=True)
    website_icons_dir = website_dir / "icons"
    website_icons_dir.mkdir(parents=True, exist_ok=True)
    if icons_src_dir.exists():
        for icon_file in icons_src_dir.glob("*.svg"):
            shutil.copy2(icon_file, site_icons_dir / icon_file.name)
            shutil.copy2(icon_file, website_icons_dir / icon_file.name)

    # 3. Copier les artefacts du catalogue (SQLite + JSON)
    for artifact_name in ("catalog.sqlite", "catalog.sqlite.gz", "manifest.json", "catalog.json"):
        src = registry_dist / artifact_name
        if src.exists():
            shutil.copy2(src, site_dir / artifact_name)
            if artifact_name in ("manifest.json", "catalog.json"):
                shutil.copy2(src, website_dir / artifact_name)

    # 4. Copier le plugin QGIS (.zip + plugins.xml)
    for plugin_artifact in ("geostrata.zip", "plugins.xml"):
        src = plugin_dist / plugin_artifact
        if src.exists():
            shutil.copy2(src, site_dir / plugin_artifact)
            shutil.copy2(src, website_dir / plugin_artifact)

    # 5. Ajouter .nojekyll pour GitHub Pages
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    print(f"[Geostrata] Site statique assemblé dans : {site_dir}")


if __name__ == "__main__":
    build_static_site()
