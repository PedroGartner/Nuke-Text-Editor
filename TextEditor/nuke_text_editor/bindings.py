"""Tabs that edit something other than a file: a Nuke knob, a node
note or the script note; and tabs that follow a growing log file."""

import io
import os

from .qt import QtCore, QtGui
from . import nuke_bridge


class KnobBinding(object):
    """A tab bound to a knob value or expression of a node."""

    def __init__(self, node_name, knob_name, kind, knob_class=""):
        self.node_name = node_name
        self.knob_name = knob_name
        self.kind = kind
        self.language = nuke_bridge.knob_language(knob_class, kind)

    @property
    def title(self):
        suffix = " (expr)" if self.kind == "expression" else ""
        return "{}.{}{}".format(self.node_name, self.knob_name, suffix)

    @property
    def tooltip(self):
        return "Knob {} of node {} - Save (Ctrl+S) writes it back to the node".format(self.knob_name, self.node_name)

    def read(self):
        return nuke_bridge.read_knob(self.node_name, self.knob_name, self.kind)

    def write(self, text):
        nuke_bridge.write_knob(self.node_name, self.knob_name, self.kind, text)

    def same_target(self, other):
        return isinstance(other, KnobBinding) and (self.node_name, self.knob_name, self.kind) == (
            other.node_name, other.knob_name, other.kind)


class NoteBinding(object):
    """A tab bound to the note stored on a node (or on the script root)."""

    language = "text"

    def __init__(self, node_name=None, script=False):
        self.script = script
        self.node_name = "root" if script else node_name

    @property
    def title(self):
        return "Script Note" if self.script else "Note: {}".format(self.node_name)

    @property
    def tooltip(self):
        where = "the script (saved inside the .nk)" if self.script else "node {}".format(self.node_name)
        return "Note stored on {} - Save (Ctrl+S) writes it".format(where)

    def read(self):
        value = nuke_bridge.get_script_note() if self.script else nuke_bridge.get_note(self.node_name)
        if value is None:
            raise LookupError("Node {} no longer exists".format(self.node_name))
        return value

    def write(self, text):
        if self.script:
            nuke_bridge.set_script_note(text)
        else:
            nuke_bridge.set_note(self.node_name, text)

    def same_target(self, other):
        return isinstance(other, NoteBinding) and (self.script, self.node_name) == (other.script, other.node_name)


class LogFollower(QtCore.QObject):
    """Appends new lines of a log file to a read-only editor."""

    def __init__(self, editor, path, interval_ms=1000):
        super().__init__(editor)
        self.editor = editor
        self.path = path
        self.offset = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.poll)
        self.reload()
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _read_from(self, offset):
        with io.open(self.path, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
        return data, offset + len(data)

    def reload(self):
        try:
            data, self.offset = self._read_from(0)
        except OSError:
            data = b""
            self.offset = 0
        self.editor.setPlainText(data.decode("utf-8", "replace").replace("\r\n", "\n"))
        self.editor.is_modified = False
        self._scroll_to_end()

    def _at_end(self):
        bar = self.editor.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _scroll_to_end(self):
        bar = self.editor.verticalScrollBar()
        bar.setValue(bar.maximum())

    def poll(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.offset:
            self.reload()  # file was truncated or rotated
            return
        if size == self.offset:
            return
        follow = self._at_end()
        try:
            data, self.offset = self._read_from(self.offset)
        except OSError:
            return
        cursor = QtGui.QTextCursor(self.editor.document())
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(data.decode("utf-8", "replace").replace("\r\n", "\n"))
        self.editor.is_modified = False
        if follow:
            self._scroll_to_end()


class GroupBinding(object):
    """A tab holding the nodes inside a Group as .nk text; saving
    rebuilds the Group from the text (undoable in Nuke)."""

    language = "nuke"
    confirm_write = True

    def __init__(self, node_name):
        self.node_name = node_name

    @property
    def title(self):
        return "Group: {}".format(self.node_name)

    @property
    def tooltip(self):
        return "Contents of {} - Save (Ctrl+S) rebuilds the Group from this text".format(self.node_name)

    def read(self):
        return nuke_bridge.group_to_text(self.node_name)

    def write(self, text):
        nuke_bridge.group_from_text(self.node_name, text)

    def same_target(self, other):
        return isinstance(other, GroupBinding) and other.node_name == self.node_name
