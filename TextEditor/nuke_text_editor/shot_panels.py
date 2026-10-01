"""Plate versions panel, pre-render check dialog and Version Up dialog."""

import os
import threading

from .qt import QtCore, QtGui, QtWidgets, qt_exec
from . import fileio
from . import nuke_bridge
from . import shotcheck
from . import textops
from . import themes
from .widgets import HoverButton, small_label

NODE_ROLE = QtCore.Qt.UserRole
PATH_ROLE = QtCore.Qt.UserRole + 1


def _severity_color(severity):
    return {"error": themes.color("error"), "warning": themes.color("builtin"),
            "info": themes.color("muted")}.get(severity, themes.color("text"))


# ------------------------------------------------------------- #
#  Plate versions                                               #
# ------------------------------------------------------------- #
class _PlateEmitter(QtCore.QObject):
    done = QtCore.Signal(object, int)  # [(read, current, newest, path)], reads checked


class _PlateScanner(threading.Thread):
    """Looks for newer versions on disk (can be slow on network drives)."""

    def __init__(self, reads):
        super().__init__()
        self.daemon = True
        self.reads = reads
        self.emitter = _PlateEmitter()

    def run(self):
        try:
            updates = shotcheck.plate_updates(self.reads)
        except Exception:
            updates = []
        self.emitter.done.emit(updates, len(self.reads))


class PlatesPanel(QtWidgets.QWidget):
    """Reads whose file has a newer version on disk, with one-click update."""

    node_activated = QtCore.Signal(str)
    plates_updated = QtCore.Signal(object)  # [(node, old_tag, new_tag)]
    scan_finished = QtCore.Signal(int)      # number of Reads with a newer version

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scanner = None
        self.check_button = HoverButton("Check Plates")
        self.check_button.clicked.connect(self.refresh)
        self.update_button = HoverButton("Update Ticked")
        self.update_button.clicked.connect(self.update_ticked)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Read", "Now", "Newest", "Newest path"])
        self.tree.setRootIsDecorated(False)
        self.tree.itemClicked.connect(self._on_item)
        self.summary = small_label("")

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.check_button)
        top.addWidget(self.update_button)
        top.addStretch()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.summary)

    def refresh(self):
        if not nuke_bridge.available():
            self.summary.setText("Checking plates works inside Nuke.")
            return
        if self._scanner is not None:
            return
        reads = nuke_bridge.plate_reads()
        self.summary.setText("Checking {} Read nodes...".format(len(reads)))
        self.check_button.setEnabled(False)
        scanner = _PlateScanner(reads)
        scanner.emitter.done.connect(lambda updates, count, s=scanner: self._finished(updates, count, s))
        self._scanner = scanner
        scanner.start()

    def _finished(self, updates, count, scanner):
        if scanner is not self._scanner:
            return
        self._scanner = None
        self.show_results(updates, count)

    def show_results(self, updates, count):
        self.check_button.setEnabled(True)
        self.tree.clear()
        for read, current, newest, path in updates:
            item = QtWidgets.QTreeWidgetItem(self.tree, [read["name"], current, newest, path])
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.Checked)
            item.setData(0, NODE_ROLE, read["name"])
            item.setData(0, PATH_ROLE, read.get("raw") or read.get("file"))
            item.setForeground(2, QtGui.QBrush(QtGui.QColor(themes.color("builtin"))))
            item.setToolTip(3, path)
        for column in range(3):
            self.tree.resizeColumnToContents(column)
        if updates:
            self.summary.setText("{} of {} Reads have a newer version. Tick the ones to update.".format(
                len(updates), count))
        else:
            self.summary.setText("{} Reads checked: all on the newest version.".format(count))
        self.scan_finished.emit(len(updates))

    def update_ticked(self):
        changed, skipped = [], []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) != QtCore.Qt.Checked:
                continue
            name = item.data(0, NODE_ROLE)
            raw = item.data(0, PATH_ROLE) or ""
            old_tag, new_tag = item.text(1), item.text(2)
            new_raw = shotcheck.replace_version_in(raw, old_tag, new_tag)
            if new_raw == raw:
                skipped.append(name)  # version comes from an expression
                continue
            if nuke_bridge.set_read_file(name, new_raw):
                changed.append((name, old_tag, new_tag))
                item.setCheckState(0, QtCore.Qt.Unchecked)
                item.setText(1, new_tag)
                item.setDisabled(True)
        text = "Updated {} Read{}.".format(len(changed), "" if len(changed) == 1 else "s")
        if skipped:
            text += " Not changed (path is an expression): {}".format(", ".join(skipped))
        if changed:
            text += " Check their frame ranges."
        self.summary.setText(text)
        if changed:
            self.plates_updated.emit(changed)

    def _on_item(self, item, _column=0):
        name = item.data(0, NODE_ROLE)
        if name:
            self.node_activated.emit(name)


