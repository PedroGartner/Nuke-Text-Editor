"""Dialogs: command palette, knob picker, version history, script diff,
expression tester, time summary and preferences."""

import datetime
import difflib
import os
import time

from .qt import QtCore, QtGui, QtWidgets, qt_exec
from . import fileio
from . import history
from . import nkparse
from . import nuke_bridge
from . import textops
from . import timelog
from .highlighter import SimpleHighlighter
from .widgets import dialog_style, HoverButton, monospace_font, small_label


# ------------------------------------------------------------- #
#  Command palette                                              #
# ------------------------------------------------------------- #
class CommandPalette(QtWidgets.QDialog):
    """Type to run any editor action or Nuke menu command.

    entries: list of (label, shortcut_text, callable).
    """

    MAX_ITEMS = 200

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setStyleSheet(dialog_style() + "QDialog { border: 1px solid #555555; }")
        self.entries = entries
        self.chosen = None

        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("Type a command...")
        self.input.textChanged.connect(self._filter)
        self.input.installEventFilter(self)
        self.list = QtWidgets.QListWidget()
        self.list.itemActivated.connect(self._run_item)
        self.list.itemClicked.connect(self._run_item)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.input)
        layout.addWidget(self.list)
        self.resize(560, 380)
        self._filter("")

    def _filter(self, text):
        scored = []
        for index, (label, shortcut, _callback) in enumerate(self.entries):
            score = textops.fuzzy_score(text, label)
            if score is not None:
                scored.append((-score, label.lower(), index))
        scored.sort()
        self.list.clear()
        for _score, _label, index in scored[: self.MAX_ITEMS]:
            label, shortcut, _callback = self.entries[index]
            item = QtWidgets.QListWidgetItem(label + ("    " + shortcut if shortcut else ""))
            item.setData(QtCore.Qt.UserRole, index)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            if key in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
                row = self.list.currentRow() + (1 if key == QtCore.Qt.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
                return True
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                item = self.list.currentItem()
                if item is not None:
                    self._run_item(item)
                return True
            if key == QtCore.Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)

    def _run_item(self, item):
        index = item.data(QtCore.Qt.UserRole)
        self.chosen = self.entries[index][2]
        self.accept()

    def show_over(self, widget):
        top_left = widget.mapToGlobal(QtCore.QPoint(0, 0))
        self.move(top_left.x() + (widget.width() - self.width()) // 2, top_left.y() + 40)
        self.input.setFocus()
        if qt_exec(self) == QtWidgets.QDialog.Accepted and self.chosen is not None:
            # Run after the popup has closed.
            QtCore.QTimer.singleShot(0, self.chosen)


# ------------------------------------------------------------- #
#  Knob picker                                                  #
# ------------------------------------------------------------- #
class KnobPickerDialog(QtWidgets.QDialog):
    """Pick a text knob or an expression of a node to edit in a tab."""

    def __init__(self, node_name, knobs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Knob - {}".format(node_name))
        self.setStyleSheet(dialog_style())
        self.knobs = knobs
        self.list = QtWidgets.QListWidget()
        for name, cls, kind in knobs:
            label = "{}  ({})".format(name, "expression" if kind == "expression" else cls)
            self.list.addItem(label)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Text knobs and knobs with expressions:"))
        layout.addWidget(self.list, 1)
        layout.addWidget(buttons)
        self.resize(360, 320)

    def selected(self):
        row = self.list.currentRow()
        return self.knobs[row] if 0 <= row < len(self.knobs) else None


# ------------------------------------------------------------- #
#  Diff helpers                                                 #
# ------------------------------------------------------------- #
def unified_diff(old_text, new_text, old_name="before", new_name="after"):
    lines = difflib.unified_diff(old_text.split("\n"), new_text.split("\n"),
                                 old_name, new_name, lineterm="", n=3)
    return "\n".join(lines)


def _diff_view():
    view = QtWidgets.QTextEdit()
    view.setReadOnly(True)
    view.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
    view.setFont(monospace_font(9))
    view.highlighter = SimpleHighlighter(view.document())
    view.highlighter.set_language("diff")
    return view


# ------------------------------------------------------------- #
#  Version history                                              #
# ------------------------------------------------------------- #
class HistoryDialog(QtWidgets.QDialog):
    """Browse earlier saved versions of a file and restore one."""

    def __init__(self, path, current_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Local History - {}".format(os.path.basename(path)))
        self.setStyleSheet(dialog_style())
        self.path = path
        self.current_text = current_text
        self.restored_text = None
        self.snapshots = history.list_snapshots(path)

        self.list = QtWidgets.QListWidget()
        for stamp, snap in self.snapshots:
            label = time.strftime("%Y-%m-%d  %H:%M:%S", time.localtime(stamp))
            item = QtWidgets.QListWidgetItem("{}   ({} KB)".format(label, max(1, os.path.getsize(snap) // 1024)))
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self._show)
        self.view = _diff_view()
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["Show version", "Compare with current"])
        self.mode.currentIndexChanged.connect(lambda _i: self._show(self.list.currentRow()))

        restore = HoverButton("Restore into Editor")
        restore.clicked.connect(self._restore)
        close = HoverButton("Close")
        close.clicked.connect(self.reject)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.mode)
        buttons.addStretch()
        buttons.addWidget(restore)
        buttons.addWidget(close)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(self.list)
        splitter.addWidget(self.view)
        splitter.setSizes([220, 600])
        layout = QtWidgets.QVBoxLayout(self)
        if not self.snapshots:
            layout.addWidget(QtWidgets.QLabel("No earlier versions yet. A snapshot is kept every time you save."))
        layout.addWidget(splitter, 1)
        layout.addLayout(buttons)
        self.resize(900, 560)
        if self.snapshots:
            self.list.setCurrentRow(0)

    def _text(self, row):
        try:
            return history.read_snapshot(self.snapshots[row][1])
        except (OSError, IndexError):
            return ""

    def _show(self, row):
        if row < 0:
            return
        text = self._text(row)
        if self.mode.currentIndex() == 1:
            diff = unified_diff(text, self.current_text, "saved version", "current")
            self.view.setPlainText(diff or "(identical)")
            self.view.highlighter.set_language("diff")
        else:
            self.view.highlighter.set_language(fileio.language_for_path(self.path))
            if fileio.is_rich_path(self.path):
                self.view.setPlainText(fileio.html_to_text(text))
            else:
                self.view.setPlainText(text)

    def _restore(self):
        row = self.list.currentRow()
        if row >= 0:
            self.restored_text = self._text(row)
            self.accept()


# ------------------------------------------------------------- #
#  Script / file diff                                           #
# ------------------------------------------------------------- #
class DiffDialog(QtWidgets.QDialog):
    """Compare two files. For .nk scripts a node-level summary is shown
    (added / removed / changed knobs) next to the text diff."""

    def __init__(self, old_path="", new_path="", new_text=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compare Files")
        self.setStyleSheet(dialog_style())
        self.new_text_override = new_text

        self.old_edit = QtWidgets.QLineEdit(old_path)
        self.new_edit = QtWidgets.QLineEdit(new_path)
        self.ignore_positions = QtWidgets.QCheckBox("Ignore node positions")
        self.ignore_positions.setChecked(True)
        compare = HoverButton("Compare")
        compare.clicked.connect(self.compare)

        grid = QtWidgets.QGridLayout()
        for row, (label, edit) in enumerate((("Old", self.old_edit), ("New", self.new_edit))):
            browse = HoverButton("...")
            browse.clicked.connect(lambda _c=False, e=edit: self._browse(e))
            grid.addWidget(QtWidgets.QLabel(label), row, 0)
            grid.addWidget(edit, row, 1)
            grid.addWidget(browse, row, 2)
        options = QtWidgets.QHBoxLayout()
        options.addWidget(self.ignore_positions)
        options.addStretch()
        options.addWidget(compare)

        self.tabs = QtWidgets.QTabWidget()
        self.node_tree = QtWidgets.QTreeWidget()
        self.node_tree.setHeaderLabels(["Node / knob", "Old", "New"])
        self.text_view = _diff_view()
        self.tabs.addTab(self.node_tree, "Nodes")
        self.tabs.addTab(self.text_view, "Text")
        self.summary = small_label("")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addLayout(options)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.summary)
        self.resize(1000, 640)
        if old_path and (new_path or new_text is not None):
            QtCore.QTimer.singleShot(0, self.compare)

    def _browse(self, edit):
        path, _f = QtWidgets.QFileDialog.getOpenFileName(self, "Choose File", edit.text() or "")
        if path:
            edit.setText(path)
            if edit is self.new_edit:
                self.new_text_override = None

    def _read(self, path):
        return fileio.read_text_file(path)[0]

    def compare(self):
        old_path = self.old_edit.text().strip()
        new_path = self.new_edit.text().strip()
        try:
            old_text = self._read(old_path)
            new_text = self.new_text_override if self.new_text_override is not None else self._read(new_path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Compare", "Could not read file:\n{}".format(exc))
            return
        self.text_view.setPlainText(unified_diff(old_text, new_text, os.path.basename(old_path),
                                                 os.path.basename(new_path) or "current") or "(identical)")
        is_nk = old_path.lower().endswith((".nk", ".gizmo")) and (not new_path or new_path.lower().endswith((".nk", ".gizmo")))
        self.tabs.setTabEnabled(0, is_nk)
        if not is_nk:
            self.tabs.setCurrentIndex(1)
            self.summary.setText("Text comparison.")
            return
        ignored = nkparse.DEFAULT_IGNORED_KNOBS if self.ignore_positions.isChecked() else ()
        result = nkparse.compare_nodes(nkparse.parse_nk(old_text), nkparse.parse_nk(new_text), ignored)
        self.node_tree.clear()

        def _section(title, color, count):
            item = QtWidgets.QTreeWidgetItem(self.node_tree, ["{} ({})".format(title, count)])
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(color)))
            item.setExpanded(True)
            return item

        added = _section("Added", "#50fa7b", len(result["added"]))
        for node in result["added"]:
            QtWidgets.QTreeWidgetItem(added, ["{}  ({})".format(node.full_name, node.cls)])
        removed = _section("Removed", "#ff5555", len(result["removed"]))
        for node in result["removed"]:
            QtWidgets.QTreeWidgetItem(removed, ["{}  ({})".format(node.full_name, node.cls)])
        changed = _section("Changed", "#f1fa8c", len(result["changed"]))
        for node, diffs in result["changed"]:
            node_item = QtWidgets.QTreeWidgetItem(changed, ["{}  ({})".format(node.full_name, node.cls)])
            for knob, old, new in diffs:
                child = QtWidgets.QTreeWidgetItem(node_item, [knob, "" if old is None else old[:200],
                                                              "" if new is None else new[:200]])
                child.setToolTip(1, old or "")
                child.setToolTip(2, new or "")
            node_item.setExpanded(len(result["changed"]) < 30)
        self.node_tree.resizeColumnToContents(0)
        self.tabs.setCurrentIndex(0)
        self.summary.setText("{} added, {} removed, {} changed nodes".format(
            len(result["added"]), len(result["removed"]), len(result["changed"])))


# ------------------------------------------------------------- #
#  Expression tester                                            #
# ------------------------------------------------------------- #
class ExpressionTester(QtWidgets.QDialog):
    """Evaluate Nuke expressions live, TCL and Python on Enter."""

    MODES = ["Nuke expression (live)", "TCL (press Enter)", "Python (press Enter)"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Expression Tester")
        self.setStyleSheet(dialog_style())
        self.setModal(False)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(self.MODES)
        self.input = QtWidgets.QLineEdit()
        self.input.setFont(monospace_font(10))
        self.input.setPlaceholderText("e.g. frame * 2   or   Grade1.white   or   [value Read1.first]")
        self.result = QtWidgets.QLabel("")
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.result.setFont(monospace_font(10))
        self.result.setWordWrap(True)
        self.history = QtWidgets.QListWidget()
        self.history.itemDoubleClicked.connect(lambda item: self.input.setText(item.text().split("  =>  ")[0]))
        self.frame_label = small_label("")

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._live)
        self.input.textChanged.connect(lambda _t: self._timer.start())
        self.input.returnPressed.connect(self.evaluate)
        self.mode.currentIndexChanged.connect(lambda _i: self.result.setText(""))

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.mode)
        layout.addWidget(self.input)
        layout.addWidget(self.result)
        layout.addWidget(self.frame_label)
        layout.addWidget(QtWidgets.QLabel("History (double-click to reuse):"))
        layout.addWidget(self.history, 1)
        self.resize(520, 360)

    def _set_result(self, text, error=False):
        self.result.setStyleSheet("color: {};".format("#ff6e6e" if error else "#50fa7b"))
        self.result.setText(text)
        frame = nuke_bridge.current_frame()
        self.frame_label.setText("Evaluated at frame {}".format(frame) if frame is not None else "")

    def _evaluate_text(self, text):
        mode = self.mode.currentIndex()
        if mode == 0:
            return nuke_bridge.evaluate_expression(text)
        if mode == 1:
            return nuke_bridge.evaluate_tcl(text)
        import __main__
        try:
            code = compile(text, "<expression tester>", "eval")
        except SyntaxError:
            code = compile(text, "<expression tester>", "exec")
            exec(code, __main__.__dict__)
            return "(executed)"
        return repr(eval(code, __main__.__dict__))

    def _live(self):
        if self.mode.currentIndex() == 0 and self.input.text().strip():
            self.evaluate(record=False)

    def evaluate(self, record=True):
        text = self.input.text().strip()
        if not text:
            return
        try:
            value = self._evaluate_text(text)
        except Exception as exc:
            self._set_result("{}: {}".format(type(exc).__name__, exc), error=True)
            return
        self._set_result(value)
        if record:
            self.history.insertItem(0, "{}  =>  {}".format(text, value))


# ------------------------------------------------------------- #
#  Time summary                                                 #
# ------------------------------------------------------------- #
class TimeSummaryDialog(QtWidgets.QDialog):
    """Weekly table of time spent per script."""

    def __init__(self, tracker=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Time per Script")
        self.setStyleSheet(dialog_style())
        if tracker is not None:
            tracker.flush()
        self.data = timelog.load()
        self.monday = timelog.week_start(datetime.date.today())

        prev_button = HoverButton("◀")
        next_button = HoverButton("▶")
        prev_button.clicked.connect(lambda: self._shift(-7))
        next_button.clicked.connect(lambda: self._shift(7))
        self.week_label = QtWidgets.QLabel("")
        copy_button = HoverButton("Copy as CSV")
        copy_button.clicked.connect(self._copy)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(prev_button)
        top.addWidget(self.week_label)
        top.addWidget(next_button)
        top.addStretch()
        top.addWidget(copy_button)

        self.table = QtWidgets.QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.total_label = small_label("", "#bbbbbb")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.total_label)
        layout.addWidget(small_label("Time counts while Nuke is in front and you are active "
                                     "(idle for more than 5 minutes stops the clock)."))
        self.resize(820, 420)
        self._fill()

    def _shift(self, days):
        self.monday += datetime.timedelta(days=days)
        self._fill()

    def _fill(self):
        days, rows = timelog.week_summary(self.data, self.monday)
        self._days, self._rows = days, rows
        self.week_label.setText("Week of {}".format(self.monday.strftime("%d %b %Y")))
        headers = ["Script"] + [datetime.date.fromisoformat(d).strftime("%a %d") for d in days] + ["Total"]
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        grand = 0
        for r, (script, values, total) in enumerate(rows):
            grand += total
            cells = [script] + [timelog.format_duration(v) if v else "" for v in values] + [timelog.format_duration(total)]
            for c, value in enumerate(cells):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.total_label.setText("Week total: {}".format(timelog.format_duration(grand)))

    def _copy(self):
        QtWidgets.QApplication.clipboard().setText(timelog.to_csv(self._days, self._rows))


# ------------------------------------------------------------- #
#  Preferences                                                  #
# ------------------------------------------------------------- #
class PreferencesDialog(QtWidgets.QDialog):
    """Editor preferences stored in QSettings, plus keyboard shortcuts.

    actions: list of (action_id, label, default_shortcut) for the
    Shortcuts tab; overrides are stored as JSON in 'shortcuts'.
    """

    BOOL_OPTIONS = [
        ("reopen_tabs", "Reopen my tabs when the editor opens"),
        ("autosave_to_file", "Autosave files (otherwise only crash recovery is saved)"),
        ("auto_open_shot_notes", "Open the shot's notes file when a script is loaded"),
        ("auto_show_editor_for_notes", "Also open the editor window for that (if closed)"),
        ("open_script_note_on_load", "Open the script note when a script that has one is loaded"),
        ("mark_nodes_with_notes", "Mark nodes that have notes in the Node Graph (restart Nuke)"),
        ("time_tracking", "Track time spent per script (restart Nuke)"),
        ("lint_on_save", "Check Python files for problems when saving"),
        ("annotate_captures", "Let me draw on Viewer captures before inserting them"),
    ]
    FOLDER_OPTIONS = [
        ("notes_folder", "Notes folder (TODO collector)"),
        ("team_snippets_folder", "Team snippets folder"),
        ("batch_gartner_log_folder", "Batch Gartner render logs folder"),
    ]
    LOCK_MODES = [("network", "On network drives"), ("always", "Always"), ("off", "Off")]
    SHOT_OPTIONS = [
        ("plates/check_on_load", "Look for newer plate versions when a script is loaded"),
        ("render_check/before_batch_gartner", "Check Write nodes before sending them to Batch Gartner"),
        ("render_check/before_local_render", "Check Write nodes before rendering inside Nuke"),
    ]
    EXPECT_OPTIONS = [
        ("render_check/colorspace", "Output colorspace", "e.g. ACES - ACEScg"),
        ("render_check/file_type", "Output file type", "e.g. exr"),
    ]

    def __init__(self, settings, actions=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setStyleSheet(dialog_style())
        self.settings = settings
        self.checks = {}
        self.folders = {}
        tabs = QtWidgets.QTabWidget()

        general = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(general)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Theme"))
        self.theme_combo = QtWidgets.QComboBox()
        from . import themes
        self.theme_combo.addItems(themes.names())
        self.theme_combo.setCurrentText(settings.get_str("theme", themes.DEFAULT))
        row.addWidget(self.theme_combo)
        row.addSpacing(20)
        row.addWidget(QtWidgets.QLabel("Warn when someone else edits a note"))
        self.lock_combo = QtWidgets.QComboBox()
        for key, label in self.LOCK_MODES:
            self.lock_combo.addItem(label, key)
        index = [k for k, _l in self.LOCK_MODES].index(settings.get_str("lock_markers", "network")) \
            if settings.get_str("lock_markers", "network") in [k for k, _l in self.LOCK_MODES] else 0
        self.lock_combo.setCurrentIndex(index)
        row.addWidget(self.lock_combo)
        row.addStretch()
        layout.addLayout(row)
        for key, label in self.BOOL_OPTIONS:
            box = QtWidgets.QCheckBox(label)
            box.setChecked(settings.get_bool(key))
            layout.addWidget(box)
            self.checks[key] = box
        form = QtWidgets.QGridLayout()
        for index, (key, label) in enumerate(self.FOLDER_OPTIONS):
            edit = QtWidgets.QLineEdit(settings.get_str(key))
            browse = HoverButton("...")
            browse.clicked.connect(lambda _c=False, e=edit: self._browse(e))
            form.addWidget(QtWidgets.QLabel(label), index, 0)
            form.addWidget(edit, index, 1)
            form.addWidget(browse, index, 2)
            self.folders[key] = edit
        layout.addLayout(form)
        layout.addStretch()
        layout.addWidget(small_label("Data folder: {}".format(fileio.data_dir())))
        tabs.addTab(general, "General")

        shot = QtWidgets.QWidget()
        shot_layout = QtWidgets.QVBoxLayout(shot)
        for key, label in self.SHOT_OPTIONS:
            box = QtWidgets.QCheckBox(label)
            box.setChecked(settings.get_bool(key))
            shot_layout.addWidget(box)
            self.checks[key] = box
        expect_form = QtWidgets.QFormLayout()
        self.expect_edits = {}
        for key, label, placeholder in self.EXPECT_OPTIONS:
            edit = QtWidgets.QLineEdit(settings.get_str(key))
            edit.setPlaceholderText(placeholder)
            expect_form.addRow(label, edit)
            self.expect_edits[key] = edit
        shot_layout.addSpacing(8)
        shot_layout.addWidget(QtWidgets.QLabel("Studio defaults for Write nodes (leave empty to skip):"))
        shot_layout.addLayout(expect_form)
        shot_layout.addWidget(small_label(
            "Without a colorspace here, a Write whose colorspace differs from its plate is mentioned as a note."))
        shot_layout.addStretch()
        tabs.addTab(shot, "Shot Checks")

        self.shortcut_edits = {}
        self.defaults = {}
        if actions:
            shortcuts_page = QtWidgets.QWidget()
            shortcuts_layout = QtWidgets.QVBoxLayout(shortcuts_page)
            table = QtWidgets.QTableWidget(len(actions), 2)
            table.setHorizontalHeaderLabels(["Command", "Shortcut"])
            table.verticalHeader().setVisible(False)
            overrides = self._overrides()
            for row_index, (action_id, label, default) in enumerate(actions):
                self.defaults[action_id] = default
                item = QtWidgets.QTableWidgetItem(label)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                table.setItem(row_index, 0, item)
                edit = QtWidgets.QKeySequenceEdit(QtGui.QKeySequence(overrides.get(action_id, default)))
                table.setCellWidget(row_index, 1, edit)
                self.shortcut_edits[action_id] = edit
            table.resizeColumnToContents(0)
            table.horizontalHeader().setStretchLastSection(True)
            reset = HoverButton("Reset All to Defaults")
            reset.clicked.connect(self._reset_shortcuts)
            shortcuts_layout.addWidget(small_label("Click a shortcut and press the new keys. "
                                                   "Clear it with Backspace in the field, then OK."))
            shortcuts_layout.addWidget(table, 1)
            shortcuts_layout.addWidget(reset)
            tabs.addTab(shortcuts_page, "Shortcuts")

        main = QtWidgets.QVBoxLayout(self)
        main.addWidget(tabs, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)
        self.resize(640, 520)

    def _overrides(self):
        import json
        try:
            data = json.loads(self.settings.get_str("shortcuts", "{}"))
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _reset_shortcuts(self):
        for action_id, edit in self.shortcut_edits.items():
            edit.setKeySequence(QtGui.QKeySequence(self.defaults.get(action_id, "")))

    def _browse(self, edit):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Folder", edit.text())
        if path:
            edit.setText(path)

    def _save(self):
        import json
        for key, box in self.checks.items():
            self.settings.set(key, box.isChecked())
        for key, edit in self.folders.items():
            self.settings.set(key, edit.text().strip())
        for key, edit in self.expect_edits.items():
            self.settings.set(key, edit.text().strip())
        self.settings.set("theme", self.theme_combo.currentText())
        self.settings.set("lock_markers", self.lock_combo.currentData())
        if self.shortcut_edits:
            overrides = {}
            for action_id, edit in self.shortcut_edits.items():
                text = edit.keySequence().toString(QtGui.QKeySequence.PortableText)
                if text != self.defaults.get(action_id, ""):
                    overrides[action_id] = text
            self.settings.set("shortcuts", json.dumps(overrides))
        self.settings.sync()
        self.accept()


def ask_line_number(parent, current, maximum):
    value, ok = QtWidgets.QInputDialog.getInt(parent, "Go to Line", "Line (1-{}):".format(maximum),
                                              current, 1, max(1, maximum))
    return value if ok else None


__all__ = [
    "CommandPalette", "KnobPickerDialog", "HistoryDialog", "DiffDialog", "ExpressionTester",
    "TimeSummaryDialog", "PreferencesDialog", "ask_line_number", "unified_diff",
]

