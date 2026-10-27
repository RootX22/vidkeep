import io

from fixtures import course_info
from rich.console import Console

from vidkeep import options, planner, preflight
from vidkeep.runner import Item, Runner, RunOptions, _folder_name, _SpaceLedger


def quiet_console():
    return Console(file=io.StringIO(), width=120)


def make_runner(tmp_path, request=None, **run_options):
    settings = options.Settings(out_dir=str(tmp_path))
    return Runner(request or planner.Request(), settings, RunOptions(**run_options), quiet_console())


def test_dry_run_plans_without_downloading(tmp_path):
    results = make_runner(tmp_path, dry_run=True).run([Item("u", info=course_info())])
    assert results[0].status == "planned"
    assert results[0].plan.format_spec == "399+140"
    assert list(tmp_path.iterdir()) == []


def test_a_plan_picked_in_advance_is_kept(tmp_path):
    fixed = planner.choose(course_info(), planner.Request(codec="compatible"))
    results = make_runner(tmp_path, dry_run=True).run([Item("u", info=course_info(), plan=fixed)])
    assert results[0].plan.format_spec == "137+140"


def test_no_match_fails_that_video_only(tmp_path):
    runner = make_runner(tmp_path, dry_run=True, request=planner.Request(max_size=1024))
    good = Item("ok", info=course_info(), plan=planner.choose(course_info(), planner.Request()))
    results = runner.run([Item("bad", info=course_info()), good])
    assert [r.status for r in results] == ["failed", "planned"]
    assert "smallest option" in results[0].error


def test_refuses_to_start_without_room_for_download_and_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "free_space", lambda path: 1024**3)
    results = make_runner(tmp_path).run([Item("u", info=course_info())])
    assert results[0].status == "failed"
    assert "not enough disk space" in results[0].error


def test_space_ledger_counts_downloads_still_running(monkeypatch):
    monkeypatch.setattr(preflight, "free_space", lambda path: 100)
    ledger = _SpaceLedger()
    assert ledger.reserve(".", 60) == (True, 100)
    assert ledger.reserve(".", 60) == (False, 40)
    ledger.release(60)
    assert ledger.reserve(".", 60)[0]


def test_playlist_folder_names_are_safe():
    assert _folder_name('Course: Part 1/2 "intro"?') == "Course_ Part 1_2 _intro_"
    assert _folder_name("  ") == "playlist"
