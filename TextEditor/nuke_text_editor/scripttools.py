"""Script health check and node search. Pure Python.

Both work on a simple node model (SceneNode) that is built either from
the live Nuke script (nuke_bridge.scene_nodes) or from .nk text
(from_nk_nodes), so the checks are the same in both cases and testable.
"""

import os
import re

from . import nkparse

READ_CLASSES = ("Read", "DeepRead", "ReadGeo", "ReadGeo2", "Camera", "Camera2", "Camera3", "Camera4",
                "Axis", "Axis2", "Axis3", "Axis4", "OCIOFileTransform", "Vectorfield", "GenerateLUT")
WRITE_CLASSES = ("Write", "DeepWrite", "WriteGeo")
# Nodes whose output normally goes nowhere.
TERMINAL_CLASSES = WRITE_CLASSES + ("Viewer", "BackdropNode", "StickyNote", "Output", "Root",
                                    "ScanlineRender", "Scene", "PostageStamp")
FILTER_SIZE_KNOBS = {
    "Blur": "size", "Defocus": "defocus", "ZDefocus2": "size", "Erode": "size", "FilterErode": "size",
    "Dilate": "size", "EdgeBlur": "size", "Glow2": "size", "Soften": "size", "Sharpen": "size",
    "Median": "size", "Bokeh": "size", "Convolve2": "size", "VectorBlur2": "scale",
}
DEFAULT_SIZE_LIMIT = 250.0

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


class SceneNode(object):
    """What the checks need to know about a node.

    dependents is None when unknown (e.g. parsed from .nk text).
    """

    __slots__ = ("name", "cls", "knobs", "dependents", "connected_inputs", "has_error", "line")

    def __init__(self, name, cls, knobs=None, dependents=None, connected_inputs=None,
                 has_error=False, line=None):
        self.name = name
        self.cls = cls
        self.knobs = knobs or {}
        self.dependents = dependents
        self.connected_inputs = connected_inputs
        self.has_error = has_error
        self.line = line

    def __repr__(self):
        return "SceneNode({} {})".format(self.cls, self.name)


class Issue(object):
    __slots__ = ("node", "severity", "message", "line")

    def __init__(self, node, severity, message, line=None):
        self.node = node
        self.severity = severity
        self.message = message
        self.line = line

    def __repr__(self):
        return "Issue({}, {}, {})".format(self.node, self.severity, self.message)


def from_nk_nodes(nk_nodes):
    """Convert nkparse.NkNode objects (no connection information)."""
    return [SceneNode(n.full_name, n.cls, dict(n.knobs), line=n.line) for n in nk_nodes if n.cls != "Root"]


def _is_true(value):
    return str(value).strip().lower() in ("true", "1", "yes")


def _first_number(value):
    match = _NUMBER_RE.search(str(value or ""))
    return float(match.group(0)) if match else None


def analyze(nodes, script_dir=None, size_limit=DEFAULT_SIZE_LIMIT, path_status=nkparse.path_status):
    """Return a list of Issue sorted by severity, then node name."""
    issues = []
    for node in nodes:
        knobs = node.knobs
        if node.has_error:
            issues.append(Issue(node.name, "error", "Node has an error", node.line))

        path = (knobs.get("file") or "").strip()
        if node.cls in READ_CLASSES and path:
            if path_status(path, script_dir) == "missing":
                issues.append(Issue(node.name, "error", "Missing file: {}".format(path), node.line))
        if node.cls in WRITE_CLASSES and not path:
            issues.append(Issue(node.name, "error", "Write node has no file path", node.line))

        if _is_true(knobs.get("disable")) and (node.dependents is None or node.dependents > 0):
            issues.append(Issue(node.name, "warning", "Disabled node left in the tree", node.line))

        size_knob = FILTER_SIZE_KNOBS.get(node.cls)
        if size_knob:
            size = _first_number(knobs.get(size_knob))
            if size is not None and size > size_limit:
                issues.append(Issue(node.name, "warning", "Very large {} ({:g}) - slow to render".format(
                    size_knob, size), node.line))

        if node.dependents == 0 and node.cls not in TERMINAL_CLASSES:
            if node.connected_inputs == 0:
                issues.append(Issue(node.name, "info", "Not connected to anything", node.line))
            else:
                issues.append(Issue(node.name, "info", "Output is not used", node.line))

    issues.sort(key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.node.lower()))
    return issues


def search(nodes, query, in_knobs=True, limit=500):
    """Find nodes by name, class or (optionally) knob value.

    Returns [(node, where)] where `where` describes the match.
    """
    query = (query or "").strip().lower()
    if not query:
        return []
    results = []
    for node in nodes:
        if query in node.name.lower():
            results.append((node, "name"))
        elif query in node.cls.lower():
            results.append((node, "class " + node.cls))
        elif in_knobs:
            for knob, value in sorted(node.knobs.items()):
                if query in str(value).lower():
                    shown = str(value).replace("\n", " ")
                    results.append((node, "{} = {}".format(knob, shown[:80])))
                    break
        if len(results) >= limit:
            break
    return results


def script_dir_of(path):
    return os.path.dirname(path) if path else None
