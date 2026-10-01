"""Outline of the current document: nodes and file paths for .nk
scripts, classes / functions for Python, headings for notes."""

import os
import re
import time

from .qt import QtCore, QtGui, QtWidgets
from . import nkparse
from .widgets import HoverButton, small_label

PY_DEF_RE = re.compile(r"^(\s*)(class|def|async def)\s+([A-Za-z_]\w*)")
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

LINE_ROLE = QtCore.Qt.UserRole
PATH_ROLE = QtCore.Qt.UserRole + 1


class OutlinePanel(QtWidgets.QWidget):
    line_activated = QtCore.Signal(int)     # 0-based line
    path_activated = QtCore.Signal(str)     # file path from a Read/Write

    STATUS_CACHE_SECONDS = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Filter")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        refresh = HoverButton("Refresh")
        refresh.clicked.connect(lambda: self.refresh(self._editor, force=True))

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._on_item)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.info = small_label("")

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.filter_edit, 1)
        top.addWidget(refresh)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.info)

        self._editor = None
        self._revision = None
        self._status_cache = {}

    # ----- Building -------------------------------------------- #
    def refresh(self, editor, force=False):
        if editor is None:
            self.tree.clear()
            self.info.setText("")
            self._editor = None
            return
        revision = (id(editor), editor.document().revision(), editor.language)
        if not force and revision == self._revision:
            return
        self._editor = editor
        self._revision = revision
        self.tree.clear()
        language = editor.language
        if language == "nuke":
            self._build_nuke(editor)
        elif language == "python":
            self._build_python(editor)
        else:
            self._build_headings(editor)
        self._apply_filter(self.filter_edit.text())

    def _item(self, parent, text, line=None, color=None, tooltip=None):
        item = QtWidgets.QTreeWidgetItem(parent, [text])
        if line is not None:
            item.setData(0, LINE_ROLE, line)
        if color:
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(color)))
        if tooltip:
            item.setToolTip(0, tooltip)
        return item

    def _build_nuke(self, editor):
        nodes = nkparse.parse_nk(editor.toPlainText())
        script_dir = os.path.dirname(editor.file_path) if editor.file_path else None
        nodes_root = self._item(self.tree, "Nodes ({})".format(len(nodes)))
        groups = {"": nodes_root}
        for node in nodes:
            parent = groups.get(node.group, nodes_root)
            item = self._item(parent, "{}  {}".format(node.name, node.cls), node.line)
            if node.cls in nkparse.GROUP_CLASSES:
                groups[node.full_name] = item

        paths = nkparse.file_paths(nodes)
        missing = 0
        paths_root = self._item(self.tree, "File paths ({})".format(len(paths)))
        for node, knob, path in paths:
            status = self._path_status(path, script_dir)
            color = {"ok": "#50fa7b", "missing": "#ff5555"}.get(status, "#f1fa8c")
            missing += status == "missing"
            label = "{}.{}: {}".format(node.name, knob, path)
            item = self._item(paths_root, label, node.line, color,
                              tooltip="{} ({})\nDouble-click to open the folder".format(path, status))
            item.setData(0, PATH_ROLE, path)
        nodes_root.setExpanded(len(nodes) < 200)
        paths_root.setExpanded(True)
        self.info.setText("{} nodes, {} paths, {} missing".format(len(nodes), len(paths), missing))

    def _path_status(self, path, script_dir):
        now = time.time()
        cached = self._status_cache.get((path, script_dir))
        if cached and now - cached[0] < self.STATUS_CACHE_SECONDS:
            return cached[1]
        status = nkparse.path_status(path, script_dir)
        self._status_cache[(path, script_dir)] = (now, status)
        return status

    def _build_python(self, editor):
        stack = [(-1, self.tree.invisibleRootItem())]
        count = 0
        for number, line in enumerate(editor.toPlainText().split("\n")):
            match = PY_DEF_RE.match(line)
            if not match:
                continue
            indent = len(match.group(1).expandtabs(4))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            kind = match.group(2)
            color = "#ffb86c" if kind == "class" else "#50fa7b"
            item = self._item(stack[-1][1], "{} {}".format(kind.split()[-1], match.group(3)), number, color)
            stack.append((indent, item))
            count += 1
        self.tree.expandAll()
        self.info.setText("{} definitions".format(count))

    def _build_headings(self, editor):
        count = 0
        block = editor.document().begin()
        stack = [(0, self.tree.invisibleRootItem())]
        while block.isValid():
            text = block.text()
            level = 0
            title = ""
            match = MD_HEADING_RE.match(text)
            if match:
                level, title = len(match.group(1)), match.group(2)
            elif hasattr(block.blockFormat(), "headingLevel") and block.blockFormat().headingLevel() > 0:
                level, title = block.blockFormat().headingLevel(), text
            if level and title.strip():
                while stack and stack[-1][0] >= level:
                    stack.pop()
                parent = stack[-1][1] if stack else self.tree.invisibleRootItem()
                item = self._item(parent, title.strip(), block.blockNumber())
                stack.append((level, item))
                count += 1
            block = block.next()
        self.tree.expandAll()
        self.info.setText("{} headings".format(count) if count else "No headings (use # Title lines)")

    # ----- Interaction ----------------------------------------- #
    def _on_item(self, item, _column=0):
        line = item.data(0, LINE_ROLE)
        if line is not None:
            self.line_activated.emit(int(line))

    def _on_double_click(self, item, _column=0):
        path = item.data(0, PATH_ROLE)
        if path:
            self.path_activated.emit(path)

    def _apply_filter(self, text):
        text = (text or "").lower()

        def _visit(item):
            visible_child = False
            for i in range(item.childCount()):
                if _visit(item.child(i)):
                    visible_child = True
            match = not text or text in item.text(0).lower()
            item.setHidden(not (match or visible_child))
            return match or visible_child

        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            _visit(root.child(i))
