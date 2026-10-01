# Text Editor for Nuke

A text editor built directly for Foundry Nuke.

Designed for compositors and technical artists who want to write notes, manage scripts, edit code and work with Nuke-related files without leaving Nuke.

![Nuke Text Editor Screenshot](TextEditor.png)

---

## Features

### Nuke Integration

- Run Python directly from the editor.
- `Ctrl+Enter` runs the selection or current line.
- `Ctrl+Shift+Enter` runs the entire file.
- Python output and tracebacks appear in the Output panel.
- Click traceback locations to jump directly to the relevant line.
- Open the editor as a floating window or dock it inside Nuke.
- Edit supported Nuke knob contents directly in the editor.
- Work with node, script and shot notes.
- Capture the current Viewer frame into rich notes.
- Jump to Nuke frames and nodes directly from notes.
- Node search across the current Nuke script.
- Inspect script structure with the Script Outline.
- View registered Nuke callbacks and their source locations.

---

## Notes

The Text Editor can be used for much more than code.

### Node Notes

Attach notes directly to Nuke nodes.

Notes are stored inside the node, so they travel with the script and remain available when nodes are copied and pasted.

Nodes containing notes can display a small note marker.

### Script Notes

Store a note associated with the current Nuke script.

### Shot Notes

Create a shared shot note file:

```text
<shot>_notes.tnote
```

Shot notes can remain consistent across multiple versions of the same Nuke script.

### Rich Notes

`.tnote` files support:

- Bold
- Italic
- Underline
- Text color
- Highlighting
- Fonts
- Alignment
- Images
- Viewer captures

Regular text and code files remain plain text.

---

## Tasks

Create task lists directly inside notes:

```text
- [ ] roto cleanup
- [ ] check edges
- [x] final grade
```

Tasks can also contain people and due dates:

```text
- [ ] fix key edges @anna due:friday
```

Supported due-date formats include `today`, `tomorrow`, weekdays, `2026-10-02`, and `02/10`.

The editor can collect open tasks from notes and display them in the TODO panel.

---

## Python Tools

- Run Python code without leaving the editor.
- Python Console under the Output panel.
- Hover help for supported Python functions.
- Problem checker for syntax errors, undefined names and unused imports.
- Press `F7` to display detected problems.

---

## Nuke Script Tools

### Script Outline
Inspect nodes, Read paths, Write paths and missing files in `.nk` files.

### Node Search
Search the current Nuke script by node name, node class or knob value. Groups are supported.

### Edit Groups as Text
Open the contents of a Group as `.nk` text, edit it and save the changes back to the Group.

### Callback Viewer
Inspect callbacks registered in the current Nuke session, including their source file and line where available.

---

## Shot Checks

The Text Editor includes workflow checks designed to help while working on shots.

### Newer Plates
Reads can be compared against files on disk to detect newer plate versions. Available updates can be reviewed before changing Read nodes.

### Pre-Render Checks
Before sending supported Write nodes to Batch Gartner, the editor can check for potential problems such as:

- Output version mismatches
- File type and extension mismatches
- Missing output folders
- Existing frames that may be overwritten
- Proxy mode
- Unexpected frame ranges
- Colorspace differences
- Missing plates
- Disabled nodes
- Nodes reporting errors
- Open shot-note tasks

---

## Batch Gartner Integration

The Text Editor can integrate with Batch Gartner when it is installed.

Selected Write nodes can be sent to Batch Gartner while preserving relevant job information. The editor can also access Batch Gartner render logs and perform pre-render checks before jobs are submitted.

Batch Gartner remains a separate tool and is not included with the Text Editor.

---

## Editing

Syntax highlighting supports:

- Python
- Nuke `.nk`
- Gizmo files
- JSON
- Markdown
- HTML / XML
- CSS
- JavaScript
- C / C++ / Java
- Bash
- Batch
- INI
- Logs
- Diff files

Editor features include line numbers, code folding, indent guides, bookmarks, bracket matching, repeated-word highlighting, autocomplete, snippets, find/replace, regex search, go to line, toggle comments, line operations, multi-cursor editing, syntax checks, configurable shortcuts and zoom controls.

---

## Additional Features

