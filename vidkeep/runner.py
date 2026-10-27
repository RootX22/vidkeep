"""Run downloads: resolve URLs, plan each video, fetch several at once with live progress."""

from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from . import accel, options, planner, preflight
from .engine import VidkeepYDL

TITLE_WIDTH = 42


@dataclass
class Item:
    url: str
    info: dict | None = None
    plan: planner.Plan | None = None  # fixed in advance, e.g. picked from the interactive menu
    template: str | None = None  # playlists write into their own folder


@dataclass
class Result:
    item: Item
    title: str = ""
    plan: planner.Plan | None = None
    status: str = "pending"  # done, planned, skipped or failed
    path: str | None = None
    error: str | None = None
    seconds: float = 0.0


class _Logger:
    """Routes yt-dlp messages: errors and warnings are kept, chatter is dropped."""

    def __init__(self, console: Console, verbose: bool):
        self.console = console
        self.verbose = verbose
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        if self.verbose:
            self.console.print(msg, style="dim", markup=False, highlight=False)

    def info(self, msg: str) -> None:
        self.debug(msg)

    def warning(self, msg: str) -> None:
        if self.verbose or "Falling back" in msg or "Retrying" in msg:
            self.console.print(f"warning: {msg}", style="yellow", markup=False, highlight=False)

    def error(self, msg: str) -> None:
        self.errors.append(re.sub(r"^ERROR:\s*", "", msg))


class _SpaceLedger:
    """Disk space promised to downloads still running, so parallel jobs cannot oversubscribe a disk."""

    def __init__(self):
        self._lock = threading.Lock()
        self._reserved = 0

    def reserve(self, out_dir: str, needed: int) -> tuple[bool, int]:
        with self._lock:
            free = preflight.free_space(out_dir) - self._reserved
            if needed > free:
                return False, free
            self._reserved += needed
            return True, free

    def release(self, needed: int) -> None:
        with self._lock:
            self._reserved -= needed


def _short(title: str) -> str:
    return title if len(title) <= TITLE_WIDTH else title[: TITLE_WIDTH - 1] + "…"


def _folder_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", title).strip() or "playlist"


@dataclass(frozen=True)
class RunOptions:
    jobs: int = 2  # videos downloaded at once
    dry_run: bool = False
    force: bool = False  # skip the free-space check
    verbose: bool = False


class _Tracker:
    """Feeds one video's yt-dlp progress and post-processing events into its progress bar."""

    def __init__(self, result: Result, progress: Progress, task: TaskID):
        self.result = result
        self.progress = progress
        self.task = task
        self.wanted = {s.format_id for s in result.plan.parts}
        self.received: dict[str, int] = {}

    def on_progress(self, event: dict) -> None:
        fmt = (event.get("info_dict") or {}).get("format_id")
        if fmt not in self.wanted:
            return  # subtitles and thumbnails report here too
        self.received[fmt] = event.get("downloaded_bytes") or 0
        self.progress.update(self.task, completed=sum(self.received.values()))

    def on_postprocess(self, event: dict) -> None:
        name = event.get("postprocessor")
        if event.get("status") == "started" and name != "MoveFiles":
            self.progress.update(self.task, description=f"[dim]{name}[/] {_short(self.result.title)}")
        if event.get("status") == "finished" and name == "MoveFiles":
            self.result.path = (event.get("info_dict") or {}).get("filepath")


