import datetime as dt
import shutil

from fixtures import course_info

from vidkeep import planner, preflight


def test_merging_needs_room_for_both_copies():
    plan = planner.choose(course_info(), planner.Request())
    assert preflight.space_needed(plan) == 2 * plan.size + preflight.SPACE_MARGIN


def test_single_stream_needs_its_own_size():
    plan = planner.choose(course_info(), planner.Request(audio_only=True))
    assert preflight.space_needed(plan) == plan.size + preflight.SPACE_MARGIN


def test_unknown_size_needs_unknown_space():
    stream = planner.Stream("1", "video", None, False, "https", "mp4", 720, "h264")
    assert preflight.space_needed(planner.Plan(video=stream)) is None


def test_free_space_of_a_folder_not_created_yet(tmp_path):
    assert preflight.free_space(str(tmp_path / "a" / "b" / "c")) == shutil.disk_usage(tmp_path).free


def test_ytdlp_age_from_its_version():
    assert preflight.ytdlp_age_days("2026.10.21", today=dt.date(2026, 10, 31)) == 10
    assert preflight.ytdlp_age_days("2026.10.21.232835", today=dt.date(2026, 10, 21)) == 0
    assert preflight.ytdlp_age_days("nightly") is None


def test_runtime_flags_only_for_runtimes_off_by_default():
    assert preflight.js_runtime_args(None) == []
    assert preflight.js_runtime_args("deno") == []
    assert preflight.js_runtime_args("node") == ["--js-runtimes", "node"]


def test_doctor_flags_missing_tools_as_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    checks = {c.name: c for c in preflight.doctor(str(tmp_path))}
    assert not checks["ffmpeg"].ok and checks["ffmpeg"].fatal
    assert not checks["JavaScript runtime"].ok and checks["JavaScript runtime"].fatal
    assert not checks["free space"].fatal


def test_doctor_warns_on_an_old_ytdlp(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "ytdlp_age_days", lambda: 400)
    check = next(c for c in preflight.doctor(str(tmp_path)) if c.name == "yt-dlp")
    assert not check.ok and not check.fatal and "pip install -U" in check.detail
