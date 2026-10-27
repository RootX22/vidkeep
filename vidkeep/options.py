"""Turn vidkeep settings into yt-dlp options.

Options are written as yt-dlp command-line flags and parsed by yt-dlp itself,
so post-processing (merging, chapters, thumbnails, subtitles) is configured
exactly as the yt-dlp CLI would configure it, not by hand-built internals.
"""

from __future__ import annotations

from dataclasses import dataclass

import yt_dlp

from . import accel
from .planner import Plan

DEFAULT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"
AUDIO_FORMATS = ("m4a", "mp3", "opus")
CONTAINERS = ("mp4", "mkv")
# What yt-dlp can embed into: asking for more makes it fail an otherwise good download.
COVER_ART_EXTS = {"mp4", "m4v", "mov", "m4a", "mkv", "mka", "mp3", "ogg", "opus", "flac"}
CHAPTER_EXTS = {"mp4", "m4v", "mov", "m4a", "mkv", "mka", "webm"}


@dataclass(frozen=True)
class Settings:
    out_dir: str = "."
    template: str = DEFAULT_TEMPLATE
    container: str = "mp4"
    audio_format: str = "m4a"
    subs: str | None = None  # comma-separated languages, e.g. "en,ar"
    split_chapters: bool = False
    metadata: bool = True
    thumbnail: bool = True
    archive: str | None = None
    cookies_from_browser: str | None = None
    limit_rate: str | None = None
    fragments: int = 8  # parallel fragments for HLS/DASH formats yt-dlp fetches itself
    js_runtime_args: tuple[str, ...] = ()
    connections: int = 4
    chunk_size: int = accel.DEFAULT_CHUNK_SIZE


def base_args(settings: Settings) -> list[str]:
    """Flags every call needs, extraction included."""
    args = list(settings.js_runtime_args)
    if settings.cookies_from_browser:
        args += ["--cookies-from-browser", settings.cookies_from_browser]
    return args


def final_ext(plan: Plan, settings: Settings) -> str:
    """The extension of the file a plan ends up as, after merging or audio conversion."""
    if plan.merges:
        return settings.container
    if plan.picture is None and plan.audio.ext != settings.audio_format:
        return settings.audio_format
    return plan.parts[0].ext


def download_args(plan: Plan, settings: Settings, template: str | None = None) -> list[str]:
    audio_only = plan.picture is None
    args = base_args(settings) + [
        "-f",
        plan.format_spec,
        "-P",
        settings.out_dir,
        "-o",
        template or settings.template,
        "-N",
        str(settings.fragments),
        "--retries",
        "10",
        "--fragment-retries",
        "10",
    ]
    if plan.merges:
        args += ["--merge-output-format", settings.container]
    converts_audio = audio_only and plan.audio and settings.audio_format != plan.audio.ext
    if converts_audio:
        args += ["-x", "--audio-format", settings.audio_format, "--audio-quality", "0"]
    ext = final_ext(plan, settings)
    if settings.metadata:
        args += ["--embed-metadata"] + (["--embed-chapters"] if not audio_only and ext in CHAPTER_EXTS else [])
    if settings.thumbnail and ext in COVER_ART_EXTS:
        args += ["--embed-thumbnail"]
    if settings.subs:
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", settings.subs, "--convert-subs", "srt"]
        if not audio_only and ext in CHAPTER_EXTS:  # the containers that hold subtitle tracks
            args += ["--embed-subs"]
    if settings.split_chapters:
        args += [
            "--split-chapters",
            "-o",
            "chapter:%(title)s [%(id)s]/%(section_number)03d - %(section_title)s.%(ext)s",
        ]
    if settings.archive:
        args += ["--download-archive", settings.archive]
    if settings.limit_rate:
        args += ["--limit-rate", settings.limit_rate]
    return args


def to_params(args: list[str], settings: Settings, **extra) -> dict:
    """yt-dlp params for ``args``, plus the parallel-range settings and any hooks in ``extra``."""
    params = yt_dlp.parse_options(args).ydl_opts
    params.update(
        vidkeep_connections=settings.connections,
        vidkeep_chunk_size=settings.chunk_size,
        quiet=True,
        no_warnings=False,
        noprogress=True,
    )
    params.update(extra)
    return params
