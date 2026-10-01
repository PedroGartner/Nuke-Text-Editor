"""A small Python linter: syntax errors, undefined names and unused
imports. Pure Python (uses the standard ast module).

It is deliberately simple: names bound anywhere in the file count as
defined, so it has few false alarms. Names that exist in Nuke's Script
Editor namespace (nuke, nukescripts...) can be passed as `extra_names`.
"""

import ast
import builtins

ALWAYS_DEFINED = {"__file__", "__name__", "__doc__", "__builtins__", "__spec__", "__loader__",
                  "__package__", "__path__", "__all__"}


class Message(object):
    __slots__ = ("line", "severity", "text")

    def __init__(self, line, severity, text):
        self.line = line
        self.severity = severity
        self.text = text

    def __repr__(self):
        return "Message({}, {}, {})".format(self.line, self.severity, self.text)


def _all_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                    for element in node.value.elts:
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            names.add(element.value)
                        elif hasattr(ast, "Str") and isinstance(element, getattr(ast, "Str")):
                            names.add(element.s)
    return names


def lint(source, extra_names=()):
    """Return a list of Message sorted by line."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Message(exc.lineno or 1, "error", "Syntax error: {}".format(exc.msg))]

    bound = set()
    loads = []
    imports = {}
    star_import = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                loads.append((node.id, node.lineno))
            else:
                bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    star_import = True
                    continue
                name = alias.asname or alias.name.split(".")[0]
                bound.add(name)
                imports.setdefault(name, node.lineno)
        elif type(node).__name__ in ("MatchAs", "MatchStar") and getattr(node, "name", None):
            bound.add(node.name)
        elif type(node).__name__ == "MatchMapping" and getattr(node, "rest", None):
            bound.add(node.rest)

    known = bound | set(dir(builtins)) | ALWAYS_DEFINED | set(extra_names)
    messages = []
    if not star_import:
        reported = set()
        for name, line in loads:
            if name not in known and name not in reported:
                reported.add(name)
                messages.append(Message(line, "warning", "Undefined name '{}'".format(name)))

    used = set(name for name, _line in loads) | _all_names(tree)
    # Attribute access like os.path counts as using 'os'.
    for name, line in sorted(imports.items(), key=lambda item: item[1]):
        if name not in used:
            messages.append(Message(line, "info", "'{}' imported but not used".format(name)))

    messages.sort(key=lambda m: m.line)
    return messages
