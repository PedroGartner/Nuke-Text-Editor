import os
import tempfile

from nuke_text_editor import dailylog, shotcheck


def _touch(path):
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    open(path, "w").close()


def test_version_tokens():
    assert shotcheck.last_version_token("/p/v003/sh010_v004.%04d.exr") == ("v004", 4)
    assert shotcheck.last_version_token("/p/dev/plate.exr") == (None, None)
    assert shotcheck.last_version_token("/p/prev01/plate.exr") == (None, None)
    assert shotcheck.replace_version_in("a/v012/b_v012.v0120.exr", "v012", "v013") == "a/v013/b_v013.v0120.exr"


def test_newer_versions_in_folders_on_disk():
    root = tempfile.mkdtemp()
    for version in ("v001", "v003", "v004", "v006"):
        _touch(os.path.join(root, "sh010", version, "sh010_plate_{}.1001.exr".format(version)))
    os.makedirs(os.path.join(root, "sh010", "v007"))  # empty: not a usable version
    current = os.path.join(root, "sh010", "v003", "sh010_plate_v003.%04d.exr")
    found = shotcheck.newer_versions(current)
    assert [tag for tag, _p in found] == ["v006", "v004"]
    assert found[0][1] == os.path.join(root, "sh010", "v006", "sh010_plate_v006.%04d.exr")
    newest = os.path.join(root, "sh010", "v006", "sh010_plate_v006.####.exr")
    assert shotcheck.newer_versions(newest) == []


def test_newer_versions_in_file_names():
    root = tempfile.mkdtemp()
    for name in ("bg_v002.1001.exr", "bg_v002.1002.exr", "bg_v003.1001.exr", "bg_v0030.1001.exr", "fg_v009.1001.exr"):
        _touch(os.path.join(root, name))
    found = shotcheck.newer_versions(os.path.join(root, "bg_v002.####.exr"))
    assert [tag for tag, _p in found] == ["v003"]
    assert shotcheck.newer_versions(os.path.join(root, "[value root.name].exr")) == []


def test_plate_updates_lists_each_folder_once():
    calls = []

    def listdir(folder):
        calls.append(folder)
        return ["v001", "v002"]

    reads = [{"name": "Read1", "file": "/plates/v001/a.exr"}, {"name": "Read2", "file": "/plates/v001/b.exr"}]
    updates = shotcheck.plate_updates(reads, exists=lambda p: True, listdir=listdir)
    assert [(r["name"], old, new) for r, old, new, _p in updates] == [("Read1", "v001", "v002"), ("Read2", "v001", "v002")]
    assert calls == ["/plates"]


def test_next_version_path():
    folder = tempfile.mkdtemp()
    script = os.path.join(folder, "sh010_comp_v012.nk")
    _touch(script)
    _touch(os.path.join(folder, "sh010_comp_v013.nk"))
    assert shotcheck.next_version_path(script) == os.path.join(folder, "sh010_comp_v014.nk")
    assert shotcheck.next_version_path(os.path.join(folder, "comp.nk")) is None


def _write(**changes):
    write = {"name": "Write1", "file": "/renders/sh010_comp_v012/sh010_comp_v012.%04d.exr", "file_type": "exr",
             "colorspace": "ACES - ACEScg", "use_limit": False, "first": 1001, "last": 1100,
             "create_directories": True, "has_error": False}
    write.update(changes)
    return write


def _root(**changes):
    root = {"first": 1001, "last": 1100, "proxy": False, "script": "/comp/sh010_comp_v012.nk"}
    root.update(changes)
    return root


def _plate(**changes):
    read = {"name": "Read1", "file": "[plate]", "first": 1001, "last": 1100, "frame_mode": "", "frame": "",
            "colorspace": "ACES - ACEScg", "disable": False}
    read.update(changes)
    return read


def _messages(issues):
    return [(i.node, i.severity, i.message) for i in issues]


def _check(write=None, root=None, upstream=None, tasks=(), expect=None, exists=lambda p: True,
           status=lambda p, d=None: "missing"):
    return shotcheck.check_write(write or _write(), root or _root(), upstream or {"reads": [_plate()]},
                                 tasks, expect, exists=exists, path_status=status)


def test_clean_write_has_no_issues():
    assert _check() == []


