# ------------------------------------------------------------- #
#  Name: FileBrowser.py ####################################### #
#  Version: 1.7 ############################################### #
#  Author: Pedro Gartner ###################################### #
# ############################################################# #
#  DESCRIPTION: ############################################### #
# ------------------------------------------------------------- #
#  - Based on Version 1.6.                                     #
#  - Divider line slightly inset (3 px margin).                 #
#  - Keeps same style, alignment, and dark theme.               #
# ------------------------------------------------------------- #

from PySide6 import QtCore, QtGui, QtWidgets
import os
import shutil


class FileBrowser(QtWidgets.QWidget):
    """Collapsible sidebar file browser with dark theme and basic controls."""

    file_selected = QtCore.Signal(str)  # Emitted when file double-clicked

    def __init__(self, parent=None):
        super(FileBrowser, self).__init__(parent)

        # ---- General layout ----
        self.setMinimumWidth(250)
        self.setMaximumWidth(400)
        self.setVisible(False)

        # ---- Dark UI style ----
        self.setStyleSheet("""
            QWidget { background-color: #232323; color: #dddddd; font-size: 11px; }
            QTreeView {
                background-color: #1e1e1e;
                border: none;
                border-radius: 3px;
            }
            QPushButton {
                background-color: #2f2f2f;
                border: 1px solid #555555;
                color: #dddddd;
                border-radius: 3px;
                padding: 4px 10px;
                min-width: 50px;
            }
            QPushButton:hover { background-color: #3f3f3f; }
        """)

        # ---- File system model ----
        self.model = QtWidgets.QFileSystemModel()
        self.model.setRootPath(QtCore.QDir.homePath())
        self.model.setFilter(QtCore.QDir.AllDirs | QtCore.QDir.Files | QtCore.QDir.NoDotAndDotDot)

        # ---- Tree view ----
        self.tree = QtWidgets.QTreeView()
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.index(QtCore.QDir.homePath()))
        self.tree.setAnimated(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, QtCore.Qt.AscendingOrder)
        self.tree.hideColumn(1)
        self.tree.hideColumn(2)
        self.tree.hideColumn(3)
        self.tree.setHeaderHidden(True)
        self.tree.doubleClicked.connect(self.open_file)

        # ---- Divider (slightly inset, softer tone) ----
        divider_wrapper = QtWidgets.QVBoxLayout()
        divider_wrapper.setContentsMargins(3, 0, 3, 0)
        divider_wrapper.setSpacing(0)

        self.divider = QtWidgets.QFrame()
        self.divider.setFrameShape(QtWidgets.QFrame.VLine)
        self.divider.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.divider.setStyleSheet("color: #333333; background-color: #333333;")
        self.divider.setFixedWidth(1)

        divider_wrapper.addWidget(self.divider)

        # ---- Bottom container ----
        self.bottom_container = QtWidgets.QFrame()
        self.bottom_container.setStyleSheet("""
            QFrame {
                background-color: #1e1e1e;
                border-top: 1px solid #444444;
            }
        """)

        # ---- Bottom button bar ----
        self.new_btn = QtWidgets.QPushButton("New")
        self.new_btn.setToolTip("Create new folder")
        self.new_btn.clicked.connect(self.create_folder)

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.setToolTip("Reload file list")
        self.refresh_btn.clicked.connect(self.refresh_view)

        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.delete_btn.setToolTip("Delete selected item")
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

        # ---- Main layout ----
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tree)
        layout.addWidget(self.bottom_container)

        # ---- Wrapper with inset divider ----
        wrapper = QtWidgets.QHBoxLayout(self)
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.setSpacing(0)
        wrapper.addLayout(layout)
        wrapper.addLayout(divider_wrapper)

    # ------------------------------------------------------------- #
    #  Sidebar Toggle                                               #
    # ------------------------------------------------------------- #
    def toggle_visibility(self):
        """Show or hide the file browser sidebar."""
        self.setVisible(not self.isVisible())

    # ------------------------------------------------------------- #
    #  File Open Handler                                            #
    # ------------------------------------------------------------- #
    def open_file(self, index):
        """Emit file path when user double-clicks a file."""
        if not self.model.isDir(index):
            path = self.model.filePath(index)
            self.file_selected.emit(path)

    # ------------------------------------------------------------- #
    #  Create Folder                                                #
    # ------------------------------------------------------------- #
    def create_folder(self):
        """Create a new folder inside the selected directory."""
        index = self.tree.currentIndex()
        base_path = self.model.filePath(index) if index.isValid() else QtCore.QDir.homePath()
        if not os.path.isdir(base_path):
            base_path = os.path.dirname(base_path)

        folder_name, ok = QtWidgets.QInputDialog.getText(self, "Create Folder", "Folder name:")
        if ok and folder_name.strip():
            new_path = os.path.join(base_path, folder_name.strip())
            try:
                os.makedirs(new_path, exist_ok=True)
                self.refresh_view()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Error", f"Could not create folder:\n{e}")

    # ------------------------------------------------------------- #
    #  Refresh View                                                 #
    # ------------------------------------------------------------- #
    def refresh_view(self):
        """Reload the file tree manually (PySide6 QFileSystemModel workaround)."""
        current_root = QtCore.QDir.homePath()
        self.model.setRootPath(current_root)
        self.tree.setRootIndex(self.model.index(current_root))

    # ------------------------------------------------------------- #
    #  Delete File or Folder                                        #
    # ------------------------------------------------------------- #
    def delete_item(self):
        """Delete the selected file or folder with confirmation."""
        index = self.tree.currentIndex()
        if not index.isValid():
            return

        path = self.model.filePath(index)
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete:\n{os.path.basename(path)}?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )

        if confirm == QtWidgets.QMessageBox.Yes:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                self.refresh_view()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Error", f"Could not delete:\n{e}")
