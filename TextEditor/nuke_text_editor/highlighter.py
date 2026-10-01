"""Regex-based syntax highlighter.

Uses Python's 're' module so it behaves the same in PySide2 and PySide6.
Adds colors on top of the editor's text color; also styles clickable
links and task checkboxes.
"""

import datetime
import re

from .qt import QtGui
from . import fileio
from . import links
from . import textops
from . import themes

# The rules below are written with the dark theme's colors; each one is
# mapped to a theme role so every theme can recolor it.
COLOR_ROLES = {
    "#ff79c6": "keyword", "#ffb86c": "builtin", "#50fa7b": "function", "#bd93f9": "number",
    "#f1fa8c": "string", "#6272a4": "comment", "#8be9fd": "type", "#ff5555": "error",
    "#f8f8f2": "text", "#6c6c6c": "done",
}


def _fmt(color=None, bold=False, italic=False, underline=False, strike=False):
    fmt = QtGui.QTextCharFormat()
    if color:
        role = COLOR_ROLES.get(color.lower())
        fmt.setForeground(QtGui.QColor(themes.color(role) if role else color))
    if bold:
        fmt.setFontWeight(QtGui.QFont.Bold)
    if italic:
        fmt.setFontItalic(True)
    if underline:
        fmt.setFontUnderline(True)
    if strike:
        fmt.setFontStrikeOut(True)
    return fmt


