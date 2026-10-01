"""Small parser for Nuke .nk scripts. Pure Python.

Used for the script outline (nodes + file paths) and for comparing two
versions of a script. It does not evaluate anything; it only reads the
brace structure Nuke writes.
"""

import glob
import os
import re

GROUP_CLASSES = ("Group", "LiveGroup")
FILE_KNOBS = ("file", "proxy", "vfield_file")
DEFAULT_IGNORED_KNOBS = ("xpos", "ypos", "selected", "note_font_size", "gl_color")

_HEADER_RE = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*\{\s*$")


class NkNode(object):
    __slots__ = ("cls", "name", "knobs", "line", "group")

    def __init__(self, cls, line, group):
        self.cls = cls
        self.name = ""
        self.knobs = {}
        self.line = line
        self.group = group

    @property
    def full_name(self):
        return self.group + "." + self.name if self.group else self.name

    def __repr__(self):
        return "NkNode({} {})".format(self.cls, self.full_name)


def _brace_delta(line):
    """Net count of unescaped braces on a line."""
    depth = 0
    escaped = False
    for ch in line:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth


def _clean_value(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == "{" and value[-1] == "}" and _brace_delta(value[1:-1]) == 0:
        value = value[1:-1].strip()
    elif len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value


def parse_nk(text):
    """Return a list of NkNode in file order."""
    lines = text.split("\n")
    nodes = []
    groups = []
    counters = {}
    i = 0
    total = len(lines)
    while i < total:
        line = lines[i]
        stripped = line.strip()
        header = _HEADER_RE.match(line)
        if header:
            node = NkNode(header.group(1), i, ".".join(groups))
            i += 1
            # Knob statements until the closing brace of the node.
            while i < total:
                statement = lines[i]
                if statement.strip() == "}":
                    i += 1
                    break
                depth = _brace_delta(statement)
                i += 1
                while depth > 0 and i < total:
                    statement += "\n" + lines[i]
                    depth += _brace_delta(lines[i])
                    i += 1
                content = statement.strip()
                if content:
                    parts = content.split(None, 1)
                    node.knobs[parts[0]] = _clean_value(parts[1] if len(parts) > 1 else "")
            # Root's "name" knob is the script path; keep the node name stable.
            name = "Root" if node.cls == "Root" else node.knobs.get("name")
            if not name:
                counters[node.cls] = counters.get(node.cls, 0) + 1
                name = node.cls if node.cls == "Root" else "{}#{}".format(node.cls, counters[node.cls])
            node.name = name
            nodes.append(node)
            if node.cls in GROUP_CLASSES:
                groups.append(node.name)
            continue

        if stripped == "end_group":
            if groups:
                groups.pop()
            i += 1
            continue

        # Any other top-level statement (push, set, version, define_window_layout_xml...)
        depth = _brace_delta(line)
        i += 1
        while depth > 0 and i < total:
            depth += _brace_delta(lines[i])
            i += 1
    return nodes


def compare_nodes(old_nodes, new_nodes, ignored=DEFAULT_IGNORED_KNOBS):
    """Compare two parsed scripts by node name.

    Returns dict with 'added', 'removed' (lists of NkNode) and 'changed'
    (list of (NkNode_new, [(knob, old, new)])).
    """
    old_map = dict((n.full_name, n) for n in old_nodes)
    new_map = dict((n.full_name, n) for n in new_nodes)
    added = [n for n in new_nodes if n.full_name not in old_map]
    removed = [n for n in old_nodes if n.full_name not in new_map]
    changed = []
    for node in new_nodes:
        old = old_map.get(node.full_name)
        if old is None:
            continue
        diffs = []
        if old.cls != node.cls:
            diffs.append(("<class>", old.cls, node.cls))
        for knob in sorted(set(old.knobs) | set(node.knobs)):
            if knob in ignored:
                continue
            a, b = old.knobs.get(knob), node.knobs.get(knob)
            if a != b:
                diffs.append((knob, a, b))
        if diffs:
            changed.append((node, diffs))
    return {"added": added, "removed": removed, "changed": changed}


def file_paths(nodes):
    """Return [(node, knob, path)] for knobs that hold file paths."""
    result = []
    for node in nodes:
        for knob in FILE_KNOBS:
            value = node.knobs.get(knob)
            if value:
                result.append((node, knob, value))
    return result


_PRINTF_RE = re.compile(r"%0?(\d*)d")
_HASH_RE = re.compile(r"#+")


def path_status(path, script_dir=None):
    """'ok', 'missing' or 'expression' (not checkable) for a Read/Write path.
    Frame patterns (%04d, ####) count as ok if any frame exists."""
    if "[" in path or "$" in path:
        return "expression"
    candidate = os.path.expanduser(path)
    if script_dir and not os.path.isabs(candidate):
        candidate = os.path.join(script_dir, candidate)
    if os.path.exists(candidate):
        return "ok"
    if "%" in candidate or "#" in candidate:
        pattern = _PRINTF_RE.sub("*", candidate)
        pattern = _HASH_RE.sub("*", pattern)
        pattern = glob.escape(os.path.dirname(pattern)) + os.sep + os.path.basename(pattern)
        return "ok" if glob.glob(pattern) else "missing"
    return "missing"
