import io
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vidkeep import native
from vidkeep.native import Host, Jobs, Places, Refused, get_args

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the Firefox host runs on macOS and Linux")

# Stands in for `python -m vidkeep`, so the host's process handling runs for real without the network.
# The link decides what it does; its file name keeps "vidkeep" in the command line like the real one.
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
    print("error: Video unavailable. This video is private", file=sys.stderr)
    sys.exit(1)
else:
    say(event="start", id=1, url=url)
    say(event="plan", id=1, title="Sample", quality="1080p av1", format="399+140", size=1000)
    say(event="progress", id=1, downloaded=500, speed=250.0)
    if "slow" in url:
        time.sleep(60)
    say(event="finished", id=1, status="done", title="Sample", path="/videos/Sample.mp4", size=1000, error=None)
"""


@pytest.fixture
def places(tmp_path):
    return Places(
        manifest_dir=tmp_path / "hosts",
        data_dir=tmp_path / "data",
        jobs_dir=tmp_path / "jobs",
        downloads=tmp_path / "Movies" / "vidkeep",
    )


@pytest.fixture
def host(places, tmp_path):
    fake = tmp_path / "fake_vidkeep.py"
    fake.write_text(FAKE_VIDKEEP)
    host = Host(places, Jobs(places.jobs_dir, (sys.executable, str(fake))))
    yield host
    for job in host.jobs.list():
        if job["status"] == "running":
            host.jobs.stop(job["id"])


def wait_for(check, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.1)
    raise AssertionError("timed out")


def job_status(host, job_id, wanted):
    return wait_for(lambda: next((j for j in host.jobs.list() if j["id"] == job_id and j["status"] == wanted), None))


def test_messages_are_length_prefixed_json_in_native_byte_order():
    stream = io.BytesIO()
    native.write_message(stream, {"type": "hello", "text": "مرحبا"})
    raw = stream.getvalue()
    assert struct.unpack("=I", raw[:4])[0] == len(raw) - 4
    stream.seek(0)
    assert native.read_message(stream) == {"type": "hello", "text": "مرحبا"}
    assert native.read_message(stream) is None


def test_a_reply_over_firefoxs_limit_is_refused_before_sending():
    with pytest.raises(ValueError):
        native.write_message(io.BytesIO(), {"blob": "x" * native.MAX_REPLY})


def test_download_requests_become_vidkeep_arguments_with_the_link_last(places):
    request = {
        "url": "https://www.youtube.com/watch?v=abc",
        "format": "399+140",
        "subs": "en,ar",
        "browser": "firefox",
        "folder": "~/Videos/courses",
    }
    assert get_args(request, places) == [
        "-o",
        str(Path("~/Videos/courses").expanduser()),
        "-f",
        "399+140",
        "--subs",
        "en,ar",
        "--cookies-from-browser",
        "firefox",
        "--",
        "https://www.youtube.com/watch?v=abc",
    ]


def test_a_playlist_request_carries_quality_and_codec_and_defaults_the_folder(places):
    args = get_args({"url": "https://example.com/list", "quality": "720", "codec": "compatible"}, places)
    assert args == ["-o", str(places.downloads), "-q", "720", "-c", "compatible", "--", "https://example.com/list"]


@pytest.mark.parametrize(
    "request_fields",
    [
        {"url": "--exec=touch /tmp/x"},
        {"url": "file:///etc/passwd"},
        {"url": "javascript:alert(1)"},
        {"url": 42},
        {"url": "https://ok.test/v", "format": "best; rm -rf ~"},
        {"url": "https://ok.test/v", "browser": "netscape"},
        {"url": "https://ok.test/v", "quality": "8k"},
        {"url": "https://ok.test/v", "subs": "en --exec x"},
        {"url": "https://ok.test/v", "folder": "relative/dir"},
    ],
)
def test_anything_that_is_not_a_plain_link_and_known_choices_is_refused(places, request_fields):
    with pytest.raises(Refused):
        get_args(request_fields, places)


def test_unknown_and_malformed_requests_get_an_error_reply(host):
    assert host.handle({"type": "format-disk"}) == {"ok": False, "error": "unknown request"}
    assert host.handle(["hello"])["ok"] is False
    assert host.handle({"type": "stop", "job": "../../etc/passwd"}) == {"ok": False, "error": "no such download"}


def test_hello_reports_the_version_and_default_folder(host, places):
    reply = host.handle({"type": "hello"})
    assert reply["ok"] and reply["version"] == native.__version__
    assert reply["folder"] == str(places.downloads)


def test_lookup_returns_vidkeeps_json_and_passes_the_browser_sign_in(host):
    reply = host.handle({"type": "info", "url": "https://ok.test/v", "browser": "firefox"})
    assert reply == {
        "ok": True,
        "info": {"title": "Sample", "args": ["--json", "--cookies-from-browser", "firefox", "--", "https://ok.test/v"]},
    }


def test_a_failed_lookup_reports_vidkeeps_error_message(host):
    reply = host.handle({"type": "info", "url": "https://ok.test/private"})
    assert reply == {"ok": False, "error": "Private video. Sign in if you've been granted access"}


def test_a_download_runs_detached_and_its_progress_is_read_back(host):
    reply = host.handle({"type": "get", "url": "https://ok.test/v", "format": "399+140"})
    assert reply["ok"]
    job = job_status(host, reply["job"], "done")
    assert job["url"] == "https://ok.test/v"
    [video] = job["videos"]
    assert (video["title"], video["quality"], video["path"], video["size"]) == (
        "Sample",
        "1080p av1",
        "/videos/Sample.mp4",
        1000,
    )


def test_a_failed_download_explains_why(host):
    job_id = host.handle({"type": "get", "url": "https://ok.test/fail"})["job"]
    job = job_status(host, job_id, "failed")
    assert job["error"] == "Video unavailable. This video is private"


def test_stopping_and_resuming_a_download(host):
    job_id = host.handle({"type": "get", "url": "https://ok.test/slow"})["job"]
    running = job_status(host, job_id, "running")
    wait_for(lambda: next(j for j in host.jobs.list() if j["id"] == job_id)["videos"])
    assert running["status"] == "running"
    assert host.handle({"type": "stop", "job": job_id}) == {"ok": True}
    job_status(host, job_id, "stopped")
    resumed = host.handle({"type": "retry", "job": job_id})["job"]
    assert resumed != job_id
    assert [j["id"] for j in host.jobs.list()] == [resumed]
    job_status(host, resumed, "running")
    assert host.handle({"type": "retry", "job": resumed}) == {"ok": False, "error": "that download is still running"}


def test_clearing_keeps_running_downloads(host):
    finished = host.handle({"type": "get", "url": "https://ok.test/v"})["job"]
    job_status(host, finished, "done")
    running = host.handle({"type": "get", "url": "https://ok.test/slow"})["job"]
    job_status(host, running, "running")
    host.handle({"type": "forget"})
    assert [j["id"] for j in host.jobs.list()] == [running]


def test_install_points_firefox_at_this_python_with_the_current_path(places):
    manifest_path = native.install(places, python="/opt/tools/vidkeep/bin/python", path="/opt/homebrew/bin:/usr/bin")
    manifest = json.loads(manifest_path.read_text())
    assert manifest_path == places.manifest_dir / "io.github.rootx22.vidkeep.json"
    assert manifest["allowed_extensions"] == ["vidkeep@rootx22.github.io"]
    assert manifest["type"] == "stdio"
    launcher = Path(manifest["path"])
    assert os.access(launcher, os.X_OK)
    text = launcher.read_text()
    assert "export PATH=/opt/homebrew/bin:/usr/bin\n" in text
    assert 'exec /opt/tools/vidkeep/bin/python -m vidkeep.native "$@"' in text
    assert native.uninstall(places) == [manifest_path, launcher]
    assert native.uninstall(places) == []


def test_firefox_looks_in_a_different_place_on_each_system(tmp_path):
    mac = Places.for_platform(tmp_path, "darwin")
    assert mac.manifest_dir == tmp_path / "Library/Application Support/Mozilla/NativeMessagingHosts"
    assert mac.downloads == tmp_path / "Movies/vidkeep"
    linux = Places.for_platform(tmp_path, "linux", env={"XDG_CACHE_HOME": str(tmp_path / "cache")})
    assert linux.manifest_dir == tmp_path / ".mozilla/native-messaging-hosts"
    assert linux.jobs_dir == tmp_path / "cache/vidkeep/jobs"
    with pytest.raises(RuntimeError):
        Places.for_platform(tmp_path, "win32")


def test_the_host_answers_over_pipes_the_way_firefox_talks_to_it(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("XDG_")}
    env["HOME"] = str(tmp_path)
    stdin = io.BytesIO()
    native.write_message(stdin, {"type": "hello"})
    native.write_message(stdin, {"type": "jobs"})
    done = subprocess.run(
        [sys.executable, "-m", "vidkeep.native", "/path/to/manifest.json", "vidkeep@rootx22.github.io"],
        input=stdin.getvalue(),
        capture_output=True,
        env=env,
        timeout=60,
        check=True,
    )
    replies = io.BytesIO(done.stdout)
    assert native.read_message(replies)["version"] == native.__version__
    assert native.read_message(replies) == {"ok": True, "jobs": []}
    assert native.read_message(replies) is None
