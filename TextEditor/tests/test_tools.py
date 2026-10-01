import datetime
import json
import os
import socket
import tempfile
import threading

from nuke_text_editor import bgartner, dailylog, lint, locks, scripttools, textops, themes


def _node(name, cls, dependents=1, inputs=1, **knobs):
    return scripttools.SceneNode(name, cls, {k: str(v) for k, v in knobs.items()},
                                 dependents=dependents, connected_inputs=inputs)


def test_health_checks():
    folder = tempfile.mkdtemp()
    open(os.path.join(folder, "plate.1001.exr"), "w").close()
    nodes = [
        _node("Read1", "Read", file=os.path.join(folder, "plate.####.exr")),
        _node("Read2", "Read", file=os.path.join(folder, "missing.####.exr")),
        _node("Write1", "Write", dependents=0, file=""),
        _node("Blur1", "Blur", size=400),
        _node("Grade1", "Grade", disable="true"),
        _node("Grade2", "Grade", dependents=0, inputs=0),
        _node("Grade3", "Grade", dependents=0, inputs=1),
        _node("Viewer1", "Viewer", dependents=0),
    ]
    nodes[0].has_error = False
    issues = scripttools.analyze(nodes)
    found = [(i.node, i.severity) for i in issues]
    assert ("Read2", "error") in found and ("Write1", "error") in found
    assert ("Read1", "error") not in found
    assert ("Blur1", "warning") in found and ("Grade1", "warning") in found
    assert ("Grade2", "info") in found and ("Grade3", "info") in found
    assert not any(n == "Viewer1" for n, _s in found)
    assert [i.severity for i in issues] == sorted([i.severity for i in issues],
                                                  key=lambda s: scripttools.SEVERITY_ORDER[s])


def test_node_search():
    nodes = [_node("Grade1", "Grade", white="1.2"), _node("Keylight", "OFXkeylight", screenColour="0 1 0"),
             _node("Read1", "Read", file="/jobs/sh010/plate.exr")]
    assert [n.name for n, _w in scripttools.search(nodes, "grade")] == ["Grade1"]
    assert [n.name for n, _w in scripttools.search(nodes, "ofx")] == ["Keylight"]
    hits = scripttools.search(nodes, "sh010")
    assert hits[0][0].name == "Read1" and hits[0][1].startswith("file =")
    assert scripttools.search(nodes, "sh010", in_knobs=False) == []


def test_lint():
    source = ("import os\nimport sys\nfrom nuke import thisNode\n\n"
              "def f(a, *rest, **kw):\n    for i in range(a):\n        print(i, undefined_thing, os.sep)\n"
              "    try:\n        pass\n    except ValueError as err:\n        print(err)\n"
              "    return [x for x in rest]\n\nprint(nuke.root())\n")
    messages = lint.lint(source)
    texts = [m.text for m in messages]
    assert "Undefined name 'undefined_thing'" in texts
    assert "Undefined name 'nuke'" in texts
    assert "'sys' imported but not used" in texts and "'thisNode' imported but not used" in texts
    assert not any("'os'" in t for t in texts)
    assert not any("'nuke'" in m.text for m in lint.lint(source, extra_names=["nuke"]))
    error = lint.lint("def f(:\n")
    assert error[0].severity == "error" and error[0].line == 1


def test_task_meta():
    today = datetime.date(2026, 9, 30)  # a Wednesday
    assert textops.task_meta("- [ ] fix edges @pedro @anna due:friday", today) == (
        ["pedro", "anna"], datetime.date(2026, 10, 2))
    assert textops.parse_due("today", today) == today
    assert textops.parse_due("tomorrow", today) == datetime.date(2026, 10, 1)
    assert textops.parse_due("wed", today) == today
    assert textops.parse_due("2026-12-01", today) == datetime.date(2026, 12, 1)
    assert textops.parse_due("03/10", today) == datetime.date(2026, 10, 3)
    assert textops.parse_due("nonsense", today) is None
    assert textops.task_meta("mail me@example.com", today) == ([], None)


