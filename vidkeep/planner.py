"""Choose which streams to download from what a video offers.

yt-dlp can select formats by expression, but it cannot answer "the best
1080p-or-lower pair of streams whose combined size fits in 1.5 GB". This module
looks at every video and audio stream with its size, picks a pair, and hands
yt-dlp the exact format ids.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CODEC_FAMILIES = {
    "av01": "av1",
    "vp09": "vp9",
    "vp9": "vp9",
    "avc1": "h264",
    "avc3": "h264",
    "h264": "h264",
    "hev1": "h265",
    "hvc1": "h265",
}

# Preference orders. "efficient" spends the fewest bytes for a given picture;
# "compatible" is the codec every player and device opens.
CODEC_ORDERS = {
    "efficient": ("av1", "vp9", "h264", "h265"),
    "compatible": ("h264",),
    "av1": ("av1",),
    "vp9": ("vp9",),
    "h264": ("h264",),
    "h265": ("h265",),
}

# Where each codec plays without extra software on a Mac.
QUICKTIME = {"h264": "yes", "h265": "yes", "av1": "M3 or newer", "vp9": "no"}

AUDIO_QUALITIES = ("low", "medium", "high")
QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360", "240", "144")
MEDIUM_AUDIO_KBPS = 128

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*$", re.IGNORECASE)
_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


class NoMatch(Exception):
    """No stream combination satisfies the request."""


def parse_size(text: str) -> int:
    """Parse ``700M``, ``1.5G``, ``2GiB`` or a plain byte count into bytes (binary units)."""
    match = _SIZE.match(text)
    if not match:
        raise ValueError(f"not a size: {text!r} (try 700M or 1.5G)")
    number, unit = match.groups()
    return int(float(number) * _UNITS[unit.lower()])


def format_size(n: int | None) -> str:
    if n is None:
        return "?"
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.0f} KiB"
    if n < 1024**3:
        return f"{n / 1024**2:.2f} MiB"
    return f"{n / 1024**3:.2f} GiB"


def codec_family(vcodec: str | None) -> str | None:
    if not vcodec or vcodec == "none":
        return None
    head = vcodec.split(".")[0].lower()
    return CODEC_FAMILIES.get(head, head)


@dataclass(frozen=True)
class Stream:
    format_id: str
    kind: str  # "video", "audio" or "muxed"
    size: int | None
    exact: bool  # size from the server rather than an estimate
    protocol: str
    ext: str
    height: int | None = None
    codec: str | None = None
    acodec: str | None = None
    bitrate: float = 0.0  # kbps
    language_preference: int = 0

    @property
    def direct(self) -> bool:
        """A single file over HTTP: exact size and eligible for parallel ranges."""
        return self.protocol in ("http", "https")


def _size_of(fmt: dict, duration: float | None) -> tuple[int | None, bool]:
    if isinstance(fmt.get("filesize"), int):
        return fmt["filesize"], True
    if fmt.get("filesize_approx"):
        return int(fmt["filesize_approx"]), False
    if fmt.get("tbr") and duration:
        return int(fmt["tbr"] * 1000 / 8 * duration), False
    return None, False


def _usable(fmt: dict) -> bool:
    note = (fmt.get("format_note") or "").lower()
    return not (
        fmt.get("has_drm")
        or fmt.get("ext") == "mhtml"  # storyboard thumbnails, not video
        or "premium" in note  # needs a paid account; fails without one
        or str(fmt.get("format_id", "")).endswith("-drc")  # compressed-dynamics duplicate track
    )


def streams(info: dict) -> list[Stream]:
    """Every usable stream of a video, classified as video-only, audio-only or muxed."""
    duration = info.get("duration")
    out = []
    for fmt in info.get("formats") or []:
        if not _usable(fmt):
            continue
        # As in yt-dlp, a missing codec means "unknown", not "absent": many sites
        # list plain files without codecs, and those carry both picture and sound.
        has_video = fmt.get("vcodec") != "none"
        has_audio = fmt.get("acodec") != "none"
        if not (has_video or has_audio):
            continue
        kind = "muxed" if has_video and has_audio else "video" if has_video else "audio"
        size, exact = _size_of(fmt, duration)
        out.append(
            Stream(
                format_id=str(fmt["format_id"]),
                kind=kind,
                size=size,
                exact=exact,
                protocol=fmt.get("protocol") or "",
                ext=fmt.get("ext") or "",
                height=fmt.get("height"),
                codec=codec_family(fmt.get("vcodec")),
                acodec=(fmt.get("acodec") or "").split(".")[0] or None,
                bitrate=float(fmt.get("tbr") or fmt.get("abr") or 0),
                language_preference=fmt.get("language_preference") or 0,
            )
        )
    return out


@dataclass(frozen=True)
class Plan:
    video: Stream | None = None
    audio: Stream | None = None
    muxed: Stream | None = None

    @property
    def parts(self) -> list[Stream]:
        return [s for s in (self.video, self.audio, self.muxed) if s]

    @property
    def format_spec(self) -> str:
        return "+".join(s.format_id for s in self.parts)

    @property
    def size(self) -> int | None:
        sizes = [s.size for s in self.parts]
        return None if any(s is None for s in sizes) else sum(sizes)

    @property
    def exact(self) -> bool:
        return all(s.exact for s in self.parts)

    @property
    def merges(self) -> bool:
        """Separate streams get joined on disk, which briefly needs room for both copies."""
        return len(self.parts) > 1

    @property
    def picture(self) -> Stream | None:
        return self.video or self.muxed

    def describe(self) -> str:
        pic = self.picture
        if pic is None:
            return f"audio only ({self.audio.acodec}, {self.audio.bitrate:.0f} kbps)"
        height = f"{pic.height}p" if pic.height else "original size"
        return f"{height} {pic.codec or pic.ext}"


@dataclass(frozen=True)
class Request:
    max_height: int | None = 1080  # None means the highest available
    codec: str = "efficient"
    audio_quality: str = "medium"
    max_size: int | None = None
    audio_only: bool = False
    prefer_audio_ext: str | None = "m4a"  # AAC fits in mp4 and plays everywhere
    formats: str | None = None  # exact ids such as "399+140", as listed by `vidkeep info --json`


def _rank_direct(s: Stream) -> int:
    return 1 if s.direct else 0


def best_video(videos: list[Stream], height: int, codec: str) -> Stream | None:
    """Highest bitrate stream at this height and codec, single files first (exact size, fast path)."""
    matches = [v for v in videos if v.height == height and v.codec == codec]
    return max(matches, key=lambda v: (_rank_direct(v), v.bitrate), default=None)


def pick_audio(audios: list[Stream], quality: str, prefer_ext: str | None) -> Stream | None:
    if quality not in AUDIO_QUALITIES:
        raise ValueError(f"audio quality must be one of {AUDIO_QUALITIES}")
    if not audios:
        return None
    # The original-language track, not a dub, then the preferred container.
    top = max(a.language_preference for a in audios)
    pool = [a for a in audios if a.language_preference == top]
    if prefer_ext and any(a.ext == prefer_ext for a in pool):
        pool = [a for a in pool if a.ext == prefer_ext]
    if quality == "low":
        return min(pool, key=lambda a: (a.bitrate, -_rank_direct(a)))
    if quality == "high":
        return max(pool, key=lambda a: (a.bitrate, _rank_direct(a)))
    return min(pool, key=lambda a: (abs(a.bitrate - MEDIUM_AUDIO_KBPS), -_rank_direct(a)))


def _fits(plan: Plan, max_size: int | None) -> bool:
    return max_size is None or (plan.size is not None and plan.size <= max_size)


def plan_for(info: dict, spec: str) -> Plan:
    """The streams named in ``spec`` ("399+140"): at most one video, one audio, or one muxed stream."""
    offered = {s.format_id: s for s in streams(info)}
    missing = [fid for fid in spec.split("+") if fid not in offered]
    if missing:
        raise NoMatch(f"format {', '.join(missing)} is not offered for this video")
    parts = [offered[fid] for fid in spec.split("+")]
    by_kind = {s.kind: s for s in parts}
    if len(by_kind) != len(parts) or ("muxed" in by_kind and len(parts) > 1):
        raise NoMatch(f"{spec!r} must name one video and one audio stream, or a single stream")
    return Plan(video=by_kind.get("video"), audio=by_kind.get("audio"), muxed=by_kind.get("muxed"))


def choose(info: dict, req: Request) -> Plan:
    """The best plan for ``req``: highest height first, then codec preference, within the size cap."""
    if req.formats:
        return plan_for(info, req.formats)
    if req.codec not in CODEC_ORDERS:
        raise ValueError(f"codec must be one of {tuple(CODEC_ORDERS)}")
    all_streams = streams(info)
    videos = [s for s in all_streams if s.kind == "video" and s.height]
    audios = [s for s in all_streams if s.kind == "audio"]
    muxed = [s for s in all_streams if s.kind == "muxed"]
    audio = pick_audio(audios, req.audio_quality, req.prefer_audio_ext)

    if req.audio_only:
        if audio is None:
            raise NoMatch("this video has no separate audio stream")
        plan = Plan(audio=audio)
        if not _fits(plan, req.max_size):
            raise NoMatch(f"the audio alone is {format_size(plan.size)}, over the size limit")
        return plan

    def allowed(height: int | None) -> bool:
        return height is not None and (req.max_height is None or height <= req.max_height)

    if audio is not None:
        for height in sorted({v.height for v in videos if allowed(v.height)}, reverse=True):
            for codec in CODEC_ORDERS[req.codec]:
                video = best_video(videos, height, codec)
                if video and _fits(Plan(video=video, audio=audio), req.max_size):
                    return Plan(video=video, audio=audio)

    for stream in sorted(muxed, key=lambda m: (m.height or 0, m.bitrate, m.size or 0), reverse=True):
        # A file of unknown height cannot be ruled out by the height cap.
        if (stream.height is None or allowed(stream.height)) and _fits(Plan(muxed=stream), req.max_size):
            return Plan(muxed=stream)

    raise NoMatch(_explain(videos, audio, req))


def _explain(videos: list[Stream], audio: Stream | None, req: Request) -> str:
    codecs = CODEC_ORDERS[req.codec]
    candidates = [v for v in videos if v.codec in codecs]
    if not candidates:
        offered = sorted({v.codec for v in videos if v.codec})
        return f"no {'/'.join(codecs)} video here (offered: {', '.join(offered) or 'none'}); try --codec efficient"
    if req.max_size is not None and audio is not None:
        sized = [Plan(video=v, audio=audio) for v in candidates if v.size is not None]
        smallest = min(sized, key=lambda p: p.size, default=None)
        if smallest is not None:
            return (
                f"nothing fits in {format_size(req.max_size)}; the smallest option is "
                f"{smallest.describe()} at {format_size(smallest.size)}"
            )
    return "no stream matches the requested quality"


@dataclass(frozen=True)
class Option:
    """One row of the quality menu: the best stream of a height and codec, with audio added."""

    plan: Plan
    quicktime: str


def menu(info: dict, audio_quality: str = "medium") -> list[Option]:
    """Every height and codec on offer with its full download size, best picture first."""
    all_streams = streams(info)
    videos = [s for s in all_streams if s.kind == "video" and s.height]
    audio = pick_audio([s for s in all_streams if s.kind == "audio"], audio_quality, "m4a")
    rows = []
    for height in sorted({v.height for v in videos}, reverse=True):
        for codec in CODEC_ORDERS["efficient"]:
            video = best_video(videos, height, codec)
            if video:
                plan = Plan(video=video, audio=audio) if audio else Plan(video=video)
                rows.append(Option(plan, QUICKTIME.get(codec, "no")))
    if not rows:
        # Sites other than YouTube often serve picture and sound in one file.
        muxed = [s for s in all_streams if s.kind == "muxed" and s.height]
        for stream in sorted(muxed, key=lambda m: (m.height, m.bitrate, m.size or 0), reverse=True):
            rows.append(Option(Plan(muxed=stream), QUICKTIME.get(stream.codec, "unknown")))
    return rows
