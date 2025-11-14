# ------------------------------------------------------------- #
#  Name: Nuke Text Editor ####################################  #
#  Version: 5.8 ##############################################  #
#  Author: Pedro Gartner #####################################  #
# ############################################################  #
#  Last Updated: 15th November, 2025 #########################  #
# ------------------------------------------------------------- #
#  DESCRIPTION: ##############################################  #
# ------------------------------------------------------------- #
#  This script creates a custom Text Editor panel for Nuke.     #
#  It is designed for artists/TDs to write and manage notes,    #
#  task lists and text files directly inside Nuke using a       #
#  simple, dark-themed interface.                               #
#                                                               #
#  Main features:                                               #
#   - Rich text editor (font family, size, bold/italic/underline#
#     text color and highlight, basic alignment).               #
#   - Optional file browser sidebar to navigate folders/files.  #
#   - Open, Save and Save As operations for .txt files.         #
#   - Recent files list (under the Options menu).               #
#   - Find / Replace dialog.                                    #
#   - Live word and character counter at the bottom.            #
#   - About link with author + GitHub information.              #
#                                                               #
#  You can safely customize:                                    #
#   - Default font family and size.                             #
#   - Colors in the style sheets.                               #
#   - Menu entries, button labels, and maximum recent files.    #
#                                                               #
#  The core structure is:                                       #
#   - PersistentTextEdit:  QTextEdit subclass with formatting   #
#                          behavior on new lines.               #
#   - FindReplaceDialog:   small dialog for searching text.     #
#   - TextEditorWidget:    main widget that builds the UI and   #
#                          all logic.                           #
#   - show_texteditor():   function used by Nuke to show/raise  #
#                          a single shared instance of the tool.#
# ------------------------------------------------------------- #

import nuke
import nukescripts
import webbrowser
import os

try:
    # Try PySide2 first (commonly used in many Nuke setups)
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    # Fallback to PySide6 if PySide2 is not available
    from PySide6 import QtCore, QtGui, QtWidgets

from FileBrowser import FileBrowser  # Custom file browser widget (separate module)

_text_editor_instance = None  # Global reference to keep a single shared instance


# ------------------------------------------------------------- #
#  Persistent TextEdit (keeps formatting on new lines)          #
# ------------------------------------------------------------- #
class PersistentTextEdit(QtWidgets.QTextEdit):
    """
    QTextEdit subclass that:
      - Preserves the current character format when pressing Enter.
      - Emits 'emptied' when the entire editor becomes empty.

    You can change or extend this class if you want to customize how
    new lines behave (for example, to reset formatting on every line).
    """
    emptied = QtCore.Signal()

    def keyPressEvent(self, event):
        # Keep the same char format on newline
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            cursor = self.textCursor()
            fmt = cursor.charFormat()
            # Call the base implementation to insert the new line
            super(PersistentTextEdit, self).keyPressEvent(event)
            # Reapply the previous character format to the new cursor position
            cursor = self.textCursor()
            cursor.mergeCharFormat(fmt)
            self.mergeCurrentCharFormat(fmt)
        else:
            # For all other keys, use default behavior
            super(PersistentTextEdit, self).keyPressEvent(event)

        # If content is empty, notify listeners (used to reapply defaults)
        if self.toPlainText().strip() == "":
            self.emptied.emit()


# ------------------------------------------------------------- #
#  Clickable label (About link)                                 #
# ------------------------------------------------------------- #
class ClickableLabel(QtWidgets.QLabel):
    """
    Small QLabel subclass that behaves like a hyperlink.
    Emits 'clicked' when it is pressed.

    This is used for the 'About' label at the bottom of the UI.
    """
    clicked = QtCore.Signal()

    def __init__(self, text=""):
        super().__init__(text)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet("color: #dddddd; text-decoration: underline;")

    def enterEvent(self, event):
        # Slight color change on hover to give visual feedback
        self.setStyleSheet("color: #ffffff; text-decoration: underline;")

    def leaveEvent(self, event):
        # Revert color when the mouse leaves
        self.setStyleSheet("color: #dddddd; text-decoration: underline;")

    def mousePressEvent(self, event):
        # Emit a signal instead of hardcoding any action here
        self.clicked.emit()


