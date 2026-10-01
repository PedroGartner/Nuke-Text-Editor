"""Find in files and the TODO collector. Both scan a folder in a
background thread so Nuke stays responsive."""

import fnmatch
import os
import re
import datetime
import threading

from .qt import QtCore, QtGui, QtWidgets
from . import fileio
from . import textops
from . import themes
from .widgets import HoverButton, small_label

DEFAULT_GLOBS = "*.txt *.tnote *.md *.py *.nk *.json *.log"
TODO_GLOBS = "*.txt *.tnote *.md *.py"
MAX_FILE_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".svn"}

PATH_ROLE = QtCore.Qt.UserRole
LINE_ROLE = QtCore.Qt.UserRole + 1
TEXT_ROLE = QtCore.Qt.UserRole + 2
META_ROLE = QtCore.Qt.UserRole + 3


def file_text(path):
    """Text of a file for searching (.tnote HTML is converted to text)."""
    text = fileio.read_text_file(path)[0]
    return fileio.html_to_text(text) if fileio.is_rich_path(path) else text


class _Emitter(QtCore.QObject):
    batch = QtCore.Signal(object)       # list of (path, [(line, label, text)])
    done = QtCore.Signal(int, int, bool)  # files_with_hits, files_scanned, cancelled


class FolderScanner(threading.Thread):
    """Walks a folder and runs `matcher(text) -> [(line, label, text)]`
    on each matching file. Results arrive through Qt signals."""

    def __init__(self, root, globs, matcher, max_files=20000):
        super().__init__()
        self.daemon = True
        self.root = root
        self.globs = globs
        self.matcher = matcher
        self.max_files = max_files
        self.emitter = _Emitter()
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        hits = scanned = 0
        pending = []
        for folder, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            dirs.sort(key=str.lower)
            for name in sorted(files, key=str.lower):
                if self.cancelled.is_set() or scanned >= self.max_files:
                    self._flush(pending)
                    self.emitter.done.emit(hits, scanned, True)
                    return
                if not any(fnmatch.fnmatch(name.lower(), g.lower()) for g in self.globs):
                    continue
                path = os.path.join(folder, name)
                try:
                    if os.path.getsize(path) > MAX_FILE_BYTES:
                        continue
                    results = self.matcher(file_text(path))
                except Exception:
                    continue
                scanned += 1
                if results:
                    hits += 1
                    pending.append((path, results))
                    if len(pending) >= 20:
                        self._flush(pending)
                        pending = []
        self._flush(pending)
        self.emitter.done.emit(hits, scanned, False)

    def _flush(self, pending):
        if pending:
            self.emitter.batch.emit(list(pending))


class _ScanPanel(QtWidgets.QWidget):
    """Shared UI: folder row, results tree, status line."""

    location_activated = QtCore.Signal(str, int, str)  # path, 0-based line, text

    def __init__(self, default_folder, parent=None):
        super().__init__(parent)
        self._scanner = None
        self._root = ""
        self.folder_edit = QtWidgets.QLineEdit(default_folder or "")
        self.folder_edit.setPlaceholderText("Folder")
        browse = HoverButton("...")
        browse.clicked.connect(self._browse)
        self.folder_row = QtWidgets.QHBoxLayout()
        self.folder_row.addWidget(self.folder_edit, 1)
        self.folder_row.addWidget(browse)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemActivated.connect(self._on_activated)
        self.tree.itemDoubleClicked.connect(self._on_activated)
        self.status = small_label("")

    def _browse(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Folder", self.folder_edit.text())
        if path:
            self.folder_edit.setText(path)

    def _on_activated(self, item, _column=0):
        path = item.data(0, PATH_ROLE)
        if path:
            line = item.data(0, LINE_ROLE)
            self.location_activated.emit(path, int(line) if line is not None else -1,
                                         item.data(0, TEXT_ROLE) or "")

    def _start(self, globs, matcher):
        self.stop()
        folder = os.path.expanduser(self.folder_edit.text().strip())
        if not os.path.isdir(folder):
            self.status.setText("Folder not found.")
            return False
        self.tree.clear()
        self.status.setText("Searching...")
        scanner = FolderScanner(folder, globs, matcher)
        # Results of a stopped scan are ignored (see the scanner check).
        scanner.emitter.batch.connect(lambda batch, s=scanner: self._add_batch(batch, s))
        scanner.emitter.done.connect(lambda h, n, c, s=scanner: self._finished(h, n, c, s))
        self._scanner = scanner
        self._root = folder
        scanner.start()
        return True

    def stop(self):
        if self._scanner is not None:
            self._scanner.cancel()
            self._scanner = None

    def _add_batch(self, batch, scanner):
        if scanner is not self._scanner:
            return
        for path, results in batch:
            rel = os.path.relpath(path, self._root) if self._root else path
            file_item = QtWidgets.QTreeWidgetItem(self.tree, ["{}  ({})".format(rel, len(results))])
            file_item.setData(0, PATH_ROLE, path)
            file_item.setForeground(0, QtGui.QBrush(QtGui.QColor("#8be9fd")))
            file_item.setToolTip(0, path)
            for result in results[:500]:
                line, label, text = result[:3]
                child = QtWidgets.QTreeWidgetItem(file_item, [label])
                child.setData(0, PATH_ROLE, path)
                child.setData(0, LINE_ROLE, line)
                child.setData(0, TEXT_ROLE, text)
                if len(result) > 3:
                    child.setData(0, META_ROLE, result[3])
                    self._decorate(child, result[3])
            file_item.setExpanded(True)
        self._after_batch()

    def _decorate(self, item, meta):
        pass

    def _after_batch(self):
        pass

    def _finished(self, hits, scanned, cancelled, scanner):
        if scanner is not self._scanner:
            return
        self._scanner = None
        self.status.setText("{} files with results, {} scanned{}".format(
            hits, scanned, " (stopped)" if cancelled else ""))


class FileSearchPanel(_ScanPanel):
    """Search text in every file of a folder."""

    def __init__(self, default_folder, parent=None):
        super().__init__(default_folder, parent)
        self.query_edit = QtWidgets.QLineEdit()
        self.query_edit.setPlaceholderText("Search in files (Enter)")
        self.query_edit.returnPressed.connect(self.search)
        self.globs_edit = QtWidgets.QLineEdit(DEFAULT_GLOBS)
        self.globs_edit.setToolTip("File patterns, separated by spaces")
        self.case_checkbox = QtWidgets.QCheckBox("Case")
        self.regex_checkbox = QtWidgets.QCheckBox("Regex")
        search_button = HoverButton("Search")
        search_button.clicked.connect(self.search)
        stop_button = HoverButton("Stop")
        stop_button.clicked.connect(self.stop)

        options = QtWidgets.QHBoxLayout()
        options.addWidget(self.case_checkbox)
        options.addWidget(self.regex_checkbox)
        options.addStretch()
        options.addWidget(search_button)
        options.addWidget(stop_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.query_edit)
        layout.addLayout(self.folder_row)
        layout.addWidget(self.globs_edit)
        layout.addLayout(options)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status)

    def focus_query(self, text=""):
        if text:
            self.query_edit.setText(text)
        self.query_edit.setFocus()
        self.query_edit.selectAll()

    def search(self):
        query = self.query_edit.text()
        if not query:
            return
        flags = 0 if self.case_checkbox.isChecked() else re.IGNORECASE
        try:
            regex = re.compile(query if self.regex_checkbox.isChecked() else re.escape(query), flags)
        except re.error as exc:
            self.status.setText("Invalid regex: {}".format(exc))
            return

        def matcher(text):
            results = []
            for number, line in enumerate(text.split("\n")):
                if regex.search(line):
                    results.append((number, "{}: {}".format(number + 1, line.strip()[:200]), line))
            return results

        self._start(self.globs_edit.text().split(), matcher)


