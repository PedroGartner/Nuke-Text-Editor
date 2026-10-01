"""Node-setup library (recipes) and snippet library panels."""

import os
import re

from .qt import QtCore, QtGui, QtWidgets, qt_exec
from . import fileio
from . import nkparse
from . import nuke_bridge
from . import snippets as snippet_store
from .widgets import dialog_style, HoverButton, monospace_font, small_label

PATH_ROLE = QtCore.Qt.UserRole


def _open_folder(path):
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))


def recipes_dir():
    return fileio.data_dir("recipes")


class RecipesPanel(QtWidgets.QWidget):
    """Save selected nodes as reusable setups and paste them back."""

    open_file = QtCore.Signal(str)
    message = QtCore.Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(lambda item, _c: self.paste())

        save_button = HoverButton("Save Selected...")
        save_button.setToolTip("Save the selected nodes as a new recipe")
        save_button.clicked.connect(self.save_selected)
        paste_button = HoverButton("Paste")
        paste_button.setToolTip("Paste the recipe into the Node Graph (or double-click)")
        paste_button.clicked.connect(self.paste)
        text_button = HoverButton("Open as Text")
        text_button.clicked.connect(self.open_as_text)
        delete_button = HoverButton("Delete")
        delete_button.clicked.connect(self.delete)
        folder_button = HoverButton("Folder")
        folder_button.clicked.connect(lambda: _open_folder(recipes_dir()))
        refresh_button = HoverButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(save_button)
        row1.addWidget(paste_button)
        row1.addStretch()
        row2 = QtWidgets.QHBoxLayout()
        for button in (text_button, delete_button, folder_button, refresh_button):
            row2.addWidget(button)
        row2.addStretch()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(row1)
        layout.addWidget(self.tree, 1)
        layout.addLayout(row2)
        layout.addWidget(small_label("Team folder: set it in Preferences."))
        self.refresh()

    def _folders(self):
        folders = [("My recipes", recipes_dir())]
        team = self.settings.get_str("team_recipes_folder")
        if team and os.path.isdir(team):
            folders.append(("Team recipes", team))
        return folders

    def refresh(self):
        self.tree.clear()
        for title, folder in self._folders():
            root = QtWidgets.QTreeWidgetItem(self.tree, [title])
            root.setForeground(0, QtGui.QBrush(QtGui.QColor("#8be9fd")))
            root.setData(0, PATH_ROLE, "")
            self._add_folder(root, folder)
            root.setExpanded(True)

    def _add_folder(self, parent, folder):
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            return
        for name in names:
            path = os.path.join(folder, name)
            if name.startswith("."):
                continue
            if os.path.isdir(path):
                item = QtWidgets.QTreeWidgetItem(parent, [name])
                item.setData(0, PATH_ROLE, "")
                self._add_folder(item, path)
            elif name.lower().endswith(".nk"):
                item = QtWidgets.QTreeWidgetItem(parent, [os.path.splitext(name)[0]])
                item.setData(0, PATH_ROLE, path)
                item.setToolTip(0, self._summary(path))

    @staticmethod
    def _summary(path):
        try:
            nodes = nkparse.parse_nk(fileio.read_text_file(path)[0])
        except Exception:
            return path
        classes = [n.cls for n in nodes if not n.group]
        text = ", ".join(classes[:12]) + (" ..." if len(classes) > 12 else "")
        return "{} nodes: {}\n{}".format(len(nodes), text, path)

    def selected_path(self):
        item = self.tree.currentItem()
        return item.data(0, PATH_ROLE) if item is not None else ""

    def save_selected(self):
        if not nuke_bridge.available():
            self.message.emit("Recipes need Nuke.")
            return
        if not nuke_bridge.selected_nodes():
            self.message.emit("Select some nodes in the Node Graph first.")
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Save Recipe",
                                                  "Recipe name (use / for sub-folders):")
        name = name.strip() if ok else ""
        if not name:
            return
        parts = [re.sub(r'[\\:*?"<>|]', "_", p).strip() for p in name.split("/") if p.strip()]
        if not parts:
            return
        path = os.path.join(recipes_dir(), *parts) + ".nk"
        if os.path.exists(path):
            reply = QtWidgets.QMessageBox.question(self, "Replace Recipe", "Replace existing recipe?\n" + path)
            if reply != QtWidgets.QMessageBox.Yes:
                return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            count = nuke_bridge.copy_selected_to_file(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Error", "Could not save recipe:\n{}".format(exc))
            return
        self.refresh()
        self.message.emit("Saved {} nodes as '{}'.".format(count, "/".join(parts)))

    def paste(self):
        path = self.selected_path()
        if not path:
            return
        try:
            if nuke_bridge.paste_file(path):
                self.message.emit("Pasted '{}'.".format(os.path.splitext(os.path.basename(path))[0]))
            else:
                self.message.emit("Pasting recipes needs Nuke.")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Error", "Could not paste recipe:\n{}".format(exc))

    def open_as_text(self):
        path = self.selected_path()
        if path:
            self.open_file.emit(path)

    def delete(self):
        path = self.selected_path()
        if not path:
            return
        if not fileio.norm_path(path).startswith(fileio.norm_path(recipes_dir())):
            self.message.emit("Team recipes can only be deleted from the team folder.")
            return
        reply = QtWidgets.QMessageBox.question(self, "Delete Recipe", "Delete recipe?\n" + path)
        if reply == QtWidgets.QMessageBox.Yes:
            try:
                os.remove(path)
            except OSError as exc:
                QtWidgets.QMessageBox.warning(self, "Error", str(exc))
            self.refresh()


class SnippetDialog(QtWidgets.QDialog):
    """Create or edit a snippet."""

    def __init__(self, snippet=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Snippet")
        self.setStyleSheet(dialog_style())
        snippet = snippet or {}
        self.name_edit = QtWidgets.QLineEdit(snippet.get("name", ""))
        self.trigger_edit = QtWidgets.QLineEdit(snippet.get("trigger", ""))
        self.trigger_edit.setToolTip("Type this word and press Tab to insert the snippet")
        self.language_combo = QtWidgets.QComboBox()
        self.language_combo.addItems(list(snippet_store.LANGUAGES))
        index = self.language_combo.findText(snippet.get("language", "any"))
        self.language_combo.setCurrentIndex(max(0, index))
        self.body_edit = QtWidgets.QPlainTextEdit(snippet.get("body", ""))
        self.body_edit.setFont(monospace_font(9))

        form = QtWidgets.QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Trigger", self.trigger_edit)
        form.addRow("Language", self.language_combo)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QtWidgets.QLabel("Body ($0 = cursor position):"))
        layout.addWidget(self.body_edit, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(460, 380)

    def _accept(self):
        if not re.match(r"^[A-Za-z_][\w-]*$", self.trigger_edit.text().strip()):
            QtWidgets.QMessageBox.warning(self, "Snippet", "The trigger must be a single word.")
            return
        self.accept()

    def snippet(self):
        trigger = self.trigger_edit.text().strip()
        return {
            "name": self.name_edit.text().strip() or trigger,
            "trigger": trigger,
            "language": self.language_combo.currentText(),
            "body": self.body_edit.toPlainText(),
        }


class SnippetsPanel(QtWidgets.QWidget):
    """Browse, insert, run and edit snippets."""

    insert_requested = QtCore.Signal(str)
    run_requested = QtCore.Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.personal = []
        self.team = []

        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Filter")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._populate)
        self.list = QtWidgets.QTreeWidget()
        self.list.setHeaderLabels(["Trigger", "Name", "Language"])
        self.list.setRootIsDecorated(False)
        self.list.itemDoubleClicked.connect(lambda item, _c: self.insert())
        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(monospace_font(9))
        self.preview.setMaximumHeight(110)
        self.list.currentItemChanged.connect(self._show_preview)

        buttons = QtWidgets.QHBoxLayout()
        for label, slot in (("Insert", self.insert), ("Run", self.run), ("New", self.new),
                            ("Edit", self.edit), ("Delete", self.delete)):
            button = HoverButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.preview)
        layout.addLayout(buttons)
        layout.addWidget(small_label("Type a trigger in the editor and press Tab to expand it."))
        self.reload()

    def reload(self):
        try:
            self.personal = snippet_store.load_personal()
        except Exception:
            self.personal = []
        self.team = snippet_store.load_team(self.settings.get_str("team_snippets_folder"))
        self._populate()

    def all_snippets(self):
        return self.personal + self.team

    def provider(self, trigger, language):
        return snippet_store.find_by_trigger(self.all_snippets(), trigger, language)

    def _populate(self, *_args):
        text = self.filter_edit.text().lower()
        self.list.clear()
        for index, snippet in enumerate(self.all_snippets()):
            haystack = (snippet["trigger"] + " " + snippet.get("name", "")).lower()
            if text and text not in haystack:
                continue
            item = QtWidgets.QTreeWidgetItem(self.list, [
                snippet["trigger"],
                snippet.get("name", "") + ("  (team)" if snippet.get("team") else ""),
                snippet.get("language", "any"),
            ])
            item.setData(0, PATH_ROLE, index)
        self.list.resizeColumnToContents(0)

    def _current(self):
        item = self.list.currentItem()
        if item is None:
            return None, None
        index = item.data(0, PATH_ROLE)
        items = self.all_snippets()
        return (index, items[index]) if 0 <= index < len(items) else (None, None)

    def _show_preview(self, *_args):
        _index, snippet = self._current()
        self.preview.setPlainText(snippet["body"] if snippet else "")

    def insert(self):
        _index, snippet = self._current()
        if snippet:
            self.insert_requested.emit(snippet["body"])

    def run(self):
        _index, snippet = self._current()
        if snippet:
            self.run_requested.emit(snippet["body"].replace("$0", ""))

    def new(self):
        dialog = SnippetDialog(parent=self)
        if qt_exec(dialog) == QtWidgets.QDialog.Accepted:
            self.personal.append(dialog.snippet())
            self._save()

    def edit(self):
        index, snippet = self._current()
        if snippet is None or index is None:
            return
        if snippet.get("team"):
            QtWidgets.QMessageBox.information(self, "Snippets", "Team snippets are edited in the team folder.")
            return
        dialog = SnippetDialog(snippet, self)
        if qt_exec(dialog) == QtWidgets.QDialog.Accepted:
            self.personal[index] = dialog.snippet()
            self._save()

    def delete(self):
        index, snippet = self._current()
        if snippet is None or index is None or snippet.get("team"):
            return
        reply = QtWidgets.QMessageBox.question(self, "Delete Snippet", "Delete '{}'?".format(snippet["trigger"]))
        if reply == QtWidgets.QMessageBox.Yes:
            del self.personal[index]
            self._save()

    def _save(self):
        try:
            snippet_store.save_personal(self.personal)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Error", "Could not save snippets:\n{}".format(exc))
        self._populate()
