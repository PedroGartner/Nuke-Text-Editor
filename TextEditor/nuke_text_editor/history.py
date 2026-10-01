"""Local version history: a snapshot of a file is kept each time it is
saved, so earlier versions can be browsed and restored. Pure Python."""

import io
import json
import os
import time

from . import fileio

MAX_SNAPSHOTS = 50
MIN_SECONDS_BETWEEN_AUTOSAVE_SNAPSHOTS = 300


def _file_dir(path):
    return fileio.data_dir("history", fileio.path_key(path))


def _write_index(folder, path):
    index = os.path.join(folder, "index.json")
    if not os.path.exists(index):
        try:
            with io.open(index, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"path": path}))
        except OSError:
            pass


def list_snapshots(path):
    """Return [(timestamp_float, snapshot_path)] newest first."""
    folder = _file_dir(path)
    result = []
    for name in os.listdir(folder):
        if name == "index.json":
            continue
        stem = name.split(".")[0]
        try:
            stamp = time.mktime(time.strptime(stem[:15], "%Y%m%d-%H%M%S")) + int(stem[16:] or 0) / 1000.0
        except (ValueError, OverflowError):
            continue
        result.append((stamp, os.path.join(folder, name)))
    result.sort(reverse=True)
    return result


def read_snapshot(snapshot_path):
    with io.open(snapshot_path, "r", encoding="utf-8") as handle:
        return handle.read()


def add_snapshot(path, text, autosave=False, now=None):
    """Store `text` as a snapshot of `path` (the content before a save).

    Skips identical consecutive snapshots, and rate-limits snapshots
    made by autosave. Returns the snapshot path or None.
    """
    folder = _file_dir(path)
    _write_index(folder, path)
    snapshots = list_snapshots(path)
    now = time.time() if now is None else now
    if snapshots:
        latest_time, latest_path = snapshots[0]
        try:
            if read_snapshot(latest_path) == text:
                return None
        except OSError:
            pass
        if autosave and now - latest_time < MIN_SECONDS_BETWEEN_AUTOSAVE_SNAPSHOTS:
            return None

    millis = int((now - int(now)) * 1000)
    name = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-{:03d}.txt".format(millis)
    snapshot_path = os.path.join(folder, name)
    fileio.write_text_file(snapshot_path, text)

    for _stamp, old_path in list_snapshots(path)[MAX_SNAPSHOTS:]:
        try:
            os.remove(old_path)
        except OSError:
            pass
    return snapshot_path
