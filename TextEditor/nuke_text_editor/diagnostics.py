"""Startup and crash logging.

- trace() writes each startup step to logs/startup.log (flushed at once),
  so after a hard crash the last line shows where it stopped.
- faulthandler writes the Python stack of a hard crash to logs/crash.log.
  On Windows it also reports exceptions that Nuke and Windows handle
  themselves (e.g. 0x8001010e from COM calls in Nuke's own threads), so
  there it only runs while the editor is being built, unless the
  environment variable NUKE_TEXT_EDITOR_CRASH_LOG is set to "all".
"""

import faulthandler
import io
import os
import sys
import time
import traceback

from . import fileio

_trace_handle = None
_crash_handle = None
_foreign_handler = False  # another tool enabled faulthandler first: leave it alone
MAX_CRASH_LOG = 512 * 1024


def _trim(path, limit=MAX_CRASH_LOG):
    """Keep only the last part of a log that grew too large."""
    try:
        if os.path.getsize(path) <= limit:
            return
        with open(path, "rb") as handle:
            handle.seek(-limit // 2, os.SEEK_END)
            tail = handle.read()
        with open(path, "wb") as handle:
            handle.write(b"(older entries removed)\n" + tail)
    except OSError:
        pass


def _whole_session():
    if not sys.platform.startswith("win"):
        return True
    return os.environ.get("NUKE_TEXT_EDITOR_CRASH_LOG", "").lower() == "all"


def logs_dir():
    return fileio.data_dir("logs")


def start_session():
    """Begin a fresh startup log and enable the crash log."""
    global _trace_handle, _crash_handle, _foreign_handler
    try:
        if _trace_handle is not None:
            _trace_handle.close()
        _trace_handle = io.open(os.path.join(logs_dir(), "startup.log"), "w", encoding="utf-8")
    except OSError:
        _trace_handle = None
    try:
        if _crash_handle is None:
            _foreign_handler = faulthandler.is_enabled()
            path = os.path.join(logs_dir(), "crash.log")
            _trim(path)
            _crash_handle = open(path, "a")
        _crash_handle.write("\n==== session {} (Python {}) ====\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), sys.version.split()[0]))
        _crash_handle.flush()
        if not _foreign_handler:
            faulthandler.enable(file=_crash_handle, all_threads=True)
    except Exception:
        pass
    trace("Python {} on {}".format(sys.version.split()[0], sys.platform))
    try:
        from .qt import QtCore, QT6
        trace("{} / Qt {}".format("PySide6" if QT6 else "PySide2", QtCore.qVersion()))
    except Exception:
        pass
    try:
        from . import nuke_bridge
        if nuke_bridge.available():
            trace("Nuke {}".format(nuke_bridge.nuke.NUKE_VERSION_STRING))
    except Exception:
        pass


def end_startup():
    """The editor is built: stop the crash log on Windows (see above)."""
    trace("startup finished")
    if _crash_handle is not None and not _foreign_handler and not _whole_session():
        try:
            faulthandler.disable()
        except Exception:
            pass


def trace(step):
    if _trace_handle is None:
        return
    try:
        _trace_handle.write("{}  {}\n".format(time.strftime("%H:%M:%S"), step))
        _trace_handle.flush()
    except Exception:
        pass


def log_exception(context):
    """Log the current exception and return its text."""
    text = traceback.format_exc()
    trace("ERROR in {}:\n{}".format(context, text))
    try:
        with io.open(os.path.join(logs_dir(), "errors.log"), "a", encoding="utf-8") as handle:
            handle.write("\n==== {} {} ====\n{}".format(time.strftime("%Y-%m-%d %H:%M:%S"), context, text))
    except OSError:
        pass
    return text
