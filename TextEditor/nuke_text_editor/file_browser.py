"""Collapsible sidebar file browser.

Lets the user pick any root folder (local, network or another drive),
search by name, create folders and move files to the trash.
"""

import os
import shutil

from .qt import QtCore, QtGui, QtWidgets, qt_exec as _exec
from . import themes

# Qt 6 moved QFileSystemModel from QtWidgets to QtGui.
FileSystemModel = getattr(QtGui, "QFileSystemModel", None) or QtWidgets.QFileSystemModel


def _norm(path):
    """Normalise a path for comparisons (case-insensitive on Windows)."""
    return os.path.normcase(os.path.normpath(path))


class FileFilterProxy(QtCore.QSortFilterProxyModel):
    """Proxy that filters by name inside the browser root.

    The root folder and all of its ancestors are always accepted,
    otherwise the tree view would lose its root index as soon as a
    search is typed. Folders are sorted before files.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._root = ""
        self._root_prefix = ""
        if hasattr(self, "setRecursiveFilteringEnabled"):
            self.setRecursiveFilteringEnabled(True)

    def set_root_path(self, path):
        self._root = _norm(path)
        self._root_prefix = self._root.rstrip(os.sep) + os.sep
        self.invalidateFilter()

    def set_filter_text(self, text):
        self._text = (text or "").strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._text:
            return True
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        if not index.isValid():
            return False
        path = _norm(model.filePath(index))
        path_prefix = path.rstrip(os.sep) + os.sep
        # Root folder or one of its ancestors: always keep.
        if path == self._root or self._root_prefix.startswith(path_prefix):
            return True
        # Anything outside the root is never shown anyway.
        if not path.startswith(self._root_prefix):
            return True
        return self._text in model.fileName(index).lower()

    def lessThan(self, left, right):
        model = self.sourceModel()
        left_is_dir = model.isDir(left)
        right_is_dir = model.isDir(right)
        if left_is_dir != right_is_dir:
            return left_is_dir
        return model.fileName(left).lower() < model.fileName(right).lower()


class FileBrowser(QtWidgets.QWidget):
    """Collapsible sidebar file browser with dark theme and basic controls."""

    file_selected = QtCore.Signal(str)  # a file was activated (double-click / Enter)
    path_deleted = QtCore.Signal(str)   # a file or folder was removed

    def __init__(self, settings, parent=None):
        super().__init__(parent)

        self.settings = settings

        self.setMinimumWidth(250)
        self.setMaximumWidth(400)
        self.setVisible(False)

        self._apply_own_style()

        # ---- Models ----
        self.proxy = FileFilterProxy(self)
        self.model = self._new_model()
        self.proxy.setSourceModel(self.model)

        # ---- Root folder row ----
        self.root_edit = QtWidgets.QLineEdit()
        self.root_edit.setToolTip("Root folder. Type a path and press Enter.")
        self.root_edit.returnPressed.connect(self._on_root_edited)

        self.up_btn = QtWidgets.QToolButton()
        self.up_btn.setText("↑")
        self.up_btn.setToolTip("Go to parent folder")
        self.up_btn.clicked.connect(self.go_up)

        self.browse_btn = QtWidgets.QToolButton()
        self.browse_btn.setText("...")
        self.browse_btn.setToolTip("Choose root folder")
        self.browse_btn.clicked.connect(self.browse_root)

        root_row = QtWidgets.QHBoxLayout()
        root_row.setContentsMargins(0, 0, 0, 4)
        root_row.setSpacing(3)
        root_row.addWidget(self.root_edit, 1)
        root_row.addWidget(self.up_btn)
        root_row.addWidget(self.browse_btn)

        # ---- Search ----
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search (expanded folders)")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.on_filter_text_changed)

        # ---- Tree view ----
        self.tree = QtWidgets.QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setAnimated(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, QtCore.Qt.AscendingOrder)
        for column in (1, 2, 3):
            self.tree.hideColumn(column)
        self.tree.setHeaderHidden(True)
        self.tree.activated.connect(self.open_file)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

        # ---- Divider ----
        divider_wrapper = QtWidgets.QVBoxLayout()
        divider_wrapper.setContentsMargins(3, 0, 3, 0)
        divider_wrapper.setSpacing(0)
        self.divider = QtWidgets.QFrame()
        self.divider.setFrameShape(QtWidgets.QFrame.VLine)
        self.divider.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.divider.setStyleSheet("color: {0}; background-color: {0};".format(themes.color("border")))
        self.divider.setFixedWidth(1)
        divider_wrapper.addWidget(self.divider)

        # ---- Bottom buttons ----
        self.bottom_container = QtWidgets.QFrame()
        self._apply_bottom_style()
        self.new_btn = QtWidgets.QPushButton("New")
        self.new_btn.setToolTip("Create new folder")
        self.new_btn.clicked.connect(self.create_folder)

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.setToolTip("Reload file list from disk")
        self.refresh_btn.clicked.connect(self.refresh_view)

        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.delete_btn.setToolTip("Move selected item to the trash")
        self.delete_btn.clicked.connect(self.delete_item)

        bottom_bar = QtWidgets.QHBoxLayout()
        bottom_bar.setContentsMargins(0, 6, 0, 6)
        bottom_bar.setSpacing(8)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.new_btn)
        bottom_bar.addWidget(self.refresh_btn)
        bottom_bar.addWidget(self.delete_btn)
        bottom_bar.addStretch()
        self.bottom_container.setLayout(bottom_bar)

        # ---- Layout ----
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 0)
        layout.setSpacing(0)
        layout.addLayout(root_row)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.tree)
        layout.addWidget(self.bottom_container)

        wrapper = QtWidgets.QHBoxLayout(self)
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.setSpacing(0)
        wrapper.addLayout(layout)
        wrapper.addLayout(divider_wrapper)

        # ---- Initial root ----
        saved_root = self.settings.get_str("browser/root", "")
        if not saved_root or not os.path.isdir(saved_root):
            saved_root = QtCore.QDir.homePath()
        self.root_path = ""
        self.set_root(str(saved_root))

    def _apply_own_style(self):
        self.setStyleSheet(themes.window_style() + themes.button_style() + """
            QPushButton {{ min-width: 50px; padding: 4px 10px; }}
            QToolButton {{ background-color: {button}; border: 1px solid {border}; border-radius: 3px; padding: 1px 5px; }}
        """.format(**themes.THEMES[themes.current_name()]))

    def _apply_bottom_style(self):
        self.bottom_container.setStyleSheet("QFrame {{ background-color: {}; border-top: 1px solid {}; }}".format(
            themes.color("panel"), themes.color("border")))

    def apply_theme(self):
        self._apply_own_style()
        self._apply_bottom_style()
        self.divider.setStyleSheet("color: {0}; background-color: {0};".format(themes.color("border")))

    # ------------------------------------------------------------- #
    #  Model / root handling                                        #
    # ------------------------------------------------------------- #
    def _new_model(self):
        model = FileSystemModel(self)
        model.setFilter(QtCore.QDir.AllDirs | QtCore.QDir.Files | QtCore.QDir.NoDotAndDotDot)
        model.setRootPath("")
        return model

    def _apply_root_index(self):
        source_root = self.model.index(self.root_path)
        self.tree.setRootIndex(self.proxy.mapFromSource(source_root))

    def set_root(self, path):
        """Show the given folder as the top of the tree."""
        path = os.path.normpath(os.path.expanduser(path))
        if not os.path.isdir(path):
            QtWidgets.QMessageBox.warning(self, "Folder not found", "Folder does not exist:\n{}".format(path))
            self.root_edit.setText(self.root_path)
            return
        self.root_path = path
        self.model.setRootPath(path)
        self.proxy.set_root_path(path)
        self._apply_root_index()
        self.root_edit.setText(path)
        self.settings.set("browser/root", path)

    def _on_root_edited(self):
        self.set_root(self.root_edit.text().strip())

    def go_up(self):
        parent = os.path.dirname(self.root_path.rstrip("\\/")) or self.root_path
        if parent and parent != self.root_path:
            self.set_root(parent)

    def browse_root(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Root Folder", self.root_path)
        if path:
            self.set_root(path)

    def _selected_source_index(self):
        index = self.tree.currentIndex()
        if not index.isValid():
            return QtCore.QModelIndex()
        return self.proxy.mapToSource(index)

    def selected_path(self):
        """Return the path of the selected item, or an empty string."""
        source_index = self._selected_source_index()
        if not source_index.isValid():
            return ""
        return self.model.filePath(source_index)

    # ------------------------------------------------------------- #
    #  Sidebar toggle                                               #
    # ------------------------------------------------------------- #
    def toggle_visibility(self):
        self.setVisible(not self.isVisible())

    # ------------------------------------------------------------- #
    #  Actions                                                      #
    # ------------------------------------------------------------- #
    def open_file(self, index):
        """Emit the file path when a file is activated."""
        source_index = self.proxy.mapToSource(index)
        if source_index.isValid() and not self.model.isDir(source_index):
            self.file_selected.emit(self.model.filePath(source_index))

    def create_folder(self):
        """Create a new folder inside the selected folder (or the root)."""
        base_path = self.selected_path() or self.root_path
        if not os.path.isdir(base_path):
            base_path = os.path.dirname(base_path)

        folder_name, ok = QtWidgets.QInputDialog.getText(self, "Create Folder", "Folder name:")
        folder_name = folder_name.strip() if ok else ""
        if not folder_name:
            return
        if any(ch in folder_name for ch in "\\/"):
            QtWidgets.QMessageBox.warning(self, "Error", "Folder name cannot contain slashes.")
            return
        new_path = os.path.join(base_path, folder_name)
        try:
            os.makedirs(new_path, exist_ok=True)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Error", "Could not create folder:\n{}".format(e))
            return
        new_index = self.proxy.mapFromSource(self.model.index(new_path))
        if new_index.isValid():
            self.tree.setCurrentIndex(new_index)

    def refresh_view(self):
        """Rebuild the model so changes made outside Nuke show up."""
        old_model = self.model
        self.model = self._new_model()
        self.proxy.setSourceModel(self.model)
        old_model.deleteLater()
        self.proxy.set_root_path(self.root_path)
        self.model.setRootPath(self.root_path)
        self._apply_root_index()

    def delete_item(self):
        """Move the selected file or folder to the trash (with confirmation)."""
        path = self.selected_path()
        if not path:
            return
        if _norm(path) == _norm(self.root_path):
            return

        can_trash = hasattr(QtCore.QFile, "moveToTrash")
        action = "Move to trash" if can_trash else "Permanently delete"
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Confirm Delete",
            "{}:\n{}?".format(action, path),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return

        try:
            trashed = False
            if can_trash:
                result = QtCore.QFile.moveToTrash(path)
                trashed = result[0] if isinstance(result, tuple) else bool(result)
                if not trashed:
                    again = QtWidgets.QMessageBox.question(
                        self,
                        "Trash Unavailable",
                        "Could not move to the trash (common on network drives).\n"
                        "Delete permanently instead?\n\n{}".format(path),
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                        QtWidgets.QMessageBox.No,
                    )
                    if again != QtWidgets.QMessageBox.Yes:
                        return
            if not trashed:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Error", "Could not delete:\n{}".format(e))
            return
        self.path_deleted.emit(path)

    def _show_context_menu(self, pos):
        index = self.tree.indexAt(pos)
        path = ""
        if index.isValid():
            self.tree.setCurrentIndex(index)
            path = self.model.filePath(self.proxy.mapToSource(index))

        menu = QtWidgets.QMenu(self)
        open_action = root_action = None
        if path and os.path.isfile(path):
            open_action = menu.addAction("Open")
        if path and os.path.isdir(path):
            root_action = menu.addAction("Set as Root")
        new_action = menu.addAction("New Folder...")
        delete_action = menu.addAction("Delete") if path else None
        menu.addSeparator()
        refresh_action = menu.addAction("Refresh")

        chosen = _exec(menu, self.tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == open_action:
            self.file_selected.emit(path)
        elif chosen == root_action:
            self.set_root(path)
        elif chosen == new_action:
            self.create_folder()
        elif chosen == delete_action:
            self.delete_item()
        elif chosen == refresh_action:
            self.refresh_view()

    # ------------------------------------------------------------- #
    #  Search filter                                                #
    # ------------------------------------------------------------- #
    def on_filter_text_changed(self, text):
        """Filter the tree by name. Only folders that have been loaded
        (expanded at least once) are searched."""
        self.proxy.set_filter_text(text)
        self._apply_root_index()
