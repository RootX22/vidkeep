"""Parallel byte-range downloads for single-file HTTP formats.

YouTube serves each video and audio stream as one file that answers HTTP range
requests. yt-dlp fetches such a file one bounded range at a time over a single
connection. This downloader fetches several bounded ranges at once and writes
each at its own offset in a preallocated file. A small state file records the
finished ranges, so an interrupted download resumes instead of starting over.

Ranges are always bounded (``bytes=start-end``): open-ended ranges are what
get throttled to playback speed, which is why a generic multi-connection
downloader can end up slower than a single yt-dlp connection.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from yt_dlp.downloader.common import FileDownloader
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError, RequestError, TransportError
from yt_dlp.utils.networking import HTTPHeaderDict

try:
    import fcntl
except ImportError:  # Windows: no advisory locks, concurrent runs are on the user
    fcntl = None

DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024
READ_BLOCK = 256 * 1024
MAX_RETRIES = 10
STATE_SUFFIX = ".vidkeep"
LOCK_SUFFIX = ".vidkeep-lock"

# Set on Ctrl+C so every range in every download stops at its next block.
ABORT = threading.Event()

_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)")


class RangeUnsupported(Exception):
    """The server answered a range request with something other than that range."""


class _Stopped(Exception):
    """Another range failed or the user interrupted; stop quietly."""


def plan_chunks(total: int, chunk_size: int) -> list[tuple[int, int]]:
    """Split ``[0, total)`` into contiguous half-open ranges of at most ``chunk_size`` bytes."""
    if total <= 0 or chunk_size <= 0:
        raise ValueError("total and chunk_size must be positive")
    return [(start, min(start + chunk_size, total)) for start in range(0, total, chunk_size)]


def supports(info: dict, params: dict) -> bool:
    """Whether a format can be fetched as parallel byte ranges."""
    size = info.get("filesize")
    chunk_size = params.get("vidkeep_chunk_size") or DEFAULT_CHUNK_SIZE
    return (
        (params.get("vidkeep_connections") or 1) > 1
        and info.get("protocol") in ("http", "https")
        and isinstance(size, int)
        and size >= 2 * chunk_size
        and not info.get("is_live")
        and not info.get("fragments")
        and not params.get("ratelimit")
        and not params.get("test")
    )


class _Counter:
    """Bytes written by all workers, safe to bump from any thread."""

    def __init__(self, start: int = 0):
        self._value = start
        self._lock = threading.Lock()

    def add(self, n: int) -> None:
        with self._lock:
            self._value += n

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


@dataclass
class RangeSink:
    """Where fetched ranges land: a preallocated file, a shared byte count and a stop signal."""

    path: str
    counter: _Counter = field(default_factory=_Counter)
    stop: threading.Event = field(default_factory=threading.Event)

    @property
    def stopping(self) -> bool:
        return self.stop.is_set() or ABORT.is_set()


@dataclass
class _Transfer:
    """One file being fetched: its ranges, which are done, and the first failure if any."""

    filename: str
    sink: RangeSink
    total: int
    chunk_size: int
    done: set[int]
    started: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)
    errors: list[Exception] = field(default_factory=list)

    def __post_init__(self):
        self.chunks = plan_chunks(self.total, self.chunk_size)
        self.resumed = sum(end - start for i, (start, end) in enumerate(self.chunks) if i in self.done)
        self.sink.counter.add(self.resumed)

    @property
    def state_path(self) -> str:
        return self.sink.path + STATE_SUFFIX

    @property
    def todo(self) -> list[int]:
        return [i for i in range(len(self.chunks)) if i not in self.done]

    def save_state(self) -> None:
        """Record finished ranges atomically, so a crash never leaves a half-written state file."""
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"total": self.total, "chunk_size": self.chunk_size, "done": sorted(self.done)}, fh)
        os.replace(tmp, self.state_path)


class ParallelRangeFD(FileDownloader):
    """A yt-dlp downloader that fetches one file over several connections at once."""

    FD_NAME = "vidkeep"

    def real_download(self, filename, info_dict):
        tmpfilename = self.temp_name(filename)
        lock_path = tmpfilename + LOCK_SUFFIX
        lock = _acquire_lock(lock_path)
        if lock is None:
            self.report_error(f"another download is already writing {tmpfilename}")
            return False
        try:
            ok = self._download_ranges(filename, tmpfilename, info_dict)
        finally:
            lock.close()  # releases the lock; the OS does the same if the process dies
        if ok:
            _remove_quietly(lock_path)
        return ok

    def _download_ranges(self, filename, tmpfilename, info_dict):
        transfer = self._open_transfer(filename, tmpfilename, info_dict["filesize"])
        self.report_destination(filename)
        connections = self.params.get("vidkeep_connections") or 4
        self.to_screen(
            f"[vidkeep] {len(transfer.todo)} ranges of {transfer.chunk_size // 1024 // 1024} MiB "
            f"over {connections} connections"
        )
        try:
            self._fetch_all(transfer, self.request_template(info_dict), connections, info_dict)
        except (RequestError, RangeUnsupported, OSError) as err:
            # yt-dlp's downloader contract: report, return False, keep the partial file for resuming.
            self.report_error(f"parallel download failed: {err}")
            return False
        return self._finish(transfer, info_dict)

    def _open_transfer(self, filename, tmpfilename, total) -> _Transfer:
        """Resume an earlier partial file, or preallocate a new one."""
        chunk_size = self.params.get("vidkeep_chunk_size") or DEFAULT_CHUNK_SIZE
        done = self._resume_state(tmpfilename, total, chunk_size)
        if not done:
            with open(tmpfilename, "wb") as fh:
                fh.truncate(total)
        transfer = _Transfer(filename, RangeSink(tmpfilename), total, chunk_size, done)
        transfer.save_state()
        return transfer

    def _fetch_all(self, transfer: _Transfer, template: dict, connections: int, info_dict: dict) -> None:
        """Run every missing range; raise the first real failure, stopping the rest."""
        with ThreadPoolExecutor(max_workers=connections) as pool:
            pending = {pool.submit(self._fetch_chunk, transfer, template, i) for i in transfer.todo}
            try:
                while pending:
                    finished, pending = wait(pending, timeout=0.5, return_when=FIRST_EXCEPTION)
                    error = next((e for e in (f.exception() for f in finished) if e is not None), None)
                    if error is not None:
                        raise transfer.errors[0] if transfer.errors else error
                    self._report(transfer, info_dict)
            except BaseException:
                transfer.sink.stop.set()
                for future in pending:
                    future.cancel()
                raise

    def _fetch_chunk(self, transfer: _Transfer, template: dict, index: int) -> None:
        start, end = transfer.chunks[index]
        try:
            self.fetch_range(template, transfer.sink, start, end)
        except _Stopped:
            raise
        except Exception as err:
            # Record the cause before signalling, so whoever sees a stopped
            # range can still report why everything stopped.
            with transfer.lock:
                transfer.errors.append(err)
            transfer.sink.stop.set()  # queued ranges stop before sending requests bound to fail too
            raise
        with transfer.lock:
            transfer.done.add(index)
            transfer.save_state()

    def _finish(self, transfer: _Transfer, info_dict) -> bool:
        if os.path.getsize(transfer.sink.path) != transfer.total:
            self.report_error(f"size mismatch after download: expected {transfer.total} bytes")
            return False
        os.remove(transfer.state_path)
        self.try_rename(transfer.sink.path, transfer.filename)
        self._hook_progress(
            {
                "status": "finished",
                "filename": transfer.filename,
                "downloaded_bytes": transfer.total,
                "total_bytes": transfer.total,
                "elapsed": time.time() - transfer.started,
            },
            info_dict,
        )
        return True

    def _report(self, transfer: _Transfer, info_dict) -> None:
        now = time.time()
        downloaded = transfer.sink.counter.value
        fresh = downloaded - transfer.resumed
        self._hook_progress(
            {
                "status": "downloading",
                "filename": transfer.filename,
                "tmpfilename": transfer.sink.path,
                "downloaded_bytes": downloaded,
                "total_bytes": transfer.total,
                "speed": self.calc_speed(transfer.started, now, fresh),
                "eta": self.calc_eta(transfer.started, now, transfer.total - transfer.resumed, fresh),
                "elapsed": now - transfer.started,
            },
            info_dict,
        )

    def request_template(self, info_dict) -> dict:
        """What every range request for this format carries, mirroring yt-dlp's own HTTP downloader."""
        extensions = {}
        impersonate = self._get_impersonate_target(info_dict)
        if impersonate is not None:
            extensions["impersonate"] = impersonate
        return {
            "url": info_dict["url"],
            "data": info_dict.get("request_data"),
            "headers": HTTPHeaderDict({"Accept-Encoding": "identity"}, info_dict.get("http_headers")),
            "extensions": extensions,
        }

    def fetch_range(self, template: dict, sink: RangeSink, start: int, end: int) -> None:
        """Download ``[start, end)`` into place, retrying from the last byte written."""
        pos = start
        attempt = 0
        while pos < end:
            if sink.stopping:
                raise _Stopped
            request = Request(
                template["url"],
                template["data"],
                HTTPHeaderDict(template["headers"], {"Range": f"bytes={pos}-{end - 1}"}),
                extensions=dict(template["extensions"]),
            )
            try:
                with contextlib.closing(self.ydl.urlopen(request)) as response, open(sink.path, "r+b") as fh:
                    _check_range(response, pos)
                    fh.seek(pos)
                    # pos advances block by block, so a retry continues from the last byte on disk.
                    while pos < end:
                        if sink.stopping:
                            raise _Stopped
                        block = response.read(min(READ_BLOCK, end - pos))
                        if not block:
                            raise TransportError("connection closed before the range was complete")
                        fh.write(block)
                        pos += len(block)
                        sink.counter.add(len(block))
            except HTTPError as err:
                # 403/404/410 mean the signed URL expired or was revoked: retrying cannot help.
                if err.status < 500 and err.status != 429:
                    raise
                attempt = self._backoff(err, attempt, sink)
            except TransportError as err:
                attempt = self._backoff(err, attempt, sink)

    def _backoff(self, err, attempt: int, sink: RangeSink) -> int:
        attempt += 1
        if attempt > MAX_RETRIES:
            raise err
        self.report_retry(err, attempt, MAX_RETRIES, fatal=False)
        if sink.stop.wait(min(2**attempt * 0.25, 8)) or ABORT.is_set():
            raise _Stopped
        return attempt

    def _resume_state(self, tmpfilename, total, chunk_size) -> set[int]:
        """Return the finished range indexes from an earlier run, or an empty set."""
        if not self.params.get("continuedl", True):
            return set()
        try:
            with open(tmpfilename + STATE_SUFFIX) as fh:
                state = json.load(fh)
            if (
                state.get("total") == total
                and state.get("chunk_size") == chunk_size
                and os.path.getsize(tmpfilename) == total
            ):
                done = {int(i) for i in state.get("done", [])}
                if done:
                    self.to_screen(f"[vidkeep] Resuming: {len(done)} ranges already on disk")
                return done
        except (OSError, ValueError, TypeError):
            pass  # missing or unreadable state: the safe answer is to start this file over
        return set()


def _acquire_lock(path: str):
    """An open file holding an exclusive lock on ``path``, or None if another process holds it."""
    fh = open(path, "a")  # noqa: SIM115 - the open handle is the lock; the caller closes it
    if fcntl is None:
        return fh
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _remove_quietly(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


def _check_range(response, start: int) -> None:
    """Refuse a reply that is not the range asked for, so no wrong bytes are ever written."""
    if response.status != 206:
        raise RangeUnsupported(f"server ignored the range request (HTTP {response.status})")
    match = _CONTENT_RANGE.match(response.headers.get("Content-Range", ""))
    if not match or int(match.group(1)) != start:
        raise RangeUnsupported("server returned a different range than requested")
