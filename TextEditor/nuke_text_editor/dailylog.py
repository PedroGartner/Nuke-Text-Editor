"""Daily log: tasks ticked off and files worked on, per day, used for
the end-of-day report. Pure Python."""

import datetime
import html
import io
import json
import os
import time

from . import fileio
from . import timelog


def log_path():
    return os.path.join(fileio.data_dir(), "daily.json")


def load(path=None):
    try:
        with io.open(path or log_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data, path=None):
    fileio.write_text_file(path or log_path(), json.dumps(data, indent=1, sort_keys=True))


def _day(data, day):
    entry = data.setdefault(day, {})
    entry.setdefault("done", [])
    entry.setdefault("touched", {})
    return entry


def record_done(text, file_path=None, when=None, path=None):
    """Remember a task that was ticked off."""
    when = when or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(when))
    data = load(path)
    entry = _day(data, day)
    item = {"time": time.strftime("%H:%M", time.localtime(when)), "text": text.strip(),
            "file": file_path or ""}
    if not any(d["text"] == item["text"] and d["file"] == item["file"] for d in entry["done"]):
        entry["done"].append(item)
        _save(data, path)


def record_undone(text, file_path=None, when=None, path=None):
    """A task was unticked again: remove it from today's list."""
    when = when or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(when))
    data = load(path)
    entry = _day(data, day)
    before = len(entry["done"])
    entry["done"] = [d for d in entry["done"] if not (d["text"] == text.strip() and d["file"] == (file_path or ""))]
    if len(entry["done"]) != before:
        _save(data, path)


def record_touched(file_path, when=None, path=None):
    """Count a save of a file for the day."""
    when = when or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(when))
    data = load(path)
    touched = _day(data, day)["touched"]
    touched[file_path] = touched.get(file_path, 0) + 1
    _save(data, path)


def record_version(script_path, tag, note="", when=None, path=None):
    """Remember a Version Up and its note for the day report."""
    when = when or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(when))
    data = load(path)
    versions = _day(data, day).setdefault("versions", [])
    versions.append({"time": time.strftime("%H:%M", time.localtime(when)), "tag": tag or "",
                     "script": os.path.basename(script_path or ""), "note": (note or "").strip()})
    _save(data, path)


def report_html(day, daily=None, times=None, user=""):
    """End-of-day report for a day ('YYYY-MM-DD') as HTML for a rich note."""
    daily = load() if daily is None else daily
    times = timelog.load() if times is None else times
    entry = daily.get(day, {})
    parts = ["<h2>Day report {}{}</h2>".format(html.escape(day), " - " + html.escape(user) if user else "")]

    scripts = sorted(times.get(day, {}).items(), key=lambda item: -item[1])
    total = sum(seconds for _s, seconds in scripts)
    parts.append("<h3>Time ({})</h3>".format(timelog.format_duration(total)))
    if scripts:
        parts.append("".join("<p>{} &nbsp; {}</p>".format(timelog.format_duration(s), html.escape(name))
                             for name, s in scripts))
    else:
        parts.append("<p>No tracked time.</p>")

    done = entry.get("done", [])
    parts.append("<h3>Done ({})</h3>".format(len(done)))
    if done:
        for item in done:
            where = " <i>({})</i>".format(html.escape(os.path.basename(item["file"]))) if item.get("file") else ""
            parts.append("<p>- [x] {}{} &nbsp; {}</p>".format(html.escape(item["text"]), where, item.get("time", "")))
    else:
        parts.append("<p>No tasks ticked off.</p>")

    versions = entry.get("versions", [])
    if versions:
        parts.append("<h3>Versions ({})</h3>".format(len(versions)))
        for item in versions:
            parts.append("<p>{} &nbsp; <b>{}</b>{}</p>".format(
                item.get("time", ""), html.escape(item.get("script", "")),
                " - " + html.escape(item["note"]) if item.get("note") else ""))

    touched = sorted(entry.get("touched", {}).items(), key=lambda item: -item[1])
    parts.append("<h3>Files worked on ({})</h3>".format(len(touched)))
    for file_path, count in touched:
        parts.append("<p>{} &nbsp; ({} saves)</p>".format(html.escape(file_path), count))

    parts.append("<h3>Notes for tomorrow</h3><p>- [ ] </p>")
    return "".join(parts)


def today():
    return datetime.date.today().isoformat()