class TodoPanel(_ScanPanel):
    """Collects unchecked tasks and TODO / FIXME from every note in a
    folder. Tasks can name people (@anna) and dates (due:friday)."""

    def __init__(self, default_folder, parent=None):
        super().__init__(default_folder, parent)
        refresh = HoverButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self.person_filter = QtWidgets.QLineEdit()
        self.person_filter.setPlaceholderText("@person or text filter")
        self.person_filter.setClearButtonEnabled(True)
        self.person_filter.textChanged.connect(lambda _t: self.apply_filter())
        self.due_filter = QtWidgets.QComboBox()
        self.due_filter.addItems(["All", "Overdue", "Due this week", "Has a due date"])
        self.due_filter.currentIndexChanged.connect(lambda _i: self.apply_filter())

        top = QtWidgets.QHBoxLayout()
        top.addWidget(small_label("Unchecked tasks and TODO / FIXME in this folder:", themes.color("ui_text")))
        top.addStretch()
        top.addWidget(refresh)
        filters = QtWidgets.QHBoxLayout()
        filters.addWidget(self.person_filter, 1)
        filters.addWidget(self.due_filter)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addLayout(self.folder_row)
        layout.addLayout(filters)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status)
        self.folder_edit.returnPressed.connect(self.refresh)

    def refresh(self):
        today = datetime.date.today()

        def matcher(text):
            results = []
            for number, kind, item_text in textops.open_items(text):
                people, due = textops.task_meta(item_text, today)
                prefix = "\u2610" if kind == "task" else kind + ":"
                label = "{} {}".format(prefix, item_text[:200])
                if due is not None:
                    label += "   [due {}]".format(due.strftime("%a %d %b"))
                meta = {"people": [p.lower() for p in people], "due": due.isoformat() if due else ""}
                results.append((number, label, item_text, meta))
            return results

        self._start(TODO_GLOBS.split(), matcher)

    def _decorate(self, item, meta):
        due = meta.get("due")
        if not due:
            return
        days = (datetime.date.fromisoformat(due) - datetime.date.today()).days
        if days < 0:
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(themes.color("error"))))
        elif days <= 2:
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(themes.color("builtin"))))

    def _after_batch(self):
        self.apply_filter()

    def apply_filter(self):
        text = self.person_filter.text().strip().lower()
        mode = self.due_filter.currentIndex()
        today = datetime.date.today()
        for i in range(self.tree.topLevelItemCount()):
            file_item = self.tree.topLevelItem(i)
            visible = 0
            for j in range(file_item.childCount()):
                child = file_item.child(j)
                meta = child.data(0, META_ROLE) or {}
                show = True
                if text:
                    if text.startswith("@"):
                        show = text[1:] in meta.get("people", [])
                    else:
                        show = text in child.text(0).lower()
                due = meta.get("due")
                due_date = datetime.date.fromisoformat(due) if due else None
                if mode == 1:
                    show = show and due_date is not None and due_date < today
                elif mode == 2:
                    show = show and due_date is not None and (due_date - today).days <= 6
                elif mode == 3:
                    show = show and due_date is not None
                child.setHidden(not show)
                visible += show
            file_item.setHidden(visible == 0)
