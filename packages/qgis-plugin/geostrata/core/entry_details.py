"""Human-readable summary of a catalog entry (format, coverage, data date, link status).

Pure Python (no QGIS import) so it can be unit-tested; the dock and the locator render it.
"""

from __future__ import annotations

import html
from typing import Callable, Dict, List, Optional, Tuple

from ..i18n import N_, tr
from .db_client import CatalogSourceRecord
from .uri_builder import entry_time_value, needs_local_copy

FORMAT_LABELS: Dict[str, str] = {
    "xyz": N_("XYZ tiles (raster)"),
    "tms": N_("TMS tiles (raster)"),
    "wms": N_("WMS service (raster)"),
    "wmts": N_("WMTS tiles (raster)"),
    "wfs": N_("WFS service (vector)"),
    "cog": N_("Cloud Optimized GeoTIFF (raster)"),
    "vrt": N_("GDAL virtual raster (VRT)"),
    "fgb": N_("FlatGeobuf (vector)"),
    "geojson": N_("GeoJSON (vector)"),
    "vectortile": N_("Vector tiles"),
    "overpass": N_("OpenStreetMap Overpass query (vector)"),
    "archive": N_("File inside a remote ZIP archive"),
    "arcgisfeatureserver": N_("ArcGIS Feature Service (vector)"),
    "arcgismapserver": N_("ArcGIS Map/Image Service (raster)"),
}


def format_label(entry: CatalogSourceRecord) -> str:
    """Return e.g. 'WMTS tiles (raster)' for the entry type."""
    source = FORMAT_LABELS.get(entry.type.lower())
    return tr(source) if source else entry.type.upper()


def coverage_label(
    entry: CatalogSourceRecord,
    country_name: Optional[Callable[[str], str]] = None,
) -> str:
    """Return the country (name + ISO code), 'Worldwide' or the regional bbox of the entry."""
    if entry.country_iso:
        iso = entry.country_iso.upper()
        name = ""
        if country_name is not None:
            try:
                name = country_name(iso) or ""
            except Exception:
                name = ""
        return f"{name} ({iso})" if name and name.upper() != iso else iso
    if entry.is_worldwide:
        return tr("Worldwide")
    return tr(
        "Regional extent ({west}, {south} to {east}, {north})",
        west=f"{entry.min_x:.2f}",
        south=f"{entry.min_y:.2f}",
        east=f"{entry.max_x:.2f}",
        north=f"{entry.max_y:.2f}",
    )


def data_date_label(entry: CatalogSourceRecord) -> str:
    """Return the free-text data date of the entry, or 'Not specified'."""
    return entry.data_date or tr("Not specified")


def link_status_label(entry: CatalogSourceRecord) -> str:
    """Describe the last automated link check (`health_status` / `health_checked_at`)."""
    checked = (entry.health_checked_at or "")[:10]
    status = (entry.health_status or "ok").lower()
    if status == "dead":
        base = tr("Unreachable")
    elif status == "degraded":
        base = tr("Degraded (slow or failing intermittently)")
    elif checked:
        base = tr("Working")
    else:
        # 'ok' is also the default of catalogs that never ran a link check.
        return tr("Not checked yet")
    if checked:
        return tr("{status}, checked on {date}", status=base, date=checked)
    return base


def describe_entry(
    entry: CatalogSourceRecord,
    country_name: Optional[Callable[[str], str]] = None,
) -> List[Tuple[str, str]]:
    """Return translated ``(label, value)`` rows describing the entry before loading it."""
    rows = [
        (tr("Provider"), entry.attribution or tr("Not specified")),
        (tr("License"), entry.license or tr("Not specified")),
        (tr("Format"), format_label(entry)),
        (tr("Coverage"), coverage_label(entry, country_name)),
        (tr("Data date"), data_date_label(entry)),
        (tr("Link status"), link_status_label(entry)),
    ]
    time_value = entry_time_value(entry)
    if time_value:
        # Time-templated source: the date that "Load" requests today.
        rows.insert(5, (tr("Date loaded"), time_value))
    if needs_local_copy(entry):
        rows.append(
            (tr("Loading"), tr("Downloaded in the background, then kept for offline use"))
        )
    return rows


def build_details_html(
    entry: CatalogSourceRecord,
    country_name: Optional[Callable[[str], str]] = None,
) -> str:
    """Render the details panel (title, description, then one table row per fact)."""
    rows = "".join(
        f"<tr><td style='padding-right:8px'><b>{html.escape(label)}</b></td>"
        f"<td>{html.escape(value)}</td></tr>"
        for label, value in describe_entry(entry, country_name)
    )
    description = html.escape(entry.localized_description())
    return (
        f"<p><b>{html.escape(entry.localized_name())}</b></p>"
        + (f"<p>{description}</p>" if description else "")
        + f"<table cellspacing='2'>{rows}</table>"
    )
