"""Nuke Text Editor.

Usage from menu.py:

    import nuke_text_editor
    nuke_text_editor.install()
"""

import importlib
import sys
import types

__version__ = "2.2.0"


def _stale_modules():
    """Modules of this package that still hold classes or functions from
    an older copy of another module of the package.

    Tool loaders that reload every .py file (in folder order) leave the
    package in that state: 'find_panel' keeps the HoverButton of the
    'widgets' module as it was before 'widgets' was reloaded.
    """
    prefix = __name__ + "."
    stale = []
    for name, module in list(sys.modules.items()):
        if not name.startswith(prefix) or module is None:
            continue
        for attr, value in list(vars(module).items()):
            if attr.startswith("__") or isinstance(value, types.ModuleType):
                continue
            source_name = getattr(value, "__module__", None)
            if not isinstance(source_name, str) or source_name == name or not source_name.startswith(prefix):
                continue
            if not isinstance(value, (type, types.FunctionType)):
                continue
            source = sys.modules.get(source_name)
            current = getattr(source, getattr(value, "__name__", attr), None) if source is not None else None
            if current is not None and current is not value and type(current) is type(value):
                stale.append(module)
                break
    return stale


def refresh_modules(max_passes=12):
    """Reload stale modules until every module sees the current version
    of the others. Returns the number of reloads."""
    reloads = 0
    for _ in range(max_passes):
        stale = _stale_modules()
        if not stale:
            break
        for module in stale:
            try:
                importlib.reload(module)
                reloads += 1
            except Exception:
                pass
    return reloads


def show_texteditor():
    """Show the editor window, or bring the open one to the front."""
    refresh_modules()
    from . import main_window
    return main_window.show_texteditor()


def create_panel():
    """Widget of the dockable panel (Pane > Text Editor)."""
    refresh_modules()
    from . import main_window
    return main_window.TextEditorPanel()


def check_writes(nodes=None, parent=None, action_label="Send Anyway"):
    """Pre-render check of Write nodes (Nuke nodes or full names; None =
    the selected Writes). Asks only when something looks wrong and returns
    True when the render should go ahead. Meant for render-queue tools:

        if not nuke_text_editor.check_writes(nodes):
            return
    """
    refresh_modules()
    from . import integration
    return integration.check_writes_before_send(nodes, parent, action_label)


def install():
    """Add menus, the dockable panel and Nuke callbacks (call from menu.py)."""
    refresh_modules()
    from . import integration
    integration.install()
