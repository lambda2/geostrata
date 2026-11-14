"""Toolchain-free UI translations: English source strings + one Python dictionary per language.

How it works
------------
* English is the source language: code calls ``tr("Load layer")`` with the English text.
* Each other language is a plain ``Dict[str, str]`` mapping the English source string to
  its translation (see ``fr.py``). Adding a third language is one more module registered
  in ``TRANSLATIONS`` below. There is no ``.ts``/``.qm`` file and no ``lrelease`` step.
* ``tests/test_core.py`` statically scans every ``tr()`` / ``tr_n()`` / ``N_()`` call of the
  plugin and fails when a string has no French translation (or mismatched placeholders).

Which language is used
----------------------
A single language drives both the plugin interface and the catalog content (layer names
and descriptions), resolved by :func:`get_preferred_language`:

1. the ``geostrata/language`` setting when it is not ``auto`` (Settings dialog);
2. otherwise the QGIS language: ``locale/userLocale`` when ``locale/overrideFlag`` is set,
   else ``QgsApplication.locale()`` (the system locale);
3. outside QGIS (unit tests): ``GEOSTRATA_LANG`` / ``LANG``, else English.

The interface then uses that language when a dictionary exists for it and English
otherwise; a catalog entry uses it when the entry carries an ``i18n`` block for it and its
English name/description otherwise. Both fall back to English independently, so e.g. a
Spanish QGIS gets an English interface with Spanish layer names where available.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional

from .fr import TRANSLATIONS as _FR

SOURCE_LANGUAGE = "en"
SETTINGS_LANGUAGE_KEY = "geostrata/language"

# language code -> {English source string: translation}
TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "fr": _FR,
}

_language_override: Optional[str] = None


def _normalize_language_code(raw: object) -> str:
    """Reduce 'fr_FR.UTF-8', 'FR', 'fr-CA'... to a lowercase 2-letter code ('' if not a language)."""
    match = re.match(r"^\s*([A-Za-z]{2,3})(?:$|[_\-.@\s])", str(raw or ""))
    if not match:
        return ""
    return match.group(1).lower()[:2]


def set_language_override(lang: Optional[str]) -> None:
    """Force the language for this process (tests, scripts). ``None`` restores auto-detection."""
    global _language_override
    _language_override = _normalize_language_code(lang) or None


def _qgis_locale_code() -> str:
    """Return the QGIS interface language, honouring the locale override flag."""
    from qgis.core import QgsApplication, QgsSettings

    settings = QgsSettings()
    raw_flag = settings.value("locale/overrideFlag", False)
    override = str(raw_flag).strip().lower() in ("true", "1", "yes")
    if override:
        code = _normalize_language_code(settings.value("locale/userLocale", ""))
        if code:
            return code
    code = _normalize_language_code(QgsApplication.locale())
    if code:
        return code
    return _normalize_language_code(settings.value("locale/userLocale", ""))


def get_preferred_language() -> str:
    """Resolve the 2-letter language used for the interface and the catalog content."""
    if _language_override:
        return _language_override
    try:
        from qgis.core import QgsSettings

        configured = str(
            QgsSettings().value(SETTINGS_LANGUAGE_KEY, "auto") or "auto"
        ).strip().lower()
        if configured and configured != "auto":
            code = _normalize_language_code(configured)
            if code:
                return code
        code = _qgis_locale_code()
        if code:
            return code
    except Exception:
        pass

    for env_name in ("GEOSTRATA_LANG", "LANG"):
        code = _normalize_language_code(os.environ.get(env_name))
        if code:
            return code
    return SOURCE_LANGUAGE


def get_ui_language() -> str:
    """Return the language actually used by the interface (English unless a dictionary exists)."""
    lang = get_preferred_language()
    return lang if lang in TRANSLATIONS else SOURCE_LANGUAGE


def N_(text: str) -> str:
    """Mark a string for translation without translating it now (module-level constants)."""
    return text


def tr(text: str, **kwargs: object) -> str:
    """Translate an English source string, then apply ``str.format(**kwargs)`` placeholders."""
    lang = get_ui_language()
    translated = text
    if lang != SOURCE_LANGUAGE:
        translated = TRANSLATIONS.get(lang, {}).get(text) or text
    if kwargs:
        try:
            return translated.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return text
    return translated


def tr_n(singular: str, plural: str, count: int, **kwargs: object) -> str:
    """Translate a counted string; ``{count}`` is always available as a placeholder."""
    if get_ui_language() == "fr":
        use_singular = count < 2  # French: 0 and 1 are singular
    else:
        use_singular = count == 1
    return tr(singular if use_singular else plural, count=count, **kwargs)
