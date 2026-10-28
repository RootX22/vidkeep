"""The Firefox add-on, the native host and vidkeep together, in a real headless Firefox.

Linux only: there Firefox looks for native hosts under $HOME, so the test gives Firefox a home of
its own and never touches the real one. CI runs it with `pytest -m firefox`.
"""

import functools
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from vidkeep import native

pytestmark = [
    pytest.mark.firefox,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs Firefox's Linux host lookup"),
]
webdriver = pytest.importorskip("selenium.webdriver")
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.firefox.options import Options  # noqa: E402
from selenium.webdriver.firefox.service import Service  # noqa: E402

ADDON = Path(__file__).resolve().parents[1] / "browser" / "firefox"
UUID = "3f6c1c0e-2a51-4f43-9a8e-5a1d7c2b9e01"


@pytest.fixture
def site(tmp_path):
    """A web server with one short video on it."""
    root = tmp_path / "site"
    root.mkdir()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=duration=5:size=320x180:rate=10"]
        + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "lecture.mp4")],
        check=True,
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def firefox(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    native.install(native.Places.for_platform(home, "linux", env={}))
    addon = tmp_path / "vidkeep.zip"
    with zipfile.ZipFile(addon, "w") as archive:
        for path in ADDON.rglob("*"):
            archive.write(path, path.relative_to(ADDON))
    options = Options()
    options.add_argument("-headless")
    options.set_preference("extensions.webextensions.uuids", json.dumps({native.EXTENSION_ID: UUID}))
    env = {key: value for key, value in os.environ.items() if not key.startswith("XDG_")}
    service = Service(service_args=["--allow-system-access"], env={**env, "HOME": str(home)})
    driver = webdriver.Firefox(options=options, service=service)
    try:
        driver.install_addon(str(addon), temporary=True)
        yield driver
    finally:
        driver.quit()


def open_popup(driver, page_url):
    # WebDriver may not navigate to an add-on page itself; Firefox's own window can.
    with driver.context(driver.CONTEXT_CHROME):
        driver.execute_script(
            "Services.wm.getMostRecentWindow('navigator:browser').openTrustedLinkIn(arguments[0], 'current')",
            f"moz-extension://{UUID}/popup.html?url={page_url}",
        )


def texts(driver, selector):
    """Read in one call: the popup redraws its lists every second, which leaves held elements stale."""
    return driver.execute_script(
        "return [...document.querySelectorAll(arguments[0])].map((element) => element.textContent)", selector
    )


def wait_for(driver, check, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.5)
    raise AssertionError("timed out; the popup said:\n" + driver.find_element(By.TAG_NAME, "body").text)


def test_the_popup_looks_up_the_page_and_vidkeep_saves_it(firefox, site, tmp_path):
    out = tmp_path / "saved"
    open_popup(firefox, f"{site}/lecture.mp4")
    version = wait_for(firefox, lambda: "".join(texts(firefox, "#version")))
    assert version == f"vidkeep {native.__version__}"
    folder = firefox.find_element(By.ID, "folder")
    folder.send_keys(str(out))
    firefox.execute_script("arguments[0].dispatchEvent(new Event('change'))", folder)
    wait_for(firefox, lambda: texts(firefox, ".choice"))
    assert texts(firefox, "#title") == ["lecture"]
    firefox.find_element(By.ID, "download").click()
    wait_for(firefox, lambda: any("Saved" in job for job in texts(firefox, ".job")))
    [saved] = out.glob("lecture*.mp4")
    assert saved.stat().st_size > 10_000
