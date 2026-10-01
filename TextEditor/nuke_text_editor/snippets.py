"""Snippet library storage. Pure Python.

Personal snippets live in ~/.nuke/text_editor/snippets.json. An optional
team folder may contain a snippets.json that is shared (read-only).
A snippet is {"name", "trigger", "language", "body"}; "$0" in the body
marks where the cursor goes after inserting.
"""

import io
import json
import os

from . import fileio

LANGUAGES = ("any", "python", "text", "markdown", "nuke", "javascript", "c_like", "bash")

DEFAULT_SNIPPETS = [
    {"name": "Loop selected nodes", "trigger": "forsel", "language": "python",
     "body": "for node in nuke.selectedNodes():\n    $0"},
    {"name": "Loop all nodes (recursive)", "trigger": "forall", "language": "python",
     "body": "for node in nuke.allNodes(recurseGroups=True):\n    $0"},
    {"name": "Nodes of a class", "trigger": "bycls", "language": "python",
     "body": "for node in nuke.allNodes(\"$0\"):\n    pass"},
    {"name": "Create Read node", "trigger": "read", "language": "python",
     "body": "read = nuke.nodes.Read()\nread[\"file\"].fromUserText(\"$0\")"},
    {"name": "Set knob on selected", "trigger": "setknob", "language": "python",
     "body": "for node in nuke.selectedNodes():\n    knob = node.knob(\"mix\")\n    if knob:\n        knob.setValue($0)"},
    {"name": "Undo block", "trigger": "undo", "language": "python",
     "body": "undo = nuke.Undo()\nundo.begin(\"$0\")\ntry:\n    pass\nfinally:\n    undo.end()"},
    {"name": "knobChanged callback", "trigger": "knobchanged", "language": "python",
     "body": "def on_knob_changed():\n    node = nuke.thisNode()\n    knob = nuke.thisKnob()\n    $0\n\n"
             "nuke.addKnobChanged(on_knob_changed, nodeClass=\"Grade\")"},
    {"name": "Script directory", "trigger": "scriptdir", "language": "python",
     "body": "os.path.dirname(nuke.root().name())$0"},
    {"name": "Task", "trigger": "todo", "language": "any", "body": "- [ ] $0"},
    {"name": "Frame expression", "trigger": "frexpr", "language": "any", "body": "[frame]$0"},
    {"name": "Noise wiggle expression", "trigger": "wiggle", "language": "any",
     "body": "noise(frame * 0.1) * $0"},
]


def personal_path():
    return os.path.join(fileio.data_dir(), "snippets.json")


def _read(path):
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return [s for s in data if isinstance(s, dict) and s.get("trigger") and "body" in s]


def load_personal():
    path = personal_path()
    if not os.path.exists(path):
        save_personal(DEFAULT_SNIPPETS)
        return [dict(s) for s in DEFAULT_SNIPPETS]
    return _read(path) or []


def save_personal(snippets):
    clean = [{"name": s.get("name", s["trigger"]), "trigger": s["trigger"],
              "language": s.get("language", "any"), "body": s["body"]} for s in snippets]
    fileio.write_text_file(personal_path(), json.dumps(clean, indent=1))


def load_team(folder):
    if not folder:
        return []
    snippets = _read(os.path.join(folder, "snippets.json")) or []
    for snippet in snippets:
        snippet["team"] = True
    return snippets


def find_by_trigger(snippets, trigger, language):
    """Personal snippets win over team ones; a language match wins over 'any'."""
    best = None
    for snippet in snippets:
        if snippet.get("trigger") != trigger:
            continue
        lang = snippet.get("language", "any")
        if lang == language:
            return snippet
        if lang == "any" and best is None:
            best = snippet
    return best
