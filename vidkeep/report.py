"""Where download progress goes: live bars for people, JSON lines for programs such as the Mac app."""

from __future__ import annotations

import itertools
import json
import os
import sys
import threading
import time

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

TITLE_WIDTH = 42
JSON_PROGRESS_INTERVAL = 0.25  # seconds between progress lines per video; a UI needs no more


def short_title(title: str) -> str:
    return title if len(title) <= TITLE_WIDTH else title[: TITLE_WIDTH - 1] + "…"


def _saved_size(result) -> int | None:
    if result.status == "done" and result.path and os.path.exists(result.path):
        return os.path.getsize(result.path)
    return None


class RichReporter:
    """A progress bar per video on the terminal."""

    def __init__(self, console: Console, visible: bool = True):
        self.visible = visible
        self.progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(bar_width=None),
            DownloadColumn(binary_units=True),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        )

    def __enter__(self):
        self.progress.__enter__()
        return self

    def __exit__(self, *exc):
        return self.progress.__exit__(*exc)

    def start(self, result):
        return self.progress.add_task(short_title(result.item.url), total=None, visible=self.visible)

    def planned(self, handle, result) -> None:
        self.progress.update(handle, description=short_title(result.title), total=result.plan.size)

    def advanced(self, handle, completed: int, speed: float | None) -> None:
        self.progress.update(handle, completed=completed)

    def stage(self, handle, result, name: str) -> None:
        self.progress.update(handle, description=f"[dim]{name}[/] {short_title(result.title)}")

    def finished(self, handle, result) -> None:
        mark = {"done": "[green]done", "skipped": "[cyan]skipped", "failed": "[red]failed"}.get(result.status)
        if mark:
            self.progress.update(handle, description=f"{mark}[/] {short_title(result.title or result.item.url)}")
        size = _saved_size(result)
        if size is not None:
            self.progress.update(handle, completed=size, total=size)


class JsonReporter:
    """One JSON object per line on stdout: start, plan, progress, stage and finished events."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._last_progress: dict[int, float] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _emit(self, **event) -> None:
        with self._lock:
            self.stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.stream.flush()

    def start(self, result) -> int:
        handle = next(self._ids)
        self._emit(event="start", id=handle, url=result.item.url)
        return handle

    def planned(self, handle, result) -> None:
        plan = result.plan
        self._emit(
            event="plan",
            id=handle,
            title=result.title,
            quality=plan.describe(),
            format=plan.format_spec,
            size=plan.size,
        )

    def advanced(self, handle, completed: int, speed: float | None) -> None:
        now = time.monotonic()
        if now - self._last_progress.get(handle, float("-inf")) < JSON_PROGRESS_INTERVAL:
            return
        self._last_progress[handle] = now
        self._emit(event="progress", id=handle, downloaded=completed, speed=speed)

    def stage(self, handle, result, name: str) -> None:
        self._emit(event="stage", id=handle, stage=name)

    def finished(self, handle, result) -> None:
        self._emit(
            event="finished",
            id=handle,
            status=result.status,
            title=result.title,
            path=result.path,
            size=_saved_size(result),
            error=result.error,
            seconds=round(result.seconds, 1),
        )
