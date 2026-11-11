import os
import sys
import time

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("sv_ttk")
from test_desktop import FAKE_VIDKEEP  # noqa: E402

from vidkeep import gui  # noqa: E402

VIDEO = {
    "title": "Big Buck Bunny",
    "channel": "Blender",
    "duration_string": "10:35",
    "playlist_count": 1,
    "default_format": "399+140",
    "audio_only": {"format": "140", "size": 10_000_000},
    "options": [
        {
            "height": 1080,
            "codec": "av1",
            "format": "399+140",
            "size": 160_000_000,
            "exact": True,
            "quicktime": "M3 or newer",
        },
        {"height": 1080, "codec": "h264", "format": "137+140", "size": 270_000_000, "exact": True, "quicktime": "yes"},
    ],
}
PLAYLIST = {
    **VIDEO,
    "playlist_count": 2,
    "playlist_title": "Open movies",
    "entries": [{"title": "Big Buck Bunny", "duration": 635}, {"title": "Sintel", "duration": 888}],
}


@pytest.fixture
def window(tmp_path):
    try:
        root = gui.make_window()
    except tk.TclError:
        pytest.skip("no display")
    fake = tmp_path / "fake_vidkeep.py"
    fake.write_text(FAKE_VIDKEEP)
    answers = {"https://ok.test/v": VIDEO, "https://www.youtube.com/watch?v=abc&list=PL1": PLAYLIST}
    app = gui.App(
        root,
        look_up=lambda url, command: answers[url],
        command=[sys.executable, str(fake)],
        settings_path=tmp_path / "settings.json",
    )
    app.settings["folder"] = str(tmp_path / "videos")
    yield app
    root.destroy()


def pump(app, done, timeout=20):
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.02)
    assert done(), "timed out"


def test_a_video_lists_its_qualities_and_downloads(window):
    window.link.set("https://ok.test/v")
    window.start_lookup()
    pump(window, lambda: window.table.get_children())
    assert window.title.cget("text") == "Big Buck Bunny"
    assert [window.table.item(row)["values"][0] for row in window.table.get_children()] == [
        "1080p",
        "1080p",
        "Audio only",
    ]
    expected = "137+140" if sys.platform == "win32" else "399+140"  # Windows prefers what its player opens
    assert window.table.selection() == (expected,)
    window.download()
    [iid] = window.download_list.get_children()
    pump(window, lambda: window.download_list.item(iid)["values"][3] == "Saved")
    assert window.download_list.item(iid)["values"][1] == "720p h264"
    assert os.path.exists(window.settings_path)


def test_a_playlist_shows_its_name_videos_and_a_way_to_take_only_the_linked_one(window):
    window.link.set("https://www.youtube.com/watch?v=abc&list=PL1")
    window.start_lookup()
    pump(window, lambda: window.entries.get_children())
    assert window.title.cget("text") == "Open movies"
    assert window.details.cget("text") == "Playlist of 2 videos · 25:23 in all"
    assert len(window.entries.get_children()) == 2
    assert window.only_video.winfo_manager() == "grid"
    request = window.request()
    assert (request.quality, request.format) == ("1080", None)
