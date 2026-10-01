"""pytest configuration.

These files are only meant for pytest. If something else imports them
(for example a Nuke auto-loader that imports every .py file), nothing
here does anything.
"""

import importlib.util
import os
import sys
import tempfile

RUNNING_UNDER_PYTEST = "_pytest.config" in sys.modules

if RUNNING_UNDER_PYTEST:
    import pytest

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    # Keep test data away from the real ~/.nuke/text_editor folder.
    os.environ["NUKE_TEXT_EDITOR_DATA"] = tempfile.mkdtemp(prefix="nte_test_")

    HAS_QT = importlib.util.find_spec("pytestqt") is not None and (
        importlib.util.find_spec("PySide6") is not None or importlib.util.find_spec("PySide2") is not None)

    # UI tests need Qt and pytest-qt; skip the file when they are missing.
    collect_ignore = [] if HAS_QT else ["test_ui.py"]

    if HAS_QT:
        @pytest.fixture
        def window(qtbot):
            from nuke_text_editor import main_window
            w = main_window.TextEditorWidget()
            qtbot.addWidget(w)
            w.show()
            yield w
            for editor in w.editors():
                editor.is_modified = False  # avoid save prompts on close
            w.close()
