"""Note templates with shot tokens. Pure Python.

Templates are plain files in ~/.nuke/text_editor/templates. Tokens such
as {shot} or {date} are replaced when a template is used; unknown tokens
are left untouched.
"""

import getpass
import io
import os
import re
import time

from . import fileio
from . import textops

TOKEN_RE = re.compile(r"\{([a-z_]+)\}")

TOKENS_HELP = [
    ("{script}", "script file name"),
    ("{script_path}", "full script path"),
    ("{shot}", "script name without version"),
    ("{version}", "script version, e.g. v012"),
    ("{frame}", "current frame"),
    ("{first_frame}", "first frame of the script"),
    ("{last_frame}", "last frame of the script"),
    ("{format}", "script format"),
    ("{user}", "user name"),
    ("{date}", "today, YYYY-MM-DD"),
    ("{time}", "current time, HH:MM"),
]

DEFAULT_TEMPLATES = {
    "Daily Notes.tnote": (
        "<h2>Dailies {date} - {shot} {version}</h2>"
        "<p><b>Artist:</b> {user}</p>"
        "<p><b>Notes:</b></p>"
        "<p>- [ ] </p>"
        "<p><b>Frames to check:</b> f{frame}</p>"
    ),
    "Shot Checklist.txt": (
        "{shot} - checklist ({date})\n"
        "\n"
        "- [ ] Plate prep / cleanup\n"
        "- [ ] Roto\n"
        "- [ ] Keying\n"
        "- [ ] Tracking\n"
        "- [ ] Grain match\n"
        "- [ ] Edge check\n"
        "- [ ] Tech check (format {format}, frames {first_frame}-{last_frame})\n"
        "- [ ] Render and review\n"
    ),
    "Client Feedback.txt": (
        "Client feedback - {shot} {version}\n"
        "Date: {date} {time}\n"
        "Reviewed by: \n"
        "\n"
        "Notes:\n"
        "- [ ] \n"
        "\n"
        "Approved: no\n"
    ),
    "Shot Notes.tnote": (
        "<h2>{shot}</h2>"
        "<p><b>Script:</b> {script_path}</p>"
        "<p><b>Frames:</b> {first_frame}-{last_frame} ({format})</p>"
        "<p><b>Tasks</b></p><p>- [ ] </p>"
        "<p><b>Log</b></p><p>{date}: </p>"
    ),
}


def templates_dir():
    return fileio.data_dir("templates")


def ensure_default_templates(folder=None):
    """Create the default templates the first time."""
    folder = folder or templates_dir()
    marker = os.path.join(folder, ".defaults_created")
    if os.path.exists(marker):
        return
    for name, content in DEFAULT_TEMPLATES.items():
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            try:
                fileio.write_text_file(path, content)
            except OSError:
                pass
    try:
        io.open(marker, "w").close()
    except OSError:
        pass


def list_templates(folder=None):
    """Return [(display_name, path)] sorted by name."""
    folder = folder or templates_dir()
    result = []
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return result
    for name in names:
        path = os.path.join(folder, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue
        result.append((os.path.splitext(name)[0], path))
    return result


def build_context(script_path=None, frame=None, first_frame=None, last_frame=None, fmt=None):
    """Token values. Missing Nuke values become empty strings."""
    now = time.localtime()
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    context = {
        "script": "", "script_path": "", "shot": "", "version": "",
        "frame": "" if frame is None else str(frame),
        "first_frame": "" if first_frame is None else str(first_frame),
        "last_frame": "" if last_frame is None else str(last_frame),
        "format": fmt or "",
        "user": user,
        "date": time.strftime("%Y-%m-%d", now),
        "time": time.strftime("%H:%M", now),
    }
    if script_path:
        filename = os.path.basename(script_path)
        base, version, _number = textops.split_version(os.path.splitext(filename)[0])
        context.update({"script": filename, "script_path": script_path, "shot": base, "version": version})
    return context


def fill_tokens(text, context):
    """Replace {token} with context values; unknown tokens stay as they are."""
    def _replace(match):
        key = match.group(1)
        return str(context[key]) if key in context else match.group(0)
    return TOKEN_RE.sub(_replace, text)
