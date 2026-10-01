"""Docked find / replace bar."""

import re

from .qt import QtCore, QtGui, QtWidgets, modifier_state
from .widgets import HoverButton


def expand_replacement(template, match):
    """Expand \\1 / $1 group references, \\n, \\t and \\\\ in a regex
    replacement string. `match` needs a captured(int) method."""
    def _repl(m):
        token = m.group(0)
        if token == "\\\\":
            return "\\"
        if token == "\\n":
            return "\n"
        if token == "\\t":
            return "\t"
        group = m.group(1) or m.group(2)
        return match.captured(int(group)) or ""
    return re.sub(r"\\\\|\\n|\\t|\\(\d)|\$(\d)", _repl, template)


class FindPanel(QtWidgets.QWidget):
    """Find / replace bar working on the editor returned by `get_editor`."""

    def __init__(self, get_editor, parent=None):
        super().__init__(parent)
        self.get_editor = get_editor
        self.setVisible(False)
        self.setStyleSheet("background-color: #262626;")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self.find_input = QtWidgets.QLineEdit()
        self.find_input.setPlaceholderText("Find")
        self.replace_input = QtWidgets.QLineEdit()
        self.replace_input.setPlaceholderText("Replace (regex: \\1 or $1 for groups)")
        self.case_checkbox = QtWidgets.QCheckBox("Case")
        self.regex_checkbox = QtWidgets.QCheckBox("Regex")
        self.word_checkbox = QtWidgets.QCheckBox("Word")
        self.status = QtWidgets.QLabel("")
        self.status.setMinimumWidth(90)
        self.status.setStyleSheet("color: #888888; font-size: 10px;")

        prev_button = HoverButton("Prev")
        next_button = HoverButton("Next")
        replace_button = HoverButton("Replace")
        replace_all_button = HoverButton("Replace All")
        close_button = HoverButton("X")

        layout.addWidget(QtWidgets.QLabel("Find:"))
        layout.addWidget(self.find_input, 1)
        layout.addWidget(QtWidgets.QLabel("Replace:"))
        layout.addWidget(self.replace_input, 1)
        for widget in (self.case_checkbox, self.regex_checkbox, self.word_checkbox, self.status,
                       prev_button, next_button, replace_button, replace_all_button, close_button):
            layout.addWidget(widget)

        next_button.clicked.connect(self.find_next)
        prev_button.clicked.connect(self.find_prev)
        replace_button.clicked.connect(self.replace_one)
        replace_all_button.clicked.connect(self.replace_all)
        close_button.clicked.connect(self.hide_panel)
        self.find_input.textChanged.connect(lambda _text: self.set_status(""))
        for checkbox in (self.case_checkbox, self.regex_checkbox, self.word_checkbox):
            checkbox.toggled.connect(lambda _checked: self.set_status(""))
        self.find_input.installEventFilter(self)
        self.replace_input.installEventFilter(self)

    # ----- Showing --------------------------------------------- #
    def show_panel(self, focus_replace=False):
        editor = self.get_editor()
        if editor is None:
            return
        self.setVisible(True)
        selected = editor.textCursor().selectedText()
        if selected and " " not in selected:
            self.find_input.setText(selected)
        target = self.replace_input if focus_replace else self.find_input
        target.setFocus()
        target.selectAll()

    def hide_panel(self):
        self.setVisible(False)
        editor = self.get_editor()
        if editor:
            editor.setFocus()

    def eventFilter(self, obj, event):
        if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride):
            key = event.key()
            handler = None
            if key == QtCore.Qt.Key_Escape:
                handler = self.hide_panel
            elif key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                if obj is self.replace_input:
                    handler = self.replace_one
                else:
                    handler = self.find_prev if modifier_state(event)[1] else self.find_next
            if handler is not None:
                event.accept()
                if event.type() == QtCore.QEvent.KeyPress:
                    handler()
                return True
        return super().eventFilter(obj, event)

    def set_status(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet("color: {}; font-size: 10px;".format("#ff5555" if error else "#888888"))

    # ----- Searching ------------------------------------------- #
    def build_regex(self):
        pattern = self.find_input.text()
        if not pattern:
            return None
        if not self.regex_checkbox.isChecked():
            pattern = QtCore.QRegularExpression.escape(pattern)
        if self.word_checkbox.isChecked():
            pattern = r"\b(?:" + pattern + r")\b"
        regex = QtCore.QRegularExpression(pattern)
        if not self.case_checkbox.isChecked():
            regex.setPatternOptions(QtCore.QRegularExpression.CaseInsensitiveOption)
        if not regex.isValid():
            self.set_status("Invalid regex", error=True)
            return None
        return regex

    @staticmethod
    def all_matches(editor, regex, limit=100000):
        it = regex.globalMatch(editor.document().toPlainText())
        matches = []
        while it.hasNext() and len(matches) < limit:
            match = it.next()
            matches.append((match.capturedStart(), match.capturedEnd(), match))
        return matches

    def _prepare(self):
        """Return (editor, matches) or None when there is nothing to do."""
        editor = self.get_editor()
        if editor is None:
            return None
        regex = self.build_regex()
        if regex is None:
            return None
        matches = self.all_matches(editor, regex)
        if not matches:
            self.set_status("No matches", error=True)
            return None
        return editor, matches

    def _select(self, editor, match, matches, wrapped):
        cursor = editor.textCursor()
        cursor.setPosition(match[0])
        cursor.setPosition(match[1], QtGui.QTextCursor.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        self.set_status("{} of {}{}".format(matches.index(match) + 1, len(matches),
                                          " (wrapped)" if wrapped else ""))

    def find_next(self):
        prepared = self._prepare()
        if prepared is None:
            return
        editor, matches = prepared
        cursor = editor.textCursor()
        s, e = cursor.selectionStart(), cursor.selectionEnd()
        target = next((m for m in matches if m[0] > s or (m[0] == s and m[1] > e)), None)
        self._select(editor, target or matches[0], matches, target is None)

    def find_prev(self):
        prepared = self._prepare()
        if prepared is None:
            return
        editor, matches = prepared
        cursor = editor.textCursor()
        s, e = cursor.selectionStart(), cursor.selectionEnd()
        target = next((m for m in reversed(matches) if m[1] < e or (m[1] == e and m[0] < s)), None)
        self._select(editor, target or matches[-1], matches, target is None)

    def _replacement(self, match):
        template = self.replace_input.text()
        return expand_replacement(template, match) if self.regex_checkbox.isChecked() else template

    def replace_one(self):
        """Replace the selection if it is a match, then find the next one."""
        editor = self.get_editor()
        if editor is None or editor.isReadOnly():
            return
        regex = self.build_regex()
        if regex is None:
            return
        cursor = editor.textCursor()
        if cursor.hasSelection():
            s, e = cursor.selectionStart(), cursor.selectionEnd()
            for start, end, match in self.all_matches(editor, regex):
                if (start, end) == (s, e):
                    cursor.insertText(self._replacement(match))
                    editor.setTextCursor(cursor)
                    break
                if start > s:
                    break
        self.find_next()

    def replace_all(self):
        prepared = self._prepare()
        if prepared is None or prepared[0].isReadOnly():
            return
        editor, matches = prepared
        doc = editor.document()
        edit = QtGui.QTextCursor(doc)
        edit.beginEditBlock()
        for start, end, match in reversed(matches):
            c = QtGui.QTextCursor(doc)
            c.setPosition(start)
            c.setPosition(end, QtGui.QTextCursor.KeepAnchor)
            c.insertText(self._replacement(match))
        edit.endEditBlock()
        self.set_status("Replaced {}".format(len(matches)))
