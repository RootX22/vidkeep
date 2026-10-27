import random
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vidkeep import accel

_RANGE = re.compile(r"bytes=(\d+)-(\d+)")


class RangeServer:
    """A local HTTP server for one file, with switches for the failures a CDN produces."""

    def __init__(self, size: int):
        self.payload = random.Random(42).randbytes(size)
        self.requests: list[tuple[int, int]] = []
        self.fail_once: set[int] = set()  # range starts answered with HTTP 500 the first time
        self.cut_once: set[int] = set()  # range starts whose connection drops halfway the first time
        self.status_for_all: int | None = None  # e.g. 403 for an expired URL
        self.ignore_range = False  # answer 200 with the whole file, like a server without range support
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                match = _RANGE.match(self.headers.get("Range", ""))
                start, end = (int(match.group(1)), int(match.group(2))) if match else (0, size - 1)
                with server._lock:
                    server.requests.append((start, end))
                    fail = start in server.fail_once
                    cut = start in server.cut_once
                    server.fail_once.discard(start)
                    server.cut_once.discard(start)
                if server.status_for_all:
                    self.send_error(server.status_for_all)
                    return
                if fail:
                    self.send_error(500)
                    return
                if server.ignore_range or not match:
                    body = server.payload
                    self.send_response(200)
                else:
                    body = server.payload[start : end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body[: len(body) // 2] if cut else body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/stream.bin"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def info(self) -> dict:
        return {
            "id": "local",
            "format_id": "137",
            "url": self.url,
            "ext": "mp4",
            "protocol": "http",
            "filesize": len(self.payload),
            "http_headers": {},
        }

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def range_server():
    server = RangeServer(1_000_000 + 123)
    yield server
    server.close()


@pytest.fixture(autouse=True)
def _reset_abort():
    accel.ABORT.clear()
    yield
    accel.ABORT.clear()
