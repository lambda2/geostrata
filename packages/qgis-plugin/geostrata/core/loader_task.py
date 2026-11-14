"""Non-blocking loading of file-based sources (ZIP archives, GeoJSON) through the file cache.

``launch_file_load_task()`` is what "Load" does for the entries selected by
``uri_builder.needs_local_copy()``:

1. a cancellable ``QgsTask`` downloads the file into the cache (core/file_cache.py),
   reporting real progress to the QGIS task manager and to a message-bar item that has
   its own Cancel button;
2. still in the task thread, the layer is created on the local copy (opening a large
   GeoJSON means parsing it, which must not happen on the main thread) and moved to the
   main thread with ``QObject.moveToThread()``, as QGIS requires for objects created in
   a task;
3. ``finished()`` (main thread) applies the style, adds the layer to the project and
   reports what happened.

Task references are kept in ``_ACTIVE_FILE_LOAD_TASKS`` until ``finished()`` so Python
never garbage-collects a running task.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, List, Optional, Tuple

from ..diagnostics import note_ignored
from ..i18n import tr
from .db_client import CatalogSourceRecord
from .file_cache import (
    CachedCopy,
    DownloadCancelled,
    DownloadError,
    FileCache,
    ProgressCallback,
    configured_file_cache,
    format_size,
)
from .uri_builder import (
    apply_layer_metadata_and_style,
    build_provider_uri,
    entry_display_name,
    instantiate_layer,
    remote_file_url,
    resolved_archive_target,
)

_ACTIVE_FILE_LOAD_TASKS: List[Any] = []
PROGRESS_ITEM_DELAY_SEC = 0.7


def entry_cache_key(entry: CatalogSourceRecord) -> Tuple[str, str]:
    """Return the (URL, archive target) pair identifying the entry's file in the cache."""
    url = remote_file_url(entry) or ""
    target = resolved_archive_target(entry) if entry.type.lower() == "archive" else ""
    return url, target


def fetch_entry_file(
    cache: FileCache,
    entry: CatalogSourceRecord,
    progress: Optional[ProgressCallback] = None,
    is_canceled: Optional[Callable[[], bool]] = None,
    in_use: Iterable[str] = (),
) -> CachedCopy:
    """Return the local copy of a file-based entry, downloading it when needed (blocking).

    Shared by the loader task and the offline extraction task; call it from a worker
    thread. Raises DownloadCancelled / DownloadError.
    """
    url, target = entry_cache_key(entry)
    if not url:
        raise DownloadError(tr("this source has no downloadable URL"))
    return cache.fetch(
        url,
        target,
        kind=entry.type.lower(),
        progress=progress,
        is_canceled=is_canceled,
        in_use=in_use,
    )


def open_layer_sources() -> List[str]:
    """Return the data sources of the project layers (main thread only).

    The cache never deletes a file one of these strings points to.
    """
    try:
        from qgis.core import QgsProject

        return [str(layer.source()) for layer in QgsProject.instance().mapLayers().values()]
    except Exception:
        return []


def cancel_active_file_load_tasks() -> None:
    """Cancel the running downloads (plugin unload)."""
    for task in list(_ACTIVE_FILE_LOAD_TASKS):
        try:
            task.cancel()
        except Exception as error:
            note_ignored(error)


def progress_text(title: str, phase: str, done: int, total: Optional[int]) -> str:
    """Return the message-bar text of a running file load."""
    if phase == "extract":
        percent = int(100 * done / total) if total else 0
        return tr("Extracting {name}: {percent} %", name=title, percent=percent)
    if phase == "open":
        return tr("Opening {name}…", name=title)
    if total:
        return tr(
            "Downloading {name}: {done} of {total}",
            name=title,
            done=format_size(done),
            total=format_size(total),
        )
    return tr("Downloading {name}: {done}", name=title, done=format_size(done))


