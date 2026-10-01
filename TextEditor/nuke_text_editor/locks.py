"""Lightweight "someone else is editing this" markers for shared notes.

While a file has unsaved changes a small hidden file
'.~lock.<name>#' is kept next to it with the user and machine. Another
artist opening the file sees a warning. Markers older than
STALE_SECONDS are ignored (e.g. after a crash). Pure Python.
"""

import getpass
import io
import json
import os
import socket
import sys
import time

STALE_SECONDS = 600


def lock_path(file_path):
    folder, name = os.path.split(file_path)
    return os.path.join(folder, ".~lock.{}#".format(name))


def me():
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    return {"user": user, "host": socket.gethostname(), "pid": os.getpid()}


def _hide_on_windows(path):
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass


def write_lock(file_path, now=None):
    """Create or refresh our marker. Returns True on success."""
    data = dict(me(), time=now or time.time())
    path = lock_path(file_path)
    try:
        if sys.platform.startswith("win") and os.path.exists(path):
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)  # NORMAL, so it can be rewritten
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data))
        _hide_on_windows(path)
        return True
    except OSError:
        return False


def read_lock(file_path):
    try:
        with io.open(lock_path(file_path), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def is_mine(lock):
    mine = me()
    return bool(lock) and lock.get("user") == mine["user"] and lock.get("host") == mine["host"] \
        and lock.get("pid") == mine["pid"]


def foreign_lock(file_path, now=None):
    """The marker of another user / Nuke session if it is recent, else None."""
    lock = read_lock(file_path)
    if not lock or is_mine(lock):
        return None
    try:
        age = (now or time.time()) - float(lock.get("time", 0))
    except (TypeError, ValueError):
        return None
    return lock if age < STALE_SECONDS else None


def remove_lock(file_path):
    """Remove our marker (never someone else's)."""
    lock = read_lock(file_path)
    if lock and is_mine(lock):
        try:
            os.remove(lock_path(file_path))
        except OSError:
            pass


def describe(lock):
    minutes = max(0, int((time.time() - float(lock.get("time", time.time()))) // 60))
    return "{} on {} ({} min ago)".format(lock.get("user", "?"), lock.get("host", "?"), minutes)


def is_network_path(path):
    """True for UNC paths, mapped network drives (Windows) and network
    mounts (NFS / SMB on Linux, /Volumes on macOS)."""
    path = os.path.abspath(path)
    if path.startswith("\\\\") or path.startswith("//"):
        return True
    if sys.platform.startswith("win"):
        drive = os.path.splitdrive(path)[0]
        if not drive:
            return False
        try:
            import ctypes
            return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4  # DRIVE_REMOTE
        except Exception:
            return False
    if sys.platform == "darwin":
        return path.startswith("/Volumes/")
    try:
        best, best_type = "", ""
        with io.open("/proc/mounts", "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 3 and (path == parts[1] or path.startswith(parts[1].rstrip("/") + "/")):
                    if len(parts[1]) > len(best):
                        best, best_type = parts[1], parts[2]
        return best_type.startswith(("nfs", "cifs", "smb", "fuse.sshfs", "afs"))
    except OSError:
        return False


def should_mark(path, mode):
    """mode: 'network', 'always' or 'off'."""
    if mode == "always":
        return True
    if mode == "network":
        return is_network_path(path)
    return False