- File Browser
- Find in Files
- Markdown Preview (`Ctrl+Shift+M`)
- Snippets and shared snippet folders
- Templates with Nuke/shot tokens
- Workspaces
- Persistent Scratchpad
- Time Tracking
- End-of-Day Reports
- HTML/PDF note export
- Dark, Nuke and Light themes
- Split View (`Ctrl+\`)

---

## Safety

- Safe temporary-file saving
- Crash recovery
- Local file history
- External file-change detection
- Session/tab restore
- Optional autosave

---

## Installation

Copy the contents of the `TextEditor` folder into your `.nuke` directory.

The important files are:

```text
menu.py
TextEditor.py
nuke_text_editor/
```

For example:

```text
~/.nuke/
    menu.py
    TextEditor.py
    nuke_text_editor/
```

If you already have your own `menu.py`, add:

```python
import nuke_text_editor
nuke_text_editor.install()
```

Restart Nuke.

The compatibility `TextEditor.py` module is included so older menu commands using:

```python
TextEditor.show_texteditor()
```

can continue to work.

---

## Supported Versions

- Nuke 13+
- Python 3.7+
- PySide2 for Nuke 13-15
- PySide6 for newer supported Nuke versions
- Windows
- macOS
- Linux

Qt loading is handled according to the Nuke version to avoid loading incompatible Qt bindings into the Nuke process.

---

## Main Keyboard Shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+N` | New tab |
| `Ctrl+O` | Open |
| `Ctrl+S` | Save |
| `Ctrl+Shift+S` | Save As |
| `Ctrl+W` | Close tab |
| `Ctrl+Tab` | Next tab |
| `Ctrl+Enter` | Run selection/current line |
| `Ctrl+Shift+Enter` | Run file |
| `Ctrl+F` | Find |
| `Ctrl+H` | Replace |
| `F3` | Find next |
| `Ctrl+Shift+F` | Find in files |
| `Ctrl+G` | Go to line |
| `Ctrl+/` | Toggle comment |
| `Ctrl+D` | Duplicate line |
| `Ctrl+Shift+K` | Delete line |
| `Alt+Up/Down` | Move line |
| `Ctrl+Space` | Autocomplete |
| `Ctrl+Shift+T` | Insert task |
| `Ctrl+B` | Bold |
| `Ctrl+I` | Italic |
| `Ctrl+U` | Underline |
| `Ctrl+F2` | Toggle bookmark |
| `F2` | Next bookmark |
| `Ctrl+Shift+E` | Toggle side panel |
| `Ctrl+J` | Toggle output panel |
| `Ctrl+Click` | Follow supported link |
| `Alt+Click` | Add cursor |
| `Ctrl+Wheel` | Zoom |
| `Ctrl+=` | Zoom in |
| `Ctrl+-` | Zoom out |
| `Ctrl+0` | Reset zoom |
| `Ctrl+\` | Split view |
| `Ctrl+Shift+\` | Move tab to other side |
| `Ctrl+Shift+N` | Node search |
| `Ctrl+Shift+M` | Markdown preview |
| `F7` | Python problem check |
| `Ctrl+`` | Python console |

The complete shortcut list is available inside the Text Editor.

---

## Data Storage

Preferences are stored using Qt `QSettings` under:

```text
PGartner / NukeTextEditor
```

Text Editor data is stored under:

```text
~/.nuke/text_editor/
```

This can include bookmarks, crash recovery, local history, templates, snippets, captured images, scratchpad data, time logs and diagnostics.

Node notes and script notes are stored inside the Nuke script where applicable.

Diagnostics are stored under:

```text
~/.nuke/text_editor/logs/
```

---

## Development

Install the development dependencies:

```bash
pip install pytest pytest-qt PySide6
```

Run the tests with:

```bash
QT_QPA_PLATFORM=offscreen pytest tests
```

---

## Related Gartner Tools

Some functionality that could otherwise overlap with the Text Editor is intentionally handled by separate Gartner tools.

- **Gartner Script Health** - Nuke script diagnostics and health analysis.
- **PG Setup Library** - saving and managing reusable Nuke node setups.
- **PG Expression Lab** - expression creation, testing and management.
- **PG Tools** - shared productivity tools and command access.
- **Snapshot Browser** - script snapshots and version browsing.

These tools are not required for the core Text Editor.

---

## Known Limitations

- File-browser search only searches folders that have been expanded at least once.
- Folding is indentation-based and its state is not currently remembered after closing a file.
- Viewer captures do not include Viewer overlays or the Viewer display LUT.
- Text changes made directly to the `.nk` representation of an open script may require reopening the script.
- Some Linux window managers use `Alt+Click` for window movement.
- Plate version detection expects versions written in forms such as `v003`.
- Read paths constructed dynamically using expressions may not be automatically checked.

---

## Repository

https://github.com/PedroGartner/NukeTextEditor

---

## License

See `LICENSE` for licensing information.

---

## Author

Created by **Pedro Gartner**

LinkedIn: https://www.linkedin.com/in/pedro-g-6b265a13a/  
IMDb: https://www.imdb.com/name/nm9884333/  
GitHub: https://github.com/PedroGartner
