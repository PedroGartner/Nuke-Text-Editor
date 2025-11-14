# Nuke Text Editor

A clean and efficient Text Editor for Nuke, designed to help artists and technical directors write notes, manage tasks, and edit text files directly inside the Nuke environment.

Developed by Pedro Gartner.

----------------------------------------

## Features

Text Formatting:
- Font family selection
- Font size control
- Bold, Italic, Underline
- Text color and highlight
- Left, Center, Right, and Justify alignment

File Management:
- New File
- Open File
- Save
- Save As
- Recent Files list

Find and Replace:
- Find next match
- Replace selection
- Replace all occurrences

File Browser Sidebar:
- Expandable and collapsible
- Browse folders on disk
- Click to load .txt files into the editor

Status Bar:
Displays the current word and character count.

About Section:
Shows the editor name, version, author, and GitHub link.

----------------------------------------

## Installation

1. Download the following files and place them in your Nuke plugin directory (for example: ~/.nuke/):

- TextEditor.py
- FileBrowser.py

2. Open or create a file named menu.py inside the same directory and add the following code:

```python
import TextEditor
nuke.menu("Nuke").addCommand(
    "PGartner/Text Editor",
    "TextEditor.show_texteditor()"
)
```

3. Restart Nuke.

After restarting, you can access the Text Editor from the menu:
PGartner > Text Editor

----------------------------------------

## Customization

You may modify the following safely:
- Default font family and size
- Theme colors (style sheets)
- Maximum number of recent files
- Toolbar layout
- Button labels
- Additional formatting tools

All important parts of the code include comments to guide customization.

----------------------------------------

## Known Issue

- The font dropdown preview popup may shift position when switching fonts.

----------------------------------------

## Author

Pedro Gartner
IMDB: https://www.imdb.com/pt/name/nm9884333/
LinkedIn: https://www.linkedin.com/in/pedro-g-6b265a13a/
GitHub: https://github.com/PedroGartner
