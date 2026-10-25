"""Package Geostrata into a standard QGIS plugin ZIP archive and generate plugins.xml."""

from __future__ import annotations

import configparser
import datetime
import os
import shutil
import xml.sax.saxutils as saxutils
import zipfile
from pathlib import Path
from typing import Dict


def read_metadata_txt(metadata_path: Path) -> Dict[str, str]:
    """Parse QGIS metadata.txt into a flat dictionary."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(metadata_path, encoding="utf-8")
    if "general" not in parser:
        return {}
    return dict(parser["general"])


def generate_plugins_xml(
    meta: Dict[str, str],
    zip_filename: str,
    download_base_url: str,
) -> str:
    """Generate a valid QGIS plugin repository plugins.xml document."""
    name = saxutils.escape(meta.get("name", "Geostrata"))
    version = saxutils.escape(meta.get("version", "1.0.0"))
    qgis_min = saxutils.escape(meta.get("qgisminimumversion", "3.28"))
    qgis_max = saxutils.escape(meta.get("qgismaximumversion", "4.99"))
    description = saxutils.escape(meta.get("description", ""))
    about = saxutils.escape(meta.get("about", ""))
    author = saxutils.escape(meta.get("author", "André Aubin"))
    homepage = saxutils.escape(meta.get("homepage", ""))
    tracker = saxutils.escape(meta.get("tracker", ""))
    repository = saxutils.escape(meta.get("repository", ""))
    tags = saxutils.escape(meta.get("tags", ""))
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    download_url = saxutils.escape(
        f"{download_base_url.rstrip('/')}/{zip_filename}"
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<plugins>
  <pyqgis_plugin name="{name}" version="{version}">
    <description>{description}</description>
    <about>{about}</about>
    <version>{version}</version>
    <qgis_minimum_version>{qgis_min}</qgis_minimum_version>
    <qgis_maximum_version>{qgis_max}</qgis_maximum_version>
    <homepage>{homepage}</homepage>
    <file_name>{saxutils.escape(zip_filename)}</file_name>
    <author_name>{author}</author_name>
    <download_url>{download_url}</download_url>
    <update_date>{now_iso}</update_date>
    <experimental>False</experimental>
    <deprecated>False</deprecated>
    <tracker>{tracker}</tracker>
    <repository>{repository}</repository>
    <tags>{tags}</tags>
  </pyqgis_plugin>
</plugins>
"""


def package_qgis_plugin() -> Path:
    """Create dist/geostrata.zip and dist/plugins.xml ready for QGIS."""
    qgis_pkg_root = Path(__file__).resolve().parent.parent
    plugin_src_dir = qgis_pkg_root / "geostrata"
    registry_dist_dir = qgis_pkg_root.parent / "registry" / "dist"
    out_dir = qgis_pkg_root / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Copy latest compiled catalog.sqlite and manifest.json into plugin folder for offline seed
    for artifact_name in ("catalog.sqlite", "manifest.json"):
        src_artifact = registry_dist_dir / artifact_name
        if src_artifact.is_file():
            shutil.copy2(src_artifact, plugin_src_dir / artifact_name)

    zip_path = out_dir / "geostrata.zip"
    if zip_path.is_file():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(plugin_src_dir.rglob("*")):
            if file_path.is_dir():
                continue
            if "__pycache__" in file_path.parts or file_path.suffix in (".pyc", ".pyo"):
                continue
            arcname = Path("geostrata") / file_path.relative_to(plugin_src_dir)
            zf.write(file_path, arcname.as_posix())

    meta = read_metadata_txt(plugin_src_dir / "metadata.txt")
    base_url = os.environ.get(
        "GEOSTRATA_BASE_URL",
        "https://lambda2.github.io/geostrata",
    )
    plugins_xml_content = generate_plugins_xml(
        meta=meta,
        zip_filename="geostrata.zip",
        download_base_url=base_url,
    )
    plugins_xml_path = out_dir / "plugins.xml"
    plugins_xml_path.write_text(plugins_xml_content, encoding="utf-8")

    print(f"Packaged QGIS plugin archive: {zip_path} ({zip_path.stat().st_size} bytes)")
    print(f"Generated QGIS repository XML: {plugins_xml_path}")
    return zip_path


if __name__ == "__main__":
    package_qgis_plugin()