def test_daily_log_report():
    path = os.path.join(tempfile.mkdtemp(), "daily.json")
    when = 1790000000.0
    dailylog.record_done("Roto the hair", "/n/sh010.tnote", when, path)
    dailylog.record_done("Roto the hair", "/n/sh010.tnote", when, path)  # duplicate ignored
    dailylog.record_done("Grain", "", when, path)
    dailylog.record_undone("Grain", "", when, path)
    dailylog.record_touched("/n/sh010.tnote", when, path)
    data = dailylog.load(path)
    day = list(data)[0]
    assert [d["text"] for d in data[day]["done"]] == ["Roto the hair"]
    report = dailylog.report_html(day, data, {day: {"sh010_comp_v012.nk": 5400}}, "pedro")
    assert "Roto the hair" in report and "1:30" in report and "sh010_comp_v012.nk" in report


def test_locks():
    folder = tempfile.mkdtemp()
    note = os.path.join(folder, "shared.tnote")
    open(note, "w").close()
    assert locks.foreign_lock(note) is None
    assert locks.write_lock(note)
    assert locks.foreign_lock(note) is None  # our own marker
    data = locks.read_lock(note)
    data["user"] = "someone_else"
    with open(locks.lock_path(note), "w") as handle:
        json.dump(data, handle)
    assert locks.foreign_lock(note)["user"] == "someone_else"
    assert locks.foreign_lock(note, now=data["time"] + locks.STALE_SECONDS + 1) is None
    locks.remove_lock(note)  # not ours: must stay
    assert os.path.exists(locks.lock_path(note))


def test_themes_complete():
    keys = set(themes.THEMES[themes.DEFAULT])
    for name, theme in themes.THEMES.items():
        assert set(theme) == keys, name
    themes.set_current("Light")
    assert themes.color("editor") == "#ffffff" and "QPushButton" in themes.button_style()
    themes.set_current("nope")
    assert themes.current_name() == themes.DEFAULT


def test_batch_gartner_protocol():
    folder = tempfile.mkdtemp()
    logs = os.path.join(folder, "renderlogs")
    os.makedirs(logs)
    cfg_path = os.path.join(folder, "cfg.json")
    with open(cfg_path, "w") as handle:
        json.dump({"receiver_port": 55555, "paths": {"log_folder": logs}}, handle)
    cfg = bgartner.config(cfg_path)
    assert bgartner.receiver_port(cfg) == 55555
    assert bgartner.find_log_folder(cfg) == logs
    for name in ("sh010_comp_v012_Write1.log", "sh020_Write1.log"):
        open(os.path.join(logs, name), "w").close()
    found = bgartner.list_logs(logs)
    assert len(found) == 2
    assert [os.path.basename(p) for _t, p in bgartner.logs_matching(found, ["sh010_comp"])] == [
        "sh010_comp_v012_Write1.log"]

    received = {}
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        conn, _addr = server.accept()
        chunks = []
        while True:
            data = conn.recv(65536)
            if not data:
                break
            chunks.append(data)
        received["msg"] = json.loads(b"".join(chunks).decode("utf-8"))
        conn.sendall(b"OK 1 job")
        conn.close()

    thread = threading.Thread(target=serve)
    thread.start()
    payload = bgartner.build_payload("/jobs/sh010.nk", [{"write_node": "Write1", "output_path": "/r/o.####.exr",
                                                          "first": 1001, "last": 1100, "nuke_exe": "", "note": "hi"}])
    assert bgartner.send(payload, port=port)
    thread.join(5)
    server.close()
    job = received["msg"]["jobs"][0]
    assert received["msg"]["version"] == 1 and job["script_path"] == "/jobs/sh010.nk" and job["note"] == "hi"
    assert not bgartner.send(payload, port=port)  # nothing listening any more
