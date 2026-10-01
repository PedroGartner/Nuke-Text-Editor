"""Side panels for working with Nuke scripts: health check, node search,
callback viewer and Markdown preview."""

import os

from .qt import QtCore, QtGui, QtWidgets
from . import nkparse
from . import nuke_bridge
from . import scripttools
from . import themes
from .widgets import HoverButton, small_label

NODE_ROLE = QtCore.Qt.UserRole
LINE_ROLE = QtCore.Qt.UserRole + 1
PATH_ROLE = QtCore.Qt.UserRole + 2
KNOB_ROLE = QtCore.Qt.UserRole + 3


def _nk_source(editor):
    """(nodes, script_dir) from the current editor if it holds a .nk script."""
    if editor is None or editor.language != "nuke":
        return None, None
    nodes = scripttools.from_nk_nodes(nkparse.parse_nk(editor.toPlainText()))
    return nodes, (os.path.dirname(editor.file_path) if editor.file_path else None)


class HealthPanel(QtWidgets.QWidget):
    """Lists problems in the open Nuke script (or in a .nk tab)."""

    node_activated = QtCore.Signal(str)   # full node name (live script)
    line_activated = QtCore.Signal(int)   # 0-based line (.nk tab)

    def __init__(self, get_editor, parent=None):
        super().__init__(parent)
        self.get_editor = get_editor
        self.check_button = HoverButton("Check Nuke Script")
        self.check_button.clicked.connect(self.check_live)
        self.tab_button = HoverButton("Check .nk Tab")
        self.tab_button.clicked.connect(self.check_tab)
        self.limit = QtWidgets.QSpinBox()
        self.limit.setRange(10, 10000)
        self.limit.setValue(int(scripttools.DEFAULT_SIZE_LIMIT))
        self.limit.setToolTip("Filter sizes above this are reported")
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Node", "Problem"])
        self.tree.setRootIsDecorated(False)
        self.tree.itemClicked.connect(self._on_item)
        self.summary = small_label("")

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.check_button)
        top.addWidget(self.tab_button)
        top.addStretch()
        top.addWidget(QtWidgets.QLabel("Max size"))
        top.addWidget(self.limit)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.summary)

    def check_live(self):
        if not nuke_bridge.available():
            self.summary.setText("Checking the live script works inside Nuke. Use 'Check .nk Tab' instead.")
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            nodes = nuke_bridge.scene_nodes(all_knobs=False)
            script = nuke_bridge.script_path()
            issues = scripttools.analyze(nodes, scripttools.script_dir_of(script), float(self.limit.value()))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._show(issues, len(nodes), live=True)

    def check_tab(self):
        nodes, script_dir = _nk_source(self.get_editor())
        if nodes is None:
            self.summary.setText("Open a .nk script in a tab first.")
            return
        issues = scripttools.analyze(nodes, script_dir, float(self.limit.value()))
        self._show(issues, len(nodes), live=False)

    def _show(self, issues, node_count, live):
        self.tree.clear()
        colors = {"error": themes.color("error"), "warning": themes.color("builtin"), "info": themes.color("muted")}
        for issue in issues:
            item = QtWidgets.QTreeWidgetItem(self.tree, [issue.node, issue.message])
            item.setForeground(1, QtGui.QBrush(QtGui.QColor(colors.get(issue.severity, themes.color("text")))))
            item.setData(0, NODE_ROLE, issue.node if live else None)
            item.setData(0, LINE_ROLE, issue.line)
            item.setToolTip(1, issue.message)
        self.tree.resizeColumnToContents(0)
        counts = {}
        for issue in issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        self.summary.setText("{} nodes checked: {} errors, {} warnings, {} notes{}".format(
            node_count, counts.get("error", 0), counts.get("warning", 0), counts.get("info", 0),
            "" if issues else " - all good"))

    def _on_item(self, item, _column=0):
        node = item.data(0, NODE_ROLE)
        line = item.data(0, LINE_ROLE)
        if node:
            self.node_activated.emit(node)
        elif line is not None:
            self.line_activated.emit(int(line))


