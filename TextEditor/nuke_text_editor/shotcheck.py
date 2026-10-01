"""Shot checks: newer plate versions, pre-render check of Write nodes and
the version history kept in the shot notes. Pure Python.

The Nuke side (nuke_bridge) collects plain dicts; everything here works
on those dicts so it is testable without Nuke.
"""

import os
import re
import time

from . import nkparse
from . import textops
from .scripttools import Issue, SEVERITY_ORDER

# v003, V12, _v0042 ... A version token is not part of a longer word.
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([vV])(\d+)(?![A-Za-z0-9])")

TIMING_CLASSES = ("TimeOffset", "Retime", "FrameRange", "FrameHold", "TimeWarp", "OFlow", "OFlow2",
                  "Kronos", "AppendClip", "TimeClip", "TimeEcho", "TimeBlur")

HISTORY_HEADING = "Version history"
HISTORY_ENTRY_RE = re.compile(r"^\s*(v\d+|\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)

_FILE_TYPE_ALIASES = {"jpg": "jpeg", "tif": "tiff", "tga": "targa", "mp4": "mov", "m4v": "mov",
                      "mov64": "mov", "ffmpeg": "mov", "sgi": "sgi", "rgb": "sgi"}
_DEFAULT_CS_RE = re.compile(r"^default\s*\((.*)\)$", re.IGNORECASE)


# ------------------------------------------------------------- #
#  Version tokens                                               #
# ------------------------------------------------------------- #
def last_version_token(path):
    """('v003', 3) for the last version token in a path, or (None, None)."""
    matches = list(TOKEN_RE.finditer(path or ""))
    if not matches:
        return None, None
    match = matches[-1]
    return match.group(0), int(match.group(2))


def _split_parts(path):
    """Path components, keeping the separators so the path can be rebuilt."""
    return re.split(r"([\\/]+)", path)


_FRAME_RE = re.compile(r"(%0?\d*d|#+|@+)")


def _literal_regex(text):
    """Regex for a piece of a file name where frame patterns (%04d, ####)
    stand for frame numbers."""
    pieces = _FRAME_RE.split(text)
    return "".join(r"-?\d+" if i % 2 else re.escape(piece) for i, piece in enumerate(pieces))


def _same_padding(current, other):
    """v003 continues as v004 ... v999, v1000 - not as v0030 or v4."""
    if len(other) == len(current):
        return True
    padded = len(current) > 1 and current.startswith("0")
    if not padded:
        return not other.startswith("0")
    return len(other) > len(current) and not other.startswith("0")


def _is_checkable(path):
    return bool(path) and "[" not in path and "$" not in path


def _default_exists(path):
    return nkparse.path_status(path) == "ok"


def _default_listdir(folder):
    try:
        return os.listdir(folder)
    except OSError:
        return []


def newer_versions(path, exists=_default_exists, listdir=_default_listdir):
    """Newer versions of a versioned file path that exist on disk,
    newest first, as [(tag, path)].

    The version folder or file name is looked up in its parent folder
    (/plates/sh010/v003/sh010_v003.%04d.exr -> v004, v005 in /plates/sh010),
    and every occurrence of the old tag in the path is replaced.
    """
    if not _is_checkable(path):
        return []
    token, number = last_version_token(path)
    if token is None or number is None:
        return []
    parts = _split_parts(path)
    index = next((i for i, part in enumerate(parts)
                  if i % 2 == 0 and any(m.group(0) == token for m in TOKEN_RE.finditer(part))), None)
    if index is None:
        return []
    component = parts[index]
    parent = "".join(parts[:index - 1]) if index >= 2 else ""
    if not parent or parent.endswith(":"):  # file system root or a drive ('E:' alone is the cwd of E)
        parent = "".join(parts[:index]) or "."
    token_match = next(m for m in TOKEN_RE.finditer(component) if m.group(0) == token)
    pattern = re.compile("^{}([vV])(\\d+){}$".format(_literal_regex(component[:token_match.start()]),
                                                     _literal_regex(component[token_match.end():])))
    found = {}
    tried = set()
    for entry in listdir(parent):
        match = pattern.match(entry)
        if not match:
            continue
        digits = match.group(2)
        other = int(digits)
        tag = match.group(1) + digits
        if other <= number or tag in tried or not _same_padding(token[1:], digits):
            continue
        tried.add(tag)
        candidate = replace_version_in(path, token, tag)
        if exists(candidate):
            found[other] = (tag, candidate)
    return [found[n] for n in sorted(found, reverse=True)]


def plate_updates(reads, exists=_default_exists, listdir=_default_listdir):
    """For read dicts {'name', 'file'}: [(read, current_tag, newest_tag, newest_path)]
    for every Read with a newer version on disk. Folders are listed once."""
    cache = {}

    def cached_listdir(folder):
        if folder not in cache:
            cache[folder] = listdir(folder)
        return cache[folder]

    result = []
    for read in reads:
        path = read.get("file") or ""
        versions = newer_versions(path, exists, cached_listdir)
        if versions:
            token, _n = last_version_token(path)
            result.append((read, token, versions[0][0], versions[0][1]))
    return result


def next_version_path(path, exists=os.path.exists):
    """sh010_comp_v012.nk -> sh010_comp_v013.nk (the next one that does not
    exist yet, keeping the zero padding). None if the file has no version."""
    folder, filename = os.path.split(path)
    matches = list(TOKEN_RE.finditer(filename))
    if not matches:
        return None
    match = matches[-1]
    prefix, digits = match.group(1), match.group(2)
    number = int(digits)
    for _ in range(1000):
        number += 1
        tag = "{}{:0{}d}".format(prefix, number, len(digits))
        candidate = os.path.join(folder, filename[:match.start()] + tag + filename[match.end():])
        if not exists(candidate):
            return candidate
    return None


def replace_version_in(text, old_tag, new_tag):
    """Replace a whole version token (not v0120 when looking for v012)."""
    if not old_tag or not text:
        return text
    pattern = re.compile(r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])".format(re.escape(old_tag)))
    return pattern.sub(new_tag, text)


