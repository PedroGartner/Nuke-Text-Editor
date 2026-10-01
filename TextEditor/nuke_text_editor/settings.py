"""Persistent preferences (Qt QSettings) with typed getters."""

from .qt import QtCore
from . import fileio

SETTINGS_ORG = "PGartner"
SETTINGS_APP = "NukeTextEditor"

DEFAULTS = {
    "zoom": 100,
    "word_wrap": True,
    "autosave_to_file": False,
    "reopen_tabs": True,
    "auto_open_shot_notes": True,
    "auto_show_editor_for_notes": False,
    "open_script_note_on_load": True,
    "mark_nodes_with_notes": True,
    "time_tracking": True,
    "team_recipes_folder": "",
    "team_snippets_folder": "",
    "notes_folder": "",
    "theme": "Dark",
    "lock_markers": "network",
    "lint_on_save": True,
    "annotate_captures": True,
    "batch_gartner_log_folder": "",
    "plates/check_on_load": True,
    "render_check/before_batch_gartner": True,
    "render_check/before_local_render": False,
    "render_check/colorspace": "",
    "render_check/file_type": "",
    "gutter/line_numbers": True,
    "gutter/fold_markers": True,
    "gutter/scale": 1.0,
    "browser/visible": True,
    "side_panel/visible": False,
}


class Settings(object):
    """Thin typed wrapper around QSettings."""

    def __init__(self):
        self._settings = QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP)

    def raw(self):
        return self._settings

    def value(self, key, default=None):
        if default is None:
            default = DEFAULTS.get(key)
        return self._settings.value(key, default)

    def get_bool(self, key, default=None):
        fallback = DEFAULTS.get(key, False) if default is None else default
        return fileio.to_bool(self._settings.value(key), fallback)

    def get_int(self, key, default=None):
        fallback = DEFAULTS.get(key, 0) if default is None else default
        return fileio.to_int(self._settings.value(key), fallback)

    def get_float(self, key, default=None):
        fallback = DEFAULTS.get(key, 0.0) if default is None else default
        return fileio.to_float(self._settings.value(key), fallback)

    def get_str(self, key, default=None):
        fallback = DEFAULTS.get(key, "") if default is None else default
        value = self._settings.value(key)
        return str(value) if value not in (None, "") else fallback

    def get_list(self, key):
        return fileio.to_list(self._settings.value(key))

    def set(self, key, value):
        self._settings.setValue(key, value)

    def sync(self):
        self._settings.sync()
