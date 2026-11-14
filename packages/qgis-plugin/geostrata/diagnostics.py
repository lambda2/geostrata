"""Trace of the errors the plugin deliberately does not show to the user."""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger("geostrata")


def note_ignored(error: BaseException, context: str = "") -> None:
    """Record an error that must not interrupt QGIS (optional UI refresh, cleanup, best-effort I/O).

    Nothing is displayed: the record goes to the "geostrata" Python logger at
    DEBUG level, so a developer can turn it on with
    ``logging.getLogger("geostrata").setLevel(logging.DEBUG)``.
    """
    _LOGGER.debug("ignored %s%s: %s", type(error).__name__, f" ({context})" if context else "", error)