# ------------------------------------------------------------- #
#  Pre-render check                                             #
# ------------------------------------------------------------- #
def shot_notes_text(script):
    """Plain text of the shot notes of a script ('' if there are none)."""
    if not script:
        return ""
    path = textops.shot_notes_path(script)
    if not os.path.isfile(path):
        return ""
    try:
        text = fileio.read_text_file(path)[0]
    except Exception:
        return ""
    return fileio.html_to_text(text) if fileio.is_rich_path(path) else text


def collect_issues(write_names, expect=None):
    """Run the pre-render check on Write nodes. Returns [Issue]."""
    script = nuke_bridge.script_path() or ""
    tasks = shotcheck.open_tasks_in(shot_notes_text(script))
    issues = []
    for index, name in enumerate(write_names):
        data = nuke_bridge.write_check_data(name)
        if data is None:
            continue
        write, root, upstream = data
        # Open tasks are listed once, not for every Write.
        issues.extend(shotcheck.check_write(write, root, upstream, tasks if index == 0 else (), expect))
    return issues


class PrerenderDialog(QtWidgets.QDialog):
    """Lists what the pre-render check found; 'Render Anyway' or 'Cancel'."""

    def __init__(self, issues, write_names, action_label="Render Anyway", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Before You Render - {}".format(", ".join(n.split(".")[-1] for n in write_names[:4])))
        self.setStyleSheet(themes.dialog_style())
        self.resize(720, 360)
        tree = QtWidgets.QTreeWidget()
        tree.setHeaderLabels(["Node", "Check"])
        tree.setRootIsDecorated(False)
        for issue in issues:
            item = QtWidgets.QTreeWidgetItem(tree, [issue.node, issue.message])
            item.setForeground(1, QtGui.QBrush(QtGui.QColor(_severity_color(issue.severity))))
            item.setToolTip(1, issue.message)
            item.setData(0, NODE_ROLE, issue.node)
        tree.resizeColumnToContents(0)
        tree.itemDoubleClicked.connect(lambda item, _c: nuke_bridge.select_node(item.data(0, NODE_ROLE)))

        counts = {}
        for issue in issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        summary = QtWidgets.QLabel("{} errors, {} warnings, {} notes. Double-click a line to select the node.".format(
            counts.get("error", 0), counts.get("warning", 0), counts.get("info", 0)))

        go = HoverButton(action_label)
        go.clicked.connect(self.accept)
        cancel = HoverButton("Cancel")
        cancel.clicked.connect(self.reject)
        cancel.setDefault(True)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(summary)
        buttons.addStretch()
        buttons.addWidget(go)
        buttons.addWidget(cancel)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(tree, 1)
        layout.addLayout(buttons)


def confirm_render(write_names, parent=None, expect=None, action_label="Render Anyway"):
    """Check Write nodes; ask only when there are errors or warnings.
    Returns True when the render should go ahead."""
    if not write_names:
        return True
    issues = collect_issues(write_names, expect)
    if not shotcheck.blocking(issues):
        return True
    dialog = PrerenderDialog(issues, write_names, action_label, parent)
    return qt_exec(dialog) == QtWidgets.QDialog.Accepted


# ------------------------------------------------------------- #
#  Version up with a note                                       #
# ------------------------------------------------------------- #
class VersionUpDialog(QtWidgets.QDialog):
    """Asks what changed before saving the next version of the script."""

    def __init__(self, script, new_path, write_count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Version Up")
        self.setStyleSheet(themes.dialog_style())
        self.resize(520, 260)
        old_tag, _n = shotcheck.last_version_token(os.path.basename(script))
        new_tag, _n = shotcheck.last_version_token(os.path.basename(new_path))
        names = QtWidgets.QLabel("{}  →  <b>{}</b>".format(os.path.basename(script), os.path.basename(new_path)))
        self.note = QtWidgets.QPlainTextEdit()
        self.note.setPlaceholderText("What changed in {}? (e.g. fixed edge chatter on hair, f1040-1080)".format(new_tag))
        self.update_writes = QtWidgets.QCheckBox(
            "Change {} in the path of {} Write node{} to {}".format(
                old_tag, write_count, "" if write_count == 1 else "s", new_tag))
        self.update_writes.setChecked(write_count > 0)
        self.update_writes.setVisible(write_count > 0)
        self.add_to_notes = QtWidgets.QCheckBox("Add to the version history in the shot notes")
        self.add_to_notes.setChecked(True)

        ok = HoverButton("Version Up")
        ok.clicked.connect(self.accept)
        cancel = HoverButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(names)
        layout.addWidget(self.note, 1)
        layout.addWidget(self.update_writes)
        layout.addWidget(self.add_to_notes)
        layout.addLayout(buttons)
        self.note.setFocus()

    def keyPressEvent(self, event):
        # Ctrl+Enter confirms from the note field.
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and \
                event.modifiers() & QtCore.Qt.ControlModifier:
            self.accept()
            return
        super().keyPressEvent(event)

    def values(self):
        return self.note.toPlainText().strip(), self.update_writes.isChecked(), self.add_to_notes.isChecked()


def add_history_entry(document, entry):
    """Add an entry under 'Version history' in a QTextDocument
    (the heading is created at the end when missing). One undo step."""
    blocks = []
    block = document.begin()
    while block.isValid():
        blocks.append(block.text())
        block = block.next()
    index, needs_heading = shotcheck.history_insert_index(blocks)
    cursor = QtGui.QTextCursor(document)
    cursor.beginEditBlock()
    try:
        plain = QtGui.QTextCharFormat()
        if needs_heading:
            cursor.movePosition(QtGui.QTextCursor.End)
            if cursor.block().text().strip():
                cursor.insertBlock(QtGui.QTextBlockFormat(), plain)
            bold = QtGui.QTextCharFormat()
            bold.setFontWeight(QtGui.QFont.Bold)
            cursor.insertText(shotcheck.HISTORY_HEADING, bold)
            cursor.insertBlock(QtGui.QTextBlockFormat(), plain)
            cursor.insertText(entry, plain)
        else:
            # New paragraph right after the heading or the last entry.
            previous = document.findBlockByNumber(index - 1)
            cursor.setPosition(previous.position() + previous.length() - 1)
            cursor.insertBlock(QtGui.QTextBlockFormat(), plain)
            cursor.insertText(entry, plain)
    finally:
        cursor.endEditBlock()


# ------------------------------------------------------------- #
#  Flows used by the editor and by Nuke's menus                 #
# ------------------------------------------------------------- #
def expectations(settings):
    return {"colorspace": settings.get_str("render_check/colorspace"),
            "file_type": settings.get_str("render_check/file_type")}


def current_user():
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def show_check(write_names, parent=None, expect=None):
    """Manual pre-render check: always reports, even when all is fine."""
    if not write_names:
        QtWidgets.QMessageBox.information(parent, "Check Writes", "No enabled Write nodes to check.")
        return
    issues = collect_issues(write_names, expect)
    if not issues:
        QtWidgets.QMessageBox.information(parent, "Check Writes", "No problems found in {}.".format(
            ", ".join(n.split(".")[-1] for n in write_names)))
        return
    dialog = PrerenderDialog(issues, write_names, "OK", parent)
    qt_exec(dialog)


def add_history_to_file(script, entry):
    """Add a version history entry to the shot notes file of a script
    (created when missing). Returns the notes path."""
    path = textops.shot_notes_path(script)
    document = QtGui.QTextDocument()
    rich = fileio.is_rich_path(path)
    if os.path.isfile(path):
        text = fileio.read_text_file(path)[0]
        if rich:
            document.setHtml(fileio.strip_body_font(text))
        else:
            document.setPlainText(text)
    add_history_entry(document, entry)
    content = fileio.strip_body_font(document.toHtml()) if rich else document.toPlainText()
    fileio.write_text_file(path, content)
    return path


def version_up_with_note(parent=None, add_entry=None):
    """Save the next version of the script after asking what changed.

    add_entry(script, entry) adds the history line to the shot notes
    (the editor passes one that uses an open notes tab). Returns the new
    path, or None when cancelled."""
    script = nuke_bridge.script_path()
    if not script:
        QtWidgets.QMessageBox.information(parent, "Version Up", "Save the script first.")
        return None
    new_path = shotcheck.next_version_path(script)
    if not new_path:
        QtWidgets.QMessageBox.information(
            parent, "Version Up", "The script name has no version number (like _v001) to count up.")
        return None
    old_tag, _n = shotcheck.last_version_token(os.path.basename(script))
    new_tag, _n = shotcheck.last_version_token(os.path.basename(new_path))
    writes = nuke_bridge.write_paths_with(old_tag)
    dialog = VersionUpDialog(script, new_path, len(writes), parent)
    if qt_exec(dialog) != QtWidgets.QDialog.Accepted:
        return None
    note, update_writes, add_to_notes = dialog.values()
    updates = [(name, shotcheck.replace_version_in(raw, old_tag, new_tag)) for name, raw in writes] \
        if update_writes else []
    try:
        nuke_bridge.save_script_as(new_path, updates)
    except Exception as exc:
        QtWidgets.QMessageBox.warning(parent, "Version Up", "Could not save {}:\n{}".format(new_path, exc))
        return None
    if add_to_notes:
        entry = shotcheck.history_entry(new_tag, note or "(no note)", current_user())
        try:
            (add_entry or add_history_to_file)(new_path, entry)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(parent, "Version Up",
                                          "Saved {}, but the shot notes could not be updated:\n{}".format(
                                              os.path.basename(new_path), exc))
    try:
        from . import dailylog
        dailylog.record_version(new_path, new_tag, note)
    except Exception:
        pass
    return new_path


def plate_history_entry(script, changed):
    """History line for updated plates: 'v012 · date · user - plates: Read1 v003 -> v005'."""
    tag, _n = shotcheck.last_version_token(os.path.basename(script or ""))
    text = "plates: " + ", ".join("{} {} → {}".format(name.split(".")[-1], old, new)
                                  for name, old, new in changed)
    return shotcheck.history_entry(tag or "", text, current_user())


class PlateCheckOnLoad(QtCore.QObject):
    """Background plate check after a script loads; calls on_found(updates, count)
    on the main thread when newer versions exist."""

    def __init__(self, on_found, parent=None):
        super().__init__(parent)
        self.on_found = on_found
        self._scanner = None

    def start(self):
        if self._scanner is not None or not nuke_bridge.available():
            return
        reads = nuke_bridge.plate_reads()
        if not reads:
            return
        scanner = _PlateScanner(reads)
        scanner.emitter.done.connect(self._done)
        self._scanner = scanner
        scanner.start()

    def _done(self, updates, count):
        self._scanner = None
        if updates:
            self.on_found(updates, count)
