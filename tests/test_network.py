"""End-to-end runs against YouTube. Skipped by default; run with ``pytest -m network``."""

import json
import shutil
import subprocess

import pytest

from vidkeep import cli

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(not shutil.which("ffprobe"), reason="needs ffmpeg"),
]

# Big Buck Bunny, Blender Foundation, licensed CC BY 3.0.
VIDEO = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def probe(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def test_parallel_download_merges_into_a_playable_file(tmp_path):
    # A 1 MiB chunk forces many parallel ranges even on a short video.
    code = cli.main(["get", VIDEO, "-q", "360", "-c", "compatible", "--chunk-size", "1M", "-o", str(tmp_path)])
    assert code == 0
    [video] = tmp_path.glob("*.mp4")
    media = probe(video)
    streams = {(s["codec_type"], s["codec_name"]) for s in media["streams"]}
    assert ("video", "h264") in streams and ("audio", "aac") in streams
    assert abs(float(media["format"]["duration"]) - 634.6) < 2
    assert not [p for p in tmp_path.iterdir() if p.suffix in (".part", ".vidkeep", ".vidkeep-lock")]


def test_audio_only(tmp_path):
    code = cli.main(["get", VIDEO, "--audio-only", "--audio-quality", "low", "-o", str(tmp_path)])
    assert code == 0
    [audio] = tmp_path.glob("*.m4a")
    assert {s["codec_type"] for s in probe(audio)["streams"]} >= {"audio"}
