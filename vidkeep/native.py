"""The program Firefox runs for the vidkeep add-on (native messaging).

Firefox starts it for every message the add-on sends and talks to it over stdin and stdout: each
message is a 4-byte length in native byte order followed by that much UTF-8 JSON. Lookups run
`vidkeep info --json` and wait for it. Downloads must outlive this short-lived process, so each one
is a detached `vidkeep get --json` writing its progress to a job file that later requests read.

Everything in a request comes from a web page's URL or the add-on's popup, so each field is
checked and the command line is built here; nothing from a message is passed through as a flag.
This module imports only the standard library and the planner, because Firefox starts it about
once a second while the popup is open.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__, planner

HOST_NAME = "io.github.rootx22.vidkeep"
EXTENSION_ID = "vidkeep@rootx22.github.io"
MAX_REPLY = 1024 * 1024  # Firefox drops larger messages from a host
INFO_TIMEOUT = 180
JOBS_SHOWN = 30
BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge", "safari", "opera", "vivaldi")
_FORMAT = re.compile(r"^[\w.+-]{1,64}$")
_LANGS = re.compile(r"^[\w-]{1,20}(,[\w-]{1,20}){0,9}$")
_JOB_ID = re.compile(r"^[0-9a-f]{12}$")


class Refused(Exception):
    """A request the host cannot carry out; its text goes back to the add-on as the error."""


# Framing


def read_message(stream) -> dict | None:
    """The next message, or None once Firefox has closed the pipe."""
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("=I", header)
    return json.loads(stream.read(length).decode("utf-8"))


def write_message(stream, message: dict) -> None:
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_REPLY:
        raise ValueError(f"reply of {len(body)} bytes is over Firefox's limit")
    stream.write(struct.pack("=I", len(body)))
    stream.write(body)
    stream.flush()


# Places


@dataclass(frozen=True)
class Places:
    """Where Firefox looks for the host manifest, and where vidkeep keeps its own files."""

    manifest_dir: Path
    data_dir: Path
    jobs_dir: Path
    downloads: Path

    @classmethod
    def for_platform(cls, home: Path, platform: str = sys.platform, env=os.environ) -> Places:
        if platform == "darwin":
            library = home / "Library"
            return cls(
                manifest_dir=library / "Application Support" / "Mozilla" / "NativeMessagingHosts",
                data_dir=library / "Application Support" / "vidkeep",
                jobs_dir=library / "Caches" / "vidkeep" / "jobs",
                downloads=home / "Movies" / "vidkeep",
            )
        if platform.startswith("linux"):
            data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
            cache = Path(env.get("XDG_CACHE_HOME") or home / ".cache")
            return cls(
                manifest_dir=home / ".mozilla" / "native-messaging-hosts",
                data_dir=data / "vidkeep",
                jobs_dir=cache / "vidkeep" / "jobs",
                downloads=home / "Videos" / "vidkeep",
            )
        raise RuntimeError("the Firefox add-on works on macOS and Linux for now")


# Installing


def install(places: Places, python: str = sys.executable, path: str | None = None) -> Path:
    """Write the launcher and the manifest that point Firefox at this Python's vidkeep."""
    # Firefox starts hosts with a bare PATH; ffmpeg and deno are wherever they are for the user now.
    search_path = path if path is not None else os.environ.get("PATH", "")
    places.data_dir.mkdir(parents=True, exist_ok=True)
    launcher = places.data_dir / "firefox-host"
    launcher.write_text(
        "#!/bin/sh\n"
        "# Written by `vidkeep browser install`. Firefox runs this to reach vidkeep.\n"
        f"export PATH={shlex.quote(search_path)}\n"
        f'exec {shlex.quote(python)} -m vidkeep.native "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    manifest = {
        "name": HOST_NAME,
        "description": "vidkeep, for its Firefox add-on",
        "path": str(launcher),
        "type": "stdio",
        "allowed_extensions": [EXTENSION_ID],
    }
    places.manifest_dir.mkdir(parents=True, exist_ok=True)
    target = places.manifest_dir / f"{HOST_NAME}.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return target


