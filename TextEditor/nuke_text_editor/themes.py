"""Color themes. Pure data: every widget and the syntax highlighter read
their colors from the current theme."""

THEMES = {
    "Dark": {
        "window": "#2c2c2c", "panel": "#262626", "editor": "#1e1e1e", "text": "#e6e6e6",
        "ui_text": "#dddddd", "muted": "#888888", "border": "#444444", "button": "#3a3a3a",
        "button_hover": "#444444", "input": "#1e1e1e", "menu": "#3c3c3c", "menu_hover": "#555555",
        "tab": "#2b2b2b", "tab_selected": "#3c3c3c", "menubar": "#1f1f1f",
        "gutter": "#252525", "gutter_text": "#7f7f7f", "gutter_active": "#ffffff",
        "current_line": "#2a2a30", "word": "#44475a", "word_other": "#3a3c4e", "bracket_bad": "#ff5555",
        "guide": "#333333", "caret": "#f8f8f2", "error_bg": "#5a1d1d", "warning_bg": "#4a4220",
        "message": "#f1fa8c",
        "keyword": "#ff79c6", "builtin": "#ffb86c", "function": "#50fa7b", "number": "#bd93f9",
        "string": "#f1fa8c", "comment": "#6272a4", "type": "#8be9fd", "error": "#ff5555",
        "link": "#8be9fd", "done": "#6c6c6c", "person": "#ffb86c", "due": "#8be9fd",
    },
    "Nuke": {
        "window": "#323232", "panel": "#2a2a2a", "editor": "#282828", "text": "#dcdcdc",
        "ui_text": "#d0d0d0", "muted": "#8a8a8a", "border": "#4a4a4a", "button": "#454545",
        "button_hover": "#525252", "input": "#262626", "menu": "#3a3a3a", "menu_hover": "#f7931e",
        "tab": "#353535", "tab_selected": "#4a4a4a", "menubar": "#2b2b2b",
        "gutter": "#2e2e2e", "gutter_text": "#808080", "gutter_active": "#f7931e",
        "current_line": "#303030", "word": "#4f4a40", "word_other": "#403c35", "bracket_bad": "#c0392b",
        "guide": "#3a3a3a", "caret": "#f0f0f0", "error_bg": "#5a2020", "warning_bg": "#4d4420",
        "message": "#f7931e",
        "keyword": "#f7931e", "builtin": "#e0b060", "function": "#8fc46a", "number": "#b99ad6",
        "string": "#d9cf7c", "comment": "#7a8a99", "type": "#79b8d9", "error": "#e05050",
        "link": "#79b8d9", "done": "#707070", "person": "#e0b060", "due": "#79b8d9",
    },
    "Light": {
        "window": "#ececec", "panel": "#e2e2e2", "editor": "#ffffff", "text": "#1e1e1e",
        "ui_text": "#202020", "muted": "#6a6a6a", "border": "#b8b8b8", "button": "#dcdcdc",
        "button_hover": "#cfcfcf", "input": "#ffffff", "menu": "#f2f2f2", "menu_hover": "#c8d8f0",
        "tab": "#dddddd", "tab_selected": "#ffffff", "menubar": "#e6e6e6",
        "gutter": "#f0f0f0", "gutter_text": "#9a9a9a", "gutter_active": "#1e1e1e",
        "current_line": "#f3f6fb", "word": "#d7e3f7", "word_other": "#e8eef8", "bracket_bad": "#f4b0b0",
        "guide": "#e2e2e2", "caret": "#1e1e1e", "error_bg": "#f8d0d0", "warning_bg": "#f6ecc0",
        "message": "#9a5b00",
        "keyword": "#a626a4", "builtin": "#c18401", "function": "#2c7a39", "number": "#986801",
        "string": "#50a14f", "comment": "#8a8a8a", "type": "#0184bc", "error": "#c0392b",
        "link": "#0b63c5", "done": "#a0a0a0", "person": "#b35c00", "due": "#0184bc",
    },
}

DEFAULT = "Dark"
_current = DEFAULT


def names():
    return list(THEMES)


def set_current(name):
    global _current
    _current = name if name in THEMES else DEFAULT


def current_name():
    return _current


def color(key):
    """Color of the current theme (falls back to the dark theme)."""
    return THEMES[_current].get(key) or THEMES[DEFAULT][key]


def dialog_style():
    t = THEMES[_current]
    return """
    QDialog, QWidget {{ background-color: {window}; color: {ui_text}; font-size: 11px; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox, QKeySequenceEdit {{
        background-color: {input}; border: 1px solid {border}; border-radius: 3px;
        padding: 3px; color: {text};
    }}
    QTreeWidget, QListWidget, QTableWidget, QTextBrowser {{
        background-color: {input}; border: 1px solid {border}; color: {ui_text};
        alternate-background-color: {panel};
    }}
    QHeaderView::section {{ background-color: {tab_selected}; color: {ui_text}; border: none; padding: 3px; }}
    QTabWidget::pane {{ border: 1px solid {border}; }}
    QTabBar::tab {{ background: {tab}; color: {ui_text}; padding: 4px 10px; }}
    QTabBar::tab:selected {{ background: {tab_selected}; }}
    """.format(**t)


def window_style():
    t = THEMES[_current]
    return """
    QWidget {{ background-color: {window}; color: {ui_text}; font-size: 11px; }}
    QTextEdit {{
        background-color: {editor}; border: 1px solid {border}; border-radius: 3px;
        padding: 6px; color: {text};
    }}
    QLineEdit, QPlainTextEdit, QTextBrowser {{
        background-color: {input}; border: 1px solid {border}; border-radius: 3px; padding: 2px 4px;
        color: {text};
    }}
    QTreeWidget, QListWidget, QTreeView, QTableWidget {{ background-color: {input}; border: 1px solid {border}; }}
    QComboBox, QFontComboBox {{
        background-color: {button}; border: 1px solid {border}; padding: 2px 5px;
        border-radius: 3px; color: {ui_text};
    }}
    QComboBox:disabled, QFontComboBox:disabled {{ color: {muted}; }}
    QLabel {{ color: {ui_text}; }}
    QMenu {{ background-color: {menu}; color: {ui_text}; }}
    QMenu::item:selected {{ background-color: {menu_hover}; }}
    QMenu::item:disabled {{ color: {muted}; }}
    QSplitter::handle {{ background-color: {panel}; }}
    QTabBar::tab {{
        margin-top: 4px; background: {tab}; color: {ui_text}; padding: 4px 8px;
        border-top-left-radius: 3px; border-top-right-radius: 3px;
    }}
    QTabBar::tab:selected {{ background: {tab_selected}; }}
    QTabBar::tab:!selected {{ margin-top: 2px; }}
    QMenuBar {{
        background: {menubar}; color: {ui_text}; padding-top: 5px; padding-bottom: 5px;
        border-bottom: 1px solid {border};
    }}
    QMenuBar::item {{ background: transparent; padding: 4px 8px; }}
    QMenuBar::item:selected {{ background: {menu_hover}; }}
    """.format(**t)


def button_style():
    t = THEMES[_current]
    return """
    QPushButton {{
        background-color: {button}; border: 1px solid {border}; padding: 4px 8px;
        border-radius: 2px; color: {ui_text}; min-width: 0px;
    }}
    QPushButton:hover {{ background-color: {button_hover}; }}
    QPushButton:checked {{ background-color: {button_hover}; border: 1px solid {muted}; }}
    QPushButton:disabled {{ color: {muted}; border: 1px solid {border}; }}
    """.format(**t)