class NodeSearchPanel(QtWidgets.QWidget):
    """Find nodes by name, class or knob value (groups included)."""

    node_activated = QtCore.Signal(str)
    line_activated = QtCore.Signal(int)

    def __init__(self, get_editor, parent=None):
        super().__init__(parent)
        self.get_editor = get_editor
        self.query = QtWidgets.QLineEdit()
        self.query.setPlaceholderText("Node name, class or knob value (Enter)")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.search)
        self.knobs_check = QtWidgets.QCheckBox("Search knob values")
        self.knobs_check.setChecked(True)
        self.source = QtWidgets.QComboBox()
        self.source.addItems(["Nuke script", ".nk tab"])
        if not nuke_bridge.available():
            self.source.setCurrentIndex(1)
        search_button = HoverButton("Search")
        search_button.clicked.connect(self.search)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Node", "Class", "Match"])
        self.tree.setRootIsDecorated(False)
        self.tree.itemClicked.connect(self._on_item)
        self.summary = small_label("")

        options = QtWidgets.QHBoxLayout()
        options.addWidget(self.knobs_check)
        options.addStretch()
        options.addWidget(self.source)
        options.addWidget(search_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.query)
        layout.addLayout(options)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.summary)

    def focus_query(self):
        self.query.setFocus()
        self.query.selectAll()

    def search(self):
        live = self.source.currentIndex() == 0
        if live:
            if not nuke_bridge.available():
                self.summary.setText("Searching the live script works inside Nuke.")
                return
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                nodes = nuke_bridge.scene_nodes(all_knobs=self.knobs_check.isChecked())
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
        else:
            nodes, _dir = _nk_source(self.get_editor())
            if nodes is None:
                self.summary.setText("Open a .nk script in a tab first.")
                return
        results = scripttools.search(nodes, self.query.text(), self.knobs_check.isChecked())
        self.tree.clear()
        for node, where in results:
            item = QtWidgets.QTreeWidgetItem(self.tree, [node.name, node.cls, where])
            item.setData(0, NODE_ROLE, node.name if live else None)
            item.setData(0, LINE_ROLE, node.line)
            item.setToolTip(2, where)
        for column in (0, 1):
            self.tree.resizeColumnToContents(column)
        self.summary.setText("{} of {} nodes match".format(len(results), len(nodes)))

    def _on_item(self, item, _column=0):
        node = item.data(0, NODE_ROLE)
        line = item.data(0, LINE_ROLE)
        if node:
            self.node_activated.emit(node)
        elif line is not None:
            self.line_activated.emit(int(line))


class CallbacksPanel(QtWidgets.QWidget):
    """Every callback registered in this Nuke session, and callback knobs
    on the selected node. Double-click opens the source."""

    open_source = QtCore.Signal(str, int)          # file, 1-based line
    edit_knob = QtCore.Signal(str, str)            # node, knob

    def __init__(self, parent=None):
        super().__init__(parent)
        refresh = HoverButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Callback", "Where"])
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.summary = small_label("Double-click to open the source (or the node's callback knob).")
        top = QtWidgets.QHBoxLayout()
        top.addWidget(small_label("Callbacks registered in this Nuke session", themes.color("ui_text")))
        top.addStretch()
        top.addWidget(refresh)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.summary)

    def refresh(self):
        self.tree.clear()
        if not nuke_bridge.available():
            self.summary.setText("The callback viewer works inside Nuke.")
            return
        tables = {}
        entries = nuke_bridge.registered_callbacks()
        for table, node_class, name, module, path, line in entries:
            parent = tables.get(table)
            if parent is None:
                parent = QtWidgets.QTreeWidgetItem(self.tree, [table, ""])
                parent.setForeground(0, QtGui.QBrush(QtGui.QColor(themes.color("type"))))
                tables[table] = parent
            where = "{}:{}".format(os.path.basename(path), line) if path else module
            label = name if node_class in ("*", None, "") else "{}  [{}]".format(name, node_class)
            item = QtWidgets.QTreeWidgetItem(parent, [label, where])
            item.setToolTip(0, "{} ({})".format(name, module))
            item.setToolTip(1, "{}:{}".format(path, line) if path else module)
            item.setData(0, PATH_ROLE, path)
            item.setData(0, LINE_ROLE, line)
        for parent in tables.values():
            parent.setText(0, "{} ({})".format(parent.text(0), parent.childCount()))

        node = nuke_bridge.selected_node_name()
        if node:
            knobs = nuke_bridge.node_callback_knobs(node)
            parent = QtWidgets.QTreeWidgetItem(self.tree, ["Selected node: {} ({})".format(node, len(knobs)), ""])
            parent.setForeground(0, QtGui.QBrush(QtGui.QColor(themes.color("builtin"))))
            for knob, script in knobs:
                item = QtWidgets.QTreeWidgetItem(parent, [knob, script.splitlines()[0][:80] if script else ""])
                item.setToolTip(1, script)
                item.setData(0, NODE_ROLE, node)
                item.setData(0, KNOB_ROLE, knob)
            parent.setExpanded(True)
        self.tree.resizeColumnToContents(0)
        self.summary.setText("{} callbacks. Double-click to open the source.".format(len(entries)))

    def _on_double_click(self, item, _column=0):
        path = item.data(0, PATH_ROLE)
        if path:
            self.open_source.emit(path, int(item.data(0, LINE_ROLE) or 1))
            return
        node, knob = item.data(0, NODE_ROLE), item.data(0, KNOB_ROLE)
        if node and knob:
            self.edit_knob.emit(node, knob)


class MarkdownPreviewPanel(QtWidgets.QWidget):
    """Live rendered view of the current Markdown tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = QtWidgets.QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.info = small_label("")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.info)
        self._revision = None

    def refresh(self, editor):
        if editor is None or editor.language != "markdown":
            self.view.setPlainText("")
            self.info.setText("Open a Markdown (.md) file to see its preview here.")
            self._revision = None
            return
        revision = (id(editor), editor.document().revision())
        if revision == self._revision:
            return
        self._revision = revision
        document = self.view.document()
        if editor.file_path:
            document.setBaseUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(editor.file_path) + "/"))
        scroll = self.view.verticalScrollBar().value()
        if hasattr(self.view, "setMarkdown"):
            self.view.setMarkdown(editor.toPlainText())
            self.info.setText("")
        else:
            self.view.setPlainText(editor.toPlainText())
            self.info.setText("Markdown preview needs Nuke 14 or newer (Qt 5.14+).")
        self.view.verticalScrollBar().setValue(scroll)
