"""Time spent per script, stored as {date: {script: seconds}}. Pure Python."""

import datetime
import io
import json
import os

from . import fileio


def log_path():
    return os.path.join(fileio.data_dir(), "timelog.json")


def load(path=None):
    path = path or log_path()
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data, path=None):
    fileio.write_text_file(path or log_path(), json.dumps(data, indent=1, sort_keys=True))


def add_seconds(data, day, script, seconds):
    """Add time for a script on a day (day as 'YYYY-MM-DD')."""
    per_day = data.setdefault(day, {})
    per_day[script] = per_day.get(script, 0) + seconds
    return data


def week_start(day):
    """Monday of the week containing `day` (a datetime.date)."""
    return day - datetime.timedelta(days=day.weekday())


def week_summary(data, monday):
    """Return (days, rows) for the week starting on `monday`.

    days: list of 7 'YYYY-MM-DD' strings.
    rows: [(script, [seconds per day], total)] sorted by total, largest first.
    """
    days = [(monday + datetime.timedelta(days=i)).isoformat() for i in range(7)]
    scripts = {}
    for index, day in enumerate(days):
        for script, seconds in data.get(day, {}).items():
            scripts.setdefault(script, [0] * 7)[index] += seconds
    rows = [(script, values, sum(values)) for script, values in scripts.items()]
    rows.sort(key=lambda row: (-row[2], row[0]))
    return days, rows


def format_duration(seconds):
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return "{}:{:02d}".format(hours, minutes)


def to_csv(days, rows):
    lines = ["script," + ",".join(days) + ",total"]
    for script, values, total in rows:
        cells = [format_duration(v) for v in values] + [format_duration(total)]
        lines.append('"{}",'.format(script.replace('"', '""')) + ",".join(cells))
    return "\n".join(lines) + "\n"
