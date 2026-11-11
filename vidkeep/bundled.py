"""The packaged programs (the Windows installer, the Mac app) carry ffmpeg, ffprobe and deno in a
bin folder next to vidkeep, so people who never installed either can still download."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def tools_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).resolve().parent / "bin"
    return folder if folder.is_dir() else None


def use_bundled_tools() -> None:
    """Put the bundled tools first on PATH, where yt-dlp and the doctor look for them."""
    folder = tools_dir()
    if folder:
        os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
