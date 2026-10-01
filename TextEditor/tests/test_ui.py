"""UI tests (Qt + pytest-qt). They run headless in CI with
QT_QPA_PLATFORM=offscreen; conftest.py skips them when Qt or pytest-qt
is missing. The `window` fixture is defined in conftest.py."""

import os
import tempfile

from nuke_text_editor import main_window
from nuke_text_editor.code_editor import AdvancedCodeEditor
from nuke_text_editor.qt import QtGui


def _tmp_file(name, content):
    path = os.path.join(tempfile.mkdtemp(), name)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    return path


def test_has_scratchpad_and_untitled_tab(window):
    names = [window._display_name(e) for e in window.editors()]
    assert "Scratchpad" in names and "Untitled" in names


def test_open_edit_save_keeps_line_endings(window):
    path = _tmp_file("notes.txt", "a\r\nb\r\n")
    editor = window.open_path(path)
    assert editor is not None and not editor.rich
    editor.moveCursor(QtGui.QTextCursor.End)
    editor.insertPlainText("c\n")
    assert editor.is_modified
    assert window.save_editor(editor)
    with open(path, "rb") as handle:
        assert handle.read() == b"a\r\nb\r\nc\r\n"
    assert not editor.is_modified


def test_opening_same_file_twice_reuses_tab(window):
    path = _tmp_file("x.py", "print(1)\n")
    first = window.open_path(path)
    second = window.open_path(path)
    assert first is second


def test_python_mode_and_run(window):
    path = _tmp_file("script.py", "value = 6 * 7\nprint(value)\n")
    editor = window.open_path(path)
    assert editor.language == "python" and editor.code_mode
    window.run_file()
    assert "42" in window.output_panel.view.toPlainText()


def test_run_error_marks_line(window):
    editor = window.create_tab()
    editor.setPlainText("a = 1\nraise ValueError('x')\n")
    window.run_file()
    assert editor.error_line == 1


def test_find_replace_all_with_groups(window):
    editor = window.create_tab()
    editor.setPlainText("sh010_v001 sh020_v002")
    panel = window.find_panel
    panel.find_input.setText(r"(sh\d+)_v(\d+)")
    panel.replace_input.setText(r"\1-\2")
    panel.regex_checkbox.setChecked(True)
    panel.replace_all()
    assert editor.toPlainText() == "sh010-001 sh020-002"


def test_toggle_comment_and_move_lines(window):
    path = _tmp_file("c.py", "a = 1\nb = 2\n")
    editor = window.open_path(path)
    editor.toggle_comment()
    assert editor.toPlainText().split("\n")[0] == "# a = 1"
    editor.toggle_comment()
    editor.move_lines(1)
    assert editor.toPlainText().split("\n")[:2] == ["b = 2", "a = 1"]


def test_rich_note_round_trip(window):
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "note.tnote")
    editor = window.create_tab()
    editor.setHtml("<p><b>bold</b> text</p>")
    editor.file_path = path
    window._apply_editor_mode(editor, path)
    assert window.save_editor(editor)
    editor2 = window.create_tab()
    window._apply_editor_mode(editor2, path)
    window._load_content(editor2, path, open(path, encoding="utf-8").read())
    assert "bold text" in editor2.toPlainText()
    assert main_window.document_has_formatting(editor2.document())


def test_snippet_expansion(window):
    editor = window.create_tab()
    editor.language = "python"
    editor.insertPlainText("forsel")
    assert editor._try_expand_snippet()
    assert editor.toPlainText().startswith("for node in nuke.selectedNodes():")


def test_editor_is_advanced(window):
    assert all(isinstance(e, AdvancedCodeEditor) for e in window.editors())


def test_split_view_moves_tabs(window):
    path = _tmp_file("a.py", "x = 1\n")
    editor = window.open_path(path)
    window.create_tab()
    window._focus_editor(editor)
    window.move_tab_to_other_side()
    assert window._tabs_of(editor) is window.split_tabs
    window.toggle_split()
    assert window._tabs_of(editor) is window.tab_widget
    assert not window.split_tabs.isVisible()


def test_theme_switch(window):
    from nuke_text_editor import themes
    themes.set_current("Light")
    window.apply_theme()
    themes.set_current("Dark")
    window.apply_theme()


def test_live_lint_marks(window):
    editor = window.create_tab()
    editor.language = "python"
    editor.setPlainText("import sys\nprint(undefined_name_xyz)\n")
    window._lint_editor(editor)
    assert editor.lint_marks.get(0) == "info" and editor.lint_marks.get(1) == "warning"


def test_console_prints_values(window):
    window.run_console_entry("6 * 7")
    assert "42" in window.output_panel.view.toPlainText()


def test_task_toggle_records_done(window):
    from nuke_text_editor import dailylog
    editor = window.create_tab()
    editor.setPlainText("- [ ] roto hair")
    editor.task_toggled.emit("roto hair", True)
    day = dailylog.load().get(dailylog.today(), {})
    assert any(d["text"] == "roto hair" for d in day.get("done", []))


def test_version_history_in_open_notes_tab(window):
    folder = tempfile.mkdtemp()
    script = os.path.join(folder, "sh010_comp_v013.nk")
    notes = os.path.join(folder, "sh010_comp_notes.tnote")
    with open(notes, "w", encoding="utf-8") as handle:
        handle.write("<p>Shot notes</p><p>Version history</p><p>v012 · 2026-09-29 · pedro - first pass</p><p>End</p>")
    editor = window.open_path(notes)
    window.add_to_version_history(script, "v013 · 2026-09-30 · pedro - edges")
    lines = [line for line in editor.toPlainText().split("\n") if line.strip()]
    assert lines == ["Shot notes", "Version history", "v012 · 2026-09-29 · pedro - first pass",
                     "v013 · 2026-09-30 · pedro - edges", "End"]
    assert not editor.is_modified  # saved, because the tab had no other edits
    with open(notes, encoding="utf-8") as handle:
        assert "v013" in handle.read()


def test_version_history_in_closed_notes_file(window):
    from nuke_text_editor import shot_panels
    folder = tempfile.mkdtemp()
    script = os.path.join(folder, "sh020_comp_v002.nk")
    path = shot_panels.add_history_to_file(script, "v002 · 2026-09-30 · pedro - new plate")
    with open(path, encoding="utf-8") as handle:
        content = handle.read()
    assert "Version history" in content and "new plate" in content


def test_plates_panel_shows_results(window):
    read = {"name": "Read1", "file": "/p/v001/a.exr", "raw": "/p/v001/a.exr"}
    window.show_plates([(read, "v001", "v002", "/p/v002/a.exr")], 3)
    tree = window.plates_panel.tree
    assert tree.topLevelItemCount() == 1 and tree.topLevelItem(0).text(2) == "v002"
