"""Qt import and small PySide2 / PySide6 compatibility helpers."""

import sys


def _binding_name():
    """Use the Qt binding the host application already runs on.

    Nuke 13-15 run on PySide2, Nuke 16+ on PySide6. Importing the other
    binding (for example a PySide6 found on a shared Python path) would
    load a second Qt into Nuke and crash it, so the order matters.
    """
    try:
        import nuke
        major = int(nuke.NUKE_VERSION_MAJOR)
    except Exception:
        major = None
    if major is not None:
        return "PySide6" if major >= 16 else "PySide2"
    for name in ("PySide6", "PySide2"):
        if name + ".QtWidgets" in sys.modules:
            return name
    return None


_BINDING = _binding_name()
if _BINDING == "PySide2":
    from PySide2 import QtCore, QtGui, QtWidgets
    QT6 = False
elif _BINDING == "PySide6":
    from PySide6 import QtCore, QtGui, QtWidgets
    QT6 = True
else:
    # Outside Nuke (standalone use, tests): prefer PySide6.
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        QT6 = True
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
        QT6 = False

__all__ = ["QtCore", "QtGui", "QtWidgets", "QT6", "qt_exec", "event_pos",
           "modifier_state", "font_families", "SignalBlocker", "parse_shortcut",
           "format_font_family", "set_format_font_family"]


def qt_exec(obj, *args):
    """Call exec() on dialogs, menus and drags in both PySide2 and PySide6."""
    method = getattr(obj, "exec", None) or getattr(obj, "exec_")
    return method(*args)


def event_pos(event):
    """Mouse / wheel event position as a QPoint."""
    if hasattr(event, "position"):
        try:
            return event.position().toPoint()
        except Exception:
            pass
    return event.pos()


def modifier_state(event):
    """Return (ctrl, shift, alt) booleans for a key or mouse event."""
    mods = event.modifiers()
    return (
        bool(mods & QtCore.Qt.ControlModifier),
        bool(mods & QtCore.Qt.ShiftModifier),
        bool(mods & QtCore.Qt.AltModifier),
    )


def font_families():
    try:
        return QtGui.QFontDatabase.families()
    except TypeError:
        return QtGui.QFontDatabase().families()


def format_font_family(fmt):
    """Font family explicitly set on a QTextCharFormat, or "".

    QTextCharFormat.fontFamily() is deprecated in Qt 6 and crashes in
    Nuke 16's PySide6 on formats without a family, so the family is read
    through font() and only when the format actually sets one.
    """
    props = [QtGui.QTextFormat.FontFamily]
    if hasattr(QtGui.QTextFormat, "FontFamilies"):
        props.append(QtGui.QTextFormat.FontFamilies)
    if not any(fmt.hasProperty(prop) for prop in props):
        return ""
    return fmt.font().family()


def set_format_font_family(fmt, family):
    """Set the font family on a QTextCharFormat (Qt 5 and Qt 6)."""
    if hasattr(fmt, "setFontFamilies"):
        fmt.setFontFamilies([family])
    else:
        fmt.setFontFamily(family)


class SignalBlocker(object):
    """Context manager that blocks signals of several widgets."""

    def __init__(self, *widgets):
        self.widgets = widgets

    def __enter__(self):
        for widget in self.widgets:
            widget.blockSignals(True)
        return self

    def __exit__(self, *_exc):
        for widget in self.widgets:
            widget.blockSignals(False)
        return False


_SPECIAL_KEYS = {
    "Tab": "Key_Tab", "Backtab": "Key_Backtab", "Up": "Key_Up", "Down": "Key_Down",
    "Left": "Key_Left", "Right": "Key_Right", "Return": "Key_Return", "Enter": "Key_Enter",
    "Space": "Key_Space", "Esc": "Key_Escape", "/": "Key_Slash", "=": "Key_Equal",
    "-": "Key_Minus", "+": "Key_Plus", "Del": "Key_Delete", "Ins": "Key_Insert",
    "PgUp": "Key_PageUp", "PgDown": "Key_PageDown", "Home": "Key_Home", "End": "Key_End",
    "Backspace": "Key_Backspace", "Delete": "Key_Delete", "\\": "Key_Backslash", "`": "Key_QuoteLeft",
}


def parse_shortcut(text):
    """Parse 'Ctrl+Shift+S' into (key, ctrl, shift, alt). Returns None if unknown.

    Ctrl+Shift+Tab is mapped to Key_Backtab because that is what Qt sends.
    """
    if not text:
        return None
    parts = text.split("+")
    # "Ctrl++" splits into ['Ctrl', '', ''] -> key '+'
    if text.endswith("++"):
        parts = parts[:-2] + ["+"]
    key_name = parts[-1]
    mods = set(p.lower() for p in parts[:-1])
    ctrl, shift, alt = "ctrl" in mods, "shift" in mods, "alt" in mods
    if key_name == "Tab" and shift:
        key_name = "Backtab"
    attr = _SPECIAL_KEYS.get(key_name, "Key_" + key_name.upper() if len(key_name) == 1 else "Key_" + key_name)
    key = getattr(QtCore.Qt, attr, None)
    if key is None:
        return None
    return (key, ctrl, shift, alt)
