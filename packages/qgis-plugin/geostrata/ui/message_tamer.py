"""Keep the QGIS message bar readable when layers produce repeated network errors."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from qgis.core import Qgis, QgsSettings
from qgis.PyQt.QtCore import QObject, QTimer

SETTINGS_TAME_MESSAGES_KEY = "geostrata/tame_network_messages"

NETWORK_MESSAGE_AUTO_HIDE_MS = 12000

# Words found in the messages QGIS and its providers emit for a failed or slow request (English and French).
_NETWORK_PATTERN = re.compile(
    r"network|r[ée]seau|timed? ?out|d[ée]lai|expir|download|t[ée]l[ée]charg|https?://|"
    r"server|serveur|host|h[ôo]te|connection|connexion|\btile|tuile|\bwm?ts\b|\bwms\b|\bwfs\b|"
    r"request|requ[êe]te|\bssl\b|certificat|\bhttp\b|\b[45]\d\d\b",
    re.IGNORECASE,
)


def tame_messages_enabled() -> bool:
    """Return True unless the user turned the message grouping off in the settings."""
    try:
        raw = QgsSettings().value(SETTINGS_TAME_MESSAGES_KEY, True)
        return str(raw).lower() not in ("false", "0", "no")
    except Exception:
        return True


def message_key(title: str, text: str) -> str:
    """Reduce a message to what makes it "the same error": host kept, paths, queries and numbers dropped."""
    combined = f"{title} {text}".lower()
    combined = re.sub(r"(https?://[^/\s'\"<>]+)[^\s'\"<>]*", r"\1", combined)
    combined = re.sub(r"<[^>]+>", " ", combined)
    combined = re.sub(r"\d+", "#", combined)
    return re.sub(r"\s+", " ", combined).strip()


def is_network_message(text: str) -> bool:
    """Tell whether a message bar text reads like a network or remote-service error."""
    return bool(_NETWORK_PATTERN.search(text or ""))


class MessageBarTamer(QObject):
    """Group identical warnings into one message bar item and auto-hide network errors.

    A slow tile server or an unreachable WFS makes QGIS push the same warning for
    every request, and those items never expire on their own. This keeps the first
    occurrence (with a ×N counter), drops the repeats, and removes network errors
    after a few seconds. Nothing is lost: QGIS still writes every message to its
    log panel.
    """

    def __init__(self, iface: Any, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._bar = iface.messageBar() if iface is not None else None
        # key -> {"item": QgsMessageBarItem, "text": original text, "count": int}
        self._shown: Dict[str, Dict[str, Any]] = {}
        self._seen_ids: Set[int] = set()
        self._connected = False
        if self._bar is not None:
            try:
                self._bar.widgetAdded.connect(self._on_widget_added)
                self._bar.widgetRemoved.connect(self._on_widget_removed)
                self._connected = True
            except Exception:
                self._connected = False

    def dispose(self) -> None:
        """Stop watching the message bar (plugin unload)."""
        if self._bar is not None and self._connected:
            try:
                self._bar.widgetAdded.disconnect(self._on_widget_added)
                self._bar.widgetRemoved.disconnect(self._on_widget_removed)
            except Exception:
                pass
        self._connected = False
        self._shown.clear()
        self._seen_ids.clear()

    def _on_widget_added(self, _widget: Any) -> None:
        # The signal argument can arrive as a bare QWidget wrapper (no title()/text()),
        # so the bar is asked for its items, which are always QgsMessageBarItem objects.
        try:
            if not tame_messages_enabled() or self._bar is None:
                return
            current_items = list(self._bar.items())
            current_ids = {id(item) for item in current_items}
            self._seen_ids &= current_ids
            for item in current_items:
                if id(item) in self._seen_ids:
                    continue
                self._seen_ids.add(id(item))
                self._handle_new_item(item)
        except Exception:
            pass

    def _handle_new_item(self, item: Any) -> None:
        text = str(item.text() or "")
        if not text:
            # Custom widgets (progress bars, buttons) are never touched.
            return
        if item.level() not in (Qgis.MessageLevel.Warning, Qgis.MessageLevel.Critical):
            return

        key = message_key(str(item.title() or ""), text)
        known = self._shown.get(key)
        if known is not None and known["item"] is not item:
            known["count"] += 1
            try:
                known["item"].setText(f"{known['text']} (×{known['count']})")
            except Exception:
                pass
            # Removing an item from inside the signal that announces it is not safe.
            QTimer.singleShot(0, lambda: self._pop(item))
            return

        self._shown[key] = {"item": item, "text": text, "count": 1}
        if is_network_message(text):
            QTimer.singleShot(NETWORK_MESSAGE_AUTO_HIDE_MS, lambda: self._pop(item))

    def _on_widget_removed(self, _widget: Any) -> None:
        try:
            remaining = {id(item) for item in self._bar.items()} if self._bar is not None else set()
        except Exception:
            remaining = set()
        stale: List[str] = [
            key for key, known in self._shown.items() if id(known["item"]) not in remaining
        ]
        for key in stale:
            self._shown.pop(key, None)

    def _pop(self, item: Any) -> None:
        try:
            if self._bar is not None:
                self._bar.popWidget(item)
        except Exception:
            # The item was already closed by the user or deleted with the bar.
            pass
