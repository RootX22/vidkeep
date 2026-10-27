"""Measure real throughput for different connection counts on the current network."""

from __future__ import annotations

import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import accel
from .engine import VidkeepYDL


@dataclass
class BenchResult:
    connections: int
    nbytes: int
    seconds: float

    @property
    def mib_per_s(self) -> float:
        return self.nbytes / self.seconds / (1024 * 1024)


def pick_bench_format(info: dict) -> dict:
    """The largest single-file HTTP stream with an exact size: the one a real download spends most time on."""
    candidates = [
        f
        for f in info.get("formats") or []
        if f.get("protocol") in ("http", "https") and isinstance(f.get("filesize"), int)
    ]
    if not candidates:
        raise ValueError("no single-file HTTP format with a known size to measure")
    return max(candidates, key=lambda f: f["filesize"])


def run(
    url: str,
    connection_counts: list[int],
    sample_mib: int,
    ydl_params: dict | None = None,
) -> tuple[dict, list[BenchResult]]:
    """Download ``sample_mib`` from a different offset for each connection count and time it.

    Every run uses the same bounded chunk size as a real download, so one
    connection measures exactly what plain yt-dlp does. Each run reads fresh
    bytes, so no run is helped by a CDN cache warmed by the one before.
    """
    params = {"quiet": True, "no_warnings": True, "noprogress": True, **(ydl_params or {})}
    sample = sample_mib * 1024 * 1024
    results = []
    with VidkeepYDL(params) as ydl:
        info = ydl.extract_info(url, download=False)
        fmt = pick_bench_format(info)
        if fmt["filesize"] < sample * len(connection_counts):
            raise ValueError("video too short for this sample size; lower --sample")
        if fmt.get("http_headers") is None:
            fmt["http_headers"] = ydl._calc_headers(fmt)
        fd = accel.ParallelRangeFD(ydl, ydl.params)
        template = fd.request_template(fmt)
        # YouTube refuses stream URLs used before this moment (HTTP 403).
        wait_s = (fmt.get("available_at") or 0) - time.time()
        if wait_s > 0:
            time.sleep(wait_s + 0.5)
        for run_index, connections in enumerate(connection_counts):
            offset = run_index * sample
            chunks = [(offset + s, offset + e) for s, e in accel.plan_chunks(sample, accel.DEFAULT_CHUNK_SIZE)]
            results.append(_timed_fetch(fd, template, chunks, connections))
    return fmt, results


def _timed_fetch(fd, template: dict, chunks: list[tuple[int, int]], connections: int) -> BenchResult:
    with tempfile.TemporaryDirectory() as tmp:
        sink = accel.RangeSink(os.path.join(tmp, "sample.bin"))
        with open(sink.path, "wb") as fh:
            fh.truncate(chunks[-1][1])  # ranges sit at their real offsets in the stream
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=connections) as pool:
            list(pool.map(lambda c: fd.fetch_range(template, sink, *c), chunks))
        elapsed = time.perf_counter() - started
    return BenchResult(connections, sink.counter.value, elapsed)