def test_write_version_type_and_folder():
    issues = _messages(_check(_write(file="/r/sh010_comp_v011.%04d.jpg"), exists=lambda p: False))
    assert ("Write1", "warning", "Output is v011 but the script is v012") in issues
    assert any(s == "error" and "File type is 'exr'" in m for _n, s, m in issues)
    issues = _messages(_check(_write(create_directories=False), exists=lambda p: False))
    assert any(s == "error" and "Output folder does not exist" in m for _n, s, m in issues)
    issues = _messages(_check(status=lambda p, d=None: "ok"))
    assert ("Write1", "info", "Output already has files; they will be overwritten") in issues
    assert any(s == "error" for _n, s, m in _messages(_check(_write(file=""))))


def test_frame_range_against_plates():
    issues = _messages(_check(root=_root(last=1110)))
    assert ("Write1", "warning", "Renders 1001-1110 but the plates cover 1001-1100") in issues
    issues = _messages(_check(_write(use_limit=True, first=1010, last=1050)))
    assert ("Write1", "info", "Renders 1010-1050 of the plate range 1001-1100") in issues
    issues = _check(root=_root(last=1110), upstream={"reads": [_plate()], "timing": ["TimeOffset1"]})
    assert issues[0].severity == "info" and "TimeOffset1" in issues[0].message
    offset = _plate(frame_mode="start at", frame="1", first=1001, last=1100)
    assert shotcheck.read_range(offset) == (1, 100)
    assert shotcheck.read_range(_plate(frame_mode="offset", frame="10")) == (1011, 1110)


def test_colorspace_and_studio_defaults():
    issues = _messages(_check(_write(colorspace="sRGB")))
    assert any(s == "info" and "differs from the plate Read1" in m for _n, s, m in issues)
    issues = _messages(_check(_write(colorspace="default (ACES - ACEScg)")))
    assert issues == []
    issues = _messages(_check(expect={"colorspace": "ACES - ACES2065-1", "file_type": "exr"}))
    assert ("Write1", "warning", "Colorspace is 'ACES - ACEScg', expected 'ACES - ACES2065-1'") in issues
    assert not any("Renders 'exr'" in m for _n, _s, m in issues)


def test_upstream_problems_and_tasks():
    upstream = {"reads": [_plate(file="/p/missing.%04d.exr")], "disabled": ["Grade3"], "errors": ["Blur1"]}
    issues = _messages(_check(upstream=upstream, tasks=["fix edges", "  "], root=_root(proxy=True)))
    assert ("Read1", "error", "Missing file: /p/missing.%04d.exr") in issues
    assert ("Blur1", "error", "Node has an error") in issues
    assert ("Grade3", "warning", "Disabled node in the tree of Write1") in issues
    assert ("Write1", "warning", "1 open task in the shot notes: fix edges") in issues
    assert ("Write1", "warning", "Proxy mode is on") in issues
    assert shotcheck.blocking(_check(upstream=upstream))
    assert not shotcheck.blocking(_check(status=lambda p, d=None: "ok"))
    assert shotcheck.open_tasks_in("- [ ] a\n- [x] b\nTODO: c\n* [ ] d") == ["a", "d"]


def test_history_entries():
    entry = shotcheck.history_entry("v013", "fixed edges\n  on hair", "pedro", when=0)
    assert entry.startswith("v013 · ") and entry.endswith(" · pedro - fixed edges on hair")
    assert shotcheck.history_insert_index(["Notes", "- [ ] a"]) == (2, True)
    blocks = ["Notes", "Version history", "v001 · a", "2026-09-30 · b", "Other"]
    assert shotcheck.history_insert_index(blocks) == (4, False)
    assert shotcheck.history_insert_index(["Version history"]) == (1, False)


def test_daily_report_lists_versions():
    path = os.path.join(tempfile.mkdtemp(), "daily.json")
    dailylog.record_version("/comp/sh010_comp_v013.nk", "v013", "fixed edges", when=0, path=path)
    day = next(iter(dailylog.load(path)))
    report = dailylog.report_html(day, daily=dailylog.load(path), times={})
    assert "sh010_comp_v013.nk" in report and "fixed edges" in report and "Versions (1)" in report
