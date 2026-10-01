"""Tie-in with Batch Gartner (the standalone render queue manager).

Sending jobs reuses Batch Gartner's own Nuke module
(BatchGartner_NukeIntegration) when it is loaded, so saving the script,
starting the app and the receiver port all behave exactly as in its own
menu. Without it, jobs are sent straight to the running app over
localhost using the same message format.
"""

import io
import json
import os
import socket
import sys

HOST = "127.0.0.1"
DEFAULT_PORT = 54321
APP_CONFIG = os.path.join(os.path.expanduser("~"), ".nuke_batch_render_config.json")
INTEGRATION_MODULE = "BatchGartner_NukeIntegration"
LOG_EXTENSIONS = (".log", ".txt")


def config(path=APP_CONFIG):
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def receiver_port(cfg=None):
    cfg = config() if cfg is None else cfg
    try:
        return int(cfg.get("receiver_port", DEFAULT_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PORT


def integration_module():
    """Batch Gartner's Nuke module if it is available, else None."""
    module = sys.modules.get(INTEGRATION_MODULE)
    if module is not None:
        return module
    try:
        return __import__(INTEGRATION_MODULE)
    except Exception:
        return None


def find_log_folder(cfg=None):
    """Look for a log folder in Batch Gartner's settings (any setting whose
    name contains 'log' and points to an existing folder)."""
    cfg = config() if cfg is None else cfg
    stack = [cfg]
    while stack:
        current = stack.pop()
        for key, value in current.items():
            if isinstance(value, dict):
                stack.append(value)
            elif isinstance(value, str) and "log" in str(key).lower():
                path = os.path.expanduser(value)
                if os.path.isdir(path):
                    return path
    return None


def list_logs(folder, limit=200):
    """Log files in a folder (recursive), newest first: [(mtime, path)]."""
    found = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if name.lower().endswith(LOG_EXTENSIONS):
                path = os.path.join(root, name)
                try:
                    found.append((os.path.getmtime(path), path))
                except OSError:
                    continue
    found.sort(reverse=True)
    return found[:limit]


def logs_matching(logs, words):
    """Logs whose file name contains any of the words (case-insensitive)."""
    words = [w.lower() for w in words if w]
    return [(t, p) for t, p in logs if any(w in os.path.basename(p).lower() for w in words)]


def build_payload(script_path, jobs):
    """jobs: list of dicts with write_node, output_path, first, last, nuke_exe (and optional note)."""
    return {"version": 1, "source": "Nuke",
            "jobs": [dict(job, script_path=script_path) for job in jobs]}


def send(payload, port=None, timeout=1.0):
    """Send a payload to the running app. Returns True when it answered OK."""
    data = json.dumps(payload).encode("utf-8")
    try:
        with socket.create_connection((HOST, port or receiver_port()), timeout=timeout) as sock:
            sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
            reply = sock.recv(1024).decode("utf-8", "replace")
    except OSError:
        return False
    return reply.startswith("OK")
