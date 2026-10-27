import pytest

from vidkeep import accel, cli, preflight
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

        def run(self, items):
            return []

    monkeypatch.setattr(cli, "Runner", FakeRunner)
    assert cli.main(["get", "--batch", str(batch)]) == 0
    assert seen["urls"] == ["https://a", "https://b"]


def test_doctor_exit_code_follows_fatal_checks(monkeypatch):
    monkeypatch.setattr(preflight, "doctor", lambda out: [preflight.Check("ffmpeg", False, "missing")])
    assert cli.main(["doctor"]) == 1
    monkeypatch.setattr(preflight, "doctor", lambda out: [preflight.Check("yt-dlp", False, "old", fatal=False)])
    assert cli.main(["doctor"]) == 0
