
# Nuke Text Editor 7  
Advanced Text Editor Panel for Nuke  
Author: **Pedro Gartner**

---

## Overview
Nuke Text Editor 7 was created to solve a real problem I faced while working both remotely and locally inside Nuke:  
**there was no clean, reliable place to write notes for the shots I was working on.**

This tool provides a dedicated workspace for:
- personal shot notes  
- notes to share with supervisors  
- importing notes from other artists or other shots  
- organizing project information clearly inside Nuke  
- keeping everything in one place  

It has evolved into a full-featured editor with a modern UI, safe syntax highlighting, autosave, and a multi-tab system — all stable inside Nuke's environment.

---

## Features

### 🔹 Multi-Tab System
- Inline **"+"** new-tab button  
- Drag-and-drop tab reordering  
- Middle-click to close  
- Tab context menu (rename, duplicate, close others/all)  
- Per-tab file tracking  
- Color-coded underline per file type  

### 🔹 Safe Regex-Based Syntax Highlighting
Supports:
- Python  
- JSON  
- Markdown  
- Nuke `.nk`  
- INI / CFG  
- HTML / XML  
- JS / TS  
- CSS  
- C / C++ / Java  
- Bash  
- Batch  
- Plain text  

Designed to be:
- Nuke-safe  
- PySide2 & PySide6 compatible  
- Medium-level (stable & predictable)

### 🔹 Word Repetition Highlighting
Highlights:
- The active word under cursor  
- All repeated occurrences in the document  
Using Dracula-style soft contrast.

### 🔹 Bracket Matching
Highlights matching parentheses, square brackets, and curly brackets.  
Unmatched brackets show a red highlight.

### 🔹 Integrated File Browser
- Browse disk  
- Click to open a file in a NEW tab  
- Create folders  
- Refresh view  
- Delete items  

### 🔹 Autosave (Mode C)
Silent autosave:
- Idle-based (5 seconds after no typing)  
- Interval-based (every 2 minutes)  
Saves only files with valid paths.

### 🔹 Docked Find / Replace Panel
- Case sensitivity  
- Regex mode  
- Whole-word search  
- Replace one  
- Replace all  

### 🔹 Formatting Tools
- Bold  
- Italic  
- Underline  
- Text color  
- Highlight color  
- Bullets  
- Alignment controls  
- Font family  
- Font size  
- Undo / Redo  

---

## Current Limitations / Work in Progress

While the tool is stable for everyday production use, a few areas are still being improved:

### 🔸 UI & Highlighting  
- Occasional **color/highlight bugs** when switching fonts or applying styles  
- Some cases where highlight removal doesn't refresh instantly  

### 🔸 Tab System  
- Minor **tab movement bugs** when dragging quickly  
- Rare cases where dragging near the "+" tab jumps the tab order  

### 🔸 File Browser Search  
- Search in the file browser is not 100% accurate  
- Future update planned to include fuzzy search, filtering, and performance improvements  

### 🔸 Missing Keyboard Shortcuts  
Planned additions:
- Ctrl+S (Save)  
- Ctrl+Shift+S (Save As)  
- Ctrl+N (New Tab)  
- Ctrl+F (Find)  
- Ctrl+W (Close Tab)  
- Ctrl+Z / Ctrl+Y (Undo/Redo)  
- Ctrl+Plus / Minus (Future zoom features)  

Shortcuts will be added in a future update in a clean, Nuke-safe implementation.

---

## Installation

Place these files in your **.nuke** folder:

```
TextEditor.py
FileBrowser.py
menu.py
```

In your `menu.py`, add:

```python
import TextEditor
nuke.menu('Nuke').addCommand('PGartner/Text Editor', 'TextEditor.show_texteditor()')
```

Restart Nuke and access it via:

**PGartner → Text Editor**

---

## File Structure

```
├── TextEditor.py    # Main UI + logic
├── FileBrowser.py   # Collapsible sidebar file browser
└── menu.py          # Nuke menu integration
```

---

## Author
**Pedro Gartner**  
- GitHub: https://github.com/PedroGartner  
- LinkedIn: https://www.linkedin.com/in/pedro-g-6b265a13a/  
- IMDB: https://www.imdb.com/pt/name/nm9884333/  

