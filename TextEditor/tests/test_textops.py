import os
import tempfile

from nuke_text_editor import textops


def test_checkbox_index():
    line = "  - [ ] do roto"
    box = line.index("[")
    assert textops.checkbox_index(line, box) == box + 1
    assert textops.checkbox_index(line, box + 2) == box + 1
    assert textops.checkbox_index(line, 12) == -1
    assert textops.checkbox_index("[ ] no bullet", 1) == -1
    assert textops.checkbox_index("1. [x] numbered", 4) == 4


def test_count_and_open_items():
    text = "- [ ] a\n- [x] b\n* [X] c\nTODO: grain\nplain\n# FIXME check edges"
    assert textops.count_tasks(text) == (2, 3)
    items = textops.open_items(text)
    assert [(n, k) for n, k, _t in items] == [(0, "task"), (3, "TODO"), (5, "FIXME")]
    assert items[1][2] == "grain"


def test_toggle_comment():
    lines = ["def f():", "    return 1", "", "  x = 2"]
    commented = textops.toggle_comment_lines(lines, "#")
    assert commented == ["# def f():", "#     return 1", "", "#   x = 2"]
    assert textops.toggle_comment_lines(commented, "#") == lines
    mixed = ["# a", "b"]
    assert textops.toggle_comment_lines(mixed, "#") == ["# # a", "# b"]


def test_versions():
    assert textops.split_version("sh010_comp_v012") == ("sh010_comp", "v012", 12)
    assert textops.split_version("sh010_v3_comp") == ("sh010_comp", "v3", 3)
    assert textops.split_version("notes") == ("notes", "", None)
    folder = tempfile.mkdtemp()
    for name in ("sh010_comp_v008.nk", "sh010_comp_v010.nk", "sh010_comp_v012.nk", "other_v011.nk"):
        open(os.path.join(folder, name), "w").close()
    current = os.path.join(folder, "sh010_comp_v012.nk")
    assert textops.previous_version_path(current) == os.path.join(folder, "sh010_comp_v010.nk")
    assert textops.previous_version_path(os.path.join(folder, "sh010_comp_v008.nk")) is None
    assert textops.shot_notes_path(current) == os.path.join(folder, "sh010_comp_notes.tnote")


def test_fuzzy():
    assert textops.fuzzy_score("sav", "Save As") is not None
    assert textops.fuzzy_score("xyz", "Save") is None
    assert textops.fuzzy_score("save", "Save") > textops.fuzzy_score("save", "File/Save All Tabs")
    assert textops.fuzzy_score("svas", "Save As") is not None


def test_snippet_expand():
    text, offset = textops.expand_snippet("for n in x:\n    $0", "  ")
    assert text == "for n in x:\n      "
    assert offset == len(text)
    text, offset = textops.expand_snippet("a$0b")
    assert (text, offset) == ("ab", 1)
    assert textops.word_before("    forsel") == "forsel"
    assert textops.word_before("x = ") == ""