class Runner:
    def __init__(
        self,
        request: planner.Request,
        settings: options.Settings,
        run_options: RunOptions | None = None,
        console: Console | None = None,
    ):
        self.request = request
        self.settings = settings
        self.opts = run_options or RunOptions()
        self.console = console or Console(stderr=True)
        self.ledger = _SpaceLedger()

    def _params(self, args: list[str], logger: _Logger, **extra) -> dict:
        return options.to_params(args, self.settings, logger=logger, **extra)

    def _logger(self) -> _Logger:
        return _Logger(self.console, self.opts.verbose)

    def resolve(self, urls: list[str]) -> list[Item]:
        """Expand playlists into their videos; keep single-video extractions for reuse."""
        logger = self._logger()
        params = self._params(options.base_args(self.settings), logger, extract_flat="in_playlist")
        items = []
        with VidkeepYDL(params) as ydl:
            for url in urls:
                info = ydl.extract_info(url, download=False)
                if info is None:
                    raise RuntimeError(logger.errors[-1] if logger.errors else f"could not read {url}")
                if info.get("_type") != "playlist":
                    items.append(Item(url, info=info))
                    continue
                folder = _folder_name(info.get("title") or info.get("id") or "playlist")
                for index, entry in enumerate(info.get("entries") or [], 1):
                    if entry and (entry.get("url") or entry.get("webpage_url")):
                        template = f"{folder}/{index:03d} - {options.DEFAULT_TEMPLATE}"
                        items.append(Item(entry.get("webpage_url") or entry["url"], template=template))
        return items

    def run(self, items: list[Item]) -> list[Result]:
        progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(bar_width=None),
            DownloadColumn(binary_units=True),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=self.console,
            transient=False,
        )
        results = [Result(item) for item in items]
        pool = ThreadPoolExecutor(max_workers=max(1, self.opts.jobs))
        try:
            with progress:
                futures = [pool.submit(self._job, result, progress) for result in results]
                for future in as_completed(futures):
                    future.result()
        except KeyboardInterrupt:
            # Leaving a `with ThreadPoolExecutor` block would wait for every
            # running download to finish; stop them and return at once instead.
            accel.ABORT.set()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return results

    def _job(self, result: Result, progress: Progress) -> None:
        item = result.item
        logger = self._logger()
        started = time.time()
        task = progress.add_task(_short(item.url), total=None, visible=not self.opts.dry_run)
        try:
            info = item.info or self.extract(item.url, logger)
            result.title = info.get("title") or info.get("id") or item.url
            progress.update(task, description=_short(result.title))
            result.plan = item.plan or planner.choose(info, self.request)
            if self.opts.dry_run:
                result.status = "planned"
                return
            self._download(result, info, _Tracker(result, progress, task), logger)
        except planner.NoMatch as err:
            result.status, result.error = "failed", str(err)
        except Exception as err:  # the boundary per video: record why it failed, keep the others going
            result.status = "failed"
            result.error = logger.errors[-1] if logger.errors else str(err)
        finally:
            result.seconds = time.time() - started
            mark = {"done": "[green]done", "skipped": "[cyan]skipped", "failed": "[red]failed"}.get(result.status)
            if mark:
                progress.update(task, description=f"{mark}[/] {_short(result.title or item.url)}")

    def extract(self, url: str, logger: _Logger | None = None) -> dict:
        logger = logger or self._logger()
        with VidkeepYDL(self._params(options.base_args(self.settings), logger)) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            raise RuntimeError(logger.errors[-1] if logger.errors else "extraction failed")
        return info

    def _download(self, result: Result, info: dict, tracker: _Tracker, logger: _Logger) -> None:
        tracker.progress.update(tracker.task, total=result.plan.size)
        args = options.download_args(result.plan, self.settings, result.item.template)
        params = self._params(
            args, logger, progress_hooks=[tracker.on_progress], postprocessor_hooks=[tracker.on_postprocess]
        )
        with VidkeepYDL(params) as ydl:
            if self.settings.archive and ydl.in_download_archive(info):
                result.status = "skipped"
                return
            reserved = self._reserve_space(result.plan)
            try:
                # The info was processed once already, by the instance that extracted it.
                # Its leftover private keys (requested_formats and the like) make a second
                # instance request the wrong thing and get HTTP 403, so hand over the clean
                # copy yt-dlp itself writes for --load-info-json.
                clean = VidkeepYDL.sanitize_info(info, remove_private_keys=True)
                processed = ydl.process_ie_result(clean, download=True) or {}
            finally:
                self.ledger.release(reserved)
        if logger.errors:
            raise RuntimeError(logger.errors[-1])
        result.status = "done"
        result.path = result.path or (processed.get("requested_downloads") or [{}])[0].get("filepath")
        if result.path and os.path.exists(result.path):
            size = os.path.getsize(result.path)
            tracker.progress.update(tracker.task, completed=size, total=size)

    def _reserve_space(self, plan: planner.Plan) -> int:
        """Claim room for this download, or refuse it before a byte is fetched."""
        needed = 0 if self.opts.force else preflight.space_needed(plan) or 0
        ok, free = self.ledger.reserve(self.settings.out_dir, needed)
        if not ok:
            raise RuntimeError(
                f"not enough disk space: needs {planner.format_size(needed)} "
                f"(download plus merge), {planner.format_size(free)} free; "
                "lower --quality, set --max-size, or pass --force"
            )
        return needed