def uninstall(places: Places) -> list[Path]:
    removed = []
    for path in (places.manifest_dir / f"{HOST_NAME}.json", places.data_dir / "firefox-host"):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


# Requests


def _url(value) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise Refused("no link to download")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise Refused("vidkeep can only download http and https links")
    return value


def _choice(request: dict, field: str, allowed) -> str | None:
    value = request.get(field)
    if value in (None, ""):
        return None
    if value not in allowed:
        raise Refused(f"unknown {field}: {value}")
    return value


def _matching(request: dict, field: str, pattern: re.Pattern) -> str | None:
    value = request.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not pattern.match(value):
        raise Refused(f"bad {field}: {value}")
    return value


def _folder(request: dict, places: Places) -> str:
    value = request.get("folder") or str(places.downloads)
    if not isinstance(value, str):
        raise Refused("bad folder")
    folder = Path(value).expanduser()
    if not folder.is_absolute():
        raise Refused("the folder must be a full path, like ~/Movies/vidkeep")
    return str(folder)


def sign_in_args(request: dict) -> list[str]:
    browser = _choice(request, "browser", BROWSERS)
    return ["--cookies-from-browser", browser] if browser else []


def get_args(request: dict, places: Places) -> list[str]:
    """The `vidkeep get` arguments for a download request; the URL always comes last, after --."""
    args = ["-o", _folder(request, places)]
    if fmt := _matching(request, "format", _FORMAT):
        args += ["-f", fmt]
    if quality := _choice(request, "quality", planner.QUALITIES):
        args += ["-q", quality]
    if codec := _choice(request, "codec", planner.CODEC_ORDERS):
        args += ["-c", codec]
    if subs := _matching(request, "subs", _LANGS):
        args += ["--subs", subs]
    return args + sign_in_args(request) + ["--", _url(request.get("url"))]


