"""Checks before downloading: the tools yt-dlp leans on, and room on disk."""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from dataclasses import dataclass

import yt_dlp

from .planner import Plan, format_size

# yt-dlp needs a JavaScript runtime to solve YouTube's challenges. Deno is
# enabled by default; the others work once enabled with --js-runtimes.
JS_RUNTIMES = ("deno", "node", "bun")
STALE_AFTER_DAYS = 60  # YouTube changes often; an old yt-dlp is the usual cause of failures
SPACE_MARGIN = 64 * 1024 * 1024


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fatal: bool = True


def js_runtime() -> str | None:
    return next((name for name in JS_RUNTIMES if shutil.which(name)), None)


def js_runtime_args(runtime: str | None) -> list[str]:
    return [] if runtime in (None, "deno") else ["--js-runtimes", runtime]


def space_needed(plan: Plan) -> int | None:
    """Bytes a download needs at its peak: merging holds both parts and the joined file at once."""
    if plan.size is None:
        return None
    return plan.size * (2 if plan.merges else 1) + SPACE_MARGIN


def free_space(path: str) -> int:
    """Free bytes on the volume that will hold ``path``, even if it does not exist yet."""
    probe = os.path.abspath(path)
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return shutil.disk_usage(probe).free


def ytdlp_age_days(version: str = yt_dlp.version.__version__, today: dt.date | None = None) -> int | None:
    """Days since this yt-dlp release, read from its date-based version string."""
    try:
        released = dt.datetime.strptime(".".join(version.split(".")[:3]), "%Y.%m.%d").date()
    except ValueError:
        return None
    return ((today or dt.date.today()) - released).days


def _tool_version(name: str) -> str | None:
    path = shutil.which(name)
    if not path:
        return None
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return path
    first = out.splitlines()[0] if out else ""
    return " ".join(first.split()[:3]) or path


def doctor(out_dir: str) -> list[Check]:
    return [
        _tool_check("ffmpeg", "joins video and audio, embeds chapters"),
        _tool_check("ffprobe", "reads media for embedding"),
        _runtime_check(),
        _ytdlp_check(),
        _space_check(out_dir),
    ]


def _tool_check(tool: str, why: str) -> Check:
    version = _tool_version(tool)
    return Check(tool, version is not None, version or f"missing: {why}")


def _runtime_check() -> Check:
    runtime = js_runtime()
    detail = runtime or "missing: install deno (brew install deno) or node; YouTube needs one"
    return Check("JavaScript runtime", runtime is not None, detail)


def _ytdlp_check() -> Check:
    age = ytdlp_age_days()
    stale = age is not None and age > STALE_AFTER_DAYS
    detail = f"{yt_dlp.version.__version__} ({age} days old)"
    if stale:
        detail += "; update: pip install -U 'yt-dlp[default]'"
    return Check("yt-dlp", not stale, detail, fatal=False)


def _space_check(out_dir: str) -> Check:
    free = free_space(out_dir)
    return Check("free space", free > 2 * 1024**3, f"{format_size(free)} at {os.path.abspath(out_dir)}", fatal=False)
