"""Running vidkeep as a child process and reading what it says back.

The Firefox add-on's host and the desktop app both start `vidkeep get --json` and `vidkeep info --json`
as separate processes, so a stuck site never freezes the program that started it. This module holds
what they share: how to start vidkeep, how to fold its progress events, and how to read its errors.
It imports only the standard library, because both callers must start quickly.
"""

from __future__ import annotations

import json
import subprocess
import sys

# Windows opens a console window for every console child unless told not to.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def command() -> list[str]:
    """How to start vidkeep from here: the packaged program runs its own command line with --cli."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--cli"]
    return [sys.executable, "-m", "vidkeep"]


def fold_event(videos: dict[int, dict], line: str) -> None:
    """Apply one line of `vidkeep get --json` output to the per-video state in ``videos``."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return  # a line still being written, or something that is not an event
    if not isinstance(event, dict):
        return
    video = videos.setdefault(event.get("id"), {"status": "starting", "downloaded": 0})
    kind = event.pop("event", None)
    event.pop("id", None)
    if kind == "plan":
        video["status"] = "downloading"
    elif kind == "stage":
        video["status"] = "processing"
    video.update(event)


def error_message(stderr: str) -> str:
    """vidkeep prints "error: ..." and maybe a hint, wrapped to 80 columns; fall back to its last line."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    starts = [index for index, line in enumerate(lines) if line.lower().startswith("error:")]
    if starts:
        return " ".join(lines[starts[-1] :])[len("error:") :].strip()
    return lines[-1] if lines else "vidkeep stopped without saying why"
