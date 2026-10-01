"""Text operations that do not need Qt: tasks, comments, versions,
fuzzy matching and snippet expansion."""

import os
import re

# ------------------------------------------------------------- #
#  Task checkboxes: "- [ ] todo" / "- [x] done"                 #
# ------------------------------------------------------------- #
TASK_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)\[( |x|X)\]")
TODO_RE = re.compile(r"\b(TODO|FIXME)\b[:\s]?(.*)")


def checkbox_index(text, column):
    """If `column` is on the [ ] box of a task line, return the index of
    the character inside the box, else -1."""
    match = TASK_RE.match(text)
    if not match:
        return -1
    box_start = match.start(2) - 1
    box_end = match.end(2) + 1
    if box_start <= column <= box_end:
        return match.start(2)
    return -1


def toggled_checkbox_char(char):
    return " " if char in ("x", "X") else "x"


def count_tasks(text):
    """Return (done, total) for task lines in the text."""
    done = total = 0
    for line in text.split("\n"):
        match = TASK_RE.match(line)
        if match:
            total += 1
            if match.group(2) in ("x", "X"):
                done += 1
    return done, total


def open_items(text):
    """Return [(line_number, kind, text)] for unchecked tasks and TODO / FIXME."""
    items = []
    for number, line in enumerate(text.split("\n")):
        match = TASK_RE.match(line)
        if match and match.group(2) == " ":
            items.append((number, "task", line[match.end():].strip()))
            continue
        todo = TODO_RE.search(line)
        if todo:
            items.append((number, todo.group(1), todo.group(2).strip() or line.strip()))
    return items


# ------------------------------------------------------------- #
#  Comments                                                     #
# ------------------------------------------------------------- #
COMMENT_PREFIX = {
    "python": "#",
    "nuke": "#",
    "bash": "#",
    "ini": "#",
    "c_like": "//",
    "javascript": "//",
    "batch": "REM",
    "markdown": "<!--",
}


def toggle_comment_lines(lines, prefix):
    """Comment or uncomment a list of lines with a line-comment prefix.

    If every non-empty line is already commented, all are uncommented;
    otherwise all are commented at the smallest indentation.
    """
    if not prefix:
        return list(lines)
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return list(lines)

    def _is_commented(line):
        return line.lstrip().startswith(prefix)

    if all(_is_commented(line) for line in non_empty):
        result = []
        for line in lines:
            if not line.strip():
                result.append(line)
                continue
            indent = len(line) - len(line.lstrip())
            rest = line[indent + len(prefix):]
            if rest.startswith(" "):
                rest = rest[1:]
            result.append(line[:indent] + rest)
        return result

    min_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
    return [line if not line.strip() else line[:min_indent] + prefix + " " + line[min_indent:]
            for line in lines]


# ------------------------------------------------------------- #
#  Script versions (sh010_comp_v012.nk)                          #
# ------------------------------------------------------------- #
VERSION_RE = re.compile(r"[._-]?v(\d+)(?=[._-]|$)", re.IGNORECASE)


def split_version(name):
    """'sh010_comp_v012' -> ('sh010_comp', 'v012', 12). No version -> (name, '', None)."""
    matches = list(VERSION_RE.finditer(name))
    if not matches:
        return name, "", None
    match = matches[-1]
    base = (name[:match.start()] + name[match.end():]).rstrip("._-")
    return base, "v" + match.group(1), int(match.group(1))


def previous_version_path(path):
    """Find the closest lower version of a versioned file in the same folder."""
    folder, filename = os.path.split(path)
    stem, ext = os.path.splitext(filename)
    base, _tag, number = split_version(stem)
    if number is None:
        return None
    best = None
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return None
    for other in names:
        other_stem, other_ext = os.path.splitext(other)
        if other_ext.lower() != ext.lower():
            continue
        other_base, _t, other_number = split_version(other_stem)
        if other_base == base and other_number is not None and other_number < number:
            if best is None or other_number > best[0]:
                best = (other_number, os.path.join(folder, other))
    return best[1] if best else None


def shot_notes_path(script_path):
    """Notes file shared by all versions of a script:
    /shots/sh010/sh010_comp_v012.nk -> /shots/sh010/sh010_comp_notes.tnote"""
    folder, filename = os.path.split(script_path)
    base, _tag, _n = split_version(os.path.splitext(filename)[0])
    return os.path.join(folder, base + "_notes.tnote")


# ------------------------------------------------------------- #
#  Fuzzy matching (command palette)                             #
# ------------------------------------------------------------- #
def fuzzy_score(query, text):
    """Score how well `query` matches `text` as a subsequence.
    Returns None when it does not match; higher is better."""
    if not query:
        return 0
    q = query.lower()
    t = text.lower()
    if q in t:
        index = t.index(q)
        return 1000 - index * 2 - (len(t) - len(q)) * 0.1
    score = 0
    position = 0
    previous = -2
    for ch in q:
        if ch == " ":
            continue
        found = t.find(ch, position)
        if found < 0:
            return None
        score += 10 if found == previous + 1 else 1
        if found == 0 or t[found - 1] in " /_-.>":
            score += 5
        previous = found
        position = found + 1
    return score - len(t) * 0.05


# ------------------------------------------------------------- #
#  Snippets                                                     #
# ------------------------------------------------------------- #
def expand_snippet(body, indent=""):
    """Return (text, cursor_offset). '$0' marks where the cursor goes;
    following lines get the current line's indentation."""
    lines = body.split("\n")
    text = "\n".join([lines[0]] + [indent + line for line in lines[1:]])
    offset = text.find("$0")
    if offset < 0:
        return text, len(text)
    return text.replace("$0", "", 1), offset


def word_before(text):
    """The identifier-like word at the end of text (snippet triggers)."""
    match = re.search(r"([A-Za-z_][\w-]*)$", text)
    return match.group(1) if match else ""


# ------------------------------------------------------------- #
#  Task details: @people and due dates                          #
# ------------------------------------------------------------- #
PERSON_RE = re.compile(r"(?<![\w@])@([A-Za-z][\w.-]*[\w])")
DUE_RE = re.compile(r"\bdue:(\S+)", re.IGNORECASE)
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_due(token, today):
    """'today', 'tomorrow', a weekday ('fri', 'friday' = next one, today
    included), 'YYYY-MM-DD' or 'DD/MM' / 'DD/MM/YYYY'. Returns a date or None."""
    import datetime
    token = token.strip().lower().rstrip(".,;)")
    if token == "today":
        return today
    if token == "tomorrow":
        return today + datetime.timedelta(days=1)
    for index, name in enumerate(_WEEKDAYS):
        if len(token) >= 3 and name.startswith(token):
            return today + datetime.timedelta(days=(index - today.weekday()) % 7)
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", token)
    try:
        if match:
            return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        match = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$", token)
        if match:
            year = int(match.group(3)) if match.group(3) else today.year
            if year < 100:
                year += 2000
            return datetime.date(year, int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None
    return None


def task_meta(text, today):
    """Return (people, due_date) found in a task's text."""
    people = PERSON_RE.findall(text)
    due = None
    match = DUE_RE.search(text)
    if match:
        due = parse_due(match.group(1), today)
    return people, due
