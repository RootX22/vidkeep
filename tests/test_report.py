import io
import json

from fixtures import course_info

from vidkeep import options, planner, report
from vidkeep.runner import Item, Runner, RunOptions


def events(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_dry_run_emits_start_plan_and_finished(tmp_path):
    out = io.StringIO()
    runner = Runner(planner.Request(), options.Settings(out_dir=str(tmp_path)), RunOptions(dry_run=True))
    runner.run([Item("https://example.com/v", info=course_info())], report.JsonReporter(out))
    kinds = [(e["event"], e.get("status")) for e in events(out)]
    assert kinds == [("start", None), ("plan", None), ("finished", "planned")]
    plan = events(out)[1]
    assert plan["format"] == "399+140" and plan["quality"] == "1080p av1" and plan["size"] > 0


def test_progress_lines_are_throttled_per_video(monkeypatch):
    out = io.StringIO()
    reporter = report.JsonReporter(out)
    clock = iter([0.0, 0.1, 0.2, 0.3, 0.6])
    monkeypatch.setattr(report.time, "monotonic", lambda: next(clock))
    for n in range(5):
        reporter.advanced(7, n * 100, 1000.0)
    assert [e["downloaded"] for e in events(out)] == [0, 300, 400]


def test_every_line_is_one_json_object():
    out = io.StringIO()
    reporter = report.JsonReporter(out)

    class Result:
        item = Item("u")
        title = 'a "quoted"\ntitle'
        status, path, error, seconds = "failed", None, "boom", 1.25

    handle = reporter.start(Result)
    reporter.finished(handle, Result)
    lines = out.getvalue().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["title"] == 'a "quoted"\ntitle'


def test_short_titles():
    assert report.short_title("x" * 10) == "x" * 10
    assert len(report.short_title("x" * 100)) == report.TITLE_WIDTH
