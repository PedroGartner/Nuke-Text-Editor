"""The editor widget used in every tab."""

import builtins
import inspect
import keyword
import re
import sys

from .qt import QtCore, QtGui, QtWidgets, event_pos, modifier_state
from . import fileio
from . import links
from . import textops
from . import themes
from .highlighter import SimpleHighlighter

INDENT_TAB_SIZE = 4

BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}"}
CLOSING_BRACKETS = {v: k for k, v in BRACKET_PAIRS.items()}


def find_matching_bracket(document, index, max_blocks=5000):
    """Return the document position of the bracket matching the one at
    `index`, or -1. Scans block by block so large files stay fast."""
    ch = document.characterAt(index)
    if ch in BRACKET_PAIRS:
        same, other, forward = ch, BRACKET_PAIRS[ch], True
    elif ch in CLOSING_BRACKETS:
        same, other, forward = ch, CLOSING_BRACKETS[ch], False
    else:
        return -1

    block = document.findBlock(index)
    offset = index - block.position()
    depth = 0
    scanned = 0
    while block.isValid() and scanned < max_blocks:
        text = block.text()
        indices = range(offset, len(text)) if forward else range(min(offset, len(text) - 1), -1, -1)
        for i in indices:
            c = text[i]
            if c == same:
                depth += 1
            elif c == other:
                depth -= 1
                if depth == 0:
                    return block.position() + i
        block = block.next() if forward else block.previous()
        scanned += 1
        if block.isValid():
            offset = 0 if forward else len(block.text()) - 1
    return -1


def describe_python_name(dotted, max_lines=14):
    """Signature and documentation of a module attribute such as
    'nuke.createNode' (only modules and classes are traversed, so no
    user code runs). Returns HTML or None."""
    import html
    parts = dotted.split(".")
    obj = sys.modules.get(parts[0])
    if obj is None:
        import __main__
        obj = getattr(__main__, parts[0], None)
    if obj is None or not (inspect.ismodule(obj) or inspect.isclass(obj)):
        return None
    for part in parts[1:]:
        if not (inspect.ismodule(obj) or inspect.isclass(obj)):
            return None
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    try:
        signature = str(inspect.signature(obj)) if callable(obj) else ""
    except (TypeError, ValueError):
        signature = ""
    doc = inspect.getdoc(obj) or ""
    if not doc and not signature:
        return None
    lines = doc.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["..."]
    return "<b>{}{}</b><pre>{}</pre>".format(html.escape(dotted), html.escape(signature),
                                          html.escape("\n".join(lines)))


# ------------------------------------------------------------- #
#  Base text edit                                               #
# ------------------------------------------------------------- #
class PersistentTextEdit(QtWidgets.QTextEdit):
    """QTextEdit with per-tab metadata.

    - In rich mode, keeps the current character format on Enter.
    - In plain mode, pasting always inserts plain text.
    - Emits 'emptied' when the document becomes empty.
    """
    emptied = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.file_path = None
        self.custom_title = None
        self.encoding = "utf-8"
        self.newline = "\n"
        self.language = "text"
        self.rich = True
        self.disk_mtime = None      # mtime after our last load / save
        self.binding = None         # KnobBinding when the tab edits a Nuke knob
        self.follower = None        # LogFollower when the tab follows a log file
        self.is_scratchpad = False
        self.highlighter = SimpleHighlighter(self.document())

    # The modified flag lives on the document, so undoing back to the
    # saved state clears it and view-only changes never set it.
    @property
    def is_modified(self):
        return self.document().isModified()

    @is_modified.setter
    def is_modified(self, value):
        self.document().setModified(bool(value))

    def set_rich(self, rich):
        self.rich = bool(rich)
        self.setAcceptRichText(self.rich)

    def keyPressEvent(self, event):
        if self.rich and event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            fmt = self.textCursor().charFormat()
            super().keyPressEvent(event)
            self.mergeCurrentCharFormat(fmt)
        else:
            super().keyPressEvent(event)
        if self.document().isEmpty():
            self.emptied.emit()


