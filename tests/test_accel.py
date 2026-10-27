import json
import os

import pytest

from vidkeep import accel
from vidkeep.engine import VidkeepYDL

CHUNK = 64 * 1024


def make_ydl(**params):
    base = {
        "quiet": True,
        "noprogress": True,
        "ignoreerrors": True,  # failures come back as a False result instead of an exception
        "vidkeep_connections": 4,
        "vidkeep_chunk_size": CHUNK,
    }
    return VidkeepYDL({**base, **params})


def download(server, tmp_path, **params):
    target = str(tmp_path / "out.mp4")
    with make_ydl(**params) as ydl:
        ok, _ = ydl.dl(target, server.info())
    return ok, target


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


class TestPlanChunks:
    def test_covers_the_file_with_contiguous_ranges(self):
        chunks = accel.plan_chunks(25, 10)
        assert chunks == [(0, 10), (10, 20), (20, 25)]

    def test_exact_multiple_has_no_empty_tail(self):
        assert accel.plan_chunks(20, 10) == [(0, 10), (10, 20)]

    @pytest.mark.parametrize("total,chunk", [(0, 10), (10, 0), (-1, 10)])
    def test_rejects_non_positive_sizes(self, total, chunk):
        with pytest.raises(ValueError):
            accel.plan_chunks(total, chunk)


class TestSupports:
    params = {"vidkeep_connections": 4, "vidkeep_chunk_size": CHUNK}
    info = {"protocol": "https", "filesize": 10 * CHUNK}

    def test_single_file_with_exact_size_is_eligible(self):
        assert accel.supports(self.info, self.params)

    @pytest.mark.parametrize(
        "change",
        [
            {"protocol": "m3u8_native"},
            {"filesize": None},
            {"filesize": CHUNK},  # too small to be worth splitting
            {"is_live": True},
            {"fragments": [{"url": "x"}]},
        ],
    )
    def test_ineligible_formats(self, change):
        assert not accel.supports({**self.info, **change}, self.params)

    @pytest.mark.parametrize("change", [{"vidkeep_connections": 1}, {"ratelimit": 1_000_000}, {"test": True}])
    def test_settings_that_turn_it_off(self, change):
        assert not accel.supports(self.info, {**self.params, **change})


def test_parallel_download_is_byte_identical(range_server, tmp_path):
    ok, target = download(range_server, tmp_path)
    assert ok
    assert read(target) == range_server.payload
    assert sorted(os.listdir(tmp_path)) == ["out.mp4"]  # no part, state or lock file left
    assert len(range_server.requests) == len(accel.plan_chunks(len(range_server.payload), CHUNK))


def test_every_request_is_a_bounded_range(range_server, tmp_path):
    download(range_server, tmp_path)
    assert all(end - start + 1 <= CHUNK for start, end in range_server.requests)


def test_progress_hooks_end_with_finished(range_server, tmp_path):
    events = []
    ok, _ = download(range_server, tmp_path, progress_hooks=[events.append])
    assert ok
    assert events[-1]["status"] == "finished"
    assert events[-1]["downloaded_bytes"] == len(range_server.payload)


def test_server_error_is_retried(range_server, tmp_path):
    range_server.fail_once = {0, 3 * CHUNK}
    ok, target = download(range_server, tmp_path)
    assert ok
    assert read(target) == range_server.payload


def test_dropped_connection_resumes_mid_range(range_server, tmp_path, monkeypatch):
    # Small reads, so part of the range is on disk before the connection drops.
    monkeypatch.setattr(accel, "READ_BLOCK", 8 * 1024)
    range_server.cut_once = {2 * CHUNK}
    ok, target = download(range_server, tmp_path)
    assert ok
    assert read(target) == range_server.payload
    # The retry asks only for the missing half of the range, not the whole range again.
    retry_starts = [s for s, _ in range_server.requests if 2 * CHUNK < s < 3 * CHUNK]
    assert retry_starts and all(s > 2 * CHUNK for s in retry_starts)


def test_expired_url_fails_without_retrying(range_server, tmp_path):
    range_server.status_for_all = 403
    ok, target = download(range_server, tmp_path, vidkeep_connections=2)
    assert not ok
    assert not os.path.exists(target)
    assert len(range_server.requests) <= 2  # one per connection, none repeated


def test_server_that_ignores_ranges_writes_nothing_wrong(range_server, tmp_path):
    range_server.ignore_range = True
    ok, target = download(range_server, tmp_path)
    assert not ok
    assert not os.path.exists(target)


def test_resume_skips_ranges_already_on_disk(range_server, tmp_path):
    target = tmp_path / "out.mp4"
    part = str(target) + ".part"
    total = len(range_server.payload)
    done = [0, 1, 2, 3, 4]
    with open(part, "wb") as fh:
        fh.truncate(total)
        fh.write(range_server.payload[: 5 * CHUNK])
    with open(part + accel.STATE_SUFFIX, "w") as fh:
        json.dump({"total": total, "chunk_size": CHUNK, "done": done}, fh)

    ok, _ = download(range_server, tmp_path)
    assert ok
    assert read(target) == range_server.payload
    assert not any(start < 5 * CHUNK for start, _ in range_server.requests)


def test_stale_state_for_another_size_starts_over(range_server, tmp_path):
    part = str(tmp_path / "out.mp4") + ".part"
    with open(part, "wb") as fh:
        fh.write(b"\0" * 100)
    with open(part + accel.STATE_SUFFIX, "w") as fh:
        json.dump({"total": 999, "chunk_size": CHUNK, "done": [0]}, fh)
    ok, target = download(range_server, tmp_path)
    assert ok
    assert read(target) == range_server.payload


@pytest.mark.skipif(accel.fcntl is None, reason="advisory locks are POSIX-only")
def test_second_writer_on_the_same_file_is_refused(range_server, tmp_path):
    part = str(tmp_path / "out.mp4") + ".part"
    holder = accel._acquire_lock(part + accel.LOCK_SUFFIX)
    try:
        ok, _ = download(range_server, tmp_path)
    finally:
        holder.close()
    assert not ok
    assert range_server.requests == []


def test_abort_stops_and_keeps_resume_state(range_server, tmp_path):
    accel.ABORT.set()
    with pytest.raises(accel._Stopped):
        download(range_server, tmp_path)
    part = str(tmp_path / "out.mp4") + ".part"
    assert os.path.exists(part + accel.STATE_SUFFIX)


def test_small_files_use_the_regular_downloader(range_server, tmp_path):
    ok, target = download(range_server, tmp_path, vidkeep_chunk_size=len(range_server.payload))
    assert ok
    assert read(target) == range_server.payload
    assert len(range_server.requests) <= 2  # yt-dlp's own single-connection path