# ------------------------------------------------------------- #
#  Pre-render check                                             #
# ------------------------------------------------------------- #
def _norm_file_type(value):
    value = (value or "").strip().lower().lstrip(".")
    return _FILE_TYPE_ALIASES.get(value, value)


def norm_colorspace(value):
    value = (value or "").strip()
    match = _DEFAULT_CS_RE.match(value)
    if match:
        value = match.group(1)
    return value.strip().lower()


def read_range(read):
    """Output frame range of a Read dict (after its frame mode), or None."""
    try:
        first, last = int(read["first"]), int(read["last"])
    except (KeyError, TypeError, ValueError):
        return None
    mode = str(read.get("frame_mode") or "").lower()
    value = str(read.get("frame") or "").strip()
    if value and mode in ("start at", "start_at"):
        try:
            start = int(float(value))
            return start, start + (last - first)
        except ValueError:
            return None
    if value and mode == "offset":
        try:
            offset = int(float(value))
            return first + offset, last + offset
        except ValueError:
            return None
    if value and mode == "expression":
        return None
    return first, last


def render_range(write, root_first, root_last):
    first, last = root_first, root_last
    if write.get("use_limit"):
        try:
            first = max(first, int(write["first"]))
            last = min(last, int(write["last"]))
        except (KeyError, TypeError, ValueError):
            pass
    return first, last


def _plate_read(reads):
    """The main plate: the upstream Read covering the most frames."""
    best, best_len = None, -1
    for read in reads:
        span = read_range(read)
        length = (span[1] - span[0]) if span else -1
        if length > best_len:
            best, best_len = read, length
    return best


