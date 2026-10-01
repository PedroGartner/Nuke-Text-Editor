from nuke_text_editor import links


def kinds(text, notes=True):
    return [(kind, value) for _s, _e, kind, value in links.find_links(text, notes)]


def test_urls_and_paths():
    assert kinds("see https://example.com/a.") == [("url", "https://example.com/a")]
    assert kinds("plate /jobs/sh010/plate.####.exr ok") == [("path", "/jobs/sh010/plate.####.exr")]
    assert kinds(r"C:\shots\sh010\comp.nk,") == [("path", r"C:\shots\sh010\comp.nk")]
    assert kinds(r"\\server\share\file.exr") == [("path", r"\\server\share\file.exr")]
    assert kinds("~/notes/todo.txt") == [("path", "~/notes/todo.txt")]


def test_not_paths():
    assert kinds("a/b and 1/2 and x // y") == []
    assert kinds("just /word here") == []


def test_frames_and_nodes_only_in_notes():
    assert kinds("check f1043 and frame 1100, fr12") == [("frame", 1043), ("frame", 1100), ("frame", 12)]
    assert kinds("fix [[Grade3]] edge") == [("node", "Grade3")]
    assert kinds("check f1043 [[Grade3]]", notes=False) == []
    assert kinds("buff1043 elf12") == []


def test_link_at():
    text = "look at f1043 please"
    assert links.link_at(text, 9) == ("frame", 1043)
    assert links.link_at(text, 2) is None
