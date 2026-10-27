"""Command line: vidkeep get | info | doctor | bench."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys

from rich.console import Console
from rich.prompt import IntPrompt
from rich.table import Table
from rich.text import Text
from yt_dlp.utils import DownloadError

from . import __version__, accel, bench, options, planner, preflight
from .report import JsonReporter
from .runner import Runner, RunOptions

QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360", "240", "144")


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _size(text: str) -> int:
    try:
        return planner.parse_size(text)
    except ValueError as err:
        raise argparse.ArgumentTypeError(str(err)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vidkeep",
        description="Fast, size-aware video downloads for offline viewing, built on yt-dlp.",
    )
    parser.add_argument("--version", action="version", version=f"vidkeep {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_get(sub)
    _add_info(sub)
    _add_doctor(sub)
    _add_bench(sub)
    return parser


def _add_get(sub) -> None:
    get = sub.add_parser("get", help="download videos, playlists or channels")
    get.add_argument("urls", nargs="*", metavar="URL")
    get.add_argument("-b", "--batch", metavar="FILE", help="read URLs from a file, one per line")
    _add_choice_flags(get)
    _add_output_flags(get)
    _add_speed_flags(get)
    run = get.add_argument_group("run")
    run.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="use your own browser session, e.g. firefox (for age-restricted videos)",
    )
    run.add_argument("--dry-run", action="store_true", help="show what would be downloaded, then stop")
    run.add_argument("--force", action="store_true", help="skip the free-space check")
    run.add_argument("-v", "--verbose", action="store_true")
    run.add_argument("--json", action="store_true", help="print progress as JSON lines on stdout, for other programs")


def _add_choice_flags(get) -> None:
    pick = get.add_argument_group("what to download")
    pick.add_argument("-q", "--quality", choices=QUALITIES, default="1080", help="highest resolution (default 1080)")
    pick.add_argument(
        "-c",
        "--codec",
        choices=tuple(planner.CODEC_ORDERS),
        default="efficient",
        help="efficient: smallest files (AV1/VP9 first); compatible: H.264, plays on every device (default efficient)",
    )
    pick.add_argument("-s", "--max-size", type=_size, metavar="SIZE", help="pick the best quality that fits, e.g. 1.5G")
    pick.add_argument("-a", "--audio-only", action="store_true", help="audio only")
    pick.add_argument("--audio-format", choices=options.AUDIO_FORMATS, default="m4a")
    pick.add_argument("--audio-quality", choices=planner.AUDIO_QUALITIES, default="medium")
    pick.add_argument("-i", "--interactive", action="store_true", help="choose the quality from a menu")
    pick.add_argument(
        "-f", "--format", metavar="SPEC", help="exact stream ids, e.g. 399+140 (listed by `vidkeep info --json`)"
    )


def _add_output_flags(get) -> None:
    extras = get.add_argument_group("extras")
    extras.add_argument("--subs", metavar="LANGS", help="subtitles to embed and save as .srt, e.g. en,ar")
    extras.add_argument("--split-chapters", action="store_true", help="also write one file per chapter")
    extras.add_argument("--no-metadata", action="store_true", help="do not embed title, chapters and description")
    extras.add_argument("--no-thumbnail", action="store_true", help="do not embed the cover image")
    out = get.add_argument_group("output")
    out.add_argument("-o", "--out", default=".", metavar="DIR", help="where to save (default: current folder)")
    out.add_argument("--template", default=options.DEFAULT_TEMPLATE, help="yt-dlp output template")
    out.add_argument("--container", choices=options.CONTAINERS, default="mp4")
    out.add_argument("--archive", metavar="FILE", help="record finished videos and skip them next time")


def _add_speed_flags(get) -> None:
    speed = get.add_argument_group("speed")
    speed.add_argument(
        "-n",
        "--connections",
        type=_positive_int,
        default=4,
        help="parallel connections per file (default 4; measure yours with `vidkeep bench`)",
    )
    speed.add_argument(
        "--chunk-size",
        type=_size,
        default=accel.DEFAULT_CHUNK_SIZE,
        metavar="SIZE",
        help="bytes per range request (default 10M)",
    )
    speed.add_argument("-j", "--jobs", type=_positive_int, default=2, help="videos downloaded at once (default 2)")
    speed.add_argument("--limit-rate", metavar="RATE", help="cap the speed, e.g. 2M (turns off parallel ranges)")


def _add_info(sub) -> None:
    info = sub.add_parser("info", help="show a video's qualities and their real download sizes")
    info.add_argument("url", metavar="URL")
    info.add_argument("--json", action="store_true", help="machine-readable output")
    info.add_argument("--cookies-from-browser", metavar="BROWSER")


def _add_doctor(sub) -> None:
    doctor = sub.add_parser("doctor", help="check ffmpeg, the JavaScript runtime, yt-dlp and disk space")
    doctor.add_argument("-o", "--out", default=".", metavar="DIR")


def _add_bench(sub) -> None:
    bench_cmd = sub.add_parser("bench", help="measure which connection count is fastest on your network")
    bench_cmd.add_argument("url", metavar="URL", help="a long video (the sample is read from it)")
    bench_cmd.add_argument("--connections", default="1,2,4,6,8", help="counts to compare (default 1,2,4,6,8)")
    bench_cmd.add_argument("--sample", type=_positive_int, default=40, metavar="MIB", help="MiB per run (default 40)")


def _settings(args, js_args: list[str]) -> options.Settings:
    return options.Settings(
        out_dir=args.out,
        template=args.template,
        container=args.container,
        audio_format=args.audio_format,
        subs=args.subs,
        split_chapters=args.split_chapters,
        metadata=not args.no_metadata,
        thumbnail=not args.no_thumbnail,
        archive=args.archive,
        cookies_from_browser=args.cookies_from_browser,
        limit_rate=args.limit_rate,
        js_runtime_args=tuple(js_args),
        connections=args.connections,
        chunk_size=args.chunk_size,
    )


def _request(args) -> planner.Request:
    return planner.Request(
        max_height=None if args.quality == "best" else int(args.quality),
        codec=args.codec,
        audio_quality=args.audio_quality,
        max_size=args.max_size,
        audio_only=args.audio_only,
        formats=args.format,
    )


def _menu_table(rows: list[planner.Option], numbered: bool = False) -> Table:
    table = Table(header_style="bold")
    if numbered:
        table.add_column("#", justify="right")
    for name, justify in (("quality", "left"), ("codec", "left"), ("size", "right"), ("QuickTime", "left")):
        table.add_column(name, justify=justify)
    for n, row in enumerate(rows, 1):
        pic = row.plan.picture
        size = planner.format_size(row.plan.size) + ("" if row.plan.exact else " ~")
        cells = [f"{pic.height}p", pic.codec, size, row.quicktime]
        table.add_row(*([str(n)] if numbered else []), *cells)
    return table


def _summary(results, console: Console) -> None:
    table = Table(header_style="bold", title="vidkeep")
    for name in ("video", "quality", "size", "time", "result"):
        table.add_column(name, overflow="fold")
    for r in results:
        size = (
            planner.format_size(os.path.getsize(r.path))
            if r.path and os.path.exists(r.path)
            else (planner.format_size(r.plan.size) if r.plan else "")
        )
        outcome = {
            "done": f"[green]saved[/] {os.path.basename(r.path or '')}",
            "planned": f"[cyan]would download[/] {r.plan.format_spec if r.plan else ''}",
            "skipped": "[cyan]already in archive[/]",
            "failed": f"[red]failed[/] {r.error or ''}",
        }.get(r.status, r.status)
        table.add_row(r.title or r.item.url, r.plan.describe() if r.plan else "", size, f"{r.seconds:.0f}s", outcome)
    console.print(table)
    folders = sorted({os.path.dirname(os.path.abspath(r.path)) for r in results if r.status == "done" and r.path})
    for folder in folders:
        console.print(f"saved in [bold]{folder}[/]")


def cmd_get(args, console: Console) -> int:
    urls = list(args.urls)
    if args.batch:
        with open(args.batch, encoding="utf-8") as fh:
            urls += [line.strip() for line in fh if line.strip() and not line.lstrip().startswith("#")]
    if not urls:
        console.print("[red]no URL given[/]")
        return 2
    runtime = preflight.js_runtime()
    if runtime is None:
        console.print("[yellow]no JavaScript runtime found; YouTube downloads will fail. Install deno.[/]")
    run_options = RunOptions(jobs=args.jobs, dry_run=args.dry_run, force=args.force, verbose=args.verbose)
    runner = Runner(_request(args), _settings(args, preflight.js_runtime_args(runtime)), run_options, console)
    items = runner.resolve(urls)
    if args.interactive:
        if len(items) != 1 or items[0].info is None or args.json:
            console.print("[red]--interactive works with a single video and without --json[/]")
            return 2
        items[0].plan = _pick_plan(items[0].info, args.audio_quality, console)
        if items[0].plan is None:
            return 1
    results = runner.run(items, JsonReporter() if args.json else None)
    if not args.json:
        _summary(results, console)
    return 0 if all(r.status in ("done", "planned", "skipped") for r in results) else 1


def _pick_plan(info: dict, audio_quality: str, console: Console) -> planner.Plan | None:
    """Show the quality menu and return the row the user picks."""
    rows = planner.menu(info, audio_quality)
    if not rows:
        console.print("[red]no separate video streams to choose from[/]")
        return None
    console.print(f"[bold]{info.get('title')}[/]")
    console.print(_menu_table(rows, numbered=True))
    choice = IntPrompt.ask("download which", console=console, choices=[str(n) for n in range(1, len(rows) + 1)])
    return rows[choice - 1].plan


def _default_format(info: dict, request: planner.Request) -> str | None:
    try:
        return planner.choose(info, request).format_spec
    except planner.NoMatch:
        return None


def _info_payload(info: dict, rows: list[planner.Option], playlist_count: int) -> dict:
    audio = _default_format(info, planner.Request(audio_only=True))
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "duration_string": info.get("duration_string"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url"),
        "chapters": len(info.get("chapters") or []),
        "playlist_count": playlist_count,
        "default_format": _default_format(info, planner.Request()),
        "audio_only": {"format": audio, "size": planner.plan_for(info, audio).size} if audio else None,
        "options": [
            {
                "height": r.plan.picture.height,
                "codec": r.plan.picture.codec or r.plan.picture.ext,
                "format": r.plan.format_spec,
                "size": r.plan.size,
                "exact": r.plan.exact,
                "quicktime": r.quicktime,
            }
            for r in rows
        ],
    }


def cmd_info(args, console: Console) -> int:
    runtime_args = preflight.js_runtime_args(preflight.js_runtime())
    settings = options.Settings(cookies_from_browser=args.cookies_from_browser, js_runtime_args=tuple(runtime_args))
    runner = Runner(planner.Request(), settings, console=console)
    items = runner.resolve([args.url])
    if not items:
        console.print("[red]nothing to show: the playlist is empty[/]")
        return 1
    if len(items) > 1 and not args.json:
        console.print(f"playlist of {len(items)} videos; showing the first")
    info = items[0].info or runner.extract(items[0].url)
    rows = planner.menu(info)
    if args.json:
        print(json.dumps(_info_payload(info, rows, len(items)), ensure_ascii=False, indent=2))
        return 0
    duration = info.get("duration_string") or info.get("duration")
    console.print(f"[bold]{info.get('title')}[/]")
    console.print(
        f"{info.get('channel') or info.get('uploader') or ''} · {duration} · "
        f"{len(info.get('chapters') or [])} chapters · subtitles: "
        f"{', '.join(sorted(info.get('subtitles') or {})) or 'auto only'}"
    )
    console.print(_menu_table(rows))
    console.print("sizes include medium-quality audio; ~ marks an estimate")
    return 0


def cmd_doctor(args, console: Console) -> int:
    checks = preflight.doctor(args.out)
    table = Table(header_style="bold")
    for name in ("check", "status", "detail"):
        table.add_column(name)
    for c in checks:
        status = "[green]ok[/]" if c.ok else ("[red]missing[/]" if c.fatal else "[yellow]warn[/]")
        table.add_row(c.name, status, c.detail)
    console.print(table)
    return 1 if any(not c.ok and c.fatal for c in checks) else 0


def cmd_bench(args, console: Console) -> int:
    counts = [_positive_int(n) for n in args.connections.split(",")]
    runtime_args = preflight.js_runtime_args(preflight.js_runtime())
    params = options.to_params(runtime_args, options.Settings()) if runtime_args else None
    console.print(f"measuring {args.sample} MiB per run with {', '.join(map(str, counts))} connections…")
    fmt, results = bench.run(args.url, counts, args.sample, ydl_params=params)
    table = Table(header_style="bold", title=f"format {fmt['format_id']}")
    for name in ("connections", "MiB/s", "Mbit/s", "seconds"):
        table.add_column(name, justify="right")
    best = max(results, key=lambda r: r.mib_per_s)
    for r in results:
        style = "bold green" if r is best else None
        table.add_row(
            str(r.connections), f"{r.mib_per_s:.2f}", f"{r.mib_per_s * 8.389:.1f}", f"{r.seconds:.1f}", style=style
        )
    console.print(table)
    console.print(f"fastest here: [bold]-n {best.connections}[/]")
    return 0


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def _print_error(console: Console, message: str) -> None:
    console.print(Text.assemble(("error: ", "bold red"), message))
    if "confirm you" in message or "cookies" in message:
        console.print(
            "YouTube wants a signed-in browser for this. Add --cookies-from-browser firefox "
            "(or chrome, safari, edge, brave), using a browser where you are signed in to YouTube."
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console(stderr=True)
    # A GUI stops a download with SIGTERM, and a child may inherit SIGINT as ignored;
    # both must take the same path as Ctrl+C so partial files stay resumable.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, _interrupt)
    handlers = {"get": cmd_get, "info": cmd_info, "doctor": cmd_doctor, "bench": cmd_bench}
    try:
        return handlers[args.command](args, console)
    except (RuntimeError, DownloadError) as err:
        _print_error(console, re.sub(r"^ERROR:\s*", "", str(err)))
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]stopped; run the same command again to resume[/]")
        # Worker threads may sit in network reads; partial files and range state
        # are already on disk, so exiting at once loses nothing.
        os._exit(130)


if __name__ == "__main__":
    sys.exit(main())