def check_write(write, root, upstream, open_tasks=(), expect=None, exists=os.path.exists,
                path_status=nkparse.path_status):
    """Problems to know about before rendering a Write node.

    write:    {'name', 'file', 'file_type', 'colorspace', 'use_limit', 'first', 'last',
               'create_directories' (None if the knob does not exist), 'has_error'}
    root:     {'first', 'last', 'proxy', 'script'}
    upstream: {'reads': [read dicts], 'disabled': [names], 'timing': [names], 'errors': [names]}
    open_tasks: open task texts from the shot notes
    expect:   {'colorspace': '', 'file_type': ''} studio expectations (empty = not checked)
    """
    expect = expect or {}
    name = write.get("name", "Write")
    issues = []
    path = (write.get("file") or "").strip()
    script = root.get("script") or ""
    script_dir = os.path.dirname(script) if script else None

    if write.get("has_error"):
        issues.append(Issue(name, "error", "The Write node has an error"))
    if not path:
        issues.append(Issue(name, "error", "No output path"))
    else:
        # File type against the extension, and against the studio default.
        ext = _norm_file_type(os.path.splitext(path)[1])
        file_type = _norm_file_type(write.get("file_type"))
        if ext and file_type and not (ext.startswith(file_type) or file_type.startswith(ext)):
            issues.append(Issue(name, "error", "File type is '{}' but the path ends in '{}'".format(
                write.get("file_type"), os.path.splitext(path)[1])))
        wanted = _norm_file_type(expect.get("file_type"))
        if wanted and (file_type or ext) != wanted:
            issues.append(Issue(name, "warning", "Renders '{}', expected '{}'".format(
                write.get("file_type") or ext, expect.get("file_type"))))

        # Output version against the script version.
        script_tag, script_number = last_version_token(os.path.basename(script))
        out_tag, out_number = last_version_token(path)
        if script_number is not None and out_number is not None and out_number != script_number:
            issues.append(Issue(name, "warning", "Output is {} but the script is {}".format(out_tag, script_tag)))

        # Output folder and existing frames.
        folder = os.path.dirname(path)
        if _is_checkable(path) and folder and not exists(folder):
            if write.get("create_directories") is False:
                issues.append(Issue(name, "error", "Output folder does not exist (and 'create directories' is off)"))
            elif write.get("create_directories") is None:
                issues.append(Issue(name, "warning", "Output folder does not exist yet"))
        elif _is_checkable(path) and path_status(path, script_dir) == "ok":
            issues.append(Issue(name, "info", "Output already has files; they will be overwritten"))

    if root.get("proxy"):
        issues.append(Issue(name, "warning", "Proxy mode is on"))

    # Frame range against the plates.
    reads = [r for r in upstream.get("reads", []) if not r.get("disable")]
    first, last = render_range(write, int(root.get("first", 1)), int(root.get("last", 1)))
    spans = [s for s in (read_range(r) for r in reads) if s]
    if spans:
        plate_first = min(s[0] for s in spans)
        plate_last = max(s[1] for s in spans)
        timing = upstream.get("timing") or []
        hint = " (timing changed by {})".format(", ".join(timing[:3])) if timing else ""
        if first < plate_first or last > plate_last:
            issues.append(Issue(name, "info" if timing else "warning",
                                "Renders {}-{} but the plates cover {}-{}{}".format(
                                    first, last, plate_first, plate_last, hint)))
        elif (first, last) != (plate_first, plate_last):
            issues.append(Issue(name, "info", "Renders {}-{} of the plate range {}-{}{}".format(
                first, last, plate_first, plate_last, hint)))

    # Colorspace against the studio default, or else against the plate.
    write_cs = norm_colorspace(write.get("colorspace"))
    wanted_cs = norm_colorspace(expect.get("colorspace"))
    if write_cs and wanted_cs and write_cs != wanted_cs:
        issues.append(Issue(name, "warning", "Colorspace is '{}', expected '{}'".format(
            write.get("colorspace"), expect.get("colorspace"))))
    elif write_cs and not wanted_cs:
        plate = _plate_read(reads)
        plate_cs = norm_colorspace(plate.get("colorspace")) if plate else ""
        if plate_cs and plate_cs != write_cs:
            issues.append(Issue(name, "info", "Colorspace '{}' differs from the plate {} ('{}')".format(
                write.get("colorspace"), plate.get("name"), plate.get("colorspace"))))

    # Upstream problems.
    for read in reads:
        read_path = read.get("file") or ""
        if _is_checkable(read_path) and path_status(read_path, script_dir) == "missing":
            issues.append(Issue(read.get("name", "Read"), "error", "Missing file: {}".format(read_path)))
    for node in upstream.get("errors", []):
        issues.append(Issue(node, "error", "Node has an error"))
    disabled = upstream.get("disabled", [])
    for node in disabled[:10]:
        issues.append(Issue(node, "warning", "Disabled node in the tree of {}".format(name)))
    if len(disabled) > 10:
        issues.append(Issue(name, "warning", "... and {} more disabled nodes".format(len(disabled) - 10)))

    tasks = [t for t in open_tasks if t.strip()]
    if tasks:
        shown = "; ".join(t[:40] for t in tasks[:3]) + (" ..." if len(tasks) > 3 else "")
        issues.append(Issue(name, "warning", "{} open task{} in the shot notes: {}".format(
            len(tasks), "" if len(tasks) == 1 else "s", shown)))

    issues.sort(key=lambda i: SEVERITY_ORDER.get(i.severity, 9))
    return issues


def blocking(issues):
    """True if any issue should stop a render until confirmed."""
    return any(i.severity in ("error", "warning") for i in issues)


def open_tasks_in(text):
    return [item for _line, kind, item in textops.open_items(text) if kind == "task"]


# ------------------------------------------------------------- #
#  Version history in the shot notes                            #
# ------------------------------------------------------------- #
def history_entry(tag, note, user="", when=None):
    """'v013 · 2026-09-30 · pedro - fixed edge chatter'"""
    day = time.strftime("%Y-%m-%d", time.localtime(when or time.time()))
    parts = [p for p in (tag, day, user) if p]
    text = " · ".join(parts)
    note = " ".join((note or "").split())
    return "{} - {}".format(text, note) if note else text


def history_insert_index(blocks):
    """Where to add an entry in a list of paragraph texts.

    Returns (index, needs_heading): insert before `index`; when the notes
    have no 'Version history' heading yet, one is added at the end."""
    heading = None
    for i, text in enumerate(blocks):
        if text.strip().rstrip(":").strip().lower() == HISTORY_HEADING.lower():
            heading = i
    if heading is None:
        return len(blocks), True
    index = heading + 1
    while index < len(blocks) and HISTORY_ENTRY_RE.match(blocks[index]):
        index += 1
    return index, False
