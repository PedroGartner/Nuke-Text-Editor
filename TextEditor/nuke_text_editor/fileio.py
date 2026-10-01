"""File reading / writing and small helpers. Pure Python (no Qt, no Nuke)."""

import codecs
import hashlib
import html
import io
import os
import re
import shutil
import sys
import tempfile

EXTENSION_LANGUAGE = {
    ".py": "python",
    ".json": "json",
    ".md": "markdown",
    ".mkd": "markdown",
    ".markdown": "markdown",
    ".nk": "nuke",
    ".gizmo": "nuke",
    ".ini": "ini",
    ".cfg": "ini",
    ".html": "html",
    ".htm": "html",
    ".xml": "html",
    ".js": "javascript",
    ".ts": "javascript",
    ".css": "css",
    ".c": "c_like",
    ".h": "c_like",
    ".cpp": "c_like",
    ".hpp": "c_like",
    ".java": "c_like",
    ".sh": "bash",
    ".bat": "batch",
    ".cmd": "batch",
    ".log": "log",
    ".diff": "diff",
    ".patch": "diff",
}

# Files with these extensions keep their formatting (stored as HTML).
RICH_EXTENSIONS = (".tnote",)

# Languages whose documents are "notes" (links to frames / nodes are active).
NOTE_LANGUAGES = ("text", "markdown")


# ------------------------------------------------------------- #
#  Small helpers                                                #
# ------------------------------------------------------------- #
def to_bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def to_int(value, default):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_list(value):
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [str(value)]


def norm_path(path):
    return os.path.normcase(os.path.abspath(path))


def path_key(path):
    """Stable short key for a file path (used for per-file storage)."""
    return hashlib.sha1(norm_path(path).encode("utf-8")).hexdigest()


def is_rich_path(path):
    return bool(path) and os.path.splitext(path)[1].lower() in RICH_EXTENSIONS


def language_for_path(path):
    if not path:
        return "text"
    return EXTENSION_LANGUAGE.get(os.path.splitext(path)[1].lower(), "text")


def data_dir(*parts):
    """Folder for editor data (bookmarks, recovery, history, recipes...)."""
    root = os.environ.get("NUKE_TEXT_EDITOR_DATA") or os.path.join(
        os.path.expanduser("~"), ".nuke", "text_editor")
    path = os.path.join(root, *parts)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def pid_alive(pid):
    """True if a process with this id is running (used for crash recovery)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform.startswith("win"):
        try:
            import ctypes
            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not process:
                return False
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(process)
            return exit_code.value == 259  # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# ------------------------------------------------------------- #
#  Reading / writing                                            #
# ------------------------------------------------------------- #
def read_text_file(path):
    """Read a text file.

    Returns (text, encoding, newline). Text always uses '\\n' line
    endings; the original encoding and line ending are returned so the
    file can be written back the same way.
    """
    with io.open(path, "rb") as handle:
        raw = handle.read()

    if raw.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
        text = raw.decode(encoding)
    else:
        try:
            encoding = "utf-8"
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            try:
                encoding = "cp1252"
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                encoding = "latin-1"  # never fails
                text = raw.decode(encoding)

    if "\r\n" in text:
        newline = "\r\n"
    elif "\r" in text:
        newline = "\r"
    else:
        newline = "\n"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, encoding, newline


def write_text_file(path, text, encoding="utf-8", newline="\n"):
    """Write a text file safely (temp file + rename).

    A crash or full disk in the middle of a save cannot leave a
    half-written file behind. Returns the encoding used (falls back to
    UTF-8 if the text cannot be stored in the original encoding).
    """
    if newline != "\n":
        text = text.replace("\n", newline)
    try:
        data = text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        encoding = "utf-8"
        data = text.encode(encoding)

    target = os.path.realpath(path)
    directory = os.path.dirname(target) or "."
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".~" + os.path.basename(target) + ".",
                                        suffix=".tmp", dir=directory)
    except OSError:
        # No permission to create files in the folder: write in place.
        with io.open(target, "wb") as handle:
            handle.write(data)
        return encoding

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(target):
            try:
                shutil.copymode(target, tmp_path)
            except OSError:
                pass
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return encoding


def file_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# ------------------------------------------------------------- #
#  HTML helpers for rich notes                                  #
# ------------------------------------------------------------- #
def strip_body_font(html_text):
    """Remove the editor's base font from the <body> style of saved HTML,
    so rich notes follow the current zoom level when reopened."""
    def _fix(match):
        style = re.sub(r"\s*font-(?:family|size)\s*:[^;]*;", "", match.group(1))
        return '<body style="{}">'.format(style)
    return re.sub(r'<body style="([^"]*)">', _fix, html_text, count=1)


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_END_RE = re.compile(r"</(p|div|li|h[1-6]|tr)>|<br\s*/?>", re.IGNORECASE)


def html_to_text(html_text):
    """Very small HTML to plain text conversion (for searching notes)."""
    body = html_text
    match = re.search(r"<body[^>]*>(.*)</body>", html_text, re.IGNORECASE | re.DOTALL)
    if match:
        body = match.group(1)
    body = body.replace("\n", " ")
    body = _BLOCK_END_RE.sub("\n", body)
    body = _TAG_RE.sub("", body)
    return html.unescape(body).replace("\u00a0", " ")


_IMG_RE = re.compile(r'(<img\b[^>]*\bsrc=")([^"]+)(")', re.IGNORECASE)


def relativize_images(html_text, note_path, base_dir=None):
    """Copy images into '<note>_files/' next to the note and point the
    HTML at those copies, so a .tnote can be moved or shared together
    with its images folder. Relative sources are resolved against
    `base_dir` (the folder the note was loaded from)."""
    note_dir = os.path.dirname(os.path.abspath(note_path))
    assets_name = os.path.splitext(os.path.basename(note_path))[0] + "_files"
    assets_dir = os.path.join(note_dir, assets_name)

    def _fix(match):
        src = html.unescape(match.group(2))
        local = src[7:] if src.startswith("file://") else src
        if local.startswith("/") and len(local) > 2 and local[2] == ":":
            local = local[1:]  # file:///C:/...
        if not os.path.isabs(local):
            if not base_dir:
                return match.group(0)
            local = os.path.normpath(os.path.join(base_dir, local))
        if not os.path.isfile(local):
            return match.group(0)
        if norm_path(os.path.dirname(local)) == norm_path(assets_dir):
            name = os.path.basename(local)
        else:
            try:
                os.makedirs(assets_dir, exist_ok=True)
                name = os.path.basename(local)
                target = os.path.join(assets_dir, name)
                if not os.path.exists(target):
                    shutil.copy2(local, target)
            except OSError:
                return match.group(0)
        return match.group(1) + html.escape(assets_name + "/" + name, quote=True) + match.group(3)

    return _IMG_RE.sub(_fix, html_text)