def launch_file_load_task(
    iface: Any,
    entry: CatalogSourceRecord,
    canvas_bbox_4326: Optional[Tuple[float, float, float, float]] = None,
    cache: Optional[FileCache] = None,
) -> Any:
    """Download (or reuse) the entry's file in the background, then add its layer.

    Must be called on the main thread. Returns the scheduled QgsTask; when the same
    file is already being downloaded, that running task is returned instead.
    """
    from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsProject, QgsTask
    from qgis.PyQt.QtWidgets import QProgressBar, QPushButton

    title = entry_display_name(entry)
    file_cache = cache or configured_file_cache()
    in_use = open_layer_sources()
    url, target = entry_cache_key(entry)
    cache_key = str(file_cache.entry_dir(url, target))
    extracts = entry.type.lower() == "archive" and bool(target)
    main_thread = QgsApplication.instance().thread()

    def notify(text: str, level: Any, duration: int = 6) -> None:
        try:
            if iface is not None and iface.messageBar() is not None:
                iface.messageBar().pushMessage("Geostrata", text, level=level, duration=duration)
        except Exception as error:
            note_ignored(error)

    for running in _ACTIVE_FILE_LOAD_TASKS:
        if getattr(running, "cache_key", None) == cache_key and not running.isCanceled():
            notify(
                tr("{name} is already being downloaded.", name=title),
                Qgis.MessageLevel.Info,
            )
            return running

    class FileLoadTask(QgsTask):
        """Background task: cached download, extraction, then layer creation."""

        def __init__(self) -> None:
            super().__init__(
                tr("Geostrata: loading {name}", name=title),
                QgsTask.Flag.CanCancel,
            )
            self.cache_key: str = cache_key
            self.layer: Any = None
            self.spec: Any = None
            self.copy: Optional[CachedCopy] = None
            self.error_message: Optional[str] = None
            self.was_canceled: bool = False
            # Read by the main thread to label the progress (plain attribute reads).
            self.phase: str = "download"
            self.bytes_done: int = 0
            self.bytes_total: Optional[int] = None

        def _on_progress(self, phase: str, done: int, total: Optional[int]) -> None:
            self.phase, self.bytes_done, self.bytes_total = phase, done, total
            fraction = (done / total) if total else 0.0
            if phase == "download":
                self.setProgress(fraction * (75.0 if extracts else 95.0))
            else:
                self.setProgress(75.0 + fraction * 20.0)

        def run(self) -> bool:
            try:
                copy = fetch_entry_file(
                    file_cache, entry, self._on_progress, self.isCanceled, in_use
                )
            except DownloadCancelled:
                self.was_canceled = True
                return False
            except DownloadError as exc:
                self.error_message = str(exc)
                return False
            except Exception as exc:
                self.error_message = str(exc) or exc.__class__.__name__
                return False

            self.copy = copy
            self.phase = "open"
            self.setProgress(96.0)
            try:
                spec = build_provider_uri(entry, canvas_bbox_4326, local_path=copy.open_path)
                layer = instantiate_layer(spec)
                if not layer.isValid():
                    del layer
                    # Not a readable dataset (e.g. an HTML error page): do not keep it.
                    file_cache.discard(url, target)
                    self.error_message = tr(
                        "the downloaded file could not be opened ({uri})", uri=spec.uri
                    )
                    return False
                # Created in this worker thread: hand it over to the main thread, where
                # finished() styles it and adds it to the project.
                layer.moveToThread(main_thread)
                self.layer, self.spec = layer, spec
                return True
            except Exception as exc:
                self.error_message = str(exc) or exc.__class__.__name__
                return False

        def finished(self, result: bool) -> None:
            try:
                if self in _ACTIVE_FILE_LOAD_TASKS:
                    _ACTIVE_FILE_LOAD_TASKS.remove(self)
            except Exception as error:
                note_ignored(error)
            close_progress_item()

            if self.was_canceled or (not result and self.isCanceled()):
                self.layer = None
                notify(
                    tr(
                        "Download cancelled: {name}. The part already downloaded is kept "
                        "and the download resumes from there next time.",
                        name=title,
                    ),
                    Qgis.MessageLevel.Info,
                )
                return

            if not result or self.layer is None or self.copy is None:
                message = tr(
                    "Could not load '{name}': {error}",
                    name=title,
                    error=self.error_message or tr("unknown error"),
                )
                QgsMessageLog.logMessage(message, "Geostrata", Qgis.MessageLevel.Warning)
                notify(message, Qgis.MessageLevel.Warning, duration=8)
                return

            layer, copy = self.layer, self.copy
            apply_layer_metadata_and_style(layer, entry)
            QgsProject.instance().addMapLayer(layer)

            day = copy.fetched_at[:10]
            if copy.stale_reason:
                notify(
                    tr(
                        "Layer loaded from the local copy of {date}: {name}. "
                        "The server could not be checked for a newer version ({error}).",
                        date=day,
                        name=title,
                        error=copy.stale_reason,
                    ),
                    Qgis.MessageLevel.Warning,
                    duration=8,
                )
            elif copy.from_cache:
                notify(
                    tr(
                        "Layer loaded from the local cache: {name} (downloaded on {date})",
                        name=title,
                        date=day,
                    ),
                    Qgis.MessageLevel.Success,
                    duration=4,
                )
            else:
                notify(
                    tr(
                        "Layer loaded: {name} ({size} downloaded and kept in the local cache)",
                        name=title,
                        size=format_size(copy.downloaded_bytes),
                    ),
                    Qgis.MessageLevel.Success,
                    duration=5,
                )

    task = FileLoadTask()

    # Message-bar item with a progress bar and a Cancel button. It appears once the
    # download has run for a moment, so cache hits and small files never flash it.
    progress_state: dict = {"item": None, "bar": None, "dismissed": False}
    started_at = time.monotonic()

    def on_progress_changed(_value: float) -> None:
        try:
            if iface is None or iface.messageBar() is None:
                return
            if progress_state["dismissed"]:
                return
            if progress_state["item"] is None:
                if (
                    task.phase == "open"
                    or task.bytes_done <= 0
                    or time.monotonic() - started_at < PROGRESS_ITEM_DELAY_SEC
                ):
                    return
                item = iface.messageBar().createMessage("Geostrata", "")
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setMaximumWidth(180)
                cancel_button = QPushButton(tr("Cancel"))
                cancel_button.clicked.connect(task.cancel)
                item.layout().addWidget(bar)
                item.layout().addWidget(cancel_button)
                iface.messageBar().pushWidget(item, Qgis.MessageLevel.Info)
                progress_state["item"], progress_state["bar"] = item, bar
            progress_state["item"].setText(
                progress_text(title, task.phase, task.bytes_done, task.bytes_total)
            )
            progress_state["bar"].setValue(int(task.progress()))
        except RuntimeError:
            # The user closed the message item: its widgets are gone, do not reopen it.
            progress_state["item"] = None
            progress_state["dismissed"] = True

    def close_progress_item() -> None:
        item, progress_state["item"] = progress_state["item"], None
        if item is None:
            return
        try:
            iface.messageBar().popWidget(item)
        except Exception as error:
            note_ignored(error)

    task.progressChanged.connect(on_progress_changed)
    _ACTIVE_FILE_LOAD_TASKS.append(task)
    QgsApplication.taskManager().addTask(task)
    return task
