"""Tracks time spent per script while Nuke is in front and the user is
active (mouse moved or frame changed in the last few minutes)."""

import datetime
import os
import time

from .qt import QtCore, QtGui, QtWidgets
from . import nuke_bridge
from . import timelog

_tracker = None


class TimeTracker(QtCore.QObject):
    TICK_SECONDS = 15
    IDLE_SECONDS = 300
    SAVE_SECONDS = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending = {}
        self._last_activity = time.time()
        self._last_save = time.time()
        self._last_cursor = None
        self._last_frame = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self.TICK_SECONDS * 1000)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.flush)

    @staticmethod
    def current_key():
        path = nuke_bridge.script_path()
        return os.path.basename(path) if path else "Untitled script"

    def _tick(self):
        now = time.time()
        cursor = QtGui.QCursor.pos()
        frame = nuke_bridge.current_frame()
        if cursor != self._last_cursor or frame != self._last_frame:
            self._last_activity = now
        self._last_cursor = cursor
        self._last_frame = frame

        active = QtGui.QGuiApplication.applicationState() == QtCore.Qt.ApplicationActive
        if active and now - self._last_activity < self.IDLE_SECONDS:
            day = datetime.date.today().isoformat()
            timelog.add_seconds(self._pending, day, self.current_key(), self.TICK_SECONDS)
        if now - self._last_save >= self.SAVE_SECONDS:
            self.flush()

    def flush(self):
        """Merge pending time into the log file (safe with several Nuke sessions)."""
        self._last_save = time.time()
        if not self._pending:
            return
        data = timelog.load()
        for day, scripts in self._pending.items():
            for script, seconds in scripts.items():
                timelog.add_seconds(data, day, script, seconds)
        try:
            timelog.save(data)
            self._pending = {}
        except OSError:
            pass


def start():
    """Start the global tracker once per Nuke session."""
    global _tracker
    app = QtWidgets.QApplication.instance()
    existing = app.property("pg_text_editor_tracker") if app is not None else None
    if _tracker is None and existing:
        return None  # already running from before a module reload
    if _tracker is None:
        _tracker = TimeTracker(app)
        _tracker.start()
        if app is not None:
            app.setProperty("pg_text_editor_tracker", True)
    return _tracker


def tracker():
    return _tracker
