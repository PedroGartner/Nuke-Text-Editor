"""Clickable links inside notes and code. Pure Python.

Link kinds:
    url    https://...           opens in the browser
    path   /jobs/sh010/plate.####.exr, C:\\..., \\\\server\\...
    frame  f1043, fr1043, frame 1043   (notes only) jumps the viewer
    node   [[Grade3]]                  (notes only) selects the node
"""

import re

URL_RE = re.compile(r"https?://[^\s<>\"']+")
# A path starts at the beginning of the text or after whitespace / quote / bracket.
PATH_RE = re.compile(
    r"(?<![^\s\"'(\[=])"
    r"(?:[A-Za-z]:[\\/]|\\\\[^\s\\/]+[\\/]|~/|/(?=[^\s/]))"
    r"[^\s\"'<>|*?]*"
)
FRAME_RE = re.compile(r"(?<![\w.])(?:f|fr|frame\s?)(\d{1,7})\b", re.IGNORECASE)
NODE_RE = re.compile(r"\[\[([A-Za-z_][\w.]*)\]\]")

_TRAILING = ".,;:)]}'\""


def _trim(value, start, end):
    """Strip trailing punctuation that is almost never part of a path/URL."""
    while value and value[-1] in _TRAILING:
        value = value[:-1]
        end -= 1
    return value, start, end


def find_links(text, notes=True):
    """Return a list of (start, end, kind, value) found in one line of text."""
    links = []
    taken = []

    def _free(start, end):
        return all(end <= s or start >= e for s, e in taken)

    for match in URL_RE.finditer(text):
        value, start, end = _trim(match.group(0), match.start(), match.end())
        links.append((start, end, "url", value))
        taken.append((start, end))

    for match in PATH_RE.finditer(text):
        value, start, end = _trim(match.group(0), match.start(), match.end())
        if len(value) < 3 or not _free(start, end):
            continue
        if value.startswith("/") and "/" not in value[1:] and "." not in value:
            continue  # a lone "/word" is usually not a path
        links.append((start, end, "path", value))
        taken.append((start, end))

    if notes:
        for match in NODE_RE.finditer(text):
            if _free(match.start(), match.end()):
                links.append((match.start(), match.end(), "node", match.group(1)))
                taken.append((match.start(), match.end()))
        for match in FRAME_RE.finditer(text):
            if _free(match.start(), match.end()):
                links.append((match.start(), match.end(), "frame", int(match.group(1))))
                taken.append((match.start(), match.end()))

    links.sort(key=lambda item: item[0])
    return links


def link_at(text, column, notes=True):
    """Return (kind, value) of the link under the given column, or None."""
    for start, end, kind, value in find_links(text, notes):
        if start <= column <= end:
            return kind, value
    return None
