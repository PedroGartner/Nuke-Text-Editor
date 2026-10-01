"""Hooks the editor into Nuke: menus, the dockable panel, script
callbacks, the node-note marker and the time tracker.

Call install() once from menu.py.
"""

import sys

from . import nuke_bridge
from .settings import Settings

PANEL_ID = "uk.co.pgartner.TextEditor"
_installed = False


def _windows():
    from . import main_window
    return main_window.live_windows()


def _editor():
    """The open editor, or a new floating one."""
    windows = _windows()
    if windows:
        return windows[0]
    return show_editor()


# ------------------------------------------------------------- #
#  Commands used by Nuke's menus                                #
# ------------------------------------------------------------- #
def show_editor():
    import nuke_text_editor
    return nuke_text_editor.show_texteditor()


def edit_selected_node_note():
    _editor().edit_node_note()


def edit_selected_knob():
    _editor().edit_selected_knob()


def edit_script_note():
    _editor().edit_script_note()


def open_shot_notes():
    _editor().open_shot_notes()


def capture_viewer():
    _editor().capture_viewer()


def show_time_summary():
    _editor().show_time_summary()


def edit_selected_group():
    _editor().edit_selected_group()


def end_of_day_report():
    _editor().end_of_day_report()


def show_plates():
    _editor().show_plates()


def check_writes():
    from . import shot_panels
    names = nuke_bridge.write_names(True) or nuke_bridge.write_names(False)
    shot_panels.show_check(names, _main_window(), shot_panels.expectations(Settings()))


def check_writes_before_send(nodes=None, parent=None, action_label="Send Anyway", respect_setting=True):
    """Pre-render check for other tools (e.g. a render queue's send command).

    nodes: Nuke nodes or full names; None = the selected Write nodes.
    Returns True when the send / render should go ahead (always, when the
    check is switched off in Preferences)."""
    from . import shot_panels
    settings = Settings()
    if respect_setting and not settings.get_bool("render_check/before_batch_gartner"):
        return True
    if nodes is None:
        names = nuke_bridge.write_names(True)
    else:
        names = [n if isinstance(n, str) else n.fullName() for n in nodes]
    return shot_panels.confirm_render(names, parent or _main_window(), shot_panels.expectations(settings),
                                      action_label)


def _main_window():
    from .qt import QtWidgets
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    return app.activeWindow()


# ------------------------------------------------------------- #
#  Callbacks                                                    #
# ------------------------------------------------------------- #
def _on_script_load():
    try:
        settings = Settings()
        windows = _windows()
        if not windows:
            script = nuke_bridge.script_path()
            wants_notes = False
            if script and settings.get_bool("auto_open_shot_notes"):
                import os
                from . import textops
                wants_notes = os.path.isfile(textops.shot_notes_path(script))
            note = nuke_bridge.get_script_note() if settings.get_bool("open_script_note_on_load") else ""
            if settings.get_bool("auto_show_editor_for_notes") and (wants_notes or (note or "").strip()):
                windows = [show_editor()]
        for window in windows:
            window.on_script_loaded()
        if not windows and settings.get_bool("plates/check_on_load"):
            _check_plates_in_background()
    except Exception:
        pass  # never break script loading


_plate_checker = None
_open_boxes = []


def _check_plates_in_background():
    """Plate check while the editor is closed: a small message when a
    Read can be updated."""
    global _plate_checker
    from . import shot_panels

    def found(updates, count):
        from .qt import QtWidgets
        box = QtWidgets.QMessageBox(_main_window())
        box.setWindowTitle("Newer Plates")
        names = ", ".join(u[0]["name"].split(".")[-1] for u in updates[:5]) + (" ..." if len(updates) > 5 else "")
        box.setText("{} Read{} can be updated to a newer version:\n{}".format(
            len(updates), "" if len(updates) == 1 else "s", names))
        show = box.addButton("Show", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("Later", QtWidgets.QMessageBox.RejectRole)
        box.setModal(False)

        def clicked(button):
            if button is show:
                window = show_editor()
                if window is not None:
                    window.show_plates(updates, count)

        box.buttonClicked.connect(clicked)
        box.show()
        _open_boxes[:] = [box]  # keep it alive while it is shown

    if _plate_checker is None:
        _plate_checker = shot_panels.PlateCheckOnLoad(found)
    _plate_checker.start()


def _before_render():
    """Nuke beforeRender callback (only acts when enabled in Preferences)."""
    if not nuke_bridge.is_gui() or not Settings().get_bool("render_check/before_local_render"):
        return
    name = nuke_bridge.this_node_name()
    if name and not check_writes_before_send([name], action_label="Render Anyway", respect_setting=False):
        raise RuntimeError("Render cancelled by the pre-render check.")


def _on_script_save():
    try:
        for window in _windows():
            window._outline_timer.start()
    except Exception:
        pass


# ------------------------------------------------------------- #
#  Install                                                      #
# ------------------------------------------------------------- #
def install():
    """Add menus, register the dockable panel and callbacks. Safe to call twice."""
    global _installed
    if _installed or not nuke_bridge.available():
        return
    nuke = nuke_bridge.nuke
    # Survives module reloads (auto-loaders): menus and callbacks are added once.
    if getattr(nuke, "_pg_text_editor_installed", False):
        _installed = True
        return
    _installed = True
    nuke._pg_text_editor_installed = True
    settings = Settings()

    nuke_bridge.add_menu_command("Nuke", "PGartner/Text Editor", show_editor)
    for label, command in (
            ("PGartner/Text Editor Tools/Open Shot Notes", open_shot_notes),
            ("PGartner/Text Editor Tools/Script Note", edit_script_note),
            ("PGartner/Text Editor Tools/Time per Script...", show_time_summary),
            ("PGartner/Text Editor Tools/End-of-Day Report", end_of_day_report),
            ("PGartner/Text Editor Tools/Check Plate Versions", show_plates),
            ("PGartner/Text Editor Tools/Check Writes Before Render", check_writes)):
        nuke_bridge.add_menu_command("Nuke", label, command)

    for label, command in (
            ("Text Editor/Note on Selected Node", edit_selected_node_note),
            ("Text Editor/Edit Knob in Text Editor...", edit_selected_knob),
            ("Text Editor/Capture Viewer into Note", capture_viewer),
            ("Text Editor/Edit Group as Text", edit_selected_group)):
        nuke_bridge.add_menu_command("Node Graph", label, command)

    nuke_bridge.register_panel("nuke_text_editor.create_panel", "Text Editor", PANEL_ID)
    nuke_bridge.add_script_callbacks(_on_script_load, _on_script_save)
    nuke_bridge.add_before_render(lambda: sys.modules[__name__]._before_render())

    if settings.get_bool("mark_nodes_with_notes"):
        nuke_bridge.install_note_marker()
    if settings.get_bool("time_tracking"):
        from . import time_tracker
        time_tracker.start()