def _error_line(stderr: str) -> str:
    """vidkeep prints "error: ..." and maybe a hint, wrapped to 80 columns; fall back to its last line."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    starts = [index for index, line in enumerate(lines) if line.lower().startswith("error:")]
    if starts:
        return " ".join(lines[starts[-1] :])[len("error:") :].strip()
    return lines[-1] if lines else "vidkeep stopped without saying why"


# Jobs


def _live_downloads() -> set[int]:
    """Pids of running `vidkeep get --json` processes. Checking the command, not just the pid,
    matters because a pid can belong to another program after a restart."""
    listing = subprocess.run(["ps", "-e", "-o", "pid=,args="], capture_output=True, text=True, check=True).stdout
    live = set()
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if "vidkeep" in command and " get --json " in command:
            live.add(int(pid))
    return live


@dataclass
class Jobs:
    """Downloads started from the add-on: a .json record, a .jsonl of progress and a .log each."""

    root: Path
    command: tuple[str, ...]

    def start(self, args: list[str]) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        job = uuid.uuid4().hex[:12]
        with open(self.root / f"{job}.jsonl", "wb") as out, open(self.root / f"{job}.log", "wb") as log:
            process = subprocess.Popen(
                [*self.command, "get", "--json", *args],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=log,
                start_new_session=True,  # Firefox ends this host; the download must go on
            )
        self._save(job, {"id": job, "args": args, "pid": process.pid, "started": time.time(), "stopped": False})
        return job

    def stop(self, job: str) -> None:
        record = self._load(job)
        if self._running(record):
            os.kill(record["pid"], signal.SIGTERM)  # vidkeep treats it as Ctrl+C: the download stays resumable
        self._save(job, {**record, "stopped": True})

    def retry(self, job: str) -> str:
        """Start the same download again; vidkeep picks up the ranges already on disk."""
        record = self._load(job)
        if self._running(record):
            raise Refused("that download is still running")
        self._remove(job)
        return self.start(record["args"])

    def forget_finished(self) -> None:
        live = _live_downloads()
        for record in self._records():
            if record["pid"] not in live:
                self._remove(record["id"])

    def list(self) -> list[dict]:
        records = sorted(self._records(), key=lambda record: record["started"], reverse=True)
        live = _live_downloads()
        return [self._describe(record, live) for record in records[:JOBS_SHOWN]]

    def path_of(self, job: str) -> str | None:
        videos = self._describe(self._load(job), _live_downloads())["videos"]
        return next((video["path"] for video in videos if video.get("path")), None)

    def _describe(self, record: dict, live: set[int]) -> dict:
        videos = self._videos(record["id"])
        if record["pid"] in live:
            status = "running"
        elif videos and all(video.get("status") in ("done", "skipped") for video in videos):
            status = "done"
        elif record["stopped"]:
            status = "stopped"
        else:
            status = "failed"
        error = None
        if status == "failed":
            error = next((video["error"] for video in videos if video.get("error")), None) or _error_line(
                (self.root / f"{record['id']}.log").read_text(encoding="utf-8", errors="replace")
            )
        return {"id": record["id"], "url": record["args"][-1], "status": status, "error": error, "videos": videos}

    def _videos(self, job: str) -> list[dict]:
        """Fold the progress events into one entry per video, in the order vidkeep started them."""
        videos: dict[int, dict] = {}
        with open(self.root / f"{job}.jsonl", encoding="utf-8") as events:
            for line in events:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # the last line may still be half written
                video = videos.setdefault(event.get("id"), {"status": "starting", "downloaded": 0})
                kind = event.pop("event", None)
                event.pop("id", None)
                if kind == "plan":
                    video["status"] = "downloading"
                elif kind == "stage":
                    video["status"] = "processing"
                video.update(event)
        return list(videos.values())

    def _running(self, record: dict) -> bool:
        return record["pid"] in _live_downloads()

    def _records(self) -> list[dict]:
        if not self.root.exists():
            return []
        return [json.loads(path.read_text(encoding="utf-8")) for path in self.root.glob("*.json")]

    def _load(self, job: str) -> dict:
        if not isinstance(job, str) or not _JOB_ID.match(job) or not (self.root / f"{job}.json").exists():
            raise Refused("no such download")
        return json.loads((self.root / f"{job}.json").read_text(encoding="utf-8"))

    def _save(self, job: str, record: dict) -> None:
        (self.root / f"{job}.json").write_text(json.dumps(record), encoding="utf-8")

    def _remove(self, job: str) -> None:
        for suffix in (".json", ".jsonl", ".log"):
            (self.root / f"{job}{suffix}").unlink(missing_ok=True)


# Serving


@dataclass
class Host:
    places: Places
    jobs: Jobs

    def handle(self, request: dict) -> dict:
        handlers = {
            "hello": lambda: {"version": __version__, "folder": str(self.places.downloads)},
            "info": lambda: {"info": self.info(request)},
            "get": lambda: {"job": self.jobs.start(get_args(request, self.places))},
            "jobs": lambda: {"jobs": self.jobs.list()},
            "stop": lambda: self.jobs.stop(request.get("job")),
            "retry": lambda: {"job": self.jobs.retry(request.get("job"))},
            "forget": self.jobs.forget_finished,
            "reveal": lambda: self.reveal(request.get("job")),
        }
        handler = handlers.get(request.get("type")) if isinstance(request, dict) else None
        if handler is None:
            return {"ok": False, "error": "unknown request"}
        try:
            return {"ok": True, **(handler() or {})}
        except Refused as err:
            return {"ok": False, "error": str(err)}

    def info(self, request: dict) -> dict:
        command = [*self.jobs.command, "info", "--json", *sign_in_args(request), "--", _url(request.get("url"))]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=INFO_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            raise Refused("the site took too long to answer") from None
        if done.returncode != 0:
            raise Refused(_error_line(done.stderr))
        return json.loads(done.stdout)

    def reveal(self, job: str) -> None:
        path = self.jobs.path_of(job)
        if not path or not os.path.exists(path):
            raise Refused("the file is not there any more")
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", path], check=False)
        else:
            subprocess.run(["xdg-open", os.path.dirname(path)], check=False)

    def serve(self, stdin, stdout) -> None:
        while (request := read_message(stdin)) is not None:
            write_message(stdout, self.handle(request))


def main() -> None:
    places = Places.for_platform(Path.home())
    host = Host(places, Jobs(places.jobs_dir, (sys.executable, "-m", "vidkeep")))
    host.serve(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    main()