# ------------------------------------------------------------- #
#  HoverButton (styled push button)                             #
# ------------------------------------------------------------- #
class HoverButton(QtWidgets.QPushButton):
    """
    Push button with a consistent dark style and hover behavior.

    Use this class whenever you add new buttons to keep the style
    consistent with the rest of the UI.
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setStyleSheet("""
            QPushButton {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                padding: 5px 10px;
                border-radius: 4px;
                color: #dddddd;
            }
            QPushButton:hover { background-color: #4c4c4c; }
            QPushButton:checked {
                background-color: #505050;
                border: 1px solid #888888;
            }
        """)


# ------------------------------------------------------------- #
#  Find / Replace Dialog                                        #
# ------------------------------------------------------------- #
class FindReplaceDialog(QtWidgets.QDialog):
    """
    Simple modal dialog for finding and replacing text in the editor.

    Hooks into the main QTextEdit instance passed in on initialization.
    """
    def __init__(self, parent, text_edit):
        super().__init__(parent)
        self.text_edit = text_edit
        self.setWindowTitle("Find / Replace")
        self.setModal(True)
        self.setFixedSize(300, 140)
        self.setStyleSheet("""
            QDialog { background-color: #2c2c2c; color: #dddddd; }
            QLineEdit { background-color: #3c3c3c; border: 1px solid #555555; color: #dddddd; padding: 4px; }
            QPushButton { background-color: #3c3c3c; border: 1px solid #555555; color: #dddddd; padding: 4px; }
            QPushButton:hover { background-color: #4c4c4c; }
        """)

        layout = QtWidgets.QGridLayout(self)

        # --- Find / Replace fields ---
        layout.addWidget(QtWidgets.QLabel("Find:"), 0, 0)
        self.find_input = QtWidgets.QLineEdit()
        layout.addWidget(self.find_input, 0, 1)

        layout.addWidget(QtWidgets.QLabel("Replace:"), 1, 0)
        self.replace_input = QtWidgets.QLineEdit()
        layout.addWidget(self.replace_input, 1, 1)

        # --- Buttons ---
        self.find_button = QtWidgets.QPushButton("Find Next")
        self.replace_button = QtWidgets.QPushButton("Replace")
        self.replace_all_button = QtWidgets.QPushButton("Replace All")

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addWidget(self.find_button)
        btn_layout.addWidget(self.replace_button)
        btn_layout.addWidget(self.replace_all_button)
        layout.addLayout(btn_layout, 2, 0, 1, 2)

        # Connect signals
        self.find_button.clicked.connect(self.find_text)
        self.replace_button.clicked.connect(self.replace_text)
        self.replace_all_button.clicked.connect(self.replace_all)

        self.last_search = ""  # Keeps track of the last search term

    def find_text(self):
        """Find the next occurrence of the text in the editor."""
        text = self.find_input.text()
        if not text:
            return

        doc = self.text_edit.document()
        cursor = self.text_edit.textCursor()

        # If the search term changed, start from the top again
        if text != self.last_search:
            cursor.movePosition(QtGui.QTextCursor.Start)

        found = doc.find(text, cursor)
        if found.isNull():
            QtWidgets.QMessageBox.information(self, "Find", "No more matches found.")
        else:
            self.text_edit.setTextCursor(found)

        self.last_search = text

    def replace_text(self):
        """Replace the current selection and jump to the next match."""
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            cursor.insertText(self.replace_input.text())
            self.find_text()  # Find the next one after replacing

    def replace_all(self):
        """Replace all occurrences of the searched text in the whole document."""
        text = self.find_input.text()
        if not text:
            return

        replace = self.replace_input.text()
        content = self.text_edit.toPlainText().replace(text, replace)
        self.text_edit.setPlainText(content)
        QtWidgets.QMessageBox.information(self, "Replace All", "All occurrences replaced.")


# ------------------------------------------------------------- #
#  Main Text Editor Widget                                      #
# ------------------------------------------------------------- #
class TextEditorWidget(QtWidgets.QWidget):
    """
    Main widget that builds and manages the Nuke Text Editor UI.

    This is the central place where you can:
      - Change default fonts.
      - Adjust styles and colors.
      - Add new buttons, menus or tools.
    """

    # Default font configuration (safe to change)
    DEFAULT_FONT_FAMILY = "Arial"
    DEFAULT_FONT_SIZE = 11

    # Maximum number of recent files to keep in the menu
    MAX_RECENT_FILES = 5

    def __init__(self, parent=None):
        super().__init__(parent)

        # Window configuration (title & size)
        self.setWindowTitle("Text Editor")
        self.setMinimumSize(800, 500)

        # Tracks whether there are unsaved changes
        self.is_modified = False
        # Path of the current file (None if untitled)
        self.current_file_path = None
        # Internal list of recent file paths
        self.recent_files = []

        # ---- Global Widget Style (colors, fonts, etc.) ----
        # You can safely tweak colors here to change the overall theme.
        self.setStyleSheet("""
            QWidget { background-color: #2c2c2c; color: #dddddd; font-size: 11px; }
            QTextEdit { background-color: #1e1e1e; border: 1px solid #444444;
                        border-radius: 4px; padding: 6px; color: #e6e6e6; }
            QComboBox, QFontComboBox { background-color: #3c3c3c;
                        border: 1px solid #555555; padding: 2px 5px;
                        border-radius: 4px; color: #dddddd; }
            QLabel { color: #e0e0e0; }
            QMenu { background-color: #3c3c3c; color: #dddddd; }
            QMenu::item:selected { background-color: #555555; }
        """)

        # ---- Main text editor area ----
        self.text_edit = PersistentTextEdit(self)
        self.text_edit.setMinimumHeight(300)
        self.text_edit.textChanged.connect(self.on_text_changed)
        self.text_edit.emptied.connect(self.on_text_emptied)
        self.apply_default_formatting()  # Apply default font and colors

        # ---- Word Count Bar (bottom-left) ----
        # Shows "Words: X | Characters: Y" and updates as you type.
        self.status_bar = QtWidgets.QLabel("Words: 0 | Characters: 0")
        self.status_bar.setAlignment(QtCore.Qt.AlignLeft)
        self.status_bar.setStyleSheet("color: #888888; padding: 4px; font-size: 10px;")
        self.text_edit.textChanged.connect(self.update_word_count)

        # ---- File browser sidebar ----
        # Comes from external FileBrowser module; used to explore the file system.
        self.file_browser = FileBrowser(self)
        self.file_browser.file_selected.connect(self.load_file_from_browser)

        # ---- Sidebar toggle button ----
        self.sidebar_button = HoverButton("Files")
        self.sidebar_button.setCheckable(True)
        self.sidebar_button.clicked.connect(self.toggle_sidebar)

        # ---- Options menu (New/Open/Save/etc.) ----
        self.options_button = HoverButton("Options")
        self.file_menu = QtWidgets.QMenu(self)
        self.file_menu.setStyleSheet("""
            QMenu { background-color: #3c3c3c; color: #dddddd; }
            QMenu::item:selected { background-color: #555555; }
        """)
        self.options_button.setMenu(self.file_menu)

        # Core file actions
        self.file_menu.addAction("New File", self.new_file)
        self.file_menu.addAction("Open", self.open)
        self.file_menu.addAction("Save", self.save)
        self.file_menu.addAction("Save As", self.save_as)
        self.file_menu.addSeparator()

        # Find / Replace action
        self.file_menu.addAction("Find / Replace", self.show_find_replace)
        self.file_menu.addSeparator()

        # Recent files submenu
        self.recent_menu = self.file_menu.addMenu("Recent Files")
        self.update_recent_files_menu()

        # ---- Formatting buttons (bold/italic/underline/colors/undo/redo) ----
        self.bold_button = HoverButton("B"); self.bold_button.setCheckable(True)
        self.italic_button = HoverButton("I"); self.italic_button.setCheckable(True)
        self.underline_button = HoverButton("U"); self.underline_button.setCheckable(True)
        self.color_button = HoverButton("Color")
        self.highlight_button = HoverButton("Highlight")
        self.undo_button = HoverButton("Undo")
        self.redo_button = HoverButton("Redo")

        # ---- Alignment dropdown ----
        # Keeps text alignment controls compact and clean.
        self.align_dropdown = QtWidgets.QComboBox()
        self.align_dropdown.addItems(["Left", "Center", "Right", "Justify"])
        self.align_dropdown.setCurrentIndex(0)
        self.align_dropdown.setMinimumWidth(80)
        self.align_dropdown.setMaximumWidth(90)
        self.align_dropdown.currentIndexChanged.connect(self.change_alignment)

        # ---- Font size dropdown ----
        self.fontsize_combo = QtWidgets.QComboBox()
        self.fontsize_combo.addItems([str(i) for i in range(8, 25)])
        self.fontsize_combo.setCurrentText(str(self.DEFAULT_FONT_SIZE))

        # ---- Font family dropdown ----
        self.fontfamily_combo = QtWidgets.QFontComboBox()
        self.fontfamily_combo.setCurrentFont(QtGui.QFont(self.DEFAULT_FONT_FAMILY))
        self.fontfamily_combo.setMaximumHeight(24)
        self.fontfamily_combo.setMaximumWidth(130)  # Adjusted width to keep toolbar compact
        self.fontfamily_combo.setMaxVisibleItems(8)
        self.fontfamily_combo.currentFontChanged.connect(self.preview_font)
        self.fontfamily_combo.activated.connect(self.change_fontfamily)

        # ---- Top toolbar layout ----
        # This row holds the sidebar toggle, options, font controls, formatting, etc.
        toolbar_layout = QtWidgets.QHBoxLayout()
        toolbar_layout.addWidget(self.sidebar_button)
        toolbar_layout.addWidget(self.options_button)
        toolbar_layout.addSpacing(10)
        toolbar_layout.addWidget(QtWidgets.QLabel("Font size:"))
        toolbar_layout.addWidget(self.fontsize_combo)
        toolbar_layout.addWidget(QtWidgets.QLabel("Font:"))
        toolbar_layout.addWidget(self.fontfamily_combo)
        toolbar_layout.addWidget(self.bold_button)
        toolbar_layout.addWidget(self.italic_button)
        toolbar_layout.addWidget(self.underline_button)
        toolbar_layout.addWidget(self.color_button)
        toolbar_layout.addWidget(self.highlight_button)
        toolbar_layout.addWidget(self.align_dropdown)
        toolbar_layout.addWidget(self.undo_button)
        toolbar_layout.addWidget(self.redo_button)
        toolbar_layout.addStretch()

        # ---- Split area (left: file browser, right: text editor) ----
        split_layout = QtWidgets.QHBoxLayout()
        split_layout.addWidget(self.file_browser)
        split_layout.addWidget(self.text_edit, 1)

        # ---- Bottom bar (word count left + About link right) ----
        about_label = ClickableLabel("About")
        about_label.setAlignment(QtCore.Qt.AlignRight)
        about_label.clicked.connect(self.show_about)

        bottom_layout = QtWidgets.QHBoxLayout()
        bottom_layout.addWidget(self.status_bar)
        bottom_layout.addStretch()
        bottom_layout.addWidget(about_label)

        # ---- Main vertical layout (toolbar -> split area -> bottom bar) ----
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.addLayout(toolbar_layout)
        main_layout.addLayout(split_layout)
        main_layout.addLayout(bottom_layout)

        # ---- Connect formatting signals ----
        self.bold_button.toggled.connect(self.toggle_bold)
        self.italic_button.toggled.connect(self.toggle_italic)
        self.underline_button.toggled.connect(self.toggle_underline)
        self.fontsize_combo.currentTextChanged.connect(self.change_fontsize)
        self.color_button.clicked.connect(self.choose_text_color)
        self.highlight_button.clicked.connect(self.choose_highlight_color)
        self.undo_button.clicked.connect(self.text_edit.undo)
        self.redo_button.clicked.connect(self.text_edit.redo)

        # Initialize the word count display
        self.update_word_count()

    # ------------------------------------------------------------- #
    #  Utility Methods                                              #
    # ------------------------------------------------------------- #
    def update_word_count(self):
        """Update the word/character counter label at the bottom."""
        text = self.text_edit.toPlainText()
        words = len(text.split())
        chars = len(text)
        self.status_bar.setText(f"Words: {words} | Characters: {chars}")

    def add_recent_file(self, path):
        """Insert a path into the recent files list and update the menu."""
        if path not in self.recent_files:
            self.recent_files.insert(0, path)
        # Keep list size under the configured maximum
        self.recent_files = self.recent_files[:self.MAX_RECENT_FILES]
        self.update_recent_files_menu()

    def update_recent_files_menu(self):
        """Rebuild the Recent Files submenu based on self.recent_files."""
        self.recent_menu.clear()
        if not self.recent_files:
            self.recent_menu.addAction("(No Recent Files)").setEnabled(False)
        else:
            for path in self.recent_files:
                action = self.recent_menu.addAction(path)
                # Use a lambda with a default argument to capture the path
                action.triggered.connect(lambda checked, p=path: self.load_file_from_browser(p))

    def show_find_replace(self):
        """Open the Find/Replace dialog for the current text editor."""
        dialog = FindReplaceDialog(self, self.text_edit)
        dialog.exec_()

    # ------------------------------------------------------------- #
    #  Core Text & Formatting Methods                               #
    # ------------------------------------------------------------- #
    def apply_default_formatting(self):
        """Apply the default font and colors to the editor and document."""
        fmt = QtGui.QTextCharFormat()
        fmt.setFont(QtGui.QFont(self.DEFAULT_FONT_FAMILY, self.DEFAULT_FONT_SIZE))
        fmt.setForeground(QtGui.QBrush(QtGui.QColor("#e6e6e6")))
        fmt.setBackground(QtGui.QBrush(QtGui.QColor("#1e1e1e")))
        self.text_edit.setCurrentCharFormat(fmt)
        self.text_edit.document().setDefaultFont(QtGui.QFont(self.DEFAULT_FONT_FAMILY, self.DEFAULT_FONT_SIZE))

    def on_text_emptied(self):
        """Called when the text editor becomes completely empty."""
        # We simply reapply the default formatting when all text is gone.
        self.apply_default_formatting()

    def toggle_sidebar(self):
        """Show or hide the file browser sidebar."""
        visible = not self.file_browser.isVisible()
        self.file_browser.setVisible(visible)
        self.sidebar_button.setChecked(visible)

    def on_text_changed(self):
        """Slot called every time the text changes."""
        self.is_modified = True
        self.update_word_count()

    def change_alignment(self, index):
        """Change the paragraph alignment based on dropdown selection."""
        if index == 0:
            self.text_edit.setAlignment(QtCore.Qt.AlignLeft)
        elif index == 1:
            self.text_edit.setAlignment(QtCore.Qt.AlignCenter)
        elif index == 2:
            self.text_edit.setAlignment(QtCore.Qt.AlignRight)
        elif index == 3:
            self.text_edit.setAlignment(QtCore.Qt.AlignJustify)

    # ------------------------------------------------------------- #
    #  File Operations                                              #
    # ------------------------------------------------------------- #
    def new_file(self):
        """Clear the editor and start a new untitled file."""
        if self.is_modified:
            reply = QtWidgets.QMessageBox.question(
                self, "Save Changes?",
                "Do you want to save before creating a new file?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel)
            if reply == QtWidgets.QMessageBox.Yes:
                self.save()
            elif reply == QtWidgets.QMessageBox.Cancel:
                return

        self.text_edit.clear()
        self.current_file_path = None
        self.is_modified = False

        # Reset formatting controls and reapply the default font
        self.bold_button.setChecked(False)
        self.italic_button.setChecked(False)
        self.underline_button.setChecked(False)
        self.apply_default_formatting()

    def load_file_from_browser(self, path):
        """Load the file at 'path' into the text editor (used by FileBrowser)."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.text_edit.setPlainText(f.read())
            self.current_file_path = path
            self.add_recent_file(path)
            self.is_modified = False
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Error", f"Could not open file:\n{e}")

    def save(self):
        """Save to the current file path, or trigger Save As if none."""
        if self.current_file_path:
            try:
                with open(self.current_file_path, "w", encoding="utf-8") as f:
                    f.write(self.text_edit.toPlainText())
                self.add_recent_file(self.current_file_path)
                self.is_modified = False
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Error", f"Could not save file:\n{e}")
        else:
            self.save_as()

    def save_as(self):
        """Ask the user where to save the file and update the current path."""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save As", "", "Text Files (*.txt);;All Files (*)"
        )
        if path:
            # Ensure the file ends with .txt to keep things consistent
            if not path.lower().endswith(".txt"):
                path += ".txt"
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.text_edit.toPlainText())
                self.current_file_path = path
                self.add_recent_file(path)
                self.is_modified = False
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Error", f"Could not save file:\n{e}")

    def open(self):
        """Show a file dialog and open a selected text file."""
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open File")
        if filename:
            with open(filename, "r", encoding="utf-8") as f:
                self.text_edit.setPlainText(f.read())
            self.current_file_path = filename
            self.add_recent_file(filename)
            self.is_modified = False

    # ------------------------------------------------------------- #
    #  Formatting / About / Close                                   #
    # ------------------------------------------------------------- #
    def preview_font(self, font):
        """Preview the chosen font family on the current selection/position."""
        fmt = QtGui.QTextCharFormat()
        fmt.setFontFamily(font.family())
        self.text_edit.mergeCurrentCharFormat(fmt)

    def change_fontsize(self, size_str):
        """Change font size for the current selection/position."""
        fmt = QtGui.QTextCharFormat()
        fmt.setFontPointSize(int(size_str))
        self.text_edit.mergeCurrentCharFormat(fmt)

    def change_fontfamily(self, font):
        """Commit font family change after selecting it in the dropdown."""
        fmt = QtGui.QTextCharFormat()
        fmt.setFontFamily(font.family())
        self.text_edit.mergeCurrentCharFormat(fmt)

    def toggle_bold(self, checked):
        """Toggle bold formatting on the current selection/position."""
        fmt = self.text_edit.currentCharFormat()
        fmt.setFontWeight(QtGui.QFont.Bold if checked else QtGui.QFont.Normal)
        self.text_edit.mergeCurrentCharFormat(fmt)

    def toggle_italic(self, checked):
        """Toggle italic formatting on the current selection/position."""
        fmt = self.text_edit.currentCharFormat()
        fmt.setFontItalic(checked)
        self.text_edit.mergeCurrentCharFormat(fmt)

    def toggle_underline(self, checked):
        """Toggle underline formatting on the current selection/position."""
        fmt = self.text_edit.currentCharFormat()
        fmt.setFontUnderline(checked)
        self.text_edit.mergeCurrentCharFormat(fmt)

    def choose_text_color(self):
        """Open a color dialog and change the text color."""
        color = QtWidgets.QColorDialog.getColor()
        if color.isValid():
            fmt = self.text_edit.currentCharFormat()
            fmt.setForeground(QtGui.QBrush(color))
            self.text_edit.mergeCurrentCharFormat(fmt)

    def choose_highlight_color(self):
        """Open a color dialog and change the background (highlight) color."""
        color = QtWidgets.QColorDialog.getColor()
        if color.isValid():
            fmt = self.text_edit.currentCharFormat()
            fmt.setBackground(QtGui.QBrush(color))
            self.text_edit.mergeCurrentCharFormat(fmt)

    def show_about(self):
        """Show a small About dialog with author and link information."""
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("About")
        msg.setIcon(QtWidgets.QMessageBox.Information)
        msg.setTextFormat(QtCore.Qt.RichText)
        msg.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        msg.setStandardButtons(QtWidgets.QMessageBox.Ok)
        msg.setText("""Nuke Text Editor 5.8<br>Pedro Gartner<br>
        <a href=\"https://github.com/PedroGartner\" style=\"text-decoration: underline; color: #ffffff;\">GitHub</a>""")
        msg.exec_()

    def closeEvent(self, event):
        """Ask to save changes when the window is closed if needed."""
        if self.is_modified:
            reply = QtWidgets.QMessageBox.question(
                self, "Save Changes?",
                "Do you want to save before closing?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel)
            if reply == QtWidgets.QMessageBox.Yes:
                self.save()
                event.accept()
            elif reply == QtWidgets.QMessageBox.No:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


# ------------------------------------------------------------- #
#  Launch in Nuke                                                #
# ------------------------------------------------------------- #
def show_texteditor():
    """
    Entry point to show (or raise) the Text Editor inside Nuke.

    Use this function in your Nuke menu.py, for example:
        import TextEditor
        nuke.menu('Nuke').addCommand('PGartner/Text Editor', 'TextEditor.show_texteditor()')

    This keeps a single global instance so opening the tool multiple
    times does not create duplicated windows.
    """
    global _text_editor_instance
    try:
        if _text_editor_instance and _text_editor_instance.isVisible():
            _text_editor_instance.raise_()
            _text_editor_instance.activateWindow()
            return _text_editor_instance
    except:
        _text_editor_instance = None

    _text_editor_instance = TextEditorWidget()
    _text_editor_instance.show()
    return _text_editor_instance