"""What the desktop app does, apart from drawing it.

Each lookup and each download is its own `vidkeep` child process, as in the Mac app, so a stuck site
never freezes the window and closing a download is just ending its process. Nothing here touches
the window toolkit; the window reads this state on a timer.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from . import child

LOOKUP_TIMEOUT = 300
FINISHED = ("done", "skipped")


class LookupFailed(Exception):
    """vidkeep could not read the link; the message is vidkeep's own."""


def look_up(url: str, command: list[str] | None = None) -> dict:
    """What `vidkeep info --json` says about a link."""
    try:
        done = subprocess.run(
            [*(command or child.command()), "info", "--json", "--", url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=LOOKUP_TIMEOUT,
            creationflags=child.NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise LookupFailed("The site took too long to answer.") from None
    if done.returncode != 0:
        raise LookupFailed(child.error_message(done.stderr))
    return json.loads(done.stdout)


def only_the_video(url: str) -> str | None:
    """For a video playing inside a playlist (watch?v=…&list=…), the link to that video alone."""
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if "v" not in query or "list" not in query:
        return None
    return urlunsplit(parts._replace(query=urlencode({"v": query["v"][0]})))


@dataclass
class Request:
    """What the window asks for: one exact format, or a quality for every video of a playlist."""

    url: str
    folder: str
    format: str | None = None
    quality: str | None = None
    codec: str | None = None
    subs: str | None = None

    def arguments(self) -> list[str]:
        args = ["-o", self.folder]
        for flag, value in (("-f", self.format), ("-q", self.quality), ("-c", self.codec), ("--subs", self.subs)):
            if value:
                args += [flag, value]
        return [*args, "--", self.url]


@dataclass
class Download:
    """One `vidkeep get --json` run; a playlist run carries several videos."""

    request: Request
    title: str
    command: list[str] = field(default_factory=child.command)
    videos: dict[int, dict] = field(default_factory=dict)
    stopped: bool = False
    returncode: int | None = None
    stderr: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start(self) -> Download:
        self._process = subprocess.Popen(
            [*self.command, "get", "--json", *self.request.arguments()],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=child.NO_WINDOW,
        )
        self._errors = threading.Thread(target=self._read_errors, daemon=True)
        self._errors.start()
        threading.Thread(target=self._read_events, daemon=True).start()
        return self

    def _read_events(self) -> None:
        for line in self._process.stdout:
            with self._lock:
                child.fold_event(self.videos, line)
        self._errors.join()  # the status must not turn "failed" before its reason is in
        self.returncode = self._process.wait()

    def _read_errors(self) -> None:
        self.stderr = self._process.stderr.read()

    def stop(self) -> None:
        """Ranges already on disk stay, so the same request later picks up where this one stopped."""
        self.stopped = True
        if self.returncode is None:
            self._process.terminate()

    def again(self) -> Download:
        return Download(self.request, self.title, self.command).start()

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(video) for video in self.videos.values()]

    @property
    def status(self) -> str:
        if self.returncode is None:
            return "running"
        videos = self.snapshot()
        if videos and all(video.get("status") in FINISHED for video in videos):
            return "done"
        return "stopped" if self.stopped else "failed"

    @property
    def error(self) -> str | None:
        if self.status != "failed":
            return None
        failed = next((video["error"] for video in self.snapshot() if video.get("error")), None)
        return failed or child.error_message(self.stderr)

    @property
    def saved_path(self) -> str | None:
        return next((video["path"] for video in self.snapshot() if video.get("path")), None)


def default_folder() -> Path:
    videos = "Movies" if sys.platform == "darwin" else "Videos"
    return Path.home() / videos / "vidkeep"


def settings_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / "vidkeep" / "settings.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "vidkeep" / "desktop.json"


def load_settings(path: Path | None = None) -> dict:
    defaults = {"folder": str(default_folder()), "subtitles": False, "languages": "en"}
    try:
        saved = json.loads((path or settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    return {**defaults, **{key: saved[key] for key in defaults if key in saved}}


def save_settings(settings: dict, path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(settings, indent=2), encoding="utf-8")
