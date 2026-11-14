"""Shared HTTPS helpers for the plugin's own downloads (catalog sync, file cache, Overpass)."""

from __future__ import annotations

import ssl
import urllib.request
from typing import Optional

_HTTPS_CONTEXT: Optional[ssl.SSLContext] = None


def https_context() -> ssl.SSLContext:
    """Return a TLS context trusting the system store plus the certifi bundle.

    The Python bundled with QGIS on macOS only sees /etc/ssl/cert.pem, which
    misses several current root certificates, so valid servers failed with
    CERTIFICATE_VERIFY_FAILED. QGIS ships certifi: add its bundle when present.
    """
    global _HTTPS_CONTEXT
    if _HTTPS_CONTEXT is None:
        context = ssl.create_default_context()
        try:
            import certifi

            context.load_verify_locations(cafile=certifi.where())
        except Exception:
            pass
        _HTTPS_CONTEXT = context
    return _HTTPS_CONTEXT


def build_direct_opener() -> urllib.request.OpenerDirector:
    """Return an opener that ignores environment proxies and verifies TLS with https_context()."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=https_context()),
    )