# ------------------------------------------------------------- #
#  Gutter                                                       #
# ------------------------------------------------------------- #
class LineNumberArea(QtWidgets.QWidget):
    """Paints line numbers, bookmarks, fold markers and error marks."""

    def __init__(self, editor):
        super().__init__(editor)
        self.code_editor = editor

    def sizeHint(self):
        return QtCore.QSize(self.code_editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.code_editor.line_number_area_paint_event(event)

    def mousePressEvent(self, event):
        self.code_editor.handle_gutter_click(event)
        super().mousePressEvent(event)

    def event(self, event):
        if event.type() == QtCore.QEvent.ToolTip:
            text = self.code_editor.gutter_tooltip(event_pos(event).y())
            if text:
                QtWidgets.QToolTip.showText(event.globalPos(), text, self)
            else:
                QtWidgets.QToolTip.hideText()
            return True
        return super().event(event)


# ------------------------------------------------------------- #
#  Editor                                                       #
# ------------------------------------------------------------- #
class AdvancedCodeEditor(PersistentTextEdit):
    """Editor with gutter, folding, bookmarks, indent guides, smart
    indentation, pairing, multi-cursor, links, task checkboxes,
    autocompletion and snippets."""

    zoom_requested = QtCore.Signal(int)
    bookmarks_changed = QtCore.Signal()
    link_activated = QtCore.Signal(str, object)
    task_toggled = QtCore.Signal(str, bool)   # task text, now checked

    QUOTES = ('"', "'")
    PAIR_FOLLOWERS = ",;:"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.show_line_numbers = True
        self.show_fold_markers = True
        self.gutter_scale = 1.0
        self.error_line = None          # 0-based block number with a syntax / run error
        self.lint_marks = {}            # {0-based line: 'error' | 'warning' | 'info'}
        self.lint_messages = {}         # {0-based line: text} (shown as tooltip)
        self.snippet_provider = None    # callable(trigger, language) -> snippet dict
        self.image_saver = None         # callable(QImage) -> path

        self.additional_cursors = []
        self._bookmarks = []            # [QTextCursor, color]; cursors follow edits
        self._next_bookmark_color = 0
        self._folds = []                # QTextCursors at folded header blocks
        self._hand_cursor = False
        self.bookmark_palette = [
            QtGui.QColor("#ff5555"), QtGui.QColor("#50fa7b"), QtGui.QColor("#8be9fd"),
            QtGui.QColor("#bd93f9"), QtGui.QColor("#f1fa8c"), QtGui.QColor("#ff79c6"),
        ]

        self.word_selections = []
        self.highlighted_word = ""

        # Autocompletion
        self._completer = QtWidgets.QCompleter(self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self._completer.setMaxVisibleItems(12)
        self._completion_model = QtCore.QStringListModel(self)
        self._completer.setModel(self._completion_model)
        self._completer.activated[str].connect(self._insert_completion)
        self.apply_theme()
        self._attr_cache = {}
        self._words_cache = (None, [])

        self.viewport().setMouseTracking(True)
        self._hover_timer = QtCore.QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(600)
        self._hover_timer.timeout.connect(self._show_hover_help)
        self._hover_pos = None

        self.line_number_area = LineNumberArea(self)
        self.document().blockCountChanged.connect(self.update_line_number_area_width)
        self.verticalScrollBar().valueChanged.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.update_line_number_area)
        self.textChanged.connect(self.update_line_number_area)
        self.update_line_number_area_width()

    @property
    def code_mode(self):
        """True for source/config files (enables pairing etc.)."""
        return self.language not in ("text", "markdown", "log")

    @property
    def notes_mode(self):
        return self.language in fileio.NOTE_LANGUAGES

    # ----- Bookmarks ------------------------------------------- #
    def bookmark_map(self):
        """{line_number: color_index} for all bookmarks."""
        result = {}
        for cursor, color in self._bookmarks:
            line = cursor.block().blockNumber()
            if line not in result:
                result[line] = color
        return result

    def set_bookmarks(self, mapping):
        self._bookmarks = []
        doc = self.document()
        for line, color in sorted(mapping.items()):
            block = doc.findBlockByNumber(int(line))
            if block.isValid():
                self._bookmarks.append([QtGui.QTextCursor(block), int(color)])
        self._next_bookmark_color = len(self._bookmarks)
        self.update_line_number_area()

    def toggle_bookmark_at_block(self, block):
        line = block.blockNumber()
        remaining = [b for b in self._bookmarks if b[0].block().blockNumber() != line]
        if len(remaining) != len(self._bookmarks):
            self._bookmarks = remaining
        else:
            color = self._next_bookmark_color % len(self.bookmark_palette)
            self._bookmarks.append([QtGui.QTextCursor(block), color])
            self._next_bookmark_color += 1
        self.update_line_number_area()
        self.bookmarks_changed.emit()

    def toggle_bookmark_current_line(self):
        self.toggle_bookmark_at_block(self.textCursor().block())

    def clear_bookmark_current_line(self):
        line = self.textCursor().block().blockNumber()
        remaining = [b for b in self._bookmarks if b[0].block().blockNumber() != line]
        if len(remaining) != len(self._bookmarks):
            self._bookmarks = remaining
            self.update_line_number_area()
            self.bookmarks_changed.emit()

    def goto_next_bookmark(self):
        lines = sorted(self.bookmark_map())
        if lines:
            current = self.textCursor().block().blockNumber()
            self.goto_line(next((ln for ln in lines if ln > current), lines[0]))

    def goto_previous_bookmark(self):
        lines = sorted(self.bookmark_map())
        if lines:
            current = self.textCursor().block().blockNumber()
            self.goto_line(next((ln for ln in reversed(lines) if ln < current), lines[-1]))

    def goto_line(self, line_number, column=0):
        """Move the cursor to a 0-based line (and column) and centre it."""
        block = self.document().findBlockByNumber(max(0, int(line_number)))
        if not block.isValid():
            block = self.document().lastBlock()
        cursor = QtGui.QTextCursor(block)
        cursor.setPosition(block.position() + min(max(0, column), max(0, block.length() - 1)))
        self.setTextCursor(cursor)
        self.center_cursor()

    def center_cursor(self):
        """QTextEdit has no centerCursor(), unlike QPlainTextEdit."""
        self.ensureCursorVisible()
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + self.cursorRect().center().y() - self.viewport().height() // 2)

    def set_error_line(self, line_number):
        """Mark a 0-based line in the gutter (None clears)."""
        self.error_line = line_number
        self.update_line_number_area()

    # ----- Gutter ---------------------------------------------- #
    def line_number_area_width(self):
        digits = len(str(max(1, self.document().blockCount())))
        if not self.show_line_numbers:
            digits = 2
        base_space = 6 + self.fontMetrics().horizontalAdvance("9") * digits + 24
        return int(base_space * float(self.gutter_scale))

    def update_line_number_area_width(self, *_args):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, *_args):
        self.viewport().update()
        self.line_number_area.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(
            QtCore.QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def _first_visible_block(self):
        block = self.cursorForPosition(QtCore.QPoint(0, 0)).block()
        previous = block.previous()
        return previous if previous.isValid() else block

    def line_number_area_paint_event(self, event):
        painter = QtGui.QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QtGui.QColor(themes.color("gutter")))
        painter.setFont(self.font())
        layout = self.document().documentLayout()
        if layout is None:
            painter.end()
            return

        scroll_y = self.verticalScrollBar().value()
        number_color = QtGui.QColor(themes.color("gutter_text"))
        active_color = QtGui.QColor(themes.color("gutter_active"))
        error_color = QtGui.QColor(themes.color("error"))
        fold_color = QtGui.QColor(themes.color("text"))
        lint_colors = {"error": QtGui.QColor(themes.color("error")),
                       "warning": QtGui.QColor(themes.color("builtin")),
                       "info": QtGui.QColor(themes.color("comment"))}
        current_block = self.textCursor().block()
        fm = self.fontMetrics()
        line_height = fm.height()
        marker_size = max(8, int(line_height * 0.6))
        bookmarks = self.bookmark_map()
        width = self.line_number_area.width()

        block = self._first_visible_block()
        while block.isValid():
            rect = layout.blockBoundingRect(block)
            top = rect.top() - scroll_y
            if top > event.rect().bottom():
                break
            if rect.bottom() - scroll_y >= event.rect().top() and block.isVisible():
                line_top = int(top)
                number = block.blockNumber()
                if number == self.error_line:
                    painter.fillRect(QtCore.QRect(0, line_top, width, line_height), QtGui.QColor(themes.color("error_bg")))
                lint_severity = self.lint_marks.get(number)
                if lint_severity:
                    painter.fillRect(QtCore.QRect(width - 3, line_top, 3, line_height), lint_colors[lint_severity])
                if self.show_line_numbers:
                    text = str(number + 1)
                    if number == self.error_line:
                        painter.setPen(error_color)
                    else:
                        painter.setPen(active_color if block == current_block else number_color)
                    painter.drawText(width - 4 - fm.horizontalAdvance(text), line_top + fm.ascent(), text)
                folded = self.is_folded(block)
                if self.show_fold_markers and (folded or self._block_can_fold(block)):
                    marker = QtCore.QRect(12, line_top + (line_height - marker_size) // 2, marker_size, marker_size)
                    self._draw_fold_marker(painter, marker, folded, fold_color)
                color_index = bookmarks.get(number)
                if color_index is not None:
                    painter.setBrush(self.bookmark_palette[color_index % len(self.bookmark_palette)])
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.drawEllipse(QtCore.QPoint(6, line_top + line_height // 2), 3, 3)
            block = block.next()
        painter.end()

    def _draw_fold_marker(self, painter, rect, folded, color):
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(themes.color("builtin")) if folded else color)
        path = QtGui.QPainterPath()
        if folded:
            path.moveTo(rect.left() + 2, rect.top() + 2)
            path.lineTo(rect.right() - 2, rect.top() + rect.height() / 2.0)
            path.lineTo(rect.left() + 2, rect.bottom() - 2)
        else:
            path.moveTo(rect.left() + 2, rect.top() + 2)
            path.lineTo(rect.right() - 2, rect.top() + 2)
            path.lineTo(rect.left() + rect.width() / 2.0, rect.bottom() - 2)
        path.closeSubpath()
        painter.drawPath(path)
        painter.restore()

    # ----- Folding --------------------------------------------- #
    @staticmethod
    def _line_indent(text):
        count = 0
        for ch in text:
            if ch == " ":
                count += 1
            elif ch == "\t":
                count += INDENT_TAB_SIZE
            else:
                break
        return count

    def is_folded(self, block):
        number = block.blockNumber()
        return any(c.block().blockNumber() == number for c in self._folds)

    def _block_can_fold(self, block):
        text = block.text()
        if not text.strip():
            return False
        nxt = block.next()
        while nxt.isValid() and not nxt.text().strip():
            nxt = nxt.next()
        return nxt.isValid() and self._line_indent(nxt.text()) > self._line_indent(text)

    def handle_gutter_click(self, event):
        """Ctrl+Click toggles a bookmark, Click toggles folding."""
        if event.button() != QtCore.Qt.LeftButton:
            return
        y = event_pos(event).y()
        block = self.cursorForPosition(QtCore.QPoint(0, y)).block()
        if not block.isValid():
            return
        rect = self.document().documentLayout().blockBoundingRect(block)
        top = rect.top() - self.verticalScrollBar().value()
        if not (top <= y <= top + rect.height()):
            return
        if modifier_state(event)[0]:
            self.toggle_bookmark_at_block(block)
        elif self.is_folded(block) or self._block_can_fold(block):
            self.toggle_fold(block)

    def toggle_fold(self, header_block):
        """Hide/show the lines indented deeper than the header."""
        text = header_block.text()
        if not text.strip():
            return
        base_indent = self._line_indent(text)
        folding = not self.is_folded(header_block)
        number = header_block.blockNumber()
        self._folds = [c for c in self._folds if c.block().blockNumber() != number]
        if folding:
            self._folds.append(QtGui.QTextCursor(header_block))

        first = header_block.next()
        last = None
        block = first
        while block.isValid():
            t = block.text()
            if t.strip() and self._line_indent(t) <= base_indent:
                break
            block.setVisible(not folding)
            if not folding:
                inner = block.blockNumber()
                self._folds = [c for c in self._folds if c.block().blockNumber() != inner]
            last = block
            block = block.next()
        if last is not None:
            self.document().markContentsDirty(first.position(), last.position() + last.length() - first.position())
        if folding and not self.textCursor().block().isVisible():
            cursor = QtGui.QTextCursor(header_block)
            cursor.movePosition(QtGui.QTextCursor.EndOfBlock)
            self.setTextCursor(cursor)
        self.update_line_number_area()

    # ----- Line operations ------------------------------------- #
    def selected_blocks(self):
        """Blocks touched by the selection (or the current block)."""
        cursor = self.textCursor()
        doc = self.document()
        start = doc.findBlock(cursor.selectionStart())
        end = doc.findBlock(cursor.selectionEnd())
        if cursor.hasSelection() and end != start and end.position() == cursor.selectionEnd():
            end = end.previous()
        blocks = []
        block = start
        while block.isValid():
            blocks.append(block)
            if block == end:
                break
            block = block.next()
        return blocks

    def _replace_block_text(self, block, text):
        if block.text() == text:
            return
        c = QtGui.QTextCursor(block)
        c.movePosition(QtGui.QTextCursor.EndOfBlock, QtGui.QTextCursor.KeepAnchor)
        c.insertText(text)

    def indent_blocks(self, blocks):
        edit = self.textCursor()
        edit.beginEditBlock()
        for block in blocks:
            QtGui.QTextCursor(block).insertText(" " * INDENT_TAB_SIZE)
        edit.endEditBlock()

    def unindent_blocks(self, blocks):
        edit = self.textCursor()
        edit.beginEditBlock()
        for block in blocks:
            text = block.text()
            count = 1 if text.startswith("\t") else min(len(text) - len(text.lstrip(" ")), INDENT_TAB_SIZE)
            if count:
                c = QtGui.QTextCursor(block)
                c.setPosition(block.position() + count, QtGui.QTextCursor.KeepAnchor)
                c.removeSelectedText()
        edit.endEditBlock()

    def toggle_comment(self):
        prefix = textops.COMMENT_PREFIX.get(self.language)
        if not prefix:
            return False
        blocks = self.selected_blocks()
        new_lines = textops.toggle_comment_lines([b.text() for b in blocks], prefix)
        edit = self.textCursor()
        edit.beginEditBlock()
        for block, line in zip(blocks, new_lines):
            self._replace_block_text(block, line)
        edit.endEditBlock()
        return True

    def _blocks_range_cursor(self, blocks):
        first, last = blocks[0], blocks[-1]
        c = QtGui.QTextCursor(self.document())
        c.setPosition(first.position())
        c.setPosition(last.position() + last.length() - 1, QtGui.QTextCursor.KeepAnchor)
        return c

    def duplicate_lines(self):
        blocks = self.selected_blocks()
        text = "\n".join(b.text() for b in blocks)
        c = QtGui.QTextCursor(blocks[-1])
        c.movePosition(QtGui.QTextCursor.EndOfBlock)
        c.insertText("\n" + text)
        self.setTextCursor(c)

    def delete_lines(self):
        blocks = self.selected_blocks()
        c = self._blocks_range_cursor(blocks)
        c.beginEditBlock()
        if blocks[-1].next().isValid():
            c.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
        elif blocks[0].previous().isValid():
            start = blocks[0].position() - 1
            end = c.position()
            c.setPosition(start)
            c.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        c.removeSelectedText()
        c.endEditBlock()
        self.setTextCursor(c)

    def move_lines(self, direction):
        """Move the current / selected lines up (-1) or down (+1)."""
        blocks = self.selected_blocks()
        neighbour = blocks[0].previous() if direction < 0 else blocks[-1].next()
        if not neighbour.isValid():
            return
        moving = [b.text() for b in blocks]
        other = neighbour.text()
        cursor = self.textCursor()
        column = cursor.positionInBlock()
        span = [neighbour] + blocks if direction < 0 else blocks + [neighbour]
        new_text = "\n".join(moving + [other] if direction < 0 else [other] + moving)
        first_line = span[0].blockNumber() if direction < 0 else span[0].blockNumber() + 1
        c = self._blocks_range_cursor(span)
        c.beginEditBlock()
        c.insertText(new_text)
        c.endEditBlock()
        first = self.document().findBlockByNumber(first_line)
        last = self.document().findBlockByNumber(first_line + len(moving) - 1)
        sel = QtGui.QTextCursor(self.document())
        if len(moving) > 1:
            sel.setPosition(first.position())
            sel.setPosition(last.position() + last.length() - 1, QtGui.QTextCursor.KeepAnchor)
        else:
            sel.setPosition(first.position() + min(column, len(first.text())))
        self.setTextCursor(sel)

    # ----- Smart typing ---------------------------------------- #
    def _char_at(self, pos):
        if pos < 0 or pos >= self.document().characterCount() - 1:
            return ""
        return self.document().characterAt(pos)

    def _auto_indent_new_line(self):
        cursor = self.textCursor()
        previous = cursor.block().previous()
        if not previous.isValid():
            return
        text = previous.text()
        indent = text[:len(text) - len(text.lstrip(" \t"))]
        stripped = text.rstrip()
        task = textops.TASK_RE.match(text)
        if self.language == "python" and stripped.endswith(":"):
            indent += " " * INDENT_TAB_SIZE
        elif self.code_mode and self.language != "python" and stripped.endswith(("{", "[", "(")):
            indent += " " * INDENT_TAB_SIZE
        elif task and not self.code_mode:
            if not text[task.end():].strip():
                # Enter on an empty task ends the list.
                self._replace_block_text(previous, "")
                return
            indent = task.group(1) + "[ ] "
        if indent:
            cursor.insertText(indent)

    def _wrap_selection(self, cursor, open_ch, close_ch):
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        cursor.beginEditBlock()
        c = QtGui.QTextCursor(self.document())
        c.setPosition(end)
        c.insertText(close_ch)
        c.setPosition(start)
        c.insertText(open_ch)
        cursor.endEditBlock()
        cursor.setPosition(start + 1)
        cursor.setPosition(end + 1, QtGui.QTextCursor.KeepAnchor)
        self.setTextCursor(cursor)

    def _insert_pair(self, cursor, open_ch, close_ch):
        cursor.insertText(open_ch + close_ch)
        cursor.movePosition(QtGui.QTextCursor.Left)
        self.setTextCursor(cursor)

    def _handle_auto_pair(self, text):
        if len(text) != 1:
            return False
        cursor = self.textCursor()
        pos = cursor.position()
        nxt = self._char_at(pos)
        prev = self._char_at(pos - 1)
        next_is_free = (not nxt) or nxt.isspace() or nxt in CLOSING_BRACKETS or nxt in self.PAIR_FOLLOWERS
        if text in BRACKET_PAIRS:
            if cursor.hasSelection():
                self._wrap_selection(cursor, text, BRACKET_PAIRS[text])
                return True
            if next_is_free:
                self._insert_pair(cursor, text, BRACKET_PAIRS[text])
                return True
            return False
        if (text in CLOSING_BRACKETS or text in self.QUOTES) and not cursor.hasSelection() and nxt == text:
            cursor.movePosition(QtGui.QTextCursor.Right)
            self.setTextCursor(cursor)
            return True
        if text in self.QUOTES:
            if cursor.hasSelection():
                self._wrap_selection(cursor, text, text)
                return True
            if prev.isalnum() or prev in ("\\", text) or not next_is_free:
                return False
            self._insert_pair(cursor, text, text)
            return True
        return False

    def _delete_empty_pair(self):
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        pos = cursor.position()
        prev, nxt = self._char_at(pos - 1), self._char_at(pos)
        if (prev in BRACKET_PAIRS and BRACKET_PAIRS[prev] == nxt) or (prev in self.QUOTES and prev == nxt):
            cursor.beginEditBlock()
            cursor.deletePreviousChar()
            cursor.deleteChar()
            cursor.endEditBlock()
            return True
        return False

    def _try_expand_snippet(self):
        if self.snippet_provider is None:
            return False
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        block = cursor.block()
        before = block.text()[:cursor.positionInBlock()]
        trigger = textops.word_before(before)
        if not trigger:
            return False
        snippet = self.snippet_provider(trigger, self.language)
        if not snippet:
            return False
        indent = block.text()[:len(block.text()) - len(block.text().lstrip(" \t"))]
        self.insert_snippet(snippet["body"], replace_chars=len(trigger), indent=indent)
        return True

    def insert_snippet(self, body, replace_chars=0, indent=None):
        cursor = self.textCursor()
        if indent is None:
            text = cursor.block().text()
            indent = text[:len(text) - len(text.lstrip(" \t"))]
        expanded, offset = textops.expand_snippet(body, indent)
        cursor.beginEditBlock()
        if replace_chars:
            cursor.movePosition(QtGui.QTextCursor.Left, QtGui.QTextCursor.KeepAnchor, replace_chars)
        start = cursor.selectionStart() if cursor.hasSelection() else cursor.position()
        cursor.insertText(expanded)
        cursor.endEditBlock()
        cursor.setPosition(start + offset)
        self.setTextCursor(cursor)

    # ----- Multi-cursor ---------------------------------------- #
    def clear_additional_cursors(self):
        if self.additional_cursors:
            self.additional_cursors = []
            self.viewport().update()

    def _handle_multi_cursor_key(self, event):
        key = event.key()
        ctrl, _shift, alt = modifier_state(event)
        if key == QtCore.Qt.Key_Escape:
            self.clear_additional_cursors()
            return True
        text = event.text()
        if key == QtCore.Qt.Key_Backspace:
            def op(c):
                if c.hasSelection():
                    c.removeSelectedText()
                else:
                    c.deletePreviousChar()
        elif key == QtCore.Qt.Key_Delete:
            def op(c):
                if c.hasSelection():
                    c.removeSelectedText()
                else:
                    c.deleteChar()
        elif key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not ctrl:
            def op(c):
                c.insertText("\n")
        elif text and text.isprintable() and not ctrl and not alt:
            def op(c):
                c.insertText(text)
        else:
            self.clear_additional_cursors()
            return False
        main = self.textCursor()
        main.beginEditBlock()
        for c in [main] + self.additional_cursors:
            op(c)
        main.endEditBlock()
        self.setTextCursor(main)
        self.viewport().update()
        return True

    # ----- Autocompletion -------------------------------------- #
    def _attribute_names(self, owner):
        """Public names of a module / class reachable by a dotted path.
        Only modules and classes are traversed (never other objects), so
        no user code runs while completing."""
        if owner in self._attr_cache:
            return self._attr_cache[owner]
        parts = owner.split(".")
        obj = sys.modules.get(parts[0])
        if obj is None:
            import __main__
            obj = getattr(__main__, parts[0], None)
        names = []
        if obj is not None and (inspect.ismodule(obj) or inspect.isclass(obj)):
            for part in parts[1:]:
                obj = getattr(obj, part, None)
                if obj is None or not (inspect.ismodule(obj) or inspect.isclass(obj)):
                    obj = None
                    break
            if obj is not None:
                try:
                    names = sorted(n for n in dir(obj) if not n.startswith("_"))
                except Exception:
                    names = []
        self._attr_cache[owner] = names
        return names

    def _document_words(self):
        revision = self.document().revision()
        if self._words_cache[0] == revision:
            return self._words_cache[1]
        text = self.toPlainText()
        words = set(re.findall(r"\b[A-Za-z_]\w{2,}\b", text[:500000]))
        if self.language == "python":
            words.update(keyword.kwlist)
            words.update(n for n in dir(builtins) if not n.startswith("_"))
        result = sorted(words, key=str.lower)
        self._words_cache = (revision, result)
        return result

    def hide_completions(self):
        self._completer.popup().hide()

    def show_completions(self, force=True, typed=""):
        """Show the completion popup for the word before the cursor."""
        if self.isReadOnly():
            return
        cursor = self.textCursor()
        before = cursor.block().text()[:cursor.positionInBlock()]
        match = re.search(r"((?:[A-Za-z_]\w*\.)*)([A-Za-z_]\w*)?$", before)
        owner = match.group(1)[:-1] if match and match.group(1) else ""
        partial = (match.group(2) or "") if match else ""

        if owner:
            names = self._attribute_names(owner)
        elif force or len(partial) >= 3:
            names = self._document_words()
        else:
            names = []
        if not force and not owner and len(partial) < 3:
            names = []
        if not force and owner and not partial and typed != ".":
            names = []
        if not names:
            self.hide_completions()
            return

        self._completion_model.setStringList(names)
        self._completer.setCompletionPrefix(partial)
        count = self._completer.completionCount()
        if count == 0 or (count == 1 and self._completer.currentCompletion() == partial):
            self.hide_completions()
            return
        popup = self._completer.popup()
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 12)
        self._completer.complete(rect)

    def _insert_completion(self, completion):
        cursor = self.textCursor()
        prefix = len(self._completer.completionPrefix())
        if prefix:
            cursor.movePosition(QtGui.QTextCursor.Left, QtGui.QTextCursor.KeepAnchor, prefix)
        cursor.insertText(completion)
        self.setTextCursor(cursor)

    def _update_completion(self, event):
        if self.language != "python":
            self.hide_completions()
            return
        text = event.text()
        if text and (text.isalnum() or text in "._"):
            self.show_completions(force=False, typed=text)
        elif event.key() != QtCore.Qt.Key_Backspace or not self._completer.popup().isVisible():
            self.hide_completions()
        else:
            self.show_completions(force=False)

    # ----- Links ----------------------------------------------- #
    def link_at_pos(self, pos):
        cursor = self.cursorForPosition(pos)
        if cursor.atBlockEnd() and pos.x() > self.cursorRect(cursor).x() + 8:
            return None  # click in the empty space after the line
        return links.link_at(cursor.block().text(), cursor.positionInBlock(), notes=self.notes_mode)

    # ----- Images ---------------------------------------------- #
    def insert_image(self, path):
        """Insert an image (scaled to the view width) into a rich note."""
        image = QtGui.QImage(path)
        if image.isNull():
            return False
        fmt = QtGui.QTextImageFormat()
        fmt.setName(path.replace("\\", "/"))
        max_width = max(200, self.viewport().width() - 60)
        if image.width() > max_width:
            fmt.setWidth(max_width)
            fmt.setHeight(image.height() * max_width / float(image.width()))
        cursor = self.textCursor()
        cursor.insertImage(fmt)
        cursor.insertText("\n")
        self.setTextCursor(cursor)
        return True

    def insertFromMimeData(self, source):
        if self.rich and source.hasImage() and self.image_saver is not None:
            image = source.imageData()
            if isinstance(image, QtGui.QPixmap):
                image = image.toImage()
            path = self.image_saver(image) if image is not None else None
            if path and self.insert_image(path):
                return
        super().insertFromMimeData(source)

    # ----- Theme, lint marks and hover help --------------------- #
    def apply_theme(self):
        self._completer.popup().setStyleSheet(
            "QListView {{ background: {}; color: {}; border: 1px solid {}; }}"
            "QListView::item:selected {{ background: {}; }}".format(
                themes.color("panel"), themes.color("text"), themes.color("border"), themes.color("word")))
        if hasattr(self, "line_number_area"):
            self.highlighter.refresh_theme()
            self.update_line_number_area()

    def set_lint(self, messages):
        """messages: list of lint.Message (1-based lines)."""
        order = {"error": 0, "warning": 1, "info": 2}
        self.lint_marks = {}
        self.lint_messages = {}
        for message in messages:
            line = message.line - 1
            current = self.lint_marks.get(line)
            if current is None or order[message.severity] < order[current]:
                self.lint_marks[line] = message.severity
            self.lint_messages.setdefault(line, []).append(message.text)
        self.update_line_number_area()

    def _show_hover_help(self):
        pos = self._hover_pos
        if pos is None or not self.underMouse():
            return
        cursor = self.cursorForPosition(pos)
        block_text = cursor.block().text()
        column = cursor.positionInBlock()
        line = cursor.blockNumber()
        if line in self.lint_messages and column <= len(block_text):
            QtWidgets.QToolTip.showText(self.viewport().mapToGlobal(pos),
                                        "\n".join(self.lint_messages[line]), self)
            return
        start = column
        while start > 0 and (block_text[start - 1].isalnum() or block_text[start - 1] in "._"):
            start -= 1
        end = column
        while end < len(block_text) and (block_text[end].isalnum() or block_text[end] == "_"):
            end += 1
        dotted = block_text[start:end].strip(".")
        text = describe_python_name(dotted) if dotted else None
        if text:
            QtWidgets.QToolTip.showText(self.viewport().mapToGlobal(pos), text, self)

    def gutter_tooltip(self, y):
        block = self.cursorForPosition(QtCore.QPoint(0, y)).block()
        messages = self.lint_messages.get(block.blockNumber())
        return "\n".join(messages) if messages else ""

    # ----- Events ---------------------------------------------- #
    def keyPressEvent(self, event):
        key = event.key()
        if self._completer.popup().isVisible() and key in (
                QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return, QtCore.Qt.Key_Escape,
                QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            event.ignore()  # the completer popup handles these
            return
        self._key_press(event)
        self._update_completion(event)

    def _key_press(self, event):
        key = event.key()
        ctrl, shift, alt = modifier_state(event)
        text = event.text()

        if self.additional_cursors and self._handle_multi_cursor_key(event):
            return

        if key == QtCore.Qt.Key_Tab and not ctrl and not alt:
            blocks = self.selected_blocks()
            if len(blocks) > 1:
                self.indent_blocks(blocks)
            elif not self._try_expand_snippet():
                self.textCursor().insertText(" " * INDENT_TAB_SIZE)
            return
        if key == QtCore.Qt.Key_Backtab:
            self.unindent_blocks(self.selected_blocks())
            return

        if self.code_mode and text and not ctrl and not alt and self._handle_auto_pair(text):
            return
        if key == QtCore.Qt.Key_Backspace and self.code_mode and not ctrl and not alt and self._delete_empty_pair():
            return

        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not shift and not ctrl:
            edit = self.textCursor()
            edit.beginEditBlock()
            super().keyPressEvent(event)
            self._auto_indent_new_line()
            edit.endEditBlock()
            return

        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            ctrl, shift, alt = modifier_state(event)
            pos = event_pos(event)
            if ctrl and not shift and not alt:
                link = self.link_at_pos(pos)
                if link is not None:
                    self.link_activated.emit(link[0], link[1])
                    return
            if alt and not ctrl and not shift:
                c = self.cursorForPosition(pos)
                same = [x for x in self.additional_cursors if x.position() == c.position()]
                if same:
                    self.additional_cursors = [x for x in self.additional_cursors if x.position() != c.position()]
                elif c.position() != self.textCursor().position():
                    self.additional_cursors.append(c)
                self.viewport().update()
                return
            self.clear_additional_cursors()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() != QtCore.Qt.LeftButton or self.isReadOnly():
            return
        if any(modifier_state(event)) or self.textCursor().hasSelection():
            return
        cursor = self.cursorForPosition(event_pos(event))
        block = cursor.block()
        index = textops.checkbox_index(block.text(), cursor.positionInBlock())
        if index >= 0:
            c = QtGui.QTextCursor(block)
            c.setPosition(block.position() + index)
            c.setPosition(block.position() + index + 1, QtGui.QTextCursor.KeepAnchor)
            new_char = textops.toggled_checkbox_char(c.selectedText())
            c.insertText(new_char)
            match = textops.TASK_RE.match(block.text())
            task_text = block.text()[match.end():].strip() if match else block.text().strip()
            self.task_toggled.emit(task_text, new_char == "x")

    def mouseMoveEvent(self, event):
        ctrl = modifier_state(event)[0]
        over_link = ctrl and not event.buttons() and self.link_at_pos(event_pos(event)) is not None
        if over_link and not self._hand_cursor:
            self.viewport().setCursor(QtCore.Qt.PointingHandCursor)
            self._hand_cursor = True
        elif not over_link and self._hand_cursor:
            self.viewport().setCursor(QtCore.Qt.IBeamCursor)
            self._hand_cursor = False
        if self.language == "python" and not event.buttons():
            self._hover_pos = event_pos(event)
            self._hover_timer.start()
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        if modifier_state(event)[0]:
            delta = event.angleDelta().y()
            if delta:
                self.zoom_requested.emit(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def paintEvent(self, event):
        """Draw the text, then indent guides and extra cursors."""
        super().paintEvent(event)
        painter = QtGui.QPainter(self.viewport())
        layout = self.document().documentLayout()
        if layout is None:
            painter.end()
            return
        pen = QtGui.QPen(QtGui.QColor(themes.color("guide")))
        pen.setStyle(QtCore.Qt.DotLine)
        painter.setPen(pen)

        scroll_y = self.verticalScrollBar().value()
        scroll_x = self.horizontalScrollBar().value()
        margin = self.document().documentMargin()
        unit = self.fontMetrics().horizontalAdvance(" ") * INDENT_TAB_SIZE
        height = self.viewport().height()
        if self.code_mode:
            block = self._first_visible_block()
            while block.isValid():
                rect = layout.blockBoundingRect(block)
                top = rect.top() - scroll_y
                if top > height:
                    break
                if block.isVisible():
                    levels = self._line_indent(block.text()) // INDENT_TAB_SIZE
                    bottom = rect.bottom() - scroll_y
                    for level in range(levels):
                        x = int(margin + (level + 0.5) * unit - scroll_x)
                        painter.drawLine(x, int(top), x, int(bottom))
                block = block.next()

        if self.additional_cursors:
            caret = QtGui.QColor(themes.color("caret"))
            for c in self.additional_cursors:
                r = self.cursorRect(c)
                painter.fillRect(QtCore.QRect(r.left(), r.top(), 2, r.height()), caret)
        painter.end()
