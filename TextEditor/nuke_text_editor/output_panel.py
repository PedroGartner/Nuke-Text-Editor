"""Output console for Python run from the editor."""

import re

from .qt import QtCore, QtGui, QtWidgets, event_pos
from . import themes
from .widgets import HoverButton, monospace_font

LOCATION_RE = re.compile(r'File "(<editor:\d+>)", line (\d+)')


class _OutputView(QtWidgets.QPlainTextEdit):
    location_clicked = QtCore.Signal(str, int)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.textCursor().hasSelection():
            return
        line = self.cursorForPosition(event_pos(event)).block().text()
        match = LOCATION_RE.search(line)
        if match:
            self.location_clicked.emit(match.group(1), int(match.group(2)))


class ConsoleInput(QtWidgets.QPlainTextEdit):
    """One-line-at-a-time Python prompt. Enter runs, Shift+Enter adds a
    line, Up / Down browse the history."""

    submitted = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(">>> Python (Enter to run, Shift+Enter for a new line, Up/Down for history)")
        self.setFont(monospace_font(9))
        self.setMaximumHeight(60)
        self.history = []
        self._history_index = 0

    def keyPressEvent(self, event):
        key = event.key()
        shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not shift:
            code = self.toPlainText()
            if code.strip():
                if not self.history or self.history[-1] != code:
                    self.history.append(code)
                self._history_index = len(self.history)
                self.clear()
                self.submitted.emit(code)
            return
        single_line = "\n" not in self.toPlainText()
        if key == QtCore.Qt.Key_Up and single_line and self.history:
            self._history_index = max(0, self._history_index - 1)
            self.setPlainText(self.history[self._history_index])
            self.moveCursor(QtGui.QTextCursor.End)
            return
        if key == QtCore.Qt.Key_Down and single_line and self.history:
            self._history_index = min(len(self.history), self._history_index + 1)
            self.setPlainText(self.history[self._history_index] if self._history_index < len(self.history) else "")
            self.moveCursor(QtGui.QTextCursor.End)
            return
        super().keyPressEvent(event)


class OutputPanel(QtWidgets.QWidget):
    """Shows printed output and tracebacks. Clicking a traceback line
    that points into an editor tab jumps to it."""

    location_clicked = QtCore.Signal(str, int)
    console_submitted = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.console = ConsoleInput()
        self.console.submitted.connect(self.console_submitted)
        self.view = _OutputView()
        self.view.setReadOnly(True)
        self.view.setFont(monospace_font(9))
        self.apply_theme()
        self.view.setMaximumBlockCount(5000)
        self.view.location_clicked.connect(self.location_clicked)

        clear_button = HoverButton("Clear")
        clear_button.clicked.connect(self.view.clear)
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(4, 2, 4, 2)
        top.addWidget(QtWidgets.QLabel("Output and console  (click a traceback line to jump to it)"))
        top.addStretch()
        top.addWidget(clear_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.console)

    def apply_theme(self):
        self.view.setStyleSheet("QPlainTextEdit {{ background-color: {}; color: {}; border: none; }}".format(
            themes.color("editor"), themes.color("text")))

    def append(self, text, kind="out"):
        """kind: 'out', 'err' or 'info'."""
        if not text:
            return
        colors = {"out": themes.color("text"), "err": themes.color("error"), "info": themes.color("comment")}
        fmt = QtGui.QTextCharFormat()
        fmt.setForeground(QtGui.QColor(colors.get(kind, themes.color("text"))))
        cursor = self.view.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(text if text.endswith("\n") else text + "\n", fmt)
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()
