from nuke_text_editor import nuke_bridge


def test_outside_nuke_everything_is_safe():
    if nuke_bridge.available():
        return  # running inside Nuke
    assert nuke_bridge.script_path() is None
    assert nuke_bridge.current_frame() is None
    assert nuke_bridge.selected_nodes() == []
    assert nuke_bridge.editable_knobs("Grade1") == []
    assert nuke_bridge.get_note("Grade1") is None
    assert nuke_bridge.menu_commands() == []
    assert nuke_bridge.capture_viewer("/tmp/x.jpg")[0] is False


def test_run_python_output_and_errors():
    out, err, line = nuke_bridge.run_python("print('hi')\nx_test_value = 41 + 1", "<editor:t>")
    assert out == "hi\n" and err == "" and line is None
    import __main__
    assert __main__.x_test_value == 42

    out, err, line = nuke_bridge.run_python("a = 1\nb = 2\nraise ValueError('boom')", "<editor:t>")
    assert "ValueError: boom" in err and line == 3

    out, err, line = nuke_bridge.run_python("def f(:\n  pass", "<editor:t>")
    assert "SyntaxError" in err and line == 1

    # Selection offset: leading newlines keep line numbers matching the file.
    out, err, line = nuke_bridge.run_python("\n\n\n1/0", "<editor:t>")
    assert "ZeroDivisionError" in err and line == 4
