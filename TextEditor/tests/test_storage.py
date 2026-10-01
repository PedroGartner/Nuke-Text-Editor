import datetime
import os
import tempfile

from nuke_text_editor import history, snippets, templates, timelog


def test_templates_tokens():
    context = templates.build_context("/jobs/sh010/sh010_comp_v012.nk", frame=1043,
                                      first_frame=1001, last_frame=1100, fmt="HD_1080")
    assert context["shot"] == "sh010_comp" and context["version"] == "v012"
    text = templates.fill_tokens("{shot} {version} f{frame} {unknown}", context)
    assert text == "sh010_comp v012 f1043 {unknown}"
    folder = tempfile.mkdtemp()
    templates.ensure_default_templates(folder)
    names = [name for name, _path in templates.list_templates(folder)]
    assert "Daily Notes" in names and "Shot Checklist" in names


def test_history_snapshots():
    path = os.path.join(tempfile.mkdtemp(), "note.txt")
    first = history.add_snapshot(path, "one", now=1000000.0)
    assert first
    assert history.add_snapshot(path, "one", now=1000100.0) is None  # identical
    assert history.add_snapshot(path, "two", autosave=True, now=1000010.0) is None  # rate limit
    assert history.add_snapshot(path, "two", now=1000020.0)
    snaps = history.list_snapshots(path)
    assert [history.read_snapshot(p) for _t, p in snaps] == ["two", "one"]
    for i in range(history.MAX_SNAPSHOTS + 5):
        history.add_snapshot(path, "v%d" % i, now=1001000.0 + i)
    assert len(history.list_snapshots(path)) == history.MAX_SNAPSHOTS


def test_timelog_summary():
    data = {}
    timelog.add_seconds(data, "2026-09-28", "sh010_comp_v012.nk", 3600)
    timelog.add_seconds(data, "2026-09-29", "sh010_comp_v012.nk", 1800)
    timelog.add_seconds(data, "2026-09-29", "sh020_comp_v001.nk", 600)
    monday = timelog.week_start(datetime.date(2026, 9, 30))
    assert monday == datetime.date(2026, 9, 28)
    days, rows = timelog.week_summary(data, monday)
    assert days[0] == "2026-09-28" and len(days) == 7
    assert rows[0][0] == "sh010_comp_v012.nk" and rows[0][2] == 5400
    assert timelog.format_duration(5400) == "1:30"
    assert timelog.to_csv(days, rows).splitlines()[1].startswith('"sh010_comp_v012.nk",1:00,0:30')


def test_snippets_store():
    items = snippets.load_personal()
    assert snippets.find_by_trigger(items, "forsel", "python")["name"] == "Loop selected nodes"
    assert snippets.find_by_trigger(items, "todo", "python")["trigger"] == "todo"  # 'any'
    assert snippets.find_by_trigger(items, "forsel", "text") is None
    items.append({"name": "Mine", "trigger": "mine", "language": "any", "body": "x$0"})
    snippets.save_personal(items)
    assert snippets.find_by_trigger(snippets.load_personal(), "mine", "text")["body"] == "x$0"
