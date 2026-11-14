"""Shared HTTPS helpers for the plugin's own downloads (catalog sync, file cache, Overpass)."""

from __future__ import annotations

import ssl
import urllib.request
from typing import Optional

from ..diagnostics import note_ignored

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
        except Exception as error:
            note_ignored(error)
        _HTTPS_CONTEXT = context
    return _HTTPS_CONTEXT


def build_direct_opener() -> urllib.request.OpenerDirector:
    """Return an opener limited to http(s), ignoring environment proxies and verifying TLS.

    Only the HTTP handlers are installed, so a `file:` or `ftp:` URL coming from a
    user-edited manifest address or a third-party catalog is refused ("unknown url
    type") instead of reading local files.
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler({}),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(context=https_context()),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener
