"""Main editor window (floating) and the dockable Nuke panel."""

import html
import io
import json
import os
import re
import time
import uuid

from .qt import (QtCore, QtGui, QtWidgets, qt_exec, event_pos, font_families,
                 SignalBlocker, parse_shortcut, format_font_family, set_format_font_family)
from . import bgartner
from . import dailylog
from . import diagnostics
from . import fileio
from . import history
from . import lint
from . import locks
from . import nuke_bridge
from . import templates
from . import textops
from . import themes
from . import time_tracker
from .annotate import annotate
from .bindings import GroupBinding, KnobBinding, LogFollower, NoteBinding
from .code_editor import (AdvancedCodeEditor, BRACKET_PAIRS, CLOSING_BRACKETS, INDENT_TAB_SIZE,
                          find_matching_bracket)
from .dialogs import (DiffDialog, HistoryDialog, KnobPickerDialog, PreferencesDialog,
                      TimeSummaryDialog, ask_line_number)
from .file_browser import FileBrowser
from .find_panel import FindPanel
from .library_panels import SnippetsPanel
from .outline_panel import OutlinePanel
from .output_panel import OutputPanel
from .script_panels import CallbacksPanel, MarkdownPreviewPanel, NodeSearchPanel
from .search_panels import FileSearchPanel, TodoPanel
from . import shot_panels
from .settings import Settings
from .widgets import ColorTabBar, HoverButton

# Tab underline / text color per file type, as theme color roles.
FILETYPE_TAB_ROLES = {
    "python": "function", "json": "type", "markdown": "keyword", "nuke": "number", "ini": "string",
    "html": "builtin", "javascript": "string", "css": "type", "c_like": "builtin", "bash": "function",
    "batch": "function", "log": "comment", "diff": "type", "rich": "text", "text": "muted",
    "binding": "builtin", "scratchpad": "string",
}


def tab_color(key):
    return themes.color(FILETYPE_TAB_ROLES.get(key, "muted"))
PREFERRED_MONOSPACE_FONTS = ("Consolas", "Menlo", "DejaVu Sans Mono", "Liberation Mono", "Courier New")
IMAGE_EXTENSIONS = (".exr", ".dpx", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".tga", ".hdr",
                    ".cin", ".mov", ".mp4", ".psd", ".sgi", ".rgb")
RICH_TOOLTIP = "Formatting is available in the Scratchpad, Untitled documents and rich notes (.tnote)."

_live_windows = []
_floating_instance = None


def live_windows():
    """Editor windows / panels that are still alive."""
    alive = []
    for window in list(_live_windows):
        try:
            window.objectName()
            alive.append(window)
        except RuntimeError:
            _live_windows.remove(window)
    return alive


class Action(object):
    """One command: used for menus, shortcuts and the command palette."""

    def __init__(self, action_id, label, callback, shortcut="", aliases=(), checkable=False,
                 checked=False, needs_nuke=False, menu=""):
        self.id = action_id
        self.label = label
        self.callback = callback
        self.shortcut = shortcut
        self.aliases = aliases
        self.checkable = checkable
        self.checked = checked
        self.needs_nuke = needs_nuke
        self.menu = menu
        self.qaction = None


class TextEditorWidget(QtWidgets.QWidget):
    """The Text Editor. Used as a floating window; TextEditorPanel is the
    dockable variant registered with Nuke's Pane menu."""

    floating = True

    DEFAULT_FONT_SIZE = 9
    MAX_RECENT_FILES = 10
    AUTOSAVE_CHECK_MS = 2000
    AUTOSAVE_IDLE_MS = 5000
    AUTOSAVE_INTERVAL_MS = 120000

    def __init__(self, parent=None):
        super().__init__(parent)
        if self.floating:
            if parent is not None:
                self.setWindowFlags(QtCore.Qt.Window)
            self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Text Editor")
        self.setMinimumSize(800, 500)
        self.setObjectName("NukeTextEditor")

        self._closing = False
        self.instance_id = uuid.uuid4().hex[:8]
        self.settings = Settings()
        themes.set_current(self.settings.get_str("theme", themes.DEFAULT))
        self.recent_files = self.settings.get_list("recent_files")[: self.MAX_RECENT_FILES]
        self.zoom = max(50, min(200, self.settings.get_int("zoom")))
        self.word_wrap = self.settings.get_bool("word_wrap")
        self.autosave_to_file = self.settings.get_bool("autosave_to_file")
        self.gutter_show_line_numbers = self.settings.get_bool("gutter/line_numbers")
        self.gutter_show_fold_markers = self.settings.get_bool("gutter/fold_markers")
        self.gutter_scale = self.settings.get_float("gutter/scale")
        families = set(font_families())
        self.font_family = next((f for f in PREFERRED_MONOSPACE_FONTS if f in families), "Courier New")

        self._run_ids = {}
        self._next_run_id = 1
        self._word_count_text = ""
        self._task_text = ""
        self._recovery_dirty = False
        self._follow_node = None
        self._follow_editor = None
        self._pending_disk_changes = set()
        self._closing = False

        trace = diagnostics.trace
        trace("settings loaded, font: " + self.font_family)
        self._build_timers()
        trace("timers built")
        self._build_ui()
        trace("widgets built")
        self._build_actions()
        trace("actions built")
        self._build_menus()
        trace("menus built")
        self._install_shortcut_filters(self)
        trace("shortcut filters installed")

        if not any(w.has_scratchpad() for w in live_windows()):
            self._create_scratchpad()
            trace("scratchpad created")
        self.create_tab()
        trace("first tab created")
        self.update_recent_files_menu()

        _live_windows.append(self)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)
        QtCore.QTimer.singleShot(0, self._startup)
        trace("window constructed")

    # ------------------------------------------------------------- #
    #  Construction                                                 #
    # ------------------------------------------------------------- #
    def _build_timers(self):
        self.last_edit_time = QtCore.QDateTime.currentDateTime()
        self.last_autosave_time = QtCore.QDateTime.currentDateTime()
        self.autosave_timer = QtCore.QTimer(self)
        self.autosave_timer.setInterval(self.AUTOSAVE_CHECK_MS)
        self.autosave_timer.timeout.connect(self.handle_autosave)

        self._deferred_timer = QtCore.QTimer(self)
        self._deferred_timer.setSingleShot(True)
        self._deferred_timer.setInterval(250)
        self._deferred_timer.timeout.connect(self._refresh_deferred)

        self._outline_timer = QtCore.QTimer(self)
        self._outline_timer.setSingleShot(True)
        self._outline_timer.setInterval(800)
        self._outline_timer.timeout.connect(self._refresh_outline)

        self._message_timer = QtCore.QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(lambda: self.message_label.setText(""))

        self._follow_timer = QtCore.QTimer(self)
        self._follow_timer.setInterval(500)
        self._follow_timer.timeout.connect(self._poll_note_follow)

        self._disk_timer = QtCore.QTimer(self)
        self._disk_timer.setSingleShot(True)
        self._disk_timer.setInterval(300)
        self._disk_timer.timeout.connect(self._process_disk_changes)

        self._lock_timer = QtCore.QTimer(self)
        self._lock_timer.setInterval(120000)  # refresh "someone is editing" markers
        self._lock_timer.timeout.connect(self._refresh_locks)

        self._watcher = QtCore.QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)

    def _build_ui(self):
        self.setStyleSheet(themes.window_style())

        # ---- Tabs (two areas for split view; the second is hidden until used) ----
        self.tab_widget = self._make_tab_widget()
        self.split_tabs = self._make_tab_widget()
        self.split_tabs.setVisible(False)
        self.tab_widgets = [self.tab_widget, self.split_tabs]
        self._active_tabs = self.tab_widget
        self.new_tab_button = QtWidgets.QToolButton()
        self.new_tab_button.setText("+")
        self.new_tab_button.setToolTip("New tab (Ctrl+N)")
        self.new_tab_button.setAutoRaise(True)
        self.new_tab_button.clicked.connect(self.new_tab)
        self.tab_widget.setCornerWidget(self.new_tab_button, QtCore.Qt.TopRightCorner)
        self.tabs_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.tabs_splitter.addWidget(self.tab_widget)
        self.tabs_splitter.addWidget(self.split_tabs)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)

        self.find_panel = FindPanel(self.current_editor)

        # ---- Panels ----
        self.output_panel = OutputPanel()
        self.output_panel.location_clicked.connect(self._on_output_location)
        self.bottom_tabs = QtWidgets.QTabWidget()
        self.bottom_tabs.addTab(self.output_panel, "Output")
        self.bottom_tabs.setVisible(False)

        notes_folder = self.settings.get_str("notes_folder")
        self.file_browser = FileBrowser(self.settings)
        self.file_browser.file_selected.connect(self.open_path)
        self.file_browser.path_deleted.connect(self.on_path_deleted)
        self.file_browser.setMinimumWidth(240)

        self.outline_panel = OutlinePanel()
        self.outline_panel.line_activated.connect(self._goto_line_in_current)
        self.outline_panel.path_activated.connect(lambda p: self.on_link("path", p))
        self.search_panel = FileSearchPanel(self.file_browser.root_path)
        self.search_panel.location_activated.connect(self._open_location)
        self.todo_panel = TodoPanel(notes_folder or self.file_browser.root_path)
        self.todo_panel.location_activated.connect(self._open_location)
        self.snippets_panel = SnippetsPanel(self.settings)
        self.snippets_panel.insert_requested.connect(self.insert_snippet)
        self.snippets_panel.run_requested.connect(lambda code: self._run(code, None, "snippet"))

        self.node_search_panel = NodeSearchPanel(self.current_editor)
        self.node_search_panel.node_activated.connect(self._select_node)
        self.node_search_panel.line_activated.connect(self._goto_line_in_current)
        self.callbacks_panel = CallbacksPanel()
        self.callbacks_panel.open_source.connect(lambda path, line: self.open_path(path, line - 1))
        self.callbacks_panel.edit_knob.connect(
            lambda node, knob: self.open_binding(KnobBinding(node, knob, "value", "PythonKnob")))
        self.preview_panel = MarkdownPreviewPanel()
        self.plates_panel = shot_panels.PlatesPanel()
        self.plates_panel.node_activated.connect(self._select_node)
        self.plates_panel.plates_updated.connect(self._on_plates_updated)
        self._plate_check = shot_panels.PlateCheckOnLoad(self._on_plates_found_on_load, self)
        self.output_panel.console_submitted.connect(self.run_console_entry)

        self.side_tabs = QtWidgets.QTabWidget()
        self.side_tabs.addTab(self.outline_panel, "Outline")
        self.side_tabs.addTab(self.search_panel, "Search")
        self.side_tabs.addTab(self.todo_panel, "TODOs")
        self.side_tabs.addTab(self.snippets_panel, "Snippets")
        self.side_tabs.addTab(self.plates_panel, "Plates")
        self.side_tabs.addTab(self.node_search_panel, "Nodes")
        self.side_tabs.addTab(self.callbacks_panel, "Callbacks")
        self.side_tabs.addTab(self.preview_panel, "Preview")
        self.side_tabs.setMinimumWidth(260)
        self.side_tabs.currentChanged.connect(lambda _i: self._outline_timer.start())

        # ---- Toolbar ----
        self.bold_button = HoverButton("B")
        self.bold_button.setCheckable(True)
        self.italic_button = HoverButton("I")
        self.italic_button.setCheckable(True)
        self.underline_button = HoverButton("U")
        self.underline_button.setCheckable(True)
        self.bullet_button = HoverButton("•")
        self.bullet_button.setToolTip("Insert bullet")
        self.task_button = HoverButton("☐")
        self.task_button.setToolTip("Insert task checkbox (click the box to tick it)")
        self.color_button = HoverButton("Color")
        self.highlight_button = HoverButton("Highlight")
        self.align_dropdown = QtWidgets.QComboBox()
        self.align_dropdown.addItems(["Left", "Center", "Right", "Justify"])
        self.align_dropdown.setMaximumWidth(90)
        self.fontsize_combo = QtWidgets.QComboBox()
        self.fontsize_combo.addItems([str(i) for i in range(8, 25)])
        self.fontsize_combo.setCurrentText(str(self.DEFAULT_FONT_SIZE))
        self.fontfamily_combo = QtWidgets.QFontComboBox()
        self.fontfamily_combo.setCurrentFont(QtGui.QFont(self.font_family))
        self.fontfamily_combo.setMaximumWidth(130)
        self.fontfamily_combo.setMaxVisibleItems(8)
        self.undo_button = HoverButton("Undo")
        self.redo_button = HoverButton("Redo")
        self.run_button = HoverButton("▶ Run")
        self.run_button.setToolTip("Run selection or current line (Ctrl+Enter); whole file: Ctrl+Shift+Enter")
        self.bookmark_prev_button = HoverButton("◀")
        self.bookmark_prev_button.setToolTip("Previous bookmark (Shift+F2)")
        self.bookmark_next_button = HoverButton("▶")
        self.bookmark_next_button.setToolTip("Next bookmark (F2)")
        self.bookmark_new_button = HoverButton("★+")
        self.bookmark_new_button.setToolTip("Toggle bookmark (Ctrl+F2, or Ctrl+Click in the gutter)")
        self.bookmark_delete_button = HoverButton("★-")
        self.bookmark_delete_button.setToolTip("Remove bookmark on this line")
        self.bookmark_list_button = HoverButton("☰")
        self.bookmark_list_button.setToolTip("Bookmark list")
        self._rich_widgets = [self.bold_button, self.italic_button, self.underline_button,
                              self.color_button, self.highlight_button, self.align_dropdown,
                              self.fontsize_combo, self.fontfamily_combo]

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(3, 3, 3, 3)
        toolbar.setSpacing(6)
        for widget in (self.bold_button, self.italic_button, self.underline_button, self.bullet_button,
                       self.task_button, self.color_button, self.highlight_button, self.align_dropdown,
                       self.fontsize_combo, self.fontfamily_combo, self.undo_button, self.redo_button,
                       self.run_button):
            toolbar.addWidget(widget)
        toolbar.addStretch()
        for widget in (self.bookmark_prev_button, self.bookmark_next_button, self.bookmark_new_button,
                       self.bookmark_delete_button, self.bookmark_list_button):
            toolbar.addWidget(widget)

        # ---- Layout ----
        editor_area = QtWidgets.QWidget()
        editor_layout = QtWidgets.QVBoxLayout(editor_area)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        editor_layout.addWidget(self.tabs_splitter, 1)
        editor_layout.addWidget(self.find_panel)

        self.center_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.center_splitter.addWidget(editor_area)
        self.center_splitter.addWidget(self.bottom_tabs)
        self.center_splitter.setStretchFactor(0, 4)
        self.center_splitter.setStretchFactor(1, 1)

        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addLayout(toolbar)
        center_layout.addWidget(self.center_splitter, 1)

        self.main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.main_splitter.addWidget(self.file_browser)
        self.main_splitter.addWidget(center)
        self.main_splitter.addWidget(self.side_tabs)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([260, 900, 300])
        self.file_browser.setVisible(self.settings.get_bool("browser/visible"))
        self.side_tabs.setVisible(self.settings.get_bool("side_panel/visible"))

        # ---- Status bar ----
        self.status_bar = QtWidgets.QLabel("")
        self.status_bar.setStyleSheet("color: {}; padding: 4px; font-size: 10px;".format(themes.color("muted")))
        self.message_label = QtWidgets.QLabel("")
        self.message_label.setStyleSheet("color: {}; padding: 4px; font-size: 10px;".format(themes.color("message")))
        self.zoom_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.zoom_slider.setRange(50, 200)
        self.zoom_slider.setValue(self.zoom)
        self.zoom_slider.setFixedWidth(140)
        self.zoom_slider.valueChanged.connect(self.set_zoom)
        zoom_minus = HoverButton("-")
        zoom_minus.setFixedWidth(22)
        zoom_minus.clicked.connect(lambda: self.zoom_step(-1))
        zoom_plus = HoverButton("+")
        zoom_plus.setFixedWidth(22)
        zoom_plus.clicked.connect(lambda: self.zoom_step(1))
        self.zoom_label = QtWidgets.QLabel("{}%".format(self.zoom))
        self.zoom_label.setFixedWidth(36)
        self.zoom_label.setStyleSheet("color: {}; font-size: 10px;".format(themes.color("muted")))
        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(self.status_bar)
        bottom.addWidget(self.message_label, 1)
        bottom.addWidget(zoom_minus)
        bottom.addWidget(zoom_plus)
        bottom.addWidget(self.zoom_slider)
        bottom.addWidget(self.zoom_label)

        self.menu_bar = QtWidgets.QMenuBar(self)
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setMenuBar(self.menu_bar)
        main_layout.addWidget(self.main_splitter, 1)
        main_layout.addLayout(bottom)

        # ---- Signals ----
        self.bold_button.toggled.connect(self.toggle_bold)
        self.italic_button.toggled.connect(self.toggle_italic)
        self.underline_button.toggled.connect(self.toggle_underline)
        self.bullet_button.clicked.connect(lambda: self._insert_text("• "))
        self.task_button.clicked.connect(self.insert_task)
        self.color_button.clicked.connect(self.choose_text_color)
        self.highlight_button.clicked.connect(self.choose_highlight_color)
        self.align_dropdown.currentIndexChanged.connect(self.change_alignment)
        self.fontsize_combo.currentTextChanged.connect(self.change_fontsize)
        self.fontfamily_combo.currentFontChanged.connect(self.change_fontfamily)
        self.undo_button.clicked.connect(self.edit_undo)
        self.redo_button.clicked.connect(self.edit_redo)
        self.run_button.clicked.connect(self.run_selection)
        self.bookmark_prev_button.clicked.connect(lambda: self._with_editor(lambda e: e.goto_previous_bookmark()))
        self.bookmark_next_button.clicked.connect(lambda: self._with_editor(lambda e: e.goto_next_bookmark()))
        self.bookmark_new_button.clicked.connect(lambda: self._with_editor(lambda e: e.toggle_bookmark_current_line()))
        self.bookmark_delete_button.clicked.connect(lambda: self._with_editor(lambda e: e.clear_bookmark_current_line()))
        self.bookmark_list_button.clicked.connect(self.show_bookmark_list)

    # ------------------------------------------------------------- #
    #  Actions, menus and shortcuts                                 #
    # ------------------------------------------------------------- #
    def _build_actions(self):
        editor_cmd = self._with_editor
        defs = [
            # File
            ("new_tab", "New Tab", self.new_tab, "Ctrl+N", (), "File"),
            ("open", "Open...", self.open, "Ctrl+O", (), "File"),
            ("open_log", "Open Log (Follow)...", self.open_log_dialog, "", (), "File"),
            ("save", "Save", self.save, "Ctrl+S", (), "File"),
            ("save_as", "Save As...", self.save_as, "Ctrl+Shift+S", (), "File"),
            ("export_html", "Export as HTML...", self.export_html, "", (), "File"),
            ("export_pdf", "Export as PDF...", self.export_pdf, "", (), "File"),
            ("history", "Local History...", self.show_history, "", (), "File"),
            ("compare_saved", "Compare with Saved", self.compare_with_saved, "", (), "File"),
            ("compare_files", "Compare Files...", self.compare_files, "", (), "File"),
            ("workspace_save", "Save Workspace As...", self.save_workspace, "", (), "File"),
            ("workspace_delete", "Delete Workspace...", self.delete_workspace, "", (), "File"),
            ("preferences", "Preferences...", self.show_preferences, "", (), "File"),
            ("close_tab", "Close Tab", self.close_current_tab, "Ctrl+W", (), "File"),
            ("close_editor", "Close Editor", self.close, "", (), "File"),
            # Edit
            ("undo", "Undo", self.edit_undo, "", (), "Edit"),
            ("redo", "Redo", self.edit_redo, "", (), "Edit"),
            ("find", "Find", lambda: self.find_panel.show_panel(), "Ctrl+F", (), "Edit"),
            ("replace", "Replace", lambda: self.find_panel.show_panel(focus_replace=True), "Ctrl+H", (), "Edit"),
            ("find_next", "Find Next", self.find_panel.find_next, "F3", (), "Edit"),
            ("find_prev", "Find Previous", self.find_panel.find_prev, "Shift+F3", (), "Edit"),
            ("find_in_files", "Find in Files", self.show_find_in_files, "Ctrl+Shift+F", (), "Edit"),
            ("goto_line", "Go to Line...", self.goto_line, "Ctrl+G", (), "Edit"),
            ("toggle_comment", "Toggle Comment", self.toggle_comment, "Ctrl+/", (), "Edit"),
            ("duplicate_line", "Duplicate Line", lambda: editor_cmd(lambda e: e.duplicate_lines(), True), "Ctrl+D", (), "Edit"),
            ("delete_line", "Delete Line", lambda: editor_cmd(lambda e: e.delete_lines(), True), "Ctrl+Shift+K", (), "Edit"),
            ("move_up", "Move Line Up", lambda: editor_cmd(lambda e: e.move_lines(-1), True), "Alt+Up", (), "Edit"),
            ("move_down", "Move Line Down", lambda: editor_cmd(lambda e: e.move_lines(1), True), "Alt+Down", (), "Edit"),
            ("complete", "Autocomplete", lambda: editor_cmd(lambda e: e.show_completions(True), True), "Ctrl+Space", (), "Edit"),
            ("insert_task", "Insert Task", self.insert_task, "Ctrl+Shift+T", (), "Edit"),
            ("toggle_bookmark", "Toggle Bookmark", lambda: editor_cmd(lambda e: e.toggle_bookmark_current_line()), "Ctrl+F2", (), "Edit"),
            ("next_bookmark", "Next Bookmark", lambda: editor_cmd(lambda e: e.goto_next_bookmark()), "F2", (), "Edit"),
            ("prev_bookmark", "Previous Bookmark", lambda: editor_cmd(lambda e: e.goto_previous_bookmark()), "Shift+F2", (), "Edit"),
            ("bookmark_list", "Bookmark List...", self.show_bookmark_list, "", (), "Edit"),
            ("bold", "Bold", lambda: self._toggle_format_button(self.bold_button), "Ctrl+B", (), "Edit"),
            ("italic", "Italic", lambda: self._toggle_format_button(self.italic_button), "Ctrl+I", (), "Edit"),
            ("underline", "Underline", lambda: self._toggle_format_button(self.underline_button), "Ctrl+U", (), "Edit"),
            # View
            ("toggle_browser", "Toggle File Browser", self.toggle_sidebar, "", (), "View"),
            ("toggle_side", "Toggle Side Panel", self.toggle_side_panel, "Ctrl+Shift+E", (), "View"),
            ("toggle_output", "Toggle Output Panel", self.toggle_output_panel, "Ctrl+J", (), "View"),
            ("show_outline", "Outline", lambda: self.show_side(self.outline_panel), "", (), "View"),
            ("show_todos", "TODOs", self.show_todos, "", (), "View"),
            ("show_snippets", "Snippets", lambda: self.show_side(self.snippets_panel), "", (), "View"),
            ("zoom_in", "Zoom In", lambda: self.zoom_step(1), "Ctrl+=", ("Ctrl++", "Ctrl+Shift++"), "View"),
            ("zoom_out", "Zoom Out", lambda: self.zoom_step(-1), "Ctrl+-", (), "View"),
            ("zoom_reset", "Reset Zoom", lambda: self.set_zoom(100), "Ctrl+0", (), "View"),
            ("gutter", "Gutter Settings...", self.show_gutter_settings, "", (), "View"),
            ("toggle_split", "Split View", self.toggle_split, "Ctrl+\\", (), "View"),
            ("move_split", "Move Tab to Other Side", self.move_tab_to_other_side, "Ctrl+Shift+\\", (), "View"),
            ("show_nodes", "Node Search", self.show_node_search, "Ctrl+Shift+N", (), "View"),
            ("show_callbacks", "Callbacks", self.show_callbacks, "", (), "View"),
            ("show_preview", "Markdown Preview", lambda: self.show_side(self.preview_panel), "Ctrl+Shift+M", (), "View"),
            ("next_tab", "Next Tab", lambda: self._cycle_tab(1), "Ctrl+Tab", (), "View"),
            ("prev_tab", "Previous Tab", lambda: self._cycle_tab(-1), "Ctrl+Shift+Tab", (), "View"),
            # Run
            ("run_selection", "Run Selection / Line", self.run_selection, "Ctrl+Return", ("Ctrl+Enter",), "Run"),
            ("run_file", "Run File", self.run_file, "Ctrl+Shift+Return", ("Ctrl+Shift+Enter",), "Run"),
            ("check_syntax", "Check for Problems (Python)", self.check_syntax, "F7", (), "Run"),
            ("console", "Python Console", self.focus_console, "Ctrl+`", (), "Run"),
            # Nuke
            ("shot_notes", "Open Shot Notes", self.open_shot_notes, "", (), "Nuke"),
            ("check_plates", "Check Plate Versions", self.show_plates, "", (), "Nuke"),
            ("check_writes", "Check Writes Before Render", self.check_writes, "", (), "Nuke"),
            ("script_note", "Script Note (saved in the .nk)", self.edit_script_note, "", (), "Nuke"),
            ("node_note", "Note on Selected Node", self.edit_node_note, "", (), "Nuke"),
            ("edit_knob", "Edit Knob of Selected Node...", self.edit_selected_knob, "", (), "Nuke"),
            ("capture_viewer", "Capture Viewer into Note", self.capture_viewer, "", (), "Nuke"),
            ("open_script_text", "Open Current Script as Text", self.open_current_script, "", (), "Nuke"),
            ("time_summary", "Time per Script...", self.show_time_summary, "", (), "Nuke"),
            ("day_report", "End-of-Day Report", self.end_of_day_report, "", (), "Nuke"),
            ("edit_group", "Edit Selected Group as Text", self.edit_selected_group, "", (), "Nuke"),
            ("to_sticky", "Send Text to New StickyNote", self.send_to_sticky, "", (), "Nuke"),
            ("to_label", "Send Text to Selected Sticky / Backdrop Label", self.send_to_label, "", (), "Nuke"),
            ("from_label", "Insert Label of Selected Sticky / Backdrop", self.insert_label, "", (), "Nuke"),
            # Batch Gartner
            ("bg_send", "Send Selected Write(s) to Batch Gartner", self.bg_send_selected, "", (), "Batch Gartner"),
            ("bg_open", "Open Batch Gartner", self.bg_open_app, "", (), "Batch Gartner"),
            ("bg_log", "Open Render Log...", self.bg_open_log, "", (), "Batch Gartner"),
            ("bg_write_log", "Latest Render Log of Selected Write", self.bg_log_for_selected, "", (), "Batch Gartner"),
            # Help
            ("shortcuts", "Keyboard Shortcuts", self.show_shortcuts, "", (), "Help"),
            ("about", "About", self.show_about, "", (), "Help"),
        ]
        self.actions = {}
        for action_id, label, callback, shortcut, aliases, menu in defs:
            action = Action(action_id, label, callback, shortcut, aliases, menu=menu)
            action.default_shortcut = shortcut
            self.actions[action_id] = action
        self._rebuild_shortcuts()

        checkables = [
            ("word_wrap", "Word Wrap", self.set_word_wrap, self.word_wrap, "View"),
            ("autosave_toggle", "Autosave to File", self.set_autosave_to_file, self.autosave_to_file, "View"),
            ("follow_notes", "Follow Selected Node's Note", self.set_follow_notes, False, "Nuke"),
        ]
        for action_id, label, callback, checked, menu in checkables:
            self.actions[action_id] = Action(action_id, label, callback, checkable=True, checked=checked, menu=menu)

    def _add_menu_action(self, menu, action_id):
        action = self.actions[action_id]
        label = action.label + ("\t" + action.shortcut if action.shortcut else "")
        qaction = menu.addAction(label)
        if action.checkable:
            qaction.setCheckable(True)
            qaction.setChecked(action.checked)
            qaction.toggled.connect(action.callback)
        else:
            qaction.triggered.connect(lambda _checked=False, cb=action.callback: cb())
        action.qaction = qaction
        return qaction

    def _build_menus(self):
        layout = [
            ("File", ["new_tab", "@templates_new", "open", "@recent", "open_log", "-", "save", "save_as",
                      "-", "export_html", "export_pdf", "-", "history", "compare_saved", "compare_files",
                      "-", "@workspaces", "workspace_save", "workspace_delete",
                      "-", "preferences", "-", "close_tab", "close_editor"]),
            ("Edit", ["undo", "redo", "-", "find", "replace", "find_next", "find_prev", "find_in_files", "-",
                      "goto_line", "toggle_comment", "duplicate_line", "delete_line", "move_up", "move_down",
                      "complete", "-", "insert_task", "@templates_insert", "-", "toggle_bookmark",
                      "next_bookmark", "prev_bookmark", "bookmark_list"]),
            ("View", ["toggle_browser", "toggle_side", "toggle_output", "toggle_split",
                      "move_split", "-", "show_outline", "show_todos", "show_snippets",
                      "show_nodes", "show_callbacks", "show_preview", "-", "word_wrap",
                      "zoom_in", "zoom_out", "zoom_reset", "gutter", "-", "autosave_toggle"]),
            ("Run", ["run_selection", "run_file", "check_syntax", "console"]),
            ("Nuke", ["shot_notes", "check_plates", "check_writes", "-", "script_note", "node_note", "follow_notes", "edit_knob", "edit_group", "-",
                      "capture_viewer", "to_sticky", "to_label", "from_label", "-",
                      "show_nodes", "show_callbacks", "-", "open_script_text",
                      "-", "time_summary", "day_report"]),
            ("Batch Gartner", ["bg_send", "bg_open", "-", "bg_log", "bg_write_log"]),
            ("Help", ["shortcuts", "about"]),
        ]
        for title, items in layout:
            menu = self.menu_bar.addMenu(title)
            for item in items:
                if item == "-":
                    menu.addSeparator()
                elif item == "@recent":
                    self.recent_menu = menu.addMenu("Recent Files")
                elif item == "@workspaces":
                    self.workspace_menu = menu.addMenu("Open Workspace")
                    self.workspace_menu.aboutToShow.connect(self._fill_workspace_menu)
                elif item == "@templates_new":
                    self.new_template_menu = menu.addMenu("New from Template")
                    self.new_template_menu.aboutToShow.connect(
                        lambda: self._fill_template_menu(self.new_template_menu, self.new_from_template))
                elif item == "@templates_insert":
                    self.insert_template_menu = menu.addMenu("Insert Template")
                    self.insert_template_menu.aboutToShow.connect(
                        lambda: self._fill_template_menu(self.insert_template_menu, self.insert_template))
                else:
                    self._add_menu_action(menu, item)
            if title == "Nuke":
                menu.aboutToShow.connect(lambda m=menu: self._update_nuke_menu(m))

    def _update_nuke_menu(self, menu):
        available = nuke_bridge.available()
        for action_id in ("script_note", "node_note", "follow_notes", "edit_knob", "capture_viewer",
                          "open_script_text", "edit_group", "to_sticky", "to_label",
                          "from_label", "check_plates", "check_writes"):
            qaction = self.actions[action_id].qaction
            if qaction is not None:
                qaction.setEnabled(available)

    def _install_shortcut_filters(self, root):
        types = (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit,
                 QtWidgets.QAbstractItemView, QtWidgets.QComboBox)
        for widget in root.findChildren(QtWidgets.QWidget):
            if isinstance(widget, types):
                widget.installEventFilter(self)

    def _match_shortcut(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & QtCore.Qt.ControlModifier)
        shift = bool(mods & QtCore.Qt.ShiftModifier)
        alt = bool(mods & QtCore.Qt.AltModifier)
        for s_key, s_ctrl, s_shift, s_alt, callback in self._shortcut_table:
            if key == s_key and (ctrl, shift, alt) == (s_ctrl, s_shift, s_alt):
                return callback
        return None

    def eventFilter(self, obj, event):
        if self._closing or not hasattr(self, "_shortcut_table"):
            return False
        etype = event.type()
        if etype in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress):
            callback = self._match_shortcut(event)
            if callback is not None:
                event.accept()
                if etype == QtCore.QEvent.KeyPress:
                    callback()
                return True
        elif etype == QtCore.QEvent.MouseButtonRelease and any(obj is t.tabBar() for t in self.tab_widgets):
            if event.button() == QtCore.Qt.MiddleButton:
                index = obj.tabAt(event_pos(event))
                tabs = next(t for t in self.tab_widgets if obj is t.tabBar())
                if index >= 0:
                    self.close_tab(index, tabs)
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------- #
    #  Small helpers                                                #
    # ------------------------------------------------------------- #
    def _with_editor(self, func, writable=False):
        editor = self.current_editor()
        if editor is None or (writable and editor.isReadOnly()):
            return None
        return func(editor)

    def _toggle_format_button(self, button):
        if button.isEnabled():
            button.toggle()

    def _cycle_tab(self, step):
        tabs = self._active_tabs
        count = tabs.count()
        if count > 1:
            tabs.setCurrentIndex((tabs.currentIndex() + step) % count)

    def show_message(self, text, msecs=6000):
        self.message_label.setText(text)
        self._message_timer.start(msecs)

    def has_scratchpad(self):
        return any(e.is_scratchpad for e in self.editors())

    def _startup(self):
        restored = False
        try:
            diagnostics.trace("startup: checking crash recovery")
            restored = self.restore_session_if_any()
            if not restored and self.settings.get_bool("reopen_tabs"):
                diagnostics.trace("startup: reopening session tabs")
                self._reopen_session_tabs()
        except Exception:
            diagnostics.log_exception("startup")
            self.show_message("Could not restore the previous session (see logs).")
        finally:
            self.autosave_timer.start()
            self._lock_timer.start()
            diagnostics.trace("startup finished")

    # ------------------------------------------------------------- #
    #  Tabs                                                         #
    # ------------------------------------------------------------- #
    def _make_tab_widget(self):
        tabs = QtWidgets.QTabWidget()
        tabs.setTabBar(ColorTabBar())
        tabs.setTabsClosable(True)
        tabs.setMovable(True)
        tabs.tabCloseRequested.connect(lambda index, t=tabs: self.close_tab(index, t))
        tabs.currentChanged.connect(lambda _index, t=tabs: self._on_tabs_current_changed(t))
        bar = tabs.tabBar()
        bar.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        bar.customContextMenuRequested.connect(lambda pos, t=tabs: self.show_tab_context_menu(pos, t))
        bar.installEventFilter(self)
        return tabs

    def editors(self):
        result = []
        for tabs in getattr(self, "tab_widgets", [self.tab_widget]):
            for i in range(tabs.count()):
                widget = tabs.widget(i)
                if isinstance(widget, AdvancedCodeEditor):
                    result.append(widget)
        return result

    def _tabs_of(self, editor):
        for tabs in self.tab_widgets:
            if tabs.indexOf(editor) >= 0:
                return tabs
        return None

    def _has_editor(self, editor):
        return editor is not None and self._tabs_of(editor) is not None

    def current_editor(self):
        widget = self._active_tabs.currentWidget()
        if isinstance(widget, AdvancedCodeEditor):
            return widget
        widget = self.tab_widget.currentWidget()
        return widget if isinstance(widget, AdvancedCodeEditor) else None

    def _focus_editor(self, editor, focus=True):
        tabs = self._tabs_of(editor)
        if tabs is None:
            return
        self._active_tabs = tabs
        tabs.setCurrentWidget(editor)
        if focus:
            editor.setFocus()
        self.on_tab_changed()

    def _on_tabs_current_changed(self, tabs):
        if tabs.count():
            self._active_tabs = tabs
        self.on_tab_changed()

    def _on_focus_changed(self, _old, new):
        if self._closing or new is None:
            return
        try:
            for tabs in self.tab_widgets:
                if tabs.isAncestorOf(new) and tabs is not self._active_tabs:
                    self._active_tabs = tabs
                    self.on_tab_changed()
                    return
        except RuntimeError:
            pass

    def create_tab(self, title=None, activate=True):
        editor = AdvancedCodeEditor(self)
        editor.setMinimumHeight(200)
        editor.custom_title = title
        editor.installEventFilter(self)
        editor.textChanged.connect(self.on_text_changed)
        editor.cursorPositionChanged.connect(self.on_cursor_moved)
        editor.currentCharFormatChanged.connect(self.on_char_format_changed)
        editor.emptied.connect(self.on_text_emptied)
        editor.zoom_requested.connect(self.zoom_step)
        editor.bookmarks_changed.connect(self.on_bookmarks_changed)
        editor.link_activated.connect(self.on_link)
        editor.task_toggled.connect(self.on_task_toggled)
        editor.document().modificationChanged.connect(self._on_modification_changed)
        editor.snippet_provider = self.snippets_panel.provider
        editor.image_saver = self._save_pasted_image
        editor.run_id = self._next_run_id
        self._run_ids["<editor:{}>".format(self._next_run_id)] = editor
        self._next_run_id += 1
        self._apply_editor_settings(editor)
        self._apply_editor_mode(editor, None)

        tabs = self._active_tabs if self._active_tabs.isVisible() or self._active_tabs is self.tab_widget \
            else self.tab_widget
        index = tabs.addTab(editor, "Untitled")
        if activate:
            self._active_tabs = tabs
            tabs.setCurrentIndex(index)
            editor.setFocus()
        self.update_tab_titles()
        return editor

    def _take_blank_or_new_tab(self):
        editor = self.current_editor()
        if (editor is not None and editor.file_path is None and not editor.is_modified
                and editor.document().isEmpty() and not editor.is_scratchpad
                and editor.binding is None and editor.follower is None
                and editor is not self._follow_editor):
            editor.setCurrentCharFormat(QtGui.QTextCharFormat())
            return editor
        return self.create_tab()

    def _editor_for_path(self, path):
        target = fileio.norm_path(path)
        for editor in self.editors():
            if editor.file_path and editor.binding is None and fileio.norm_path(editor.file_path) == target:
                return editor
        return None

    def new_tab(self):
        self.create_tab()

    def on_tab_changed(self, *_args):
        editor = self.current_editor()
        if editor is None:
            return
        self._update_formatting_state(editor)
        self.on_char_format_changed(editor.currentCharFormat())
        self._update_extra_selections(editor)
        self._update_status()
        self._deferred_timer.start()
        self._outline_timer.start()

    # ----- Split view ------------------------------------------ #
    def toggle_split(self):
        """Show / hide the second editor area. Hiding moves its tabs back."""
        if self.split_tabs.isVisible():
            for editor in [self.split_tabs.widget(i) for i in range(self.split_tabs.count())]:
                self._move_editor(editor, self.tab_widget)
            self.split_tabs.setVisible(False)
            self._active_tabs = self.tab_widget
        else:
            self.split_tabs.setVisible(True)
            editor = self.current_editor()
            if editor is not None and self.tab_widget.count() > 1:
                self._move_editor(editor, self.split_tabs)
            else:
                self._active_tabs = self.split_tabs
                self.create_tab()
            width = self.tabs_splitter.width()
            self.tabs_splitter.setSizes([width // 2, width // 2])

    def move_tab_to_other_side(self):
        editor = self.current_editor()
        if editor is None:
            return
        source = self._tabs_of(editor)
        if source is None:
            return
        target = self.split_tabs if source is self.tab_widget else self.tab_widget
        if target is self.split_tabs and not self.split_tabs.isVisible():
            self.split_tabs.setVisible(True)
            width = self.tabs_splitter.width()
            self.tabs_splitter.setSizes([width // 2, width // 2])
        if source.count() == 1 and source is self.tab_widget:
            self.show_message("Keep at least one tab on the left.")
            return
        self._move_editor(editor, target)
        if self.split_tabs.count() == 0:
            self.split_tabs.setVisible(False)

    def _move_editor(self, editor, target):
        source = self._tabs_of(editor)
        if source is None or source is target:
            return
        source.removeTab(source.indexOf(editor))
        target.addTab(editor, "")
        self.update_tab_titles()
        self._focus_editor(editor)

    # ----- Closing tabs ---------------------------------------- #
    def close_current_tab(self):
        editor = self.current_editor()
        if editor is not None:
            self.close_editor_tab(editor)

    def close_tab(self, index, tabs=None):
        """Close the tab at index of a tab area (default: the active one)."""
        tabs = tabs or self._active_tabs
        editor = tabs.widget(index)
        if not isinstance(editor, AdvancedCodeEditor):
            return True
        return self.close_editor_tab(editor)

    def close_editor_tab(self, editor):
        """Close a tab, asking to save first. Returns False if cancelled."""
        if editor.is_scratchpad:
            self.show_message("The Scratchpad is always here; it saves itself.")
            return False
        if editor is self._follow_editor:
            self.actions["follow_notes"].qaction.setChecked(False)
            return True
        if editor.is_modified and editor.follower is None:
            self._focus_editor(editor)
            reply = QtWidgets.QMessageBox.question(
                self, "Save Changes?",
                "Save changes to \"{}\" before closing?".format(self._display_name(editor)),
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Save)
            if reply == QtWidgets.QMessageBox.Cancel:
                return False
            if reply == QtWidgets.QMessageBox.Save and not self.save_editor(editor):
                return False
        self._dispose_editor(editor)
        if not self.editors():
            self.create_tab()
        self._recovery_dirty = True
        return True

    def _dispose_editor(self, editor):
        if not editor.is_modified:
            self._save_bookmarks(editor)
        if editor.follower is not None:
            editor.follower.stop()
        path = editor.file_path
        self._release_lock(editor)
        self._run_ids.pop("<editor:{}>".format(editor.run_id), None)
        tabs = self._tabs_of(editor)
        if tabs is not None:
            tabs.removeTab(tabs.indexOf(editor))
        editor.deleteLater()
        if self.split_tabs.count() == 0 and self.split_tabs.isVisible():
            self.split_tabs.setVisible(False)
            self._active_tabs = self.tab_widget
        if self.tab_widget.count() == 0 and self.split_tabs.count():
            self._move_editor(self.split_tabs.widget(0), self.tab_widget)
        if path and self._editor_for_path(path) is None:
            self._unwatch(path)

    def show_tab_context_menu(self, pos, tabs=None):
        tabs = tabs or self.tab_widget
        tab_bar = tabs.tabBar()
        index = tab_bar.tabAt(pos)
        menu = QtWidgets.QMenu(self)
        new_tab_action = menu.addAction("New Tab")
        if index < 0:
            if qt_exec(menu, tab_bar.mapToGlobal(pos)) == new_tab_action:
                self._active_tabs = tabs
                self.new_tab()
            return
        editor = tabs.widget(index)
        menu.addSeparator()
        rename_action = menu.addAction("Rename Tab...")
        duplicate_action = menu.addAction("Duplicate Tab")
        move_action = menu.addAction("Move to Other Side")
        reveal_action = menu.addAction("Show in File Browser") if editor.file_path else None
        copy_path_action = menu.addAction("Copy Path") if editor.file_path else None
        menu.addSeparator()
        close_action = menu.addAction("Close Tab")
        close_others_action = menu.addAction("Close Other Tabs")
        close_all_action = menu.addAction("Close All Tabs")
        action = qt_exec(menu, tab_bar.mapToGlobal(pos))
        if action is None:
            return
        if action == new_tab_action:
            self._active_tabs = tabs
            self.new_tab()
        elif action == rename_action:
            self.rename_tab(editor)
        elif action == duplicate_action:
            self.duplicate_tab(editor)
        elif action == move_action:
            self._focus_editor(editor)
            self.move_tab_to_other_side()
        elif reveal_action is not None and action == reveal_action:
            self.file_browser.setVisible(True)
            self.file_browser.set_root(os.path.dirname(editor.file_path))
        elif copy_path_action is not None and action == copy_path_action:
            QtWidgets.QApplication.clipboard().setText(editor.file_path)
        elif action == close_action:
            self.close_editor_tab(editor)
        elif action == close_others_action:
            self.close_other_tabs(editor)
        elif action == close_all_action:
            self.close_all_tabs()

    def rename_tab(self, editor):
        new_title, ok = QtWidgets.QInputDialog.getText(self, "Rename Tab", "New name:",
                                                       text=self._display_name(editor))
        if ok and new_title.strip():
            editor.custom_title = new_title.strip()
            self.update_tab_titles()

    def duplicate_tab(self, source):
        rich = source.rich
        content = source.toHtml() if rich else source.toPlainText()
        editor = self.create_tab("{} (copy)".format(self._display_name(source)))
        editor.language = source.language
        editor.set_rich(rich)
        editor.highlighter.set_language(source.language)
        if rich:
            editor.setHtml(content)
        else:
            editor.setPlainText(content)
        editor.is_modified = True
        self._update_formatting_state(editor)
        self.update_tab_titles()

    def close_other_tabs(self, keep):
        for editor in self.editors():
            if editor is keep or editor.is_scratchpad:
                continue
            if not self.close_editor_tab(editor):
                break

    def close_all_tabs(self):
        for editor in self.editors():
            if editor.is_scratchpad:
                continue
            if not self.close_editor_tab(editor):
                break

    def _display_name(self, editor):
        if editor.is_scratchpad:
            return "Scratchpad"
        if editor.binding is not None:
            return editor.binding.title
        if editor is self._follow_editor:
            return "Note: {}".format(self._follow_node) if self._follow_node else "Node Note (follow)"
        if editor.custom_title:
            return editor.custom_title
        if editor.file_path:
            name = os.path.basename(editor.file_path)
            return "Log: " + name if editor.follower is not None else name
        return "Untitled"

    def _on_modification_changed(self, modified):
        editor = self.sender()
        if isinstance(editor, QtGui.QTextDocument):
            editor = next((e for e in self.editors() if e.document() is editor), None)
        if isinstance(editor, AdvancedCodeEditor):
            if modified:
                self._take_lock(editor)
            else:
                self._release_lock(editor)
        self.update_tab_titles()

    def update_tab_titles(self, *_args):
        for tabs in getattr(self, "tab_widgets", [self.tab_widget]):
            tab_bar = tabs.tabBar()
            for index in range(tabs.count()):
                editor = tabs.widget(index)
                if not isinstance(editor, AdvancedCodeEditor):
                    continue
                label = self._display_name(editor)
                if editor.is_modified and editor.follower is None and not editor.is_scratchpad:
                    label += " \u25cf"
                tabs.setTabText(index, label)
                if editor.binding is not None:
                    tooltip, color = editor.binding.tooltip, tab_color("binding")
                elif editor.is_scratchpad:
                    tooltip, color = "Always-saved scratchpad", tab_color("scratchpad")
                else:
                    tooltip = editor.file_path or "Not saved yet"
                    key = "rich" if editor.file_path and fileio.is_rich_path(editor.file_path) else editor.language
                    color = tab_color(key)
                tabs.setTabToolTip(index, tooltip)
                tab_bar.setTabTextColor(index, QtGui.QColor(color))
                if editor.is_scratchpad:
                    for side in (QtWidgets.QTabBar.LeftSide, QtWidgets.QTabBar.RightSide):
                        button = tab_bar.tabButton(index, side)
                        if button is not None:
                            button.hide()

    # ------------------------------------------------------------- #
    #  Editor appearance / mode                                     #
    # ------------------------------------------------------------- #
    def _apply_editor_mode(self, editor, path, language=None):
        """Untitled documents, the scratchpad and .tnote files are rich;
        every other file is plain text."""
        editor.language = language or fileio.language_for_path(path)
        editor.set_rich((path is None or fileio.is_rich_path(path)) and editor.binding is None
                        and editor.follower is None)
        editor.highlighter.set_language(editor.language)
        if editor is self.current_editor():
            self._update_formatting_state(editor)

    def _apply_font(self, editor):
        point_size = self.DEFAULT_FONT_SIZE * self.zoom / 100.0
        font = QtGui.QFont(self.font_family)
        font.setPointSizeF(point_size)
        font.setStyleHint(QtGui.QFont.Monospace)
        # A widget style sheet is needed: the window's "font-size: 11px"
        # rule would otherwise override setFont() on the editor.
        editor.setStyleSheet("QTextEdit {{ font-family: '{}'; font-size: {:.1f}pt; }}".format(
            self.font_family, point_size))
        editor.setFont(font)
        editor.document().setDefaultFont(font)
        editor.line_number_area.setFont(font)
        editor.setTabStopDistance(QtGui.QFontMetricsF(font).horizontalAdvance(" ") * INDENT_TAB_SIZE)
        editor.update_line_number_area_width()
        editor.update_line_number_area()

    def _apply_editor_settings(self, editor):
        self._apply_font(editor)
        editor.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth if self.word_wrap else QtWidgets.QTextEdit.NoWrap)
        self.apply_gutter_settings(editor)

    def _update_formatting_state(self, editor):
        rich = editor.rich and not editor.isReadOnly()
        tips = {self.bold_button: "Bold (Ctrl+B)", self.italic_button: "Italic (Ctrl+I)",
                self.underline_button: "Underline (Ctrl+U)", self.color_button: "Text color",
                self.highlight_button: "Highlight color", self.align_dropdown: "Alignment",
                self.fontsize_combo: "Font size", self.fontfamily_combo: "Font"}
        for widget in self._rich_widgets:
            widget.setEnabled(rich)
            widget.setToolTip(tips[widget] if rich else RICH_TOOLTIP)

    def set_zoom(self, value):
        value = max(50, min(200, int(value)))
        self.zoom = value
        if self.zoom_slider.value() != value:
            with SignalBlocker(self.zoom_slider):
                self.zoom_slider.setValue(value)
        self.zoom_label.setText("{}%".format(value))
        for editor in self.editors():
            self._apply_font(editor)
        self.settings.set("zoom", value)

    def zoom_step(self, direction):
        self.set_zoom(self.zoom + 10 * direction)

    def set_word_wrap(self, enabled):
        self.word_wrap = bool(enabled)
        mode = QtWidgets.QTextEdit.WidgetWidth if self.word_wrap else QtWidgets.QTextEdit.NoWrap
        for editor in self.editors():
            editor.setLineWrapMode(mode)
        self.settings.set("word_wrap", self.word_wrap)

    def set_autosave_to_file(self, enabled):
        self.autosave_to_file = bool(enabled)
        self.settings.set("autosave_to_file", self.autosave_to_file)
        self.show_message("Autosave to file is {}.".format("ON" if enabled else "OFF"))

    def apply_gutter_settings(self, editor=None):
        editor = editor or self.current_editor()
        if editor is None:
            return
        editor.show_line_numbers = self.gutter_show_line_numbers
        editor.show_fold_markers = self.gutter_show_fold_markers
        editor.gutter_scale = self.gutter_scale
        editor.update_line_number_area_width()
        editor.update_line_number_area()

    def show_gutter_settings(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Gutter Settings")
        layout = QtWidgets.QVBoxLayout(dlg)
        cb_numbers = QtWidgets.QCheckBox("Show line numbers")
        cb_numbers.setChecked(self.gutter_show_line_numbers)
        cb_folds = QtWidgets.QCheckBox("Show fold markers")
        cb_folds.setChecked(self.gutter_show_fold_markers)
        size_value = QtWidgets.QLabel("{}%".format(int(self.gutter_scale * 100)))
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(80, 200)
        slider.setValue(int(self.gutter_scale * 100))
        slider.valueChanged.connect(lambda v: size_value.setText("{}%".format(v)))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Gutter size:"))
        row.addWidget(slider)
        row.addWidget(size_value)
        layout.addWidget(cb_numbers)
        layout.addWidget(cb_folds)
        layout.addLayout(row)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if qt_exec(dlg) == QtWidgets.QDialog.Accepted:
            self.gutter_show_line_numbers = cb_numbers.isChecked()
            self.gutter_show_fold_markers = cb_folds.isChecked()
            self.gutter_scale = slider.value() / 100.0
            self.settings.set("gutter/line_numbers", self.gutter_show_line_numbers)
            self.settings.set("gutter/fold_markers", self.gutter_show_fold_markers)
            self.settings.set("gutter/scale", self.gutter_scale)
            for editor in self.editors():
                self.apply_gutter_settings(editor)

    def toggle_sidebar(self):
        visible = not self.file_browser.isVisible()
        self.file_browser.setVisible(visible)
        self.settings.set("browser/visible", visible)

    def toggle_side_panel(self):
        visible = not self.side_tabs.isVisible()
        self.side_tabs.setVisible(visible)
        self.settings.set("side_panel/visible", visible)
        if visible:
            self._outline_timer.start()

    def show_side(self, panel):
        self.side_tabs.setVisible(True)
        self.settings.set("side_panel/visible", True)
        self.side_tabs.setCurrentWidget(panel)
        if panel is self.outline_panel:
            self._refresh_outline(force=True)
        elif panel is self.preview_panel:
            self.preview_panel.refresh(self.current_editor())

    def toggle_output_panel(self):
        self.bottom_tabs.setVisible(not self.bottom_tabs.isVisible())

    def show_find_in_files(self):
        self.show_side(self.search_panel)
        editor = self.current_editor()
        selected = editor.textCursor().selectedText() if editor else ""
        self.search_panel.focus_query(selected if " " not in selected else "")

    def show_todos(self):
        self.show_side(self.todo_panel)
        self.todo_panel.refresh()

    # ------------------------------------------------------------- #
    #  Status / highlights / outline                                #
    # ------------------------------------------------------------- #
    def _update_status(self):
        editor = self.current_editor()
        if editor is None:
            self.status_bar.setText("")
            return
        cursor = editor.textCursor()
        parts = ["Ln {}, Col {}".format(cursor.blockNumber() + 1, cursor.positionInBlock() + 1)]
        if self._word_count_text:
            parts.append(self._word_count_text)
        if self._task_text:
            parts.append(self._task_text)
        self.status_bar.setText(" | ".join(parts))

    def _update_counts(self):
        editor = self.current_editor()
        if editor is None:
            return
        text = editor.toPlainText()
        self._word_count_text = "Words: {} | Characters: {}".format(len(text.split()), len(text))
        done, total = textops.count_tasks(text)
        self._task_text = "Tasks {}/{}".format(done, total) if total else ""
        self._update_status()

    def on_text_changed(self):
        self.last_edit_time = QtCore.QDateTime.currentDateTime()
        self._recovery_dirty = True
        self._deferred_timer.start()
        self._outline_timer.start()

    def on_cursor_moved(self):
        editor = self.current_editor()
        if editor is None:
            return
        word = self._word_under_cursor(editor)
        if word != editor.highlighted_word:
            editor.word_selections = []
        self._update_extra_selections(editor)
        self._update_status()
        self._sync_alignment(editor)
        self._deferred_timer.start()

    def _refresh_deferred(self):
        editor = self.current_editor()
        if editor is None:
            return
        editor.highlighted_word = self._word_under_cursor(editor)
        editor.word_selections = self._compute_word_selections(editor, editor.highlighted_word)
        self._update_extra_selections(editor)
        self._update_counts()
        if editor.language == "python" and self.settings.get_bool("lint_on_save"):
            revision = editor.document().revision()
            if getattr(editor, "_lint_revision", None) != revision:
                editor._lint_revision = revision
                self._lint_editor(editor)

    def _refresh_outline(self, force=False):
        if not self.side_tabs.isVisible():
            return
        current = self.side_tabs.currentWidget()
        if current is self.outline_panel:
            self.outline_panel.refresh(self.current_editor(), force=force)
        elif current is self.preview_panel:
            self.preview_panel.refresh(self.current_editor())

    @staticmethod
    def _word_under_cursor(editor):
        cursor = editor.textCursor()
        if cursor.hasSelection():
            return ""
        cursor.select(QtGui.QTextCursor.WordUnderCursor)
        return cursor.selectedText()

    def _update_extra_selections(self, editor):
        selections = []
        line_sel = QtWidgets.QTextEdit.ExtraSelection()
        line_cursor = editor.textCursor()
        line_cursor.clearSelection()
        line_sel.cursor = line_cursor
        line_fmt = QtGui.QTextCharFormat()
        line_fmt.setBackground(QtGui.QColor(themes.color("current_line")))
        line_fmt.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
        line_sel.format = line_fmt
        selections.append(line_sel)
        selections.extend(editor.word_selections)
        selections.extend(self._bracket_selections(editor))
        editor.setExtraSelections(selections)

    def _bracket_selections(self, editor):
        cursor = editor.textCursor()
        if cursor.hasSelection():
            return []
        pos = cursor.position()
        index = -1
        for candidate in (pos - 1, pos):
            ch = editor._char_at(candidate)
            if ch and (ch in BRACKET_PAIRS or ch in CLOSING_BRACKETS):
                index = candidate
                break
        if index < 0:
            return []
        doc = editor.document()
        partner = find_matching_bracket(doc, index)
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(QtGui.QColor(themes.color("word") if partner >= 0 else themes.color("bracket_bad")))
        selections = []
        for i in ((index, partner) if partner >= 0 else (index,)):
            c = QtGui.QTextCursor(doc)
            c.setPosition(i)
            c.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
            sel = QtWidgets.QTextEdit.ExtraSelection()
            sel.cursor = c
            sel.format = fmt
            selections.append(sel)
        return selections

    def _compute_word_selections(self, editor, word, max_matches=500, max_chars=2000000):
        if not word or len(word) < 2 or not any(ch.isalnum() for ch in word):
            return []
        doc = editor.document()
        if doc.characterCount() > max_chars:
            return []
        regex = QtCore.QRegularExpression(r"\b" + QtCore.QRegularExpression.escape(word) + r"\b")
        it = regex.globalMatch(doc.toPlainText())
        cursor = editor.textCursor()
        cursor.select(QtGui.QTextCursor.WordUnderCursor)
        active = (cursor.selectionStart(), cursor.selectionEnd())
        main_fmt = QtGui.QTextCharFormat()
        main_fmt.setBackground(QtGui.QColor(themes.color("word")))
        other_fmt = QtGui.QTextCharFormat()
        other_fmt.setBackground(QtGui.QColor(themes.color("word_other")))
        selections = []
        while it.hasNext() and len(selections) < max_matches:
            match = it.next()
            start, end = match.capturedStart(), match.capturedEnd()
            if start < 0 or end <= start:
                continue
            c = QtGui.QTextCursor(doc)
            c.setPosition(start)
            c.setPosition(end, QtGui.QTextCursor.KeepAnchor)
            sel = QtWidgets.QTextEdit.ExtraSelection()
            sel.cursor = c
            sel.format = main_fmt if (start, end) == active else other_fmt
            selections.append(sel)
        return selections if len(selections) > 1 else []

    # ------------------------------------------------------------- #
    #  Navigation                                                   #
    # ------------------------------------------------------------- #
    def _goto_line_in_current(self, line):
        editor = self.current_editor()
        if editor is not None:
            editor.goto_line(line)
            editor.setFocus()

    def goto_line(self):
        editor = self.current_editor()
        if editor is None:
            return
        line = ask_line_number(self, editor.textCursor().blockNumber() + 1, editor.document().blockCount())
        if line is not None:
            editor.goto_line(line - 1)
            editor.setFocus()

    def _open_location(self, path, line, text):
        editor = self.open_path(path)
        if editor is None:
            return
        if editor.rich and text:
            found = editor.document().find(text.strip())
            if not found.isNull():
                editor.setTextCursor(found)
                editor.center_cursor()
                return
        if line >= 0:
            editor.goto_line(line)

    def toggle_comment(self):
        editor = self.current_editor()
        if editor is None or editor.isReadOnly():
            return
        if not editor.toggle_comment():
            self.show_message("No line comment style for this file type.")

    def insert_task(self):
        editor = self.current_editor()
        if editor is None or editor.isReadOnly():
            return
        cursor = editor.textCursor()
        block = cursor.block()
        if textops.TASK_RE.match(block.text()):
            return
        if block.text().strip():
            cursor.movePosition(QtGui.QTextCursor.EndOfBlock)
            cursor.insertText("\n")
        indent = block.text()[:len(block.text()) - len(block.text().lstrip())] if not block.text().strip() else ""
        cursor.insertText(indent + "- [ ] ")
        editor.setTextCursor(cursor)
        editor.setFocus()

    def _insert_text(self, text):
        editor = self.current_editor()
        if editor is not None and not editor.isReadOnly():
            editor.textCursor().insertText(text)
            editor.setFocus()

    def insert_snippet(self, body):
        editor = self.current_editor()
        if editor is not None and not editor.isReadOnly():
            editor.insert_snippet(body)
            editor.setFocus()

    # ------------------------------------------------------------- #
    #  Links                                                        #
    # ------------------------------------------------------------- #
    def on_link(self, kind, value):
        if kind == "url":
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(value))
        elif kind == "frame":
            if nuke_bridge.goto_frame(value):
                self.show_message("Frame {}".format(value))
            else:
                self.show_message("Frame links work inside Nuke.")
        elif kind == "node":
            if nuke_bridge.select_node(value):
                self.show_message("Selected {}".format(value))
            elif nuke_bridge.available():
                self.show_message("Node {} not found.".format(value))
            else:
                self.show_message("Node links work inside Nuke.")
        elif kind == "path":
            self._open_path_link(value)

    def _open_path_link(self, value):
        path = os.path.expanduser(value)
        editor = self.current_editor()
        if not os.path.isabs(path) and editor is not None and editor.file_path:
            path = os.path.join(os.path.dirname(editor.file_path), path)
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        is_image = os.path.splitext(re.sub(r"(%0?\d*d|#+)", "0", path))[1].lower() in IMAGE_EXTENSIONS
        if is_image:
            menu = QtWidgets.QMenu(self)
            read_action = menu.addAction("Create Read Node")
            read_action.setEnabled(nuke_bridge.available())
            folder_action = menu.addAction("Open Folder")
            copy_action = menu.addAction("Copy Path")
            chosen = qt_exec(menu, QtGui.QCursor.pos())
            if chosen == read_action:
                if nuke_bridge.create_read(path):
                    self.show_message("Created Read for {}".format(os.path.basename(path)))
            elif chosen == folder_action:
                self._open_folder(folder)
            elif chosen == copy_action:
                QtWidgets.QApplication.clipboard().setText(path)
            return
        if os.path.isfile(path):
            self.open_path(path)
        elif os.path.isdir(folder):
            self._open_folder(folder)
        else:
            self.show_message("Not found: {}".format(path))

    def _open_folder(self, folder):
        if os.path.isdir(folder):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))
        else:
            self.show_message("Folder not found: {}".format(folder))

    # ------------------------------------------------------------- #
    #  Bookmarks                                                    #
    # ------------------------------------------------------------- #
    def _bookmark_store_path(self, file_path):
        return os.path.join(fileio.data_dir("bookmarks"), fileio.path_key(file_path) + ".json")

    def _load_bookmarks(self, editor):
        if not editor.file_path:
            return
        store = self._bookmark_store_path(editor.file_path)
        legacy = editor.file_path + ".bookmarks.json"  # written by version 1
        source = store if os.path.exists(store) else (legacy if os.path.exists(legacy) else None)
        if source is None:
            editor.set_bookmarks({})
            return
        try:
            with io.open(source, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return
        mapping = {}
        for item in data.get("bookmarks", []) if isinstance(data, dict) else []:
            try:
                line, color = int(item.get("line", -1)), int(item.get("color", 0))
            except (TypeError, ValueError, AttributeError):
                continue
            if line >= 0:
                mapping[line] = color
        editor.set_bookmarks(mapping)

    def _save_bookmarks(self, editor):
        if not editor.file_path or editor.binding is not None:
            return
        store = self._bookmark_store_path(editor.file_path)
        mapping = editor.bookmark_map()
        try:
            if not mapping:
                if os.path.exists(store):
                    os.remove(store)
                return
            data = {"path": editor.file_path,
                    "bookmarks": [{"line": l, "color": c} for l, c in sorted(mapping.items())]}
            fileio.write_text_file(store, json.dumps(data, indent=1))
        except Exception:
            pass

    def on_bookmarks_changed(self):
        editor = self.sender()
        if isinstance(editor, AdvancedCodeEditor) and editor.file_path and not editor.is_modified:
            self._save_bookmarks(editor)

    def show_bookmark_list(self):
        editor = self.current_editor()
        if editor is None:
            return
        bookmarks = editor.bookmark_map()
        if not bookmarks:
            QtWidgets.QMessageBox.information(self, "Bookmarks", "No bookmarks in this document.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Bookmarks")
        layout = QtWidgets.QVBoxLayout(dialog)
        list_widget = QtWidgets.QListWidget()
        lines = sorted(bookmarks)
        for line in lines:
            text = editor.document().findBlockByNumber(line).text().strip() or "<empty line>"
            item = QtWidgets.QListWidgetItem("Line {}: {}".format(line + 1, text))
            item.setForeground(editor.bookmark_palette[bookmarks[line] % len(editor.bookmark_palette)])
            list_widget.addItem(item)

        def on_item(item):
            row = list_widget.row(item)
            if 0 <= row < len(lines):
                editor.goto_line(lines[row])
            dialog.accept()

        list_widget.itemActivated.connect(on_item)
        list_widget.itemDoubleClicked.connect(on_item)
        layout.addWidget(list_widget)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        box.rejected.connect(dialog.reject)
        layout.addWidget(box)
        dialog.resize(420, 260)
        qt_exec(dialog)

    # ------------------------------------------------------------- #
    #  Formatting (rich documents)                                  #
    # ------------------------------------------------------------- #
    def _rich_editor(self):
        editor = self.current_editor()
        return editor if editor is not None and editor.rich and not editor.isReadOnly() else None

    def _merge_format(self, fmt):
        editor = self._rich_editor()
        if editor:
            editor.mergeCurrentCharFormat(fmt)
            editor.setFocus()

    def on_char_format_changed(self, fmt):
        with SignalBlocker(self.bold_button, self.italic_button, self.underline_button,
                           self.fontsize_combo, self.fontfamily_combo):
            self.bold_button.setChecked(fmt.font().bold())
            self.italic_button.setChecked(fmt.fontItalic())
            self.underline_button.setChecked(fmt.fontUnderline())
            size = fmt.fontPointSize() if fmt.hasProperty(QtGui.QTextFormat.FontPointSize) else self.DEFAULT_FONT_SIZE
            self.fontsize_combo.setCurrentText(str(int(round(size))))
            family = format_font_family(fmt)
            self.fontfamily_combo.setCurrentFont(QtGui.QFont(family or self.font_family))

    def _sync_alignment(self, editor):
        alignment = editor.alignment()
        if alignment & QtCore.Qt.AlignJustify:
            index = 3
        elif alignment & QtCore.Qt.AlignRight:
            index = 2
        elif alignment & QtCore.Qt.AlignHCenter:
            index = 1
        else:
            index = 0
        if self.align_dropdown.currentIndex() != index:
            with SignalBlocker(self.align_dropdown):
                self.align_dropdown.setCurrentIndex(index)

    def on_text_emptied(self):
        editor = self.sender()
        if isinstance(editor, AdvancedCodeEditor) and editor.rich:
            editor.setCurrentCharFormat(QtGui.QTextCharFormat())

    def change_alignment(self, index):
        editor = self._rich_editor()
        if editor is not None:
            editor.setAlignment([QtCore.Qt.AlignLeft, QtCore.Qt.AlignCenter,
                                 QtCore.Qt.AlignRight, QtCore.Qt.AlignJustify][index])

    def change_fontsize(self, size_str):
        try:
            size = float(size_str)
        except ValueError:
            return
        fmt = QtGui.QTextCharFormat()
        fmt.setFontPointSize(size)
        self._merge_format(fmt)

    def change_fontfamily(self, font):
        fmt = QtGui.QTextCharFormat()
        set_format_font_family(fmt, font.family())
        self._merge_format(fmt)

    def toggle_bold(self, checked):
        fmt = QtGui.QTextCharFormat()
        fmt.setFontWeight(QtGui.QFont.Bold if checked else QtGui.QFont.Normal)
        self._merge_format(fmt)

    def toggle_italic(self, checked):
        fmt = QtGui.QTextCharFormat()
        fmt.setFontItalic(checked)
        self._merge_format(fmt)

    def toggle_underline(self, checked):
        fmt = QtGui.QTextCharFormat()
        fmt.setFontUnderline(checked)
        self._merge_format(fmt)

    def choose_text_color(self):
        if self._rich_editor() is None:
            return
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor("#ffffff"), self)
        if color.isValid():
            fmt = QtGui.QTextCharFormat()
            fmt.setForeground(QtGui.QBrush(color))
            self._merge_format(fmt)

    def choose_highlight_color(self):
        if self._rich_editor() is None:
            return
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor("#44475a"), self)
        if color.isValid():
            fmt = QtGui.QTextCharFormat()
            fmt.setBackground(QtGui.QBrush(color))
            self._merge_format(fmt)

    def edit_undo(self):
        self._with_editor(lambda e: e.undo(), True)

    def edit_redo(self):
        self._with_editor(lambda e: e.redo(), True)

    def _save_pasted_image(self, image):
        path = os.path.join(fileio.data_dir("images"), "paste_{}.png".format(time.strftime("%Y%m%d_%H%M%S")))
        return path if image.save(path, "PNG") else None

    # ------------------------------------------------------------- #
    #  Opening files                                                #
    # ------------------------------------------------------------- #
    def open(self):
        editor = self.current_editor()
        start = os.path.dirname(editor.file_path) if editor and editor.file_path else self.file_browser.root_path
        paths, _f = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Open File", start, "All Files (*);;Rich Notes (*.tnote);;Text Files (*.txt);;Nuke Scripts (*.nk)")
        for path in paths:
            self.open_path(path)

    def open_path(self, path, line=None):
        """Open a file in a tab (or switch to it if it is already open)."""
        path = os.path.abspath(os.path.expanduser(path))
        existing = self._editor_for_path(path)
        if existing is not None:
            self._focus_editor(existing)
            if line is not None:
                existing.goto_line(line)
            return existing
        if not os.path.isfile(path):
            QtWidgets.QMessageBox.warning(self, "Error", "File not found:\n{}".format(path))
            if path in self.recent_files:
                self.recent_files.remove(path)
                self._store_recent_files()
            return None
        try:
            text, encoding, newline = fileio.read_text_file(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Error", "Could not open file:\n{}\n\n{}".format(path, exc))
            return None

        editor = self._take_blank_or_new_tab()
        editor.custom_title = None
        self._apply_editor_mode(editor, path)
        self._load_content(editor, path, text)
        editor.file_path = path
        editor.encoding = encoding
        editor.newline = newline
        editor.disk_mtime = fileio.file_mtime(path)
        editor.moveCursor(QtGui.QTextCursor.Start)
        editor.is_modified = False
        self._load_bookmarks(editor)
        self._watch(path)
        self.add_recent_file(path)
        self.update_tab_titles()
        self.on_tab_changed()
        if line is not None:
            editor.goto_line(line)
        self._warn_if_locked(editor)
        return editor

    def _load_content(self, editor, path, text):
        if editor.rich:
            editor.document().setBaseUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(path) + "/"))
            editor.setHtml(fileio.strip_body_font(text))
        else:
            editor.setPlainText(text)

    def open_log_dialog(self):
        path, _f = QtWidgets.QFileDialog.getOpenFileName(self, "Open Log", self.file_browser.root_path,
                                                         "Logs (*.log *.txt);;All Files (*)")
        if path:
            self.open_log(path)

    def open_log(self, path):
        """Open a log file read-only and keep appending new lines."""
        for editor in self.editors():
            if editor.follower is not None and fileio.norm_path(editor.file_path) == fileio.norm_path(path):
                self._focus_editor(editor)
                return editor
        editor = self.create_tab()
        editor.file_path = os.path.abspath(path)
        editor.setReadOnly(True)
        editor.set_rich(False)
        editor.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        editor.follower = LogFollower(editor, editor.file_path)
        self._apply_editor_mode(editor, None, language="log")
        self.update_tab_titles()
        self.on_tab_changed()
        return editor

    def on_path_deleted(self, path):
        deleted = fileio.norm_path(path)
        for editor in self.editors():
            if not editor.file_path or editor.binding is not None:
                continue
            current = fileio.norm_path(editor.file_path)
            if current == deleted or current.startswith(deleted.rstrip(os.sep) + os.sep):
                editor.is_modified = True
                self.show_message("{} was deleted on disk; its tab is kept as unsaved.".format(
                    os.path.basename(editor.file_path)))

    def add_recent_file(self, path):
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[: self.MAX_RECENT_FILES]
        self._store_recent_files()

    def _store_recent_files(self):
        self.settings.set("recent_files", list(self.recent_files))
        self.update_recent_files_menu()

    def update_recent_files_menu(self):
        self.recent_menu.clear()
        if not self.recent_files:
            self.recent_menu.addAction("(No Recent Files)").setEnabled(False)
            return
        for path in self.recent_files:
            action = self.recent_menu.addAction(path)
            action.triggered.connect(lambda _c=False, p=path: self.open_path(p))
        self.recent_menu.addSeparator()
        clear = self.recent_menu.addAction("Clear Recent Files")
        clear.triggered.connect(lambda _c=False: (self.recent_files.clear(), self._store_recent_files()))

    # ----- Watching files for outside changes ------------------ #
    def _watch(self, path):
        if path and os.path.isfile(path) and path not in self._watcher.files():
            self._watcher.addPath(path)

    def _unwatch(self, path):
        if path in self._watcher.files():
            self._watcher.removePath(path)

    def _on_file_changed(self, path):
        self._pending_disk_changes.add(path)
        self._disk_timer.start()

    def _process_disk_changes(self):
        paths = list(self._pending_disk_changes)
        self._pending_disk_changes.clear()
        for path in paths:
            if os.path.isfile(path):
                self._watch(path)  # safe-save replaces the file, which drops the watch
            editor = self._editor_for_path(path)
            if editor is None or editor.follower is not None:
                continue
            mtime = fileio.file_mtime(path)
            if mtime is None:
                editor.is_modified = True
                self.show_message("{} was deleted or moved on disk.".format(os.path.basename(path)))
                continue
            if editor.disk_mtime is not None and abs(mtime - editor.disk_mtime) < 0.001:
                continue  # our own save
            if not editor.is_modified:
                self._reload_editor(editor)
                self.show_message("Reloaded {} (changed on disk).".format(os.path.basename(path)))
                continue
            self._focus_editor(editor)
            reply = QtWidgets.QMessageBox.question(
                self, "File Changed on Disk",
                "\"{}\" was changed outside the editor.\n\nReload it and lose your unsaved changes?".format(
                    os.path.basename(path)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if reply == QtWidgets.QMessageBox.Yes:
                self._reload_editor(editor)
            else:
                editor.disk_mtime = mtime  # do not ask again for this change

    def _reload_editor(self, editor):
        try:
            text, encoding, newline = fileio.read_text_file(editor.file_path)
        except Exception as exc:
            self.show_message("Could not reload: {}".format(exc))
            return
        position = editor.textCursor().position()
        scroll = editor.verticalScrollBar().value()
        bookmarks = editor.bookmark_map()
        self._load_content(editor, editor.file_path, text)
        editor.encoding, editor.newline = encoding, newline
        editor.disk_mtime = fileio.file_mtime(editor.file_path)
        editor.set_bookmarks(bookmarks)
        cursor = editor.textCursor()
        cursor.setPosition(min(position, editor.document().characterCount() - 1))
        editor.setTextCursor(cursor)
        editor.verticalScrollBar().setValue(scroll)
        editor.is_modified = False

    # ------------------------------------------------------------- #
    #  Saving                                                       #
    # ------------------------------------------------------------- #
    def save(self):
        editor = self.current_editor()
        return self.save_editor(editor) if editor else False

    def save_as(self):
        editor = self.current_editor()
        return self.save_editor(editor, save_as=True) if editor else False

    def _editor_content(self, editor, rich, path=None):
        if rich:
            content = fileio.strip_body_font(editor.toHtml())
            if path:
                base_dir = None
                if editor.file_path:
                    base_dir = os.path.dirname(editor.file_path)
                content = fileio.relativize_images(content, path, base_dir)
            return content
        return editor.toPlainText()

    def _ask_save_path(self, editor):
        rich_filter = "Rich Note (*.tnote)"
        text_filter = "Text Files (*.txt)"
        all_filter = "All Files (*)"
        keeps_formatting = editor.rich and (fileio.is_rich_path(editor.file_path) or
                                            document_has_formatting(editor.document()))
        default_filter = rich_filter if keeps_formatting else text_filter
        if editor.file_path and not editor.is_scratchpad:
            start = editor.file_path
        else:
            name = re.sub(r'[\\/:*?"<>|]', "_", self._display_name(editor))
            start = os.path.join(self.file_browser.root_path, name)
        path, selected = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save As", start, ";;".join([rich_filter, text_filter, all_filter]), default_filter)
        if not path:
            return None
        if not os.path.splitext(path)[1]:
            if selected == rich_filter:
                path += ".tnote"
            elif selected == text_filter:
                path += ".txt"
        return os.path.abspath(path)

    def _confirm_plain_save(self, editor, path):
        if editor.rich and not fileio.is_rich_path(path) and document_has_formatting(editor.document()):
            reply = QtWidgets.QMessageBox.question(
                self, "Formatting Will Be Lost",
                "\"{}\" is saved as plain text, so bold, colors, sizes, images and alignment will be "
                "removed.\n\nSave as a rich note (.tnote) to keep formatting.\n\nSave as plain text anyway?".format(
                    os.path.basename(path)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
            return reply == QtWidgets.QMessageBox.Yes
        return True

    def _save_binding(self, editor):
        if getattr(editor.binding, "confirm_write", False):
            reply = QtWidgets.QMessageBox.question(
                self, "Rebuild Group",
                "Replace the contents of {} with this text?\n\n(You can undo it in Nuke.)".format(
                    editor.binding.node_name),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
            if reply != QtWidgets.QMessageBox.Yes:
                return False
        try:
            editor.binding.write(editor.toPlainText())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Error", "Could not write to Nuke:\n{}".format(exc))
            return False
        editor.is_modified = False
        self.show_message("Saved to {}".format(editor.binding.title))
        return True

    def save_editor(self, editor, save_as=False, quiet=False):
        """Save a tab. Returns True on success, False if cancelled or failed."""
        if editor.binding is not None and not save_as:
            return self._save_binding(editor)
        if editor.follower is not None and not save_as:
            return True
        if editor.is_scratchpad and not save_as:
            return self._save_scratchpad()

        path = editor.file_path
        new_path = save_as or not path or editor.is_scratchpad or editor.follower is not None
        if new_path:
            path = self._ask_save_path(editor)
            if not path:
                return False
            other = self._editor_for_path(path)
            if other is not None and other is not editor:
                QtWidgets.QMessageBox.warning(self, "File Is Open",
                                              "That file is open in another tab. Close it first:\n{}".format(path))
                return False
            if not self._confirm_plain_save(editor, path):
                return False

        lock = locks.foreign_lock(path) if self._lock_mode() != "off" else None
        if lock:
            if quiet:
                return False  # never autosave over someone else's edits
            reply = QtWidgets.QMessageBox.question(
                self, "Someone Is Editing This",
                "{} is being edited by {}.\n\nSave anyway and possibly overwrite their changes?".format(
                    os.path.basename(path), locks.describe(lock)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
            if reply != QtWidgets.QMessageBox.Yes:
                return False

        target_rich = fileio.is_rich_path(path)
        same_file = bool(editor.file_path) and fileio.norm_path(editor.file_path) == fileio.norm_path(path)
        encoding = editor.encoding if same_file else "utf-8"
        content = self._editor_content(editor, target_rich, path)

        # Keep the previous version in local history.
        if os.path.isfile(path):
            try:
                previous = fileio.read_text_file(path)[0]
                if previous != content:
                    history.add_snapshot(path, previous, autosave=quiet)
            except Exception:
                pass

        try:
            used = fileio.write_text_file(path, content, encoding, editor.newline)
        except Exception as exc:
            if quiet:
                self.show_message("Autosave failed for {}: {}".format(os.path.basename(path), exc))
            else:
                QtWidgets.QMessageBox.warning(self, "Error", "Could not save file:\n{}\n\n{}".format(path, exc))
            return False
        if used != encoding and not quiet:
            self.show_message("Saved as UTF-8 (text did not fit the original {} encoding).".format(encoding))

        if editor.is_scratchpad or editor.follower is not None:
            # "Save As" of the scratchpad or a log saves a copy.
            self.add_recent_file(path)
            self.show_message("Saved a copy as {}".format(path))
            return True

        editor.encoding = used
        if new_path:
            if editor.binding is not None:
                editor.binding = None  # the tab becomes a normal file
            was_rich = editor.rich
            editor.file_path = path
            editor.custom_title = None
            self._apply_editor_mode(editor, path)
            if was_rich and not editor.rich:
                bookmarks = editor.bookmark_map()
                position = editor.textCursor().position()
                editor.setPlainText(editor.toPlainText())
                editor.set_bookmarks(bookmarks)
                cursor = editor.textCursor()
                cursor.setPosition(min(position, editor.document().characterCount() - 1))
                editor.setTextCursor(cursor)
            if target_rich:
                editor.document().setBaseUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(path) + "/"))
            self.add_recent_file(path)
        editor.disk_mtime = fileio.file_mtime(path)
        self._watch(path)
        editor.is_modified = False
        self._save_bookmarks(editor)
        self._recovery_dirty = True
        self.update_tab_titles()
        try:
            dailylog.record_touched(path)
        except Exception:
            pass
        if editor.language == "python":
            if self.settings.get_bool("lint_on_save"):
                self._lint_editor(editor)
            self._check_python(editor, quiet=True)
        return True

    # ----- Scratchpad ------------------------------------------ #
    def _scratchpad_path(self):
        return os.path.join(fileio.data_dir(), "scratchpad.tnote")

    def _create_scratchpad(self):
        editor = self.create_tab(activate=False)
        editor.is_scratchpad = True
        path = self._scratchpad_path()
        editor.document().setBaseUrl(QtCore.QUrl.fromLocalFile(fileio.data_dir() + "/"))
        if os.path.isfile(path):
            try:
                editor.setHtml(fileio.strip_body_font(fileio.read_text_file(path)[0]))
            except Exception:
                pass
        editor.is_modified = False
        self.tab_widget.tabBar().moveTab(self.tab_widget.indexOf(editor), 0)
        self.update_tab_titles()

    def _save_scratchpad(self):
        for editor in self.editors():
            if editor.is_scratchpad and editor.is_modified:
                path = self._scratchpad_path()
                try:
                    fileio.write_text_file(path, self._editor_content(editor, True, path))
                    editor.is_modified = False
                except Exception as exc:
                    self.show_message("Could not save the scratchpad: {}".format(exc))
                    return False
        return True

    # ------------------------------------------------------------- #
    #  Autosave, crash recovery and sessions                        #
    # ------------------------------------------------------------- #
    def _recovery_path(self):
        return os.path.join(fileio.data_dir("recovery"), "{}-{}.json".format(os.getpid(), self.instance_id))

    def handle_autosave(self):
        if not self._recovery_dirty:
            return
        now = QtCore.QDateTime.currentDateTime()
        idle = self.last_edit_time.msecsTo(now) >= self.AUTOSAVE_IDLE_MS
        overdue = self.last_autosave_time.msecsTo(now) >= self.AUTOSAVE_INTERVAL_MS
        if idle or overdue:
            self._recovery_dirty = False
            self.last_autosave_time = now
            self.autosave_all()

    def autosave_all(self):
        self._save_scratchpad()
        if self.autosave_to_file:
            for editor in self.editors():
                if (editor.file_path and editor.is_modified and editor.binding is None
                        and editor.follower is None and not editor.is_scratchpad):
                    self.save_editor(editor, quiet=True)
        self.save_session_backup()

    def save_session_backup(self):
        """Store unsaved work so it can be restored after a crash."""
        tabs = []
        unsaved = 0
        for editor in self.editors():
            if editor.is_scratchpad or editor.follower is not None or editor is self._follow_editor:
                continue
            entry = {
                "title": self._display_name(editor) if editor.binding is not None else editor.custom_title,
                "file_path": None if editor.binding is not None else editor.file_path,
                "rich": editor.rich,
                "language": editor.language,
                "modified": editor.is_modified,
                "encoding": editor.encoding,
                "newline": editor.newline,
            }
            if editor.is_modified:
                entry["content"] = self._editor_content(editor, editor.rich)
                unsaved += 1
            elif not editor.file_path or editor.binding is not None:
                continue
            tabs.append(entry)
        path = self._recovery_path()
        try:
            if not unsaved:
                if os.path.exists(path):
                    os.remove(path)
                return
            data = {"pid": os.getpid(), "instance": self.instance_id, "tabs": tabs,
                    "current_index": 0}
            fileio.write_text_file(path, json.dumps(data))
        except Exception as exc:
            self.show_message("Could not write crash-recovery file: {}".format(exc))

    def _orphan_recovery_files(self):
        """Recovery files whose editor is no longer running."""
        folder = fileio.data_dir("recovery")
        live_ids = set(w.instance_id for w in live_windows())
        found = []
        legacy = os.path.join(fileio.data_dir(), "recovery.json")  # version 1
        if os.path.isfile(legacy):
            found.append(legacy)
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            pid, _sep, rest = name[:-5].partition("-")
            if fileio.pid_alive(pid) and (int(pid) != os.getpid() or rest in live_ids):
                continue
            found.append(os.path.join(folder, name))
        return found

    def restore_session_if_any(self):
        """Offer to restore unsaved work from editors that did not close
        cleanly. Returns True if something was restored."""
        files = self._orphan_recovery_files()
        if not files:
            return False
        tabs = []
        for path in files:
            try:
                with io.open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                tabs.extend(t for t in data.get("tabs", []) if isinstance(t, dict))
            except Exception:
                continue
        unsaved = [t for t in tabs if t.get("modified")]
        if not unsaved:
            for path in files:
                try:
                    os.remove(path)
                except OSError:
                    pass
            return False

        names = [t.get("title") or (os.path.basename(t["file_path"]) if t.get("file_path") else "Untitled")
                 for t in unsaved[:8]]
        backup_dir = fileio.data_dir("recovery", "declined")
        reply = QtWidgets.QMessageBox.question(
            self, "Restore Unsaved Work",
            "Unsaved work from an editor that did not close cleanly was found:\n\n  {}{}\n\nRestore it?\n\n"
            "(If you choose No, a copy is kept in {})".format(
                "\n  ".join(names), "\n  ..." if len(unsaved) > 8 else "", backup_dir),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.Yes)
        if reply != QtWidgets.QMessageBox.Yes:
            for path in files:
                try:
                    os.replace(path, os.path.join(backup_dir, os.path.basename(path)))
                except OSError:
                    pass
            return False

        for t in tabs:
            file_path = t.get("file_path")
            if not t.get("modified"):
                if file_path and os.path.isfile(file_path):
                    self.open_path(file_path)
                continue
            editor = self._take_blank_or_new_tab()
            editor.custom_title = t.get("title")
            self._apply_editor_mode(editor, file_path, language=t.get("language"))
            editor.set_rich(bool(t.get("rich", editor.rich)))
            content = t.get("content") or ""
            if editor.rich:
                if file_path:
                    editor.document().setBaseUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(file_path) + "/"))
                editor.setHtml(content)
            else:
                editor.setPlainText(content)
            editor.file_path = file_path
            editor.encoding = t.get("encoding") or "utf-8"
            editor.newline = t.get("newline") or "\n"
            editor.disk_mtime = fileio.file_mtime(file_path) if file_path else None
            editor.is_modified = True
            if file_path:
                self._load_bookmarks(editor)
                self._watch(file_path)
        for path in files:
            try:
                os.remove(path)
            except OSError:
                pass
        self.update_tab_titles()
        self._recovery_dirty = True
        return True

    def _store_session_tabs(self):
        entries = []
        for editor in self.editors():
            if editor.is_scratchpad or editor.binding is not None or not editor.file_path:
                continue
            if editor.follower is not None:
                entries.append({"log": editor.file_path})
            else:
                entries.append({"path": editor.file_path, "cursor": editor.textCursor().position()})
        self.settings.set("session/tabs", json.dumps(entries))
        current = self.current_editor()
        self.settings.set("session/current_path", current.file_path if current is not None and current.file_path else "")

    def _reopen_session_tabs(self):
        try:
            entries = json.loads(self.settings.get_str("session/tabs", "[]"))
        except ValueError:
            return
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if entry.get("log") and os.path.isfile(entry["log"]):
                self.open_log(entry["log"])
            elif entry.get("path") and os.path.isfile(entry["path"]):
                editor = self.open_path(entry["path"])
                if editor is not None:
                    cursor = editor.textCursor()
                    cursor.setPosition(min(int(entry.get("cursor", 0)), editor.document().characterCount() - 1))
                    editor.setTextCursor(cursor)
        current_path = self.settings.get_str("session/current_path", "")
        editor = self._editor_for_path(current_path) if current_path else None
        if editor is not None:
            self._focus_editor(editor)

    def _on_app_quit(self):
        """Nuke is quitting: keep unsaved work for next time (no prompts)."""
        try:
            self._save_scratchpad()
            self._flush_follow_note()
            self.save_session_backup()
            self._store_session_tabs()
            self.settings.sync()
            for editor in self.editors():
                self._release_lock(editor)
        except RuntimeError:
            pass

    # ------------------------------------------------------------- #
    #  Running Python                                               #
    # ------------------------------------------------------------- #
    def run_selection(self):
        editor = self.current_editor()
        if editor is None:
            return
        cursor = editor.textCursor()
        if cursor.hasSelection():
            start_line = editor.document().findBlock(cursor.selectionStart()).blockNumber()
            code = cursor.selectedText().replace(" ", "\n").replace(" ", "\n")
            label = "selection"
        else:
            start_line = cursor.blockNumber()
            code = cursor.block().text()
            label = "line {}".format(start_line + 1)
            if not code.strip():
                return
            code = code.strip()
        self._run("\n" * start_line + code, editor, label)

    def run_file(self):
        editor = self.current_editor()
        if editor is not None:
            self._run(editor.toPlainText(), editor, self._display_name(editor))

    def _run(self, code, editor, label):
        self.bottom_tabs.setVisible(True)
        self.bottom_tabs.setCurrentWidget(self.output_panel)
        tag = "<editor:{}>".format(editor.run_id) if editor is not None else "<snippet>"
        self.output_panel.append(">>> Running {}".format(label), "info")
        started = time.time()
        output, error, error_line = nuke_bridge.run_python(code, tag)
        self.output_panel.append(output, "out")
        if error:
            self.output_panel.append(error, "err")
        else:
            self.output_panel.append("Done in {:.2f}s".format(time.time() - started), "info")
        if editor is not None:
            editor.set_error_line(error_line - 1 if error and error_line else None)
            editor.setFocus()

    def _on_output_location(self, tag, line):
        editor = self._run_ids.get(tag)
        if self._has_editor(editor):
            self._focus_editor(editor)
            editor.goto_line(line - 1)
            editor.setFocus()

    def _check_python(self, editor, quiet=False):
        try:
            compile(editor.toPlainText(), editor.file_path or "<editor>", "exec")
        except SyntaxError as exc:
            editor.set_error_line((exc.lineno or 1) - 1)
            self.show_message("Syntax error line {}: {}".format(exc.lineno, exc.msg), 10000)
            return False
        editor.set_error_line(None)
        if not quiet:
            self.show_message("No syntax errors.")
        return True

    def check_syntax(self):
        editor = self.current_editor()
        if editor is None:
            return
        if editor.language != "python":
            self.show_message("Problem checking is for Python files.")
            return
        if self._check_python(editor, quiet=True):
            self._lint_editor(editor, report=True)

    def show_expression_tester(self):
        if self._expression_tester is None:
            self._expression_tester = ExpressionTester(self)
        self._expression_tester.show()
        self._expression_tester.raise_()
        self._expression_tester.input.setFocus()

    # ------------------------------------------------------------- #
    #  Nuke: notes, knobs, viewer, recipes, diff                    #
    # ------------------------------------------------------------- #
    def _need_nuke(self):
        if not nuke_bridge.available():
            self.show_message("This command works inside Nuke.")
            return False
        return True

    def open_binding(self, binding):
        for editor in self.editors():
            if editor.binding is not None and editor.binding.same_target(binding):
                self._focus_editor(editor)
                return editor
        try:
            text = binding.read()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Error", str(exc))
            return None
        editor = self.create_tab()
        editor.binding = binding
        self._apply_editor_mode(editor, None, language=binding.language)
        editor.setPlainText(text)
        editor.is_modified = False
        self.update_tab_titles()
        self.on_tab_changed()
        return editor

    def edit_script_note(self):
        if self._need_nuke():
            self.open_binding(NoteBinding(script=True))

    def edit_node_note(self):
        if not self._need_nuke():
            return
        name = nuke_bridge.selected_node_name()
        if name is None:
            self.show_message("Select a single node first.")
            return
        self.open_binding(NoteBinding(name))

    def edit_selected_knob(self):
        if not self._need_nuke():
            return
        name = nuke_bridge.selected_node_name()
        if name is None:
            self.show_message("Select a single node first.")
            return
        knobs = nuke_bridge.editable_knobs(name)
        if not knobs:
            self.show_message("{} has no text knobs or expressions to edit.".format(name))
            return
        dialog = KnobPickerDialog(name, knobs, self)
        choice = dialog.selected() if qt_exec(dialog) == QtWidgets.QDialog.Accepted else None
        if choice:
            knob, cls, kind = choice
            self.open_binding(KnobBinding(name, knob, kind, cls))

    def set_follow_notes(self, enabled):
        if enabled and not nuke_bridge.available():
            self.show_message("Following node notes works inside Nuke.")
            self.actions["follow_notes"].qaction.setChecked(False)
            return
        if enabled:
            editor = self.create_tab()
            editor.set_rich(False)
            editor.setPlaceholderText("Select a single node to see and edit its note.")
            self._follow_editor = editor
            self._follow_node = None
            self.update_tab_titles()
            self._poll_note_follow()
            self._follow_timer.start()
        else:
            self._follow_timer.stop()
            self._flush_follow_note()
            editor = self._follow_editor
            self._follow_editor = None
            if self._has_editor(editor):
                self._dispose_editor(editor)
                if not self.editors():
                    self.create_tab()

    def _flush_follow_note(self):
        editor = self._follow_editor
        if editor is not None and self._follow_node and editor.is_modified:
            try:
                nuke_bridge.set_note(self._follow_node, editor.toPlainText())
            except Exception as exc:
                self.show_message("Could not save note: {}".format(exc))
            editor.is_modified = False

    def _poll_note_follow(self):
        editor = self._follow_editor
        if editor is None:
            return
        name = nuke_bridge.selected_node_name()
        if name == self._follow_node:
            return
        self._flush_follow_note()
        self._follow_node = name
        if name is None:
            editor.setPlainText("")
            editor.setReadOnly(True)
        else:
            editor.setReadOnly(False)
            editor.setPlainText(nuke_bridge.get_note(name) or "")
        editor.is_modified = False
        self.update_tab_titles()

    def open_shot_notes(self):
        script = nuke_bridge.script_path()
        if not script:
            self.show_message("Save the Nuke script first; shot notes live next to it.")
            return
        path = textops.shot_notes_path(script)
        if not os.path.isfile(path):
            content = "<h2>{shot}</h2><p>- [ ] </p>"
            for name, template_path in templates.list_templates():
                if name == "Shot Notes":
                    try:
                        content = fileio.read_text_file(template_path)[0]
                    except Exception:
                        pass
            context = templates.build_context(**nuke_bridge.template_context())
            try:
                fileio.write_text_file(path, templates.fill_tokens(content, context))
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "Error", "Could not create shot notes:\n{}".format(exc))
                return
        self.open_path(path)

    def on_script_loaded(self):
        """Called by the Nuke onScriptLoad callback."""
        script = nuke_bridge.script_path()
        if script and self.settings.get_bool("auto_open_shot_notes"):
            path = textops.shot_notes_path(script)
            if os.path.isfile(path):
                self.open_path(path)
        if self.settings.get_bool("plates/check_on_load"):
            self._plate_check.start()
        if self.settings.get_bool("open_script_note_on_load"):
            note = nuke_bridge.get_script_note()
            if note and note.strip():
                self.open_binding(NoteBinding(script=True))
        self._outline_timer.start()

    def capture_viewer(self):
        if not self._need_nuke():
            return
        editor = self._rich_editor()
        if editor is None:
            self.show_message("Captures go into rich notes: the Scratchpad, an Untitled tab or a .tnote.")
            return
        path = os.path.join(fileio.data_dir("images"), "capture_{}.jpg".format(time.strftime("%Y%m%d_%H%M%S")))
        ok, message = nuke_bridge.capture_viewer(path)
        if ok:
            frame = nuke_bridge.current_frame()
            if self.settings.get_bool("annotate_captures"):
                path = annotate(path, self)
            editor.insert_image(path)
            editor.textCursor().insertText("f{}  {}\n".format(frame, os.path.basename(nuke_bridge.script_path() or "")))
        self.show_message(message)

    def save_recipe(self):
        self.show_side(self.recipes_panel)
        self.recipes_panel.save_selected()

    def compare_with_previous_version(self):
        editor = self.current_editor()
        path = editor.file_path if editor and editor.file_path and editor.language == "nuke" else nuke_bridge.script_path()
        if not path:
            self.show_message("Open or load a versioned .nk script first.")
            return
        previous = textops.previous_version_path(path)
        if not previous:
            self.show_message("No earlier version found next to {}.".format(os.path.basename(path)))
            return
        current = self._editor_for_path(path)
        new_text = current.toPlainText() if current is not None and current.is_modified else None
        qt_exec(DiffDialog(previous, path, new_text, self))

    def compare_with_saved(self):
        editor = self.current_editor()
        if editor is None or not editor.file_path or editor.binding is not None:
            self.show_message("This tab has no saved file.")
            return
        text = fileio.html_to_text(editor.toHtml()) if editor.rich else editor.toPlainText()
        dialog = DiffDialog(editor.file_path, "", text, self)
        dialog.new_edit.setText("(current editor text)")
        qt_exec(dialog)

    def compare_files(self):
        editor = self.current_editor()
        start = editor.file_path if editor and editor.file_path else ""
        qt_exec(DiffDialog(start, "", None, self))

    def open_current_script(self):
        path = nuke_bridge.script_path()
        if not path:
            self.show_message("The current script has not been saved yet.")
            return
        self.open_path(path)
        self.show_message("Text edits to the open script apply only after you reopen it in Nuke.", 10000)

    def show_time_summary(self):
        qt_exec(TimeSummaryDialog(time_tracker.tracker(), self))

    def show_history(self):
        editor = self.current_editor()
        if editor is None or not editor.file_path or editor.binding is not None:
            self.show_message("Local history is kept for saved files.")
            return
        current = self._editor_content(editor, editor.rich)
        dialog = HistoryDialog(editor.file_path, current, self)
        if qt_exec(dialog) == QtWidgets.QDialog.Accepted and dialog.restored_text is not None:
            if editor.rich:
                editor.setHtml(dialog.restored_text)
            else:
                cursor = QtGui.QTextCursor(editor.document())
                cursor.select(QtGui.QTextCursor.Document)
                cursor.insertText(dialog.restored_text)  # undoable
            editor.is_modified = True
            self.show_message("Restored an earlier version. Save to keep it.")

    # ------------------------------------------------------------- #
    #  Templates and export                                         #
    # ------------------------------------------------------------- #
    def _fill_template_menu(self, menu, handler):
        templates.ensure_default_templates()
        menu.clear()
        for name, path in templates.list_templates():
            action = menu.addAction(name)
            action.triggered.connect(lambda _c=False, p=path: handler(p))
        menu.addSeparator()
        folder = menu.addAction("Open Templates Folder")
        folder.triggered.connect(lambda _c=False: self._open_folder(templates.templates_dir()))

    def _template_text(self, path):
        text = fileio.read_text_file(path)[0]
        return templates.fill_tokens(text, templates.build_context(**nuke_bridge.template_context()))

    def new_from_template(self, path):
        try:
            text = self._template_text(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Error", str(exc))
            return
        editor = self.create_tab(os.path.splitext(os.path.basename(path))[0])
        if fileio.is_rich_path(path):
            editor.setHtml(text)
        else:
            editor.setPlainText(text)
        editor.is_modified = True
        editor.moveCursor(QtGui.QTextCursor.End)

    def insert_template(self, path):
        editor = self.current_editor()
        if editor is None or editor.isReadOnly():
            return
        try:
            text = self._template_text(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Error", str(exc))
            return
        if fileio.is_rich_path(path):
            if editor.rich:
                editor.textCursor().insertHtml(text)
            else:
                editor.textCursor().insertText(fileio.html_to_text(text))
        else:
            editor.textCursor().insertText(text)

    def _export_document(self, editor):
        """A QTextDocument suitable for export (Markdown rendered if possible)."""
        if editor.rich:
            return editor.document(), False
        doc = QtGui.QTextDocument()
        font = QtGui.QFont(self.font_family)
        font.setPointSize(10)
        doc.setDefaultFont(font)
        text = editor.toPlainText()
        if editor.language == "markdown" and hasattr(doc, "setMarkdown"):
            doc.setMarkdown(text)
        else:
            doc.setPlainText(text)
        return doc, True

    def _export_path(self, editor, extension, title):
        base = os.path.splitext(editor.file_path)[0] if editor.file_path else os.path.join(
            self.file_browser.root_path, re.sub(r'[\\/:*?"<>|]', "_", self._display_name(editor)))
        path, _f = QtWidgets.QFileDialog.getSaveFileName(self, title, base + extension,
                                                         "*{}".format(extension))
        if path and not path.lower().endswith(extension):
            path += extension
        return path

    def export_html(self):
        editor = self.current_editor()
        if editor is None:
            return
        path = self._export_path(editor, ".html", "Export as HTML")
        if not path:
            return
        doc, _temporary = self._export_document(editor)
        if editor.rich or editor.language == "markdown":
            content = doc.toHtml()
        else:
            content = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>{0}</title></head>"
                       "<body><pre style='font-family: Consolas, monospace;'>{1}</pre></body></html>").format(
                html.escape(self._display_name(editor)), html.escape(editor.toPlainText()))
        try:
            fileio.write_text_file(path, content)
            self.show_message("Exported {}".format(path))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Error", str(exc))

    def export_pdf(self):
        editor = self.current_editor()
        if editor is None:
            return
        path = self._export_path(editor, ".pdf", "Export as PDF")
        if not path:
            return
        doc, _temporary = self._export_document(editor)
        writer = QtGui.QPdfWriter(path)
        try:
            writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
        except Exception:
            pass
        writer.setTitle(self._display_name(editor))
        printer = getattr(doc, "print_", None) or getattr(doc, "print")
        try:
            printer(writer)
            self.show_message("Exported {}".format(path))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Error", "PDF export failed:\n{}".format(exc))

    # ------------------------------------------------------------- #
    #  Palette, preferences, help                                   #
    # ------------------------------------------------------------- #
    def show_command_palette(self):
        entries = []
        for action in self.actions.values():
            if action.checkable:
                callback = (lambda a=action: a.qaction.toggle() if a.qaction else None)
            else:
                callback = action.callback
            entries.append(("{} / {}".format(action.menu, action.label), action.shortcut, callback))
        for path, callback in nuke_bridge.menu_commands():
            entries.append((path, "", callback))
        CommandPalette(entries, self).show_over(self)

    def show_preferences(self):
        shortcut_rows = [(a.id, "{} / {}".format(a.menu, a.label), getattr(a, "default_shortcut", a.shortcut))
                         for a in self.actions.values() if not a.checkable]
        dialog = PreferencesDialog(self.settings, shortcut_rows, self)
        if qt_exec(dialog) != QtWidgets.QDialog.Accepted:
            return
        themes.set_current(self.settings.get_str("theme", themes.DEFAULT))
        self.apply_theme()
        self._rebuild_shortcuts()
        self.autosave_to_file = self.settings.get_bool("autosave_to_file")
        qaction = self.actions["autosave_toggle"].qaction
        if qaction is not None:
            with SignalBlocker(qaction):
                qaction.setChecked(self.autosave_to_file)
        self.snippets_panel.reload()
        notes = self.settings.get_str("notes_folder")
        if notes:
            self.todo_panel.folder_edit.setText(notes)
        if self.settings.get_bool("mark_nodes_with_notes"):
            nuke_bridge.install_note_marker()

    def show_about(self):
        from . import __version__
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("About")
        msg.setIcon(QtWidgets.QMessageBox.Information)
        msg.setTextFormat(QtCore.Qt.RichText)
        msg.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        msg.setText("Nuke Text Editor {}<br>Pedro Gartner<br>"
                    "<a href='https://github.com/PedroGartner' style='color: #ffffff;'>GitHub</a>".format(__version__))
        qt_exec(msg)

    def show_shortcuts(self):
        rows = [(a.shortcut, "{} / {}".format(a.menu, a.label)) for a in self.actions.values() if a.shortcut]
        rows += [
            ("Tab / Shift+Tab", "Indent / unindent (Tab also expands snippets)"),
            ("Ctrl+Click", "Follow a link: URL, file path, f1043 frame, [[Node]]"),
            ("Alt+Click", "Add a cursor (Esc to clear)"),
            ("Click on [ ]", "Tick / untick a task"),
            ("Ctrl+Click in gutter", "Toggle bookmark"),
            ("Ctrl+Wheel", "Zoom"),
        ]
        body = "".join("<tr><td style='padding-right:16px'><b>{}</b></td><td>{}</td></tr>".format(
            html.escape(k), html.escape(v)) for k, v in rows)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        view = QtWidgets.QTextBrowser()
        view.setHtml("<table>{}</table>".format(body))
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(view)
        dialog.resize(560, 560)
        qt_exec(dialog)

    # ------------------------------------------------------------- #
    #  Shortcuts, theme and workspaces                              #
    # ------------------------------------------------------------- #
    def _shortcut_overrides(self):
        try:
            data = json.loads(self.settings.get_str("shortcuts", "{}"))
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _rebuild_shortcuts(self):
        """Apply user shortcut overrides and rebuild the lookup table."""
        overrides = self._shortcut_overrides()
        self._shortcut_table = []
        for action in self.actions.values():
            if action.checkable:
                continue
            default = getattr(action, "default_shortcut", action.shortcut)
            action.shortcut = overrides.get(action.id, default)
            texts = ((action.shortcut,) if action.shortcut else ()) + (
                tuple(action.aliases) if action.shortcut == default else ())
            for text in texts:
                parsed = parse_shortcut(text)
                if parsed is not None:
                    self._shortcut_table.append(parsed + (action.callback,))
            if action.qaction is not None:
                action.qaction.setText(action.label + ("\t" + action.shortcut if action.shortcut else ""))

    def apply_theme(self):
        """Re-style everything with the current theme."""
        self.setStyleSheet(themes.window_style())
        for button in self.findChildren(HoverButton):
            button.setStyleSheet(themes.button_style())
        self.output_panel.apply_theme()
        self.file_browser.apply_theme()
        self.message_label.setStyleSheet("color: {}; padding: 4px; font-size: 10px;".format(themes.color("message")))
        for editor in self.editors():
            editor.apply_theme()
            self._update_extra_selections(editor)
        self.update_tab_titles()

    def _workspaces(self):
        try:
            data = json.loads(self.settings.get_str("workspaces", "{}"))
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _session_entries(self):
        entries = []
        for editor in self.editors():
            if editor.is_scratchpad or editor.binding is not None or not editor.file_path:
                continue
            if editor.follower is not None:
                entries.append({"log": editor.file_path})
            else:
                entries.append({"path": editor.file_path, "cursor": editor.textCursor().position(),
                                "split": self._tabs_of(editor) is self.split_tabs})
        return entries

    def save_workspace(self):
        entries = self._session_entries()
        if not entries:
            self.show_message("Open some saved files first; a workspace remembers file tabs.")
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Save Workspace", "Workspace name:")
        name = name.strip() if ok else ""
        if not name:
            return
        data = self._workspaces()
        data[name] = {"tabs": entries}
        self.settings.set("workspaces", json.dumps(data))
        self.show_message("Workspace '{}' saved ({} tabs).".format(name, len(entries)))

    def _fill_workspace_menu(self):
        self.workspace_menu.clear()
        data = self._workspaces()
        if not data:
            self.workspace_menu.addAction("(No Workspaces)").setEnabled(False)
            return
        for name in sorted(data, key=str.lower):
            action = self.workspace_menu.addAction("{}  ({} tabs)".format(name, len(data[name].get("tabs", []))))
            action.triggered.connect(lambda _c=False, n=name: self.open_workspace(n))

    def open_workspace(self, name):
        workspace = self._workspaces().get(name)
        if not workspace:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Open Workspace", "Close the current tabs first?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.No)
        if reply == QtWidgets.QMessageBox.Cancel:
            return
        if reply == QtWidgets.QMessageBox.Yes:
            before = len(self.editors())
            self.close_all_tabs()
            if len(self.editors()) >= before and before > 1:
                return  # the user cancelled a save prompt
        missing = 0
        for entry in workspace.get("tabs", []):
            if entry.get("log"):
                if os.path.isfile(entry["log"]):
                    self.open_log(entry["log"])
                continue
            path = entry.get("path")
            if not path or not os.path.isfile(path):
                missing += 1
                continue
            editor = self.open_path(path)
            if editor is None:
                continue
            if entry.get("split"):
                if not self.split_tabs.isVisible():
                    self.split_tabs.setVisible(True)
                self._move_editor(editor, self.split_tabs)
        self.show_message("Opened workspace '{}'{}.".format(name, " ({} files missing)".format(missing) if missing else ""))

    def delete_workspace(self):
        data = self._workspaces()
        if not data:
            self.show_message("There are no saved workspaces.")
            return
        name, ok = QtWidgets.QInputDialog.getItem(self, "Delete Workspace", "Workspace:",
                                                  sorted(data, key=str.lower), 0, False)
        if ok and name in data:
            del data[name]
            self.settings.set("workspaces", json.dumps(data))
            self.show_message("Workspace '{}' deleted.".format(name))

    # ------------------------------------------------------------- #
    #  Lint, console, tasks, day report                             #
    # ------------------------------------------------------------- #
    def _lint_editor(self, editor, report=False):
        if editor.language != "python":
            editor.set_lint([])
            return []
        text = editor.toPlainText()
        if len(text) > 300000:
            return []
        import __main__
        messages = lint.lint(text, extra_names=list(__main__.__dict__.keys()))
        editor.set_lint(messages)
        if report:
            if messages:
                first = messages[0]
                self.show_message("{} problem(s). Line {}: {}".format(len(messages), first.line, first.text), 10000)
            else:
                self.show_message("No problems found.")
        return messages

    def run_console_entry(self, code):
        self.bottom_tabs.setVisible(True)
        self.output_panel.append(">>> " + code.replace("\n", "\n... "), "info")
        output, error = nuke_bridge.run_console(code)
        self.output_panel.append(output, "out")
        if error:
            self.output_panel.append(error, "err")

    def focus_console(self):
        self.bottom_tabs.setVisible(True)
        self.bottom_tabs.setCurrentWidget(self.output_panel)
        self.output_panel.console.setFocus()

    def on_task_toggled(self, text, checked):
        editor = self.sender()
        path = editor.file_path if isinstance(editor, AdvancedCodeEditor) and editor.file_path else ""
        if isinstance(editor, AdvancedCodeEditor) and editor.is_scratchpad:
            path = "Scratchpad"
        try:
            if checked:
                dailylog.record_done(text, path)
            else:
                dailylog.record_undone(text, path)
        except Exception:
            pass

    def end_of_day_report(self):
        tracker = time_tracker.tracker()
        if tracker is not None:
            tracker.flush()
        import getpass
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
        html_text = dailylog.report_html(dailylog.today(), user=user)
        editor = self.create_tab("Day report {}".format(dailylog.today()))
        editor.setHtml(html_text)
        editor.is_modified = True
        self.show_message("Save it as a .tnote to keep it, or export it as PDF / HTML.")

    # ------------------------------------------------------------- #
    #  Shared-note markers                                          #
    # ------------------------------------------------------------- #
    def _lock_mode(self):
        return self.settings.get_str("lock_markers", "network")

    def _lockable(self, editor):
        return (editor.file_path and editor.binding is None and editor.follower is None
                and not editor.is_scratchpad and os.path.isfile(editor.file_path)
                and locks.should_mark(editor.file_path, self._lock_mode()))

    def _take_lock(self, editor):
        if getattr(editor, "_lock_taken", False) or not self._lockable(editor):
            return
        if locks.write_lock(editor.file_path):
            editor._lock_taken = True

    def _release_lock(self, editor):
        if getattr(editor, "_lock_taken", False):
            locks.remove_lock(editor.file_path)
            editor._lock_taken = False

    def _refresh_locks(self):
        for editor in self.editors():
            if getattr(editor, "_lock_taken", False):
                locks.write_lock(editor.file_path)

    def _warn_if_locked(self, editor):
        if not editor.file_path or self._lock_mode() == "off":
            return True
        lock = locks.foreign_lock(editor.file_path)
        if lock:
            QtWidgets.QMessageBox.information(
                self, "Someone Is Editing This",
                "{}\n\nis being edited by {}.\nYour saves could overwrite their changes.".format(
                    os.path.basename(editor.file_path), locks.describe(lock)))
            return False
        return True

    # ------------------------------------------------------------- #
    #  Nuke: health, search, callbacks, groups, labels              #
    # ------------------------------------------------------------- #
    def _select_node(self, name):
        if nuke_bridge.select_node(name):
            self.show_message("Selected {}".format(name))
        else:
            self.show_message("Node {} not found.".format(name))

    def show_health(self):
        self.show_side(self.health_panel)
        editor = self.current_editor()
        if nuke_bridge.available():
            self.health_panel.check_live()
        elif editor is not None and editor.language == "nuke":
            self.health_panel.check_tab()

    def show_node_search(self):
        self.show_side(self.node_search_panel)
        self.node_search_panel.focus_query()

    def show_callbacks(self):
        self.show_side(self.callbacks_panel)
        self.callbacks_panel.refresh()

    def edit_selected_group(self):
        if not self._need_nuke():
            return
        name = nuke_bridge.selected_node_name()
        if name is None:
            self.show_message("Select a single Group node first.")
            return
        self.open_binding(GroupBinding(name))

    def _text_for_nuke(self):
        editor = self.current_editor()
        if editor is None:
            return ""
        selected = editor.textCursor().selectedText().replace("\u2029", "\n")
        return selected or editor.toPlainText()

    def send_to_sticky(self):
        if not self._need_nuke():
            return
        text = self._text_for_nuke()
        if not text.strip():
            self.show_message("Nothing to send.")
            return
        name = nuke_bridge.create_sticky(text)
        self.show_message("Created {}".format(name))

    def send_to_label(self):
        if not self._need_nuke():
            return
        name = nuke_bridge.selected_label_node()
        if name is None:
            self.show_message("Select a StickyNote or Backdrop first.")
            return
        nuke_bridge.set_label(name, self._text_for_nuke())
        self.show_message("Updated the label of {}".format(name))

    def insert_label(self):
        if not self._need_nuke():
            return
        name = nuke_bridge.selected_label_node()
        if name is None:
            self.show_message("Select a StickyNote or Backdrop first.")
            return
        self._insert_text(nuke_bridge.get_label(name) or "")

    # ------------------------------------------------------------- #
    #  Plates, pre-render check, version up                         #
    # ------------------------------------------------------------- #
    def show_plates(self, updates=None, count=0):
        self.show_side(self.plates_panel)
        if updates is not None:
            self.plates_panel.show_results(updates, count)
        else:
            self.plates_panel.refresh()

    def _on_plates_found_on_load(self, updates, count):
        self.show_plates(updates, count)
        self.show_message("{} Read{} can be updated to a newer plate version (see Plates).".format(
            len(updates), "" if len(updates) == 1 else "s"), 12000)

    def _on_plates_updated(self, changed):
        script = nuke_bridge.script_path()
        if not script:
            return
        try:
            self.add_to_version_history(script, shot_panels.plate_history_entry(script, changed))
        except Exception as exc:
            self.show_message("Plates updated; the shot notes could not be changed: {}".format(exc))
            return
        self.show_message("Plates updated and noted in the shot notes.")

    def check_writes(self):
        if not self._need_nuke():
            return
        names = nuke_bridge.write_names(selected_only=True) or nuke_bridge.write_names(selected_only=False)
        shot_panels.show_check(names, self, shot_panels.expectations(self.settings))

    def _render_check_ok(self, write_names):
        if not self.settings.get_bool("render_check/before_batch_gartner"):
            return True
        return shot_panels.confirm_render(write_names, self, shot_panels.expectations(self.settings),
                                          "Send Anyway")

    def version_up_with_note(self):
        if not self._need_nuke():
            return
        new_path = shot_panels.version_up_with_note(self, self.add_to_version_history)
        if new_path:
            self.show_message("Saved {}".format(os.path.basename(new_path)))

    def _open_editor_for(self, path):
        target = os.path.normcase(os.path.abspath(path))
        for editor in self.editors():
            if editor.file_path and os.path.normcase(os.path.abspath(editor.file_path)) == target:
                return editor
        return None

    def add_to_version_history(self, script, entry):
        """Add a line under 'Version history' in the shot notes. An open
        notes tab is changed in place (and saved if it had no other edits)."""
        path = textops.shot_notes_path(script)
        editor = self._open_editor_for(path)
        if editor is None:
            shot_panels.add_history_to_file(script, entry)
            return
        had_changes = editor.is_modified
        shot_panels.add_history_entry(editor.document(), entry)
        if not had_changes:
            self.save_editor(editor, quiet=True)

    # ------------------------------------------------------------- #
    #  Batch Gartner                                                #
    # ------------------------------------------------------------- #
    def bg_send_selected(self):
        if not self._need_nuke():
            return
        module = bgartner.integration_module()
        if module is not None and all(hasattr(module, n) for n in ("_write_nodes", "_payload", "_send")):
            nodes = module._write_nodes(True)
            if not nodes:
                return
            if not self._render_check_ok([n.fullName() for n in nodes]):
                return
            payload = module._payload(nodes, False)
            if not payload:
                return
            for job in payload.get("jobs", []):
                note = nuke_bridge.get_note(job.get("write_node", "")) or ""
                if note.strip():
                    job["note"] = note
            module._send(payload)
            self.show_message("Sent {} job(s) to Batch Gartner.".format(len(payload.get("jobs", []))))
            return
        if module is not None and hasattr(module, "send_selected_writes"):
            module.send_selected_writes()
            return
        script, jobs = nuke_bridge.selected_write_jobs()
        if not script:
            self.show_message("Save the script first.")
            return
        if not jobs:
            self.show_message("Select at least one enabled Write node.")
            return
        if nuke_bridge.script_is_modified():
            self.show_message("Save the script first: Batch Gartner renders the saved .nk file.")
            return
        if not self._render_check_ok([job["write_node"] for job in jobs]):
            return
        if bgartner.send(bgartner.build_payload(script, jobs)):
            self.show_message("Sent {} job(s) to Batch Gartner.".format(len(jobs)))
        else:
            self.show_message("Batch Gartner is not running (its Nuke menu can start it).")

    def bg_open_app(self):
        module = bgartner.integration_module()
        if module is not None and hasattr(module, "open_batch_renderer"):
            module.open_batch_renderer()
        elif not bgartner.send({"version": 1, "source": "Nuke", "command": "show", "jobs": [{}]}):
            self.show_message("Batch Gartner is not running.")

    def _bg_log_folder(self, ask=True):
        folder = self.settings.get_str("batch_gartner_log_folder")
        if folder and os.path.isdir(folder):
            return folder
        folder = bgartner.find_log_folder()
        if folder:
            return folder
        if not ask:
            return None
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Where does Batch Gartner write render logs?")
        if folder:
            self.settings.set("batch_gartner_log_folder", folder)
        return folder or None

    def _pick_log(self, logs, title):
        if not logs:
            self.show_message("No render logs found.")
            return
        labels = ["{}   {}".format(time.strftime("%Y-%m-%d %H:%M", time.localtime(t)), os.path.basename(p))
                  for t, p in logs[:50]]
        label, ok = QtWidgets.QInputDialog.getItem(self, title, "Newest first:", labels, 0, False)
        if ok and label in labels:
            self.open_log(logs[labels.index(label)][1])

    def bg_open_log(self):
        folder = self._bg_log_folder()
        if folder:
            self._pick_log(bgartner.list_logs(folder), "Batch Gartner Render Logs")

    def bg_log_for_selected(self):
        if not self._need_nuke():
            return
        folder = self._bg_log_folder()
        if not folder:
            return
        _script, jobs = nuke_bridge.selected_write_jobs()
        script = nuke_bridge.script_path() or ""
        words = [os.path.splitext(os.path.basename(script))[0]] + [j["write_node"].split(".")[-1] for j in jobs]
        logs = bgartner.logs_matching(bgartner.list_logs(folder), words)
        if len(logs) == 1 or (logs and jobs):
            self.open_log(logs[0][1])
        else:
            self._pick_log(logs or bgartner.list_logs(folder), "Render Logs")

    # ------------------------------------------------------------- #
    #  Closing                                                      #
    # ------------------------------------------------------------- #
    def _shutdown(self):
        """Stop timers and store state; nothing is written afterwards."""
        self._closing = True
        for timer in (self.autosave_timer, self._deferred_timer, self._outline_timer, self._follow_timer,
                      self._lock_timer):
            timer.stop()
        for editor in self.editors():
            if editor.follower is not None:
                editor.follower.stop()
            self._release_lock(editor)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.focusChanged.disconnect(self._on_focus_changed)
            except (RuntimeError, TypeError):
                pass
        self.search_panel.stop()
        self.todo_panel.stop()

    def closeEvent(self, event):
        self._flush_follow_note()
        for editor in self.editors():
            if not editor.is_modified or editor.is_scratchpad or editor.follower is not None:
                continue
            if editor is self._follow_editor:
                continue
            self._focus_editor(editor)
            reply = QtWidgets.QMessageBox.question(
                self, "Save Changes?",
                "Save changes to \"{}\" before closing the editor?".format(self._display_name(editor)),
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Save)
            if reply == QtWidgets.QMessageBox.Cancel:
                event.ignore()
                return
            if reply == QtWidgets.QMessageBox.Save and not self.save_editor(editor):
                event.ignore()
                return
        self._save_scratchpad()
        self._store_session_tabs()
        self._shutdown()
        for editor in self.editors():
            if not editor.is_modified:
                self._save_bookmarks(editor)
        try:
            recovery = self._recovery_path()
            if os.path.exists(recovery):
                os.remove(recovery)
        except OSError:
            pass
        self.settings.set("browser/visible", self.file_browser.isVisible())
        self.settings.sync()
        event.accept()


class TextEditorPanel(TextEditorWidget):
    """Dockable version for Nuke's panes (Pane menu > Text Editor).

    Nuke does not ask a panel before closing it, so unsaved work is kept
    in the crash-recovery snapshot whenever the panel is hidden."""

    floating = False

    def __init__(self, parent=None):
        diagnostics.start_session()
        diagnostics.trace("opening docked panel")
        try:
            super().__init__(parent)
        finally:
            diagnostics.end_startup()

    def hideEvent(self, event):
        try:
            self._save_scratchpad()
            self._flush_follow_note()
            self.save_session_backup()
            self._store_session_tabs()
        except RuntimeError:
            pass
        super().hideEvent(event)


def document_has_formatting(document):
    """True if the document contains formatting or images that would be
    lost when saving as plain text."""
    align_mask = QtCore.Qt.AlignHCenter | QtCore.Qt.AlignRight | QtCore.Qt.AlignJustify
    props = [QtGui.QTextFormat.ForegroundBrush, QtGui.QTextFormat.BackgroundBrush,
             QtGui.QTextFormat.FontPointSize, QtGui.QTextFormat.FontFamily]
    if hasattr(QtGui.QTextFormat, "FontFamilies"):
        props.append(QtGui.QTextFormat.FontFamilies)
    block = document.begin()
    while block.isValid():
        if bool(block.blockFormat().alignment() & align_mask):
            return True
        try:
            ranges = block.textFormats()
        except AttributeError:
            return True
        for format_range in ranges:
            fmt = format_range.format
            if fmt.isImageFormat():
                return True
            font = fmt.font()
            if font.bold() or font.italic() or font.underline():
                return True
            if any(fmt.hasProperty(prop) for prop in props):
                return True
        block = block.next()
    return False


# ------------------------------------------------------------- #
#  Launching                                                    #
# ------------------------------------------------------------- #
def _nuke_main_window():
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    for widget in app.topLevelWidgets():
        if (isinstance(widget, QtWidgets.QMainWindow)
                and widget.metaObject().className() == "Foundry::UI::DockMainWindow"):
            return widget
    return None


FLOATING_PROPERTY = "pg_text_editor_floating"


def _find_floating():
    """The open floating editor, also when this module was reloaded after
    it was created (module globals start empty again then)."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    for widget in app.topLevelWidgets():
        try:
            if widget.property(FLOATING_PROPERTY) and widget.isVisible():
                return widget
        except RuntimeError:
            continue
    return None


def _clear_floating(*_args):
    global _floating_instance
    _floating_instance = None


def show_texteditor():
    """Show the floating editor, or bring the open one to the front."""
    global _floating_instance
    window = _floating_instance or _find_floating()
    if window is not None:
        try:
            if window.isMinimized():
                window.showNormal()
            window.show()
            window.raise_()
            window.activateWindow()
            return window
        except RuntimeError:
            _floating_instance = None
    diagnostics.start_session()
    diagnostics.trace("opening floating window")
    try:
        window = TextEditorWidget(_nuke_main_window())
        window.destroyed.connect(_clear_floating)
        window.setProperty(FLOATING_PROPERTY, True)
        _floating_instance = window
        window.show()
        diagnostics.trace("window shown")
        diagnostics.end_startup()
    except Exception:
        diagnostics.end_startup()
        text = diagnostics.log_exception("opening the editor")
        print(text)
        QtWidgets.QMessageBox.critical(
            _nuke_main_window(), "Text Editor",
            "The Text Editor could not open:\n\n{}\nDetails were saved in {}".format(
                text.strip().splitlines()[-1], diagnostics.logs_dir()))
        return None
    return window
