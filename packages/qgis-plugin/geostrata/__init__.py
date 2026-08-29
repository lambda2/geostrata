"""Geostrata QGIS Plugin entry point."""

from typing import Any


def classFactory(iface: Any) -> Any:
    """Load GeostrataPlugin class required by QGIS."""
    from .plugin import GeostrataPlugin

    return GeostrataPlugin(iface)
