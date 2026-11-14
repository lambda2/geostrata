"""Share dialog generating an Geostrata YAML snippet from an active QGIS layer."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, List, Optional

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMapLayer,
    QgsProject,
)
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr

GITHUB_NEW_ISSUE_URL = "https://github.com/lambda2/geostrata/issues/new"


def _slugify(value: str) -> str:
    lowered = value.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or "new-source"


class GeostrataShareDialog(QDialog):
    """Dialog to generate YAML and submit a new layer to Geostrata."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Geostrata — Share a layer (YAML)"))
        self.setMinimumSize(680, 520)
        self._layers: List[QgsMapLayer] = list(
            QgsProject.instance().mapLayers().values()
        )
        self._build_ui()
        if self._layers:
            self._on_layer_selected(0)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        intro = QLabel(
            tr(
                "Select a layer of your QGIS project to generate its Geostrata "
                "YAML entry automatically."
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self._layer_combo = QComboBox(self)
        for layer in self._layers:
            self._layer_combo.addItem(layer.name(), layer.id())
        self._layer_combo.currentIndexChanged.connect(self._on_layer_selected)
        form.addRow(tr("QGIS layer:"), self._layer_combo)

        self._category_combo = QComboBox(self)
        for cat in [
            "basemap",
            "elevation",
            "hydrography",
            "transport",
            "admin",
            "buildings",
            "landuse",
            "imagery",
            "utilities",
            "crisis",
            "population",
            "health",
            "hazard",
            "weather",
        ]:
            self._category_combo.addItem(cat)
        self._category_combo.currentIndexChanged.connect(self._regenerate_yaml)
        form.addRow(tr("Category:"), self._category_combo)

        self._country_edit = QLineEdit(self)
        self._country_edit.setPlaceholderText(tr("E.g. FR (leave empty if worldwide)"))
        self._country_edit.textChanged.connect(self._regenerate_yaml)
        form.addRow(tr("ISO-2 country code:"), self._country_edit)

        self._license_edit = QLineEdit(tr("CC BY 4.0"), self)
        self._license_edit.textChanged.connect(self._regenerate_yaml)
        form.addRow(tr("License:"), self._license_edit)

        self._attribution_edit = QLineEdit(tr("© Data provider"), self)
        self._attribution_edit.textChanged.connect(self._regenerate_yaml)
        form.addRow(tr("Attribution:"), self._attribution_edit)

        layout.addLayout(form)

        self._yaml_preview = QPlainTextEdit(self)
        layout.addWidget(self._yaml_preview)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton(tr("Copy YAML"), self)
        issue_btn = QPushButton(tr("Propose on GitHub (pre-filled issue)…"), self)
        close_btn = QPushButton(tr("Close"), self)

        copy_btn.clicked.connect(self._on_copy_yaml)
        issue_btn.clicked.connect(self._on_open_github_issue)
        close_btn.clicked.connect(self.accept)

        btn_row.addWidget(copy_btn)
        btn_row.addWidget(issue_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _on_layer_selected(self, index: int) -> None:
        if index < 0 or index >= len(self._layers):
            return
        layer = self._layers[index]
        attribution = layer.attribution() if hasattr(layer, "attribution") else ""
        if attribution:
            self._attribution_edit.setText(attribution)
        self._regenerate_yaml()

    def _infer_source_type(self, layer: QgsMapLayer) -> str:
        provider = (layer.providerType() or "").lower()
        source = layer.source()
        if provider == "wfs":
            return "wfs"
        if provider == "wms":
            if "type=xyz" in source:
                return "xyz"
            if "tileMatrixSet=" in source or "SERVICE=WMTS" in source.upper():
                return "wmts"
            return "wms"
        if provider == "vectortile":
            return "vectortile"
        if source.lower().endswith(".vrt"):
            return "vrt"
        if source.lower().endswith((".tif", ".tiff")):
            return "cog"
        if source.lower().endswith(".fgb"):
            return "fgb"
        return "geojson"

    def _compute_layer_bbox_4326(self, layer: QgsMapLayer) -> List[float]:
        try:
            extent = layer.extent()
            src_crs = layer.crs()
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            if src_crs.isValid() and src_crs != wgs84:
                transform = QgsCoordinateTransform(
                    src_crs, wgs84, QgsProject.instance()
                )
                extent = transform.transformBoundingBox(extent)
            return [
                round(max(-180.0, extent.xMinimum()), 4),
                round(max(-90.0, extent.yMinimum()), 4),
                round(min(180.0, extent.xMaximum()), 4),
                round(min(90.0, extent.yMaximum()), 4),
            ]
        except Exception:
            return [-180.0, -90.0, 180.0, 90.0]

    def _regenerate_yaml(self, *_: Any) -> None:
        idx = self._layer_combo.currentIndex()
        if idx < 0 or idx >= len(self._layers):
            self._yaml_preview.setPlainText(tr("# No layer is open in the project."))
            return

        layer = self._layers[idx]
        country = self._country_edit.text().strip().upper()
        prefix = f"{country.lower()}-" if len(country) == 2 else "global-"
        entry_id = f"{prefix}{_slugify(layer.name())}"
        source_type = self._infer_source_type(layer)
        category = self._category_combo.currentText()
        crs = layer.crs().authid() or "EPSG:4326"
        bbox = self._compute_layer_bbox_4326(layer)
        license_str = self._license_edit.text().strip() or "Open Data"
        attribution_str = self._attribution_edit.text().strip() or "© Open Data"

        country_line = f"\ncountry_iso: {country}" if len(country) == 2 else ""

        yaml_text = (
            f"id: {entry_id}\n"
            f"name: {layer.name()}\n"
            f"description: Layer shared from QGIS ({layer.name()}).\n"
            f"i18n:\n"
            f"  fr:\n"
            f"    name: {layer.name()}\n"
            f"    description: Couche partagée depuis QGIS ({layer.name()}).\n"
            f"type: {source_type}\n"
            f"category: {category}\n"
            f"url: {layer.source()}\n"
            f"crs: {crs}{country_line}\n"
            f"bbox: [{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}]\n"
            f"tags:\n"
            f"  - {category}\n"
            f'license: "{license_str}"\n'
            f'attribution: "{attribution_str}"\n'
        )
        self._yaml_preview.setPlainText(yaml_text)

    def _on_copy_yaml(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._yaml_preview.toPlainText())
            QMessageBox.information(
                self,
                "Geostrata",
                tr("YAML entry copied to the clipboard."),
            )

    def _on_open_github_issue(self) -> None:
        yaml_body = self._yaml_preview.toPlainText()
        # The issue is read by the maintainers: always written in English.
        title = "New source: " + (
            self._layer_combo.currentText() or "Geographic source"
        )
        body = (
            "### New Geostrata source proposal\n\n"
            f"```yaml\n{yaml_body}```\n"
        )
        query = urllib.parse.urlencode({"title": title, "body": body})
        QDesktopServices.openUrl(QUrl(f"{GITHUB_NEW_ISSUE_URL}?{query}"))
