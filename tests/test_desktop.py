import json
import sys
import time

import pytest

from vidkeep import child, desktop
from vidkeep.desktop import Download, LookupFailed, Request

# Stands in for `python -m vidkeep`: the link decides what it does, so the desktop controller's
# process handling runs for real without the network.
FAKE_VIDKEEP = r"""
import json, sys, time
command, url = sys.argv[1], sys.argv[-1]
say = lambda **event: print(json.dumps(event), flush=True)
if command == "info":
    if "private" in url:
        print("error: Private video. Sign in if you've been granted access", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"title": "Sample", "args": sys.argv[2:]}))
elif "fail" in url:
    print("error: Video unavailable", file=sys.stderr)
    sys.exit(1)
else:
    for video in (1, 2) if "list" in url else (1,):
        say(event="start", id=video, url=url)
        say(event="plan", id=video, title=f"Part {video}", quality="720p h264", format="22", size=1000)
        say(event="progress", id=video, downloaded=400, speed=200.0)
        if "slow" in url:
            time.sleep(60)
        say(event="finished", id=video, status="done", title=f"Part {video}", path=f"/v/{video}.mp4", size=1000)
"""


@pytest.fixture
def fake(tmp_path):
    script = tmp_path / "fake_vidkeep.py"
    script.write_text(FAKE_VIDKEEP)
    return [sys.executable, str(script)]


def finished(download, timeout=20):
    deadline = time.monotonic() + timeout
    while download.status == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    return download


def test_lookup_returns_vidkeeps_json(fake):
    assert desktop.look_up("https://ok.test/v", fake)["args"] == ["--json", "--", "https://ok.test/v"]


def test_a_failed_lookup_carries_vidkeeps_message(fake):
    with pytest.raises(LookupFailed, match="^Private video"):
        desktop.look_up("https://ok.test/private", fake)


def test_requests_put_the_link_last_after_a_double_dash():
    single = Request("https://x.test/v", "/videos", format="399+140", subs="en,ar")
    assert single.arguments() == ["-o", "/videos", "-f", "399+140", "--subs", "en,ar", "--", "https://x.test/v"]
    playlist = Request("https://x.test/list", "/videos", quality="720", codec="compatible")
    assert playlist.arguments() == ["-o", "/videos", "-q", "720", "-c", "compatible", "--", "https://x.test/list"]


def test_a_download_reports_each_video_and_finishes_done(fake):
    download = finished(Download(Request("https://ok.test/list", "/v", quality="720"), "Course", fake).start())
    assert download.status == "done"
    assert [video["title"] for video in download.snapshot()] == ["Part 1", "Part 2"]
    assert download.saved_path == "/v/1.mp4"


def test_a_failed_download_says_why(fake):
    download = finished(Download(Request("https://ok.test/fail", "/v"), "x", fake).start())
    assert (download.status, download.error) == ("failed", "Video unavailable")


def test_stopping_then_downloading_again(fake):
    download = Download(Request("https://ok.test/slow", "/v"), "x", fake).start()
    deadline = time.monotonic() + 20
    while not download.snapshot() and time.monotonic() < deadline:
        time.sleep(0.05)
    download.stop()
    assert finished(download).status == "stopped"
    again = download.again()
    assert again.request == download.request and again.status == "running"
    again.stop()


def test_only_the_video_drops_the_playlist_from_a_watch_link():
    link = "https://www.youtube.com/watch?v=M788vUWI2Rk&list=PLx&index=2"
    assert desktop.only_the_video(link) == "https://www.youtube.com/watch?v=M788vUWI2Rk"
    assert desktop.only_the_video("https://www.youtube.com/playlist?list=PLx") is None
    assert desktop.only_the_video("https://www.youtube.com/watch?v=abc") is None


def test_settings_keep_known_keys_and_survive_a_broken_file(tmp_path):
    path = tmp_path / "settings.json"
    assert desktop.load_settings(path)["languages"] == "en"
    desktop.save_settings({"folder": "/v", "subtitles": True, "languages": "ar", "junk": 1}, path)
    assert desktop.load_settings(path) == {"folder": "/v", "subtitles": True, "languages": "ar"}
    path.write_text("{not json")
    assert desktop.load_settings(path)["folder"] == str(desktop.default_folder())


def test_progress_events_fold_into_one_entry_per_video():
    videos = {}
    for event in (
        {"event": "plan", "id": 1, "title": "A", "size": 10},
        {"event": "progress", "id": 1, "downloaded": 4, "speed": 2.0},
        {"event": "stage", "id": 1, "stage": "Merger"},
    ):
        child.fold_event(videos, json.dumps(event))
    child.fold_event(videos, '{"event": "progress", "id": 1, "downl')  # cut off mid-line
    child.fold_event(videos, "[1, 2]")
    assert videos == {
        1: {"status": "processing", "downloaded": 4, "title": "A", "size": 10, "speed": 2.0, "stage": "Merger"}
    }
