import json
import sys

import pytest
from fixtures import course_info

from vidkeep import accel, cli, planner, preflight
from vidkeep.planner import Request


def parse(*argv):
    return cli.build_parser().parse_args(argv)


def test_get_defaults():
    args = parse("get", "https://example.com/v")
    assert (args.quality, args.codec, args.connections, args.jobs) == ("1080", "efficient", 4, 2)
    assert args.chunk_size == accel.DEFAULT_CHUNK_SIZE
    assert cli._request(args) == Request(max_height=1080)


def test_best_quality_has_no_cap():
    assert cli._request(parse("get", "u", "-q", "best")).max_height is None


def test_size_flags_accept_units():
    args = parse("get", "u", "--max-size", "1.5G", "--chunk-size", "4M")
    assert args.max_size == int(1.5 * 1024**3)
    assert args.chunk_size == 4 * 1024**2


@pytest.mark.parametrize("argv", [["--max-size", "huge"], ["-n", "0"], ["-j", "-1"], ["-q", "999"]])
def test_bad_values_are_usage_errors(argv):
    with pytest.raises(SystemExit) as exc:
        parse("get", "u", *argv)
    assert exc.value.code == 2


def test_get_without_urls_is_a_usage_error(capsys):
    assert cli.main(["get"]) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="the Firefox add-on works on macOS and Linux")
def test_browser_install_and_uninstall_write_and_remove_the_firefox_host(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert cli.main(["browser", "install"]) == 0
    [manifest] = tmp_path.rglob("io.github.rootx22.vidkeep.json")
    assert json.loads(manifest.read_text())["allowed_extensions"] == ["vidkeep@rootx22.github.io"]
    assert cli.main(["browser", "uninstall"]) == 0
    assert not manifest.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_browser_install_on_windows_says_it_is_not_supported_yet(capsys):
    assert cli.main(["browser", "install"]) == 1
    assert "macOS and Linux" in capsys.readouterr().err


def test_batch_file_skips_blanks_and_comments(tmp_path, monkeypatch):
    batch = tmp_path / "urls.txt"
    batch.write_text("# watch later\nhttps://a\n\n  https://b  \n")
    seen = {}

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        def resolve(self, urls):
            seen["urls"] = urls
            return []

        def run(self, items, reporter=None):
            return []

    monkeypatch.setattr(cli, "Runner", FakeRunner)
    assert cli.main(["get", "--batch", str(batch)]) == 0
    assert seen["urls"] == ["https://a", "https://b"]


def test_exact_format_and_json_flags():
    args = parse("get", "u", "-f", "399+140", "--json")
    assert cli._request(args).formats == "399+140"
    assert args.json


def test_info_payload_gives_a_ui_everything_it_shows():
    info = {
        **course_info(),
        "channel": "EmbarkX",
        "thumbnail": "https://i.ytimg.com/x.jpg",
        "duration_string": "11:31:41",
    }
    payload = cli._info_payload(info, planner.menu(info), playlist_count=1)
    assert payload["default_format"] == "399+140"
    assert payload["audio_only"] == {"format": "140", "size": 640 * 1024**2}
    assert payload["options"][0] == {
        "height": 1080,
        "codec": "av1",
        "format": "399+140",
        "size": int(1.12 * 1024**3) + 640 * 1024**2,
        "exact": True,
        "quicktime": "M3 or newer",
    }
    assert (payload["channel"], payload["playlist_count"]) == ("EmbarkX", 1)


def test_extraction_errors_print_cleanly_with_a_cookie_hint(monkeypatch, capsys):
    class BlockedRunner:
        def __init__(self, *args, **kwargs):
            pass

        def resolve(self, urls):
            raise RuntimeError("[youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser")

    monkeypatch.setattr(cli, "Runner", BlockedRunner)
    assert cli.main(["info", "https://youtu.be/abc"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "error: [youtube] abc: Sign in" in err
    assert "--cookies-from-browser firefox" in err


def test_sigterm_takes_the_ctrl_c_path():
    with pytest.raises(KeyboardInterrupt):
        cli._interrupt(15, None)


def test_doctor_exit_code_follows_fatal_checks(monkeypatch):
    monkeypatch.setattr(preflight, "doctor", lambda out: [preflight.Check("ffmpeg", False, "missing")])
    assert cli.main(["doctor"]) == 1
    monkeypatch.setattr(preflight, "doctor", lambda out: [preflight.Check("yt-dlp", False, "old", fatal=False)])
    assert cli.main(["doctor"]) == 0