class SimpleHighlighter(QtGui.QSyntaxHighlighter):
    """Single-line rules plus multi-line spans (triple-quoted strings,
    block comments). Block state 0 means 'not inside a span'."""

    STRING_SQ = r"'(?:[^'\\\n]|\\.)*'"
    STRING_DQ = r'"(?:[^"\\\n]|\\.)*"'
    NUMBER = r"\b[0-9]+(\.[0-9]+)?\b"

    def __init__(self, document):
        # Explicit base call: super() can misbehave for this class in some Nuke builds.
        QtGui.QSyntaxHighlighter.__init__(self, document)
        self.language = "text"
        self.rules = []
        self.spans = []  # (start_regex, end_regex, format, state_id)
        self.notes = True
        self._build_special_formats()

    def _build_special_formats(self):
        self.link_format = _fmt(themes.color("link"), underline=True)
        self.task_box_format = _fmt("#bd93f9", bold=True)
        self.task_done_format = _fmt("#6c6c6c", strike=True)
        self.person_format = _fmt(themes.color("person"), bold=True)
        self.due_format = _fmt(themes.color("due"), bold=True)
        self.overdue_format = _fmt(themes.color("error"), bold=True)

    def refresh_theme(self):
        """Rebuild all formats with the current theme's colors."""
        self._build_special_formats()
        self._build_rules(self.language)
        self.rehighlight()

    def set_language(self, language):
        """Change language and rebuild highlighting rules."""
        lang = (language or "text").lower()
        if lang == self.language and (self.rules or lang == "text"):
            return
        self.language = lang
        self.notes = lang in fileio.NOTE_LANGUAGES
        self._build_rules(lang)
        self.rehighlight()

    def _build_rules(self, lang):
        rules = []
        spans = []

        def add(pattern, color, bold=False, italic=False, flags=0):
            rules.append((re.compile(pattern, flags), _fmt(color, bold, italic)))

        def span(start, end, color, state, italic=False):
            spans.append((re.compile(start), re.compile(end), _fmt(color, italic=italic), state))

        if lang == "python":
            keywords = [
                "and", "as", "assert", "async", "await", "break", "class", "continue",
                "def", "del", "elif", "else", "except", "False", "finally", "for",
                "from", "global", "if", "import", "in", "is", "lambda", "None",
                "nonlocal", "not", "or", "pass", "raise", "return", "True", "try",
                "while", "with", "yield",
            ]
            add(r"\b(" + "|".join(keywords) + r")\b", "#ff79c6", bold=True)
            add(r"\b(self|cls|nuke|nukescripts)\b", "#ffb86c")
            add(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\()", "#50fa7b")
            add(self.NUMBER, "#bd93f9")
            add(self.STRING_SQ, "#f1fa8c")
            add(self.STRING_DQ, "#f1fa8c")
            add(r"#.*$", "#6272a4", italic=True)
            span(r'"""', r'"""', "#f1fa8c", 1)
            span(r"'''", r"'''", "#f1fa8c", 2)

        elif lang == "json":
            add(self.STRING_DQ, "#f1fa8c")
            add(r'"(?:[^"\\\n]|\\.)*"(?=\s*:)', "#8be9fd", bold=True)
            add(self.NUMBER, "#bd93f9")
            add(r"\b(true|false|null)\b", "#bd93f9", bold=True)

        elif lang == "markdown":
            add(r"^#{1,6}.*$", "#ff79c6", bold=True)
            add(r"\*\*[^*]+\*\*", "#f1fa8c", bold=True)
            add(r"(?<!\*)\*[^*\s][^*]*\*(?!\*)", "#bd93f9", italic=True)
            add(r"`[^`]+`", "#bd93f9")
            add(r"\[[^\]]+\]\([^)]+\)", "#8be9fd")
            span(r"<!--", r"-->", "#6272a4", 4, italic=True)

        elif lang == "nuke":
            nk_keywords = [
                "push", "pop", "set", "add_layer", "inputs", "version", "end_group",
                "addUserKnob", "Animated", "Group", "Root", "Viewer",
            ]
            add(r"^\s*[A-Za-z_][\w.]*(?=\s*\{\s*$)", "#50fa7b", bold=True)
            add(r"\b(" + "|".join(nk_keywords) + r")\b", "#ff79c6", bold=True)
            add(r"^\s+name\s+.*$", "#ffb86c")
            add(self.NUMBER, "#bd93f9")
            add(self.STRING_DQ, "#f1fa8c")
            add(r"^\s*#.*$", "#6272a4", italic=True)

        elif lang == "ini":
            add(r"^\[[^\]]+\]", "#ff79c6", bold=True)
            add(r"^[A-Za-z0-9_.]+(?=\s*=)", "#8be9fd")
            add(self.STRING_DQ, "#f1fa8c")
            add(self.NUMBER, "#bd93f9")
            add(r"^\s*[;#].*$", "#6272a4", italic=True)

        elif lang == "html":
            add(r"</?[A-Za-z_][A-Za-z0-9_\-]*", "#ff79c6", bold=True)
            add(r"\b[A-Za-z_:][A-Za-z0-9_\-:]*=", "#8be9fd")
            add(self.STRING_DQ, "#f1fa8c")
            span(r"<!--", r"-->", "#6272a4", 4, italic=True)

        elif lang == "javascript":
            js_keywords = [
                "var", "let", "const", "function", "return", "if", "else",
                "for", "while", "do", "switch", "case", "break", "continue",
                "new", "this", "class", "extends", "super", "import", "from",
                "export", "default", "true", "false", "null", "undefined",
            ]
            add(r"\b(" + "|".join(js_keywords) + r")\b", "#ff79c6", bold=True)
            add(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\()", "#50fa7b")
            add(self.NUMBER, "#bd93f9")
            add(self.STRING_SQ, "#f1fa8c")
            add(self.STRING_DQ, "#f1fa8c")
            add(r"//.*$", "#6272a4", italic=True)
            span(r"/\*", r"\*/", "#6272a4", 3, italic=True)

        elif lang == "css":
            add(r"\.[A-Za-z0-9_\-]+", "#8be9fd")
            add(r"#[A-Za-z0-9_\-]+", "#8be9fd")
            add(r"\b[A-Za-z\-]+(?=\s*:)", "#ff79c6")
            add(r":[^;{]+;", "#f1fa8c")
            span(r"/\*", r"\*/", "#6272a4", 3, italic=True)

        elif lang == "c_like":
            c_keywords = [
                "auto", "break", "case", "char", "const", "continue", "default",
                "do", "double", "else", "enum", "extern", "float", "for", "goto",
                "if", "inline", "int", "long", "register", "return", "short",
                "signed", "sizeof", "static", "struct", "switch", "typedef",
                "union", "unsigned", "void", "volatile", "while", "class",
                "public", "private", "protected", "virtual", "template",
                "typename", "namespace", "using",
            ]
            add(r"\b(" + "|".join(c_keywords) + r")\b", "#ff79c6", bold=True)
            add(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\()", "#50fa7b")
            add(self.NUMBER, "#bd93f9")
            add(self.STRING_SQ, "#f1fa8c")
            add(self.STRING_DQ, "#f1fa8c")
            add(r"//.*$", "#6272a4", italic=True)
            span(r"/\*", r"\*/", "#6272a4", 3, italic=True)

        elif lang == "bash":
            add(r"\b(if|then|else|elif|fi|for|in|do|done|case|esac|function|return|while)\b",
                "#ff79c6", bold=True)
            add(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", "#8be9fd")
            add(self.NUMBER, "#bd93f9")
            add(r"'[^']*'", "#f1fa8c")
            add(self.STRING_DQ, "#f1fa8c")
            add(r"(^|\s)#.*$", "#6272a4", italic=True)

        elif lang == "batch":
            add(r"^@?echo\s+(on|off)", "#ff79c6", bold=True, flags=re.IGNORECASE)
            add(r"\b(if|else|for|in|do|goto|call|set|echo|exit)\b", "#ff79c6", bold=True, flags=re.IGNORECASE)
            add(r"%[A-Za-z_][A-Za-z0-9_]*%", "#8be9fd")
            add(r"^\s*(rem\b|::).*$", "#6272a4", italic=True, flags=re.IGNORECASE)

        elif lang == "log":
            add(r"^\S*\d{2}:\d{2}:\d{2}\S*", "#6c6c6c")
            add(r"\b(Frame|frame)\s+\d+", "#8be9fd")
            add(r"\d+(\.\d+)?\s*%", "#50fa7b")
            add(r".*\b(WARNING|Warning|warning)\b.*", "#f1fa8c")
            add(r".*\b(ERROR|Error|error|FAILED|Failed|Traceback|Exception|Killed)\b.*", "#ff5555", bold=True)

        elif lang == "diff":
            add(r"^\+.*$", "#50fa7b")
            add(r"^-.*$", "#ff5555")
            add(r"^@@.*$", "#8be9fd")
            add(r"^(\+\+\+|---) .*$", "#f8f8f2", bold=True)

        self.rules = rules
        self.spans = spans

    def _apply_spans(self, text):
        self.setCurrentBlockState(0)
        if not self.spans:
            return
        pos = 0
        state = self.previousBlockState()
        active = next((s for s in self.spans if s[3] == state), None)
        if active is not None:
            end = active[1].search(text, 0)
            if end is None:
                self.setFormat(0, len(text), active[2])
                self.setCurrentBlockState(active[3])
                return
            self.setFormat(0, end.end(), active[2])
            pos = end.end()
        while pos <= len(text):
            best = None
            for span in self.spans:
                match = span[0].search(text, pos)
                if match and (best is None or match.start() < best[1].start()):
                    best = (span, match)
            if best is None:
                return
            span, start = best
            end = span[1].search(text, start.end())
            if end is None:
                self.setFormat(start.start(), len(text) - start.start(), span[2])
                self.setCurrentBlockState(span[3])
                return
            self.setFormat(start.start(), end.end() - start.start(), span[2])
            pos = end.end()

    def highlightBlock(self, text):
        for regex, text_format in self.rules:
            for match in regex.finditer(text):
                start, end = match.start(), match.end()
                if end > start:
                    self.setFormat(start, end - start, text_format)
        self._apply_spans(text)

        if self.language != "log":
            for start, end, _kind, _value in links.find_links(text, notes=self.notes):
                self.setFormat(start, end - start, self.link_format)

        if self.notes:
            for match in textops.PERSON_RE.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), self.person_format)
            for match in textops.DUE_RE.finditer(text):
                due = textops.parse_due(match.group(1), datetime.date.today())
                done = bool(textops.TASK_RE.match(text)) and textops.TASK_RE.match(text).group(2) in ("x", "X")
                fmt = self.overdue_format if (due is not None and due < datetime.date.today() and not done) \
                    else self.due_format
                self.setFormat(match.start(), match.end() - match.start(), fmt)

        task = textops.TASK_RE.match(text)
        if task:
            box_start = task.start(2) - 1
            self.setFormat(box_start, 3, self.task_box_format)
            if task.group(2) in ("x", "X") and len(text) > task.end():
                self.setFormat(task.end(), len(text) - task.end(), self.task_done_format)
