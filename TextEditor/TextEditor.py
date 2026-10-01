"""Compatibility module for menus created for version 1:

    TextEditor.show_texteditor()

keeps working. New installs only need the nuke_text_editor folder and menu.py.
"""

import nuke_text_editor


def show_texteditor():
    """Open the Text Editor (or bring it to the front)."""
    return nuke_text_editor.show_texteditor()
