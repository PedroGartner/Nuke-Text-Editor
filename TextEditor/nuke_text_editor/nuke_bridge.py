"""Every call into Nuke's API goes through this module.

All functions are safe to call outside Nuke: they return None, False or
an empty list, so the editor also runs as a plain Qt application (and in
tests).
"""

import io
import os
import sys
import traceback

try:
    import nuke  # noqa: F401
except ImportError:
    nuke = None

NOTE_KNOB = "pg_note"
SCRIPT_NOTE_KNOB = "pg_script_note"

TEXT_KNOB_CLASSES = (
    "String_Knob", "EvalString_Knob", "Multiline_Eval_String_Knob",
    "PyScript_Knob", "PythonKnob", "Script_Knob", "File_Knob",
)
PYTHON_KNOB_CLASSES = ("PyScript_Knob", "PythonKnob")


def available():
    return nuke is not None


# ------------------------------------------------------------- #
#  Script / frame information                                   #
# ------------------------------------------------------------- #
def script_path():
    """Path of the open script, or None if unsaved / outside Nuke."""
    if nuke is None:
        return None
    try:
        name = nuke.root().name()
    except Exception:
        return None
    return None if not name or name == "Root" else name


def current_frame():
    if nuke is None:
        return None
    try:
        return int(nuke.frame())
    except Exception:
        return None


def frame_range():
    if nuke is None:
        return None, None
    try:
        root = nuke.root()
        return int(root["first_frame"].value()), int(root["last_frame"].value())
    except Exception:
        return None, None


def format_name():
    if nuke is None:
        return None
    try:
        fmt = nuke.root().format()
        name = fmt.name()
        size = "{}x{}".format(fmt.width(), fmt.height())
        return "{} {}".format(name, size) if name else size
    except Exception:
        return None


def template_context():
    """Values used by note templates."""
    first, last = frame_range()
    return {
        "script_path": script_path(),
        "frame": current_frame(),
        "first_frame": first,
        "last_frame": last,
        "fmt": format_name(),
    }


def goto_frame(frame):
    if nuke is None:
        return False
    try:
        nuke.frame(int(frame))
        return True
    except Exception:
        return False


# ------------------------------------------------------------- #
#  Nodes                                                        #
# ------------------------------------------------------------- #
def _node(full_name):
    if nuke is None:
        return None
    if full_name in ("root", "Root", ""):
        return nuke.root()
    try:
        return nuke.toNode(full_name)
    except Exception:
        return None


def selected_nodes():
    if nuke is None:
        return []
    try:
        return nuke.selectedNodes()
    except Exception:
        return []


def selected_node_name():
    """Full name of the single selected node, or None."""
    nodes = selected_nodes()
    if len(nodes) != 1:
        return None
    try:
        return nodes[0].fullName()
    except Exception:
        return None


def select_node(full_name):
    """Select a node by name and centre the node graph on it."""
    node = _node(full_name)
    if node is None or node is (nuke.root() if nuke else None):
        return False
    try:
        for other in nuke.selectedNodes():
            other.setSelected(False)
        node.setSelected(True)
        if "." not in full_name:
            center = [node.xpos() + node.screenWidth() / 2.0, node.ypos() + node.screenHeight() / 2.0]
            nuke.zoom(max(nuke.zoom(), 1.0), center)
        return True
    except Exception:
        return False


def create_read(path):
    """Create a Read node for a file or sequence (frame range detected by Nuke)."""
    if nuke is None:
        return False
    try:
        read = nuke.createNode("Read")
        read["file"].fromUserText(path.replace("\\", "/"))
        return True
    except Exception:
        return False


# ------------------------------------------------------------- #
#  Running Python                                               #
# ------------------------------------------------------------- #
def run_python(code, filename="<editor>"):
    """Run code in the __main__ namespace, like Nuke's Script Editor.

    Returns (output, error_text, error_line). Output includes anything
    printed. error_line is the 1-based line in `code` where it failed.
    """
    import __main__
    buffer = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    error_text = ""
    error_line = None
    sys.stdout = sys.stderr = buffer
    try:
        compiled = compile(code, filename, "exec")
        exec(compiled, __main__.__dict__)
    except SystemExit:
        pass
    except SyntaxError as exc:
        error_text = "".join(traceback.format_exception_only(type(exc), exc))
        error_line = exc.lineno
    except Exception:
        etype, value, tb = sys.exc_info()
        frames = traceback.extract_tb(tb)
        for frame in frames:
            if frame.filename == filename:
                error_line = frame.lineno
        error_text = "".join(traceback.format_exception(etype, value, tb.tb_next if tb else None))
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return buffer.getvalue(), error_text, error_line


# ------------------------------------------------------------- #
#  Knobs                                                        #
# ------------------------------------------------------------- #
def editable_knobs(full_name):
    """[(knob_name, knob_class, kind)] for text knobs and knobs with
    expressions. kind is 'value' or 'expression'."""
    node = _node(full_name)
    if node is None:
        return []
    result = []
    try:
        for name, knob in sorted(node.knobs().items()):
            if name in (NOTE_KNOB, SCRIPT_NOTE_KNOB):
                continue
            cls = knob.Class()
            if cls in TEXT_KNOB_CLASSES:
                result.append((name, cls, "value"))
            try:
                if knob.hasExpression():
                    result.append((name, cls, "expression"))
            except Exception:
                pass
    except Exception:
        return result
    return result


def knob_language(knob_class, kind):
    return "python" if kind == "value" and knob_class in PYTHON_KNOB_CLASSES else "text"


def read_knob(full_name, knob_name, kind):
    node = _node(full_name)
    knob = node.knob(knob_name) if node is not None else None
    if knob is None:
        raise LookupError("{}.{} no longer exists".format(full_name, knob_name))
    if kind == "expression":
        return knob.animation(0).expression() if knob.animation(0) else ""
    value = knob.value()
    return "" if value is None else str(value)


def write_knob(full_name, knob_name, kind, text):
    node = _node(full_name)
    knob = node.knob(knob_name) if node is not None else None
    if knob is None:
        raise LookupError("{}.{} no longer exists".format(full_name, knob_name))
    undo = nuke.Undo()
    undo.begin("Edit {}.{}".format(full_name, knob_name))
    try:
        if kind == "expression":
            knob.setExpression(text.strip())
        else:
            knob.setValue(text)
    finally:
        undo.end()


# ------------------------------------------------------------- #
#  Node notes and script notes                                  #
# ------------------------------------------------------------- #
def get_note(full_name, knob_name=NOTE_KNOB):
    node = _node(full_name)
    if node is None:
        return None
    knob = node.knob(knob_name)
    return knob.value() if knob is not None else ""


def set_note(full_name, text, knob_name=NOTE_KNOB):
    """Store a note in a hidden knob. An empty note removes the knob."""
    node = _node(full_name)
    if node is None:
        raise LookupError("Node {} no longer exists".format(full_name))
    knob = node.knob(knob_name)
    undo = nuke.Undo()
    undo.begin("Edit note")
    try:
        if not text.strip():
            if knob is not None:
                node.removeKnob(knob)
            return
        if knob is None:
            knob = nuke.Multiline_Eval_String_Knob(knob_name, "note")
            invisible = getattr(nuke, "INVISIBLE", None)
            if invisible is not None:
                knob.setFlag(invisible)
            node.addKnob(knob)
        knob.setValue(text)
    finally:
        undo.end()


def get_script_note():
    return get_note("root", SCRIPT_NOTE_KNOB)


def set_script_note(text):
    set_note("root", text, SCRIPT_NOTE_KNOB)


_marker_installed = False
_in_autolabel = False


def _note_autolabel():
    """Adds a small marker under the label of nodes that have a note."""
    global _in_autolabel
    if _in_autolabel:
        return None  # never recurse through Nuke's own autolabel
    try:
        node = nuke.thisNode()
        knob = node.knob(NOTE_KNOB)
        if knob is None or not knob.value().strip():
            return None
        # Nuke's default label function is registered as __main__.autolabel
        # (also available as nuke.autolabel); reuse it so labels stay normal.
        base = None
        try:
            import __main__
            default = getattr(nuke, "autolabel", None) or getattr(__main__, "autolabel", None)
            if default is not None and default is not _note_autolabel:
                _in_autolabel = True
                try:
                    base = default()
                finally:
                    _in_autolabel = False
        except Exception:
            base = None
        return (base or node.name()) + "\n✎ note"
    except Exception:
        return None


def install_note_marker():
    global _marker_installed
    # The flag also lives on the nuke module, so reloading this module
    # (e.g. by an auto-loader) never registers the marker twice.
    if nuke is None or _marker_installed or getattr(nuke, "_pg_note_marker_installed", False):
        return
    try:
        nuke.addAutolabel(_note_autolabel)
        _marker_installed = True
        nuke._pg_note_marker_installed = True
    except Exception:
        pass


# ------------------------------------------------------------- #
#  Node setups (recipes)                                        #
# ------------------------------------------------------------- #
def copy_selected_to_file(path):
    """Save the selected nodes to a .nk file. Returns number of nodes."""
    nodes = selected_nodes()
    if not nodes:
        return 0
    nuke.nodeCopy(path.replace("\\", "/"))
    return len(nodes)


def paste_file(path):
    if nuke is None:
        return False
    nuke.nodePaste(path.replace("\\", "/"))
    return True


# ------------------------------------------------------------- #
#  Viewer capture                                               #
# ------------------------------------------------------------- #
def capture_viewer(path):
    """Render the active viewer's input at the current frame to a JPEG.
    Returns (ok, message)."""
    if nuke is None:
        return False, "Only available inside Nuke."
    viewer = nuke.activeViewer()
    if viewer is None:
        return False, "No active Viewer."
    index = viewer.activeInput()
    source = viewer.node().input(index) if index is not None else None
    if source is None:
        return False, "The active Viewer has no input."
    frame = nuke.frame()
    nuke.Undo.disable()
    write = None
    try:
        write = nuke.nodes.Write(file=path.replace("\\", "/"), file_type="jpeg", channels="rgb")
        write.setInput(0, source)
        nuke.execute(write, frame, frame)
    except Exception as exc:
        return False, "Capture failed: {}".format(exc)
    finally:
        if write is not None:
            try:
                nuke.delete(write)
            except Exception:
                pass
        nuke.Undo.enable()
    if not os.path.isfile(path):
        return False, "Capture failed: no image was written."
    return True, "Captured frame {}".format(frame)


# ------------------------------------------------------------- #
#  Commands and expressions                                     #
# ------------------------------------------------------------- #
def menu_commands(menu_name="Nuke"):
    """[(path, callable)] for every command in a Nuke menu."""
    if nuke is None:
        return []
    result = []

    def _walk(menu, prefix):
        try:
            items = menu.items()
        except Exception:
            return
        for item in items:
            try:
                name = item.name().replace("&", "")
            except Exception:
                continue
            if not name or name.startswith("@"):
                continue
            if isinstance(item, nuke.Menu):
                _walk(item, prefix + name + " / ")
            elif hasattr(item, "invoke"):
                result.append((prefix + name, item.invoke))

    try:
        _walk(nuke.menu(menu_name), "Nuke / ")
    except Exception:
        pass
    return result


def evaluate_expression(expression):
    """Evaluate a Nuke expression at the current frame. Returns str."""
    if nuke is None:
        raise RuntimeError("Only available inside Nuke.")
    return repr(nuke.expression(expression))


def evaluate_tcl(command):
    if nuke is None:
        raise RuntimeError("Only available inside Nuke.")
    return str(nuke.tcl(command))


# ------------------------------------------------------------- #
#  Callbacks                                                    #
# ------------------------------------------------------------- #
def add_script_callbacks(on_load, on_save):
    if nuke is None:
        return
    try:
        nuke.addOnScriptLoad(on_load)
        nuke.addOnScriptSave(on_save)
    except Exception:
        pass


def add_menu_command(menu_path, label, command, shortcut=None):
    """Add a command to a Nuke menu (e.g. 'Nuke', 'Node Graph')."""
    if nuke is None:
        return
    try:
        menu = nuke.menu(menu_path)
        if shortcut:
            menu.addCommand(label, command, shortcut)
        else:
            menu.addCommand(label, command)
    except Exception:
        pass


def register_panel(widget_path, title, panel_id):
    """Make the editor available as a dockable panel (Pane menu)."""
    if nuke is None:
        return
    try:
        # Nuke evaluates widget_path in __main__; make the package reachable
        # there even when it was imported by a tool loader instead of menu.py.
        import __main__
        package = widget_path.split(".")[0]
        if package in sys.modules and not hasattr(__main__, package):
            setattr(__main__, package, sys.modules[package])
        import nukescripts
        nukescripts.panels.registerWidgetAsPanel(widget_path, title, panel_id)
    except Exception:
        pass


# ------------------------------------------------------------- #
#  Live script model (health check, node search)                #
# ------------------------------------------------------------- #
HEALTH_KNOBS = ("file", "disable", "size", "defocus", "scale")


def scene_nodes(all_knobs=False):
    """SceneNode list for every node in the script (groups included).

    With all_knobs=False only the knobs the health check needs are read,
    which keeps it fast on big scripts."""
    from .scripttools import SceneNode
    if nuke is None:
        return []
    result = []
    try:
        nodes = nuke.allNodes(recurseGroups=True)
    except Exception:
        return []
    for node in nodes:
        try:
            cls = node.Class()
            knobs = {}
            names = node.knobs().keys() if all_knobs else [k for k in HEALTH_KNOBS if node.knob(k) is not None]
            for name in names:
                knob = node.knob(name)
                if knob is None:
                    continue
                try:
                    knobs[name] = knob.toScript() if all_knobs else str(knob.value())
                except Exception:
                    continue
            if "file" in knobs and cls not in ("Write", "DeepWrite", "WriteGeo"):
                try:
                    knobs["file"] = nuke.filename(node) or knobs["file"]
                except Exception:
                    pass
            try:
                dependents = len(node.dependents())
            except Exception:
                dependents = None
            try:
                connected = sum(1 for i in range(node.inputs()) if node.input(i) is not None)
            except Exception:
                connected = None
            try:
                has_error = bool(node.hasError())
            except Exception:
                has_error = False
            result.append(SceneNode(node.fullName(), cls, knobs, dependents, connected, has_error))
        except Exception:
            continue
    return result


# ------------------------------------------------------------- #
#  Group contents as text                                       #
# ------------------------------------------------------------- #
GROUP_NODE_CLASSES = ("Group", "LiveGroup")


def _temp_nk():
    import tempfile
    handle, path = tempfile.mkstemp(suffix=".nk", prefix="nte_group_")
    os.close(handle)
    return path


def group_to_text(full_name):
    """The nodes inside a Group, as .nk text."""
    group = _node(full_name)
    if group is None:
        raise LookupError("Node {} not found".format(full_name))
    if group.Class() not in GROUP_NODE_CLASSES:
        raise ValueError("{} is a {}, not a Group. For a gizmo use 'Copy to Group' first.".format(
            full_name, group.Class()))
    path = _temp_nk()
    try:
        group.begin()
        try:
            previous = [n for n in nuke.allNodes() if n.isSelected()]
            for n in nuke.allNodes():
                n.setSelected(True)
            nuke.nodeCopy(path.replace("\\", "/"))
            for n in nuke.allNodes():
                n.setSelected(n in previous)
        finally:
            group.end()
        with io.open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def group_from_text(full_name, text):
    """Replace the contents of a Group with .nk text (undoable).
    Connections into the Group are kept."""
    group = _node(full_name)
    if group is None or group.Class() not in GROUP_NODE_CLASSES:
        raise LookupError("Group {} not found".format(full_name))
    path = _temp_nk()
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    inputs = [group.input(i) for i in range(group.inputs())]
    undo = nuke.Undo()
    undo.begin("Rebuild {}".format(full_name))
    try:
        group.begin()
        try:
            for n in nuke.allNodes():
                nuke.delete(n)
            nuke.nodePaste(path.replace("\\", "/"))
            for n in nuke.allNodes():
                n.setSelected(False)
        finally:
            group.end()
        for index, node in enumerate(inputs):
            if node is not None and index < group.inputs():
                group.setInput(index, node)
    finally:
        undo.end()
        try:
            os.remove(path)
        except OSError:
            pass


# ------------------------------------------------------------- #
#  Callbacks                                                    #
# ------------------------------------------------------------- #
CALLBACK_TABLES = (
    "onCreates", "onUserCreates", "onDestroys", "knobChangeds", "updateUIs", "autolabels",
    "onScriptLoads", "onScriptSaves", "onScriptCloses", "beforeRenders", "beforeFrameRenders",
    "afterFrameRenders", "afterRenders", "renderProgresses", "filenameFilters", "validateFilenames",
    "autoSaveFilters", "autoSaveRestoreFilters", "autoSaveDeleteFilters",
)
NODE_CALLBACK_KNOBS = ("knobChanged", "onCreate", "onDestroy", "updateUI", "autolabel",
                       "beforeRender", "beforeFrameRender", "afterFrameRender", "afterRender")


def _describe_callable(func):
    import inspect
    target = getattr(func, "__wrapped__", func)
    name = getattr(target, "__qualname__", None) or getattr(target, "__name__", None) or repr(target)
    module = getattr(target, "__module__", "") or ""
    try:
        path = inspect.getsourcefile(target) or ""
        line = inspect.getsourcelines(target)[1]
    except Exception:
        path, line = "", None
    return name, module, path, line


def registered_callbacks():
    """[(table, node_class, name, module, file, line)] for Nuke's global callbacks."""
    if nuke is None:
        return []
    result = []
    sources = [getattr(nuke, "callbacks", None), nuke]
    for table in CALLBACK_TABLES:
        entries = None
        for source in sources:
            entries = getattr(source, table, None) if source is not None else None
            if entries:
                break
        if not entries:
            continue
        groups = entries.items() if isinstance(entries, dict) else [("*", entries)]
        for node_class, items in groups:
            for item in items or []:
                func = item[0] if isinstance(item, (tuple, list)) and item else item
                if not callable(func):
                    continue
                name, module, path, line = _describe_callable(func)
                result.append((table, node_class, name, module, path, line))
    return result


def node_callback_knobs(full_name):
    """[(knob, script)] for callback knobs that are set on a node."""
    node = _node(full_name)
    if node is None:
        return []
    result = []
    for name in NODE_CALLBACK_KNOBS:
        knob = node.knob(name)
        try:
            value = knob.value() if knob is not None else ""
        except Exception:
            value = ""
        if value and str(value).strip():
            result.append((name, str(value)))
    return result


# ------------------------------------------------------------- #
#  StickyNote / Backdrop labels                                 #
# ------------------------------------------------------------- #
LABEL_CLASSES = ("StickyNote", "BackdropNode")


def create_sticky(text):
    if nuke is None:
        return None
    undo = nuke.Undo()
    undo.begin("Sticky note from Text Editor")
    try:
        sticky = nuke.createNode("StickyNote", inpanel=False)
        sticky["label"].setValue(text)
        return sticky.name()
    finally:
        undo.end()


def selected_label_node():
    """Full name of the selected StickyNote / Backdrop, or None."""
    for node in selected_nodes():
        try:
            if node.Class() in LABEL_CLASSES:
                return node.fullName()
        except Exception:
            continue
    return None


def get_label(full_name):
    node = _node(full_name)
    return node["label"].value() if node is not None and node.knob("label") is not None else None


def set_label(full_name, text):
    node = _node(full_name)
    if node is None or node.knob("label") is None:
        raise LookupError("{} has no label".format(full_name))
    undo = nuke.Undo()
    undo.begin("Label from Text Editor")
    try:
        node["label"].setValue(text)
    finally:
        undo.end()


# ------------------------------------------------------------- #
#  Console                                                      #
# ------------------------------------------------------------- #
def run_console(code):
    """Run one console entry. Expressions print their value like the
    Python prompt does. Returns (output, error_text)."""
    try:
        compile(code, "<console>", "eval")
        is_expression = True
    except SyntaxError:
        is_expression = False
    if is_expression:
        wrapped = "__nte_value = ({})\nif __nte_value is not None:\n    print(repr(__nte_value))\n".format(code)
        output, error, _line = run_python(wrapped, "<console>")
        import __main__
        __main__.__dict__.pop("__nte_value", None)
        return output, error
    output, error, _line = run_python(code, "<console>")
    return output, error


# ------------------------------------------------------------- #
#  Batch Gartner jobs (fallback when its module is not loaded)  #
# ------------------------------------------------------------- #
WRITE_CLASSES = ("Write", "Write2", "DeepWrite")


def selected_write_jobs():
    """(script_path, [job dict]) for selected, enabled Write nodes."""
    if nuke is None:
        return None, []
    script = script_path()
    first, last = frame_range()
    jobs = []
    exe = os.path.abspath(sys.executable)
    exe = exe if "nuke" in os.path.basename(exe).lower() else ""
    for node in selected_nodes():
        try:
            if node.Class() not in WRITE_CLASSES or node["disable"].value():
                continue
            try:
                output = nuke.filename(node) or node["file"].value()
            except Exception:
                output = node["file"].value()
            jobs.append({"write_node": node.fullName(), "output_path": output, "first": first,
                         "last": last, "nuke_exe": exe, "note": get_note(node.fullName()) or ""})
        except Exception:
            continue
    return script, jobs


def script_is_modified():
    try:
        return bool(nuke.root().modified())
    except Exception:
        return False


# ------------------------------------------------------------- #
#  Plates, pre-render check and version up                      #
# ------------------------------------------------------------- #
PLATE_CLASSES = ("Read", "DeepRead")
RENDER_WRITE_CLASSES = ("Write", "DeepWrite")


def _knob_value(node, name, default=None):
    knob = node.knob(name)
    if knob is None:
        return default
    try:
        return knob.value()
    except Exception:
        return default


def _evaluated_file(node):
    try:
        return nuke.filename(node) or _knob_value(node, "file", "") or ""
    except Exception:
        return _knob_value(node, "file", "") or ""


def _read_dict(node):
    return {
        "name": node.fullName(),
        "cls": node.Class(),
        "file": _evaluated_file(node),
        "raw": _knob_value(node, "file", "") or "",
        "first": _knob_value(node, "first"),
        "last": _knob_value(node, "last"),
        "frame_mode": _knob_value(node, "frame_mode", ""),
        "frame": _knob_value(node, "frame", ""),
        "colorspace": _knob_value(node, "colorspace", ""),
        "disable": bool(_knob_value(node, "disable", False)),
    }


def plate_reads():
    """Read dicts for every Read / DeepRead in the script (groups included)."""
    if nuke is None:
        return []
    result = []
    try:
        nodes = nuke.allNodes(recurseGroups=True)
    except Exception:
        return []
    for node in nodes:
        try:
            if node.Class() in PLATE_CLASSES and _knob_value(node, "file"):
                result.append(_read_dict(node))
        except Exception:
            continue
    return result


def set_read_file(full_name, new_raw):
    """Point a Read at another file and reload it (one undo step)."""
    node = _node(full_name)
    if node is None or node.knob("file") is None:
        return False
    undo = getattr(nuke, "Undo", None)
    try:
        if undo is not None:
            undo.begin("Update plate version")
        node["file"].setValue(new_raw)
        reload_knob = node.knob("reload")
        if reload_knob is not None:
            try:
                reload_knob.execute()
            except Exception:
                pass
        return True
    except Exception:
        return False
    finally:
        if undo is not None:
            try:
                undo.end()
            except Exception:
                pass


def write_names(selected_only=True):
    """Full names of enabled Write nodes (selected, or all in the script)."""
    if nuke is None:
        return []
    try:
        nodes = nuke.selectedNodes() if selected_only else nuke.allNodes(recurseGroups=True)
    except Exception:
        return []
    names = []
    for node in nodes:
        try:
            if node.Class() in RENDER_WRITE_CLASSES and not _knob_value(node, "disable", False):
                names.append(node.fullName())
        except Exception:
            continue
    return names


def _upstream(node):
    """Reads, disabled nodes, timing nodes and nodes with errors above a node."""
    from .shotcheck import TIMING_CLASSES
    result = {"reads": [], "disabled": [], "timing": [], "errors": []}
    flags = getattr(nuke, "INPUTS", 1) | getattr(nuke, "HIDDEN_INPUTS", 2)
    seen = set()
    stack = list(node.dependencies(flags))
    while stack:
        current = stack.pop()
        try:
            name = current.fullName()
        except Exception:
            continue
        if name in seen:
            continue
        seen.add(name)
        try:
            cls = current.Class()
            if _knob_value(current, "disable", False):
                result["disabled"].append(name)
            if cls in PLATE_CLASSES:
                result["reads"].append(_read_dict(current))
            elif cls in TIMING_CLASSES:
                result["timing"].append(name)
            try:
                if current.hasError() and cls not in ("Viewer",):
                    result["errors"].append(name)
            except Exception:
                pass
            stack.extend(current.dependencies(flags))
        except Exception:
            continue
    for key in ("disabled", "timing", "errors"):
        result[key].sort()
    return result


def write_check_data(full_name):
    """(write, root, upstream) dicts for shotcheck.check_write, or None."""
    node = _node(full_name)
    if node is None:
        return None
    first, last = frame_range()
    try:
        proxy = bool(nuke.root()["proxy"].value())
    except Exception:
        proxy = False
    create_dirs = _knob_value(node, "create_directories", None)
    try:
        has_error = bool(node.hasError())
    except Exception:
        has_error = False
    write = {
        "name": full_name,
        "file": _evaluated_file(node),
        "file_type": _knob_value(node, "file_type", ""),
        "colorspace": _knob_value(node, "colorspace", ""),
        "use_limit": bool(_knob_value(node, "use_limit", False)),
        "first": _knob_value(node, "first"),
        "last": _knob_value(node, "last"),
        "create_directories": None if create_dirs is None else bool(create_dirs),
        "has_error": has_error,
    }
    root = {"first": first or 1, "last": last or 1, "proxy": proxy, "script": script_path() or ""}
    return write, root, _upstream(node)


def this_node_name():
    """Full name of nuke.thisNode() inside a callback."""
    try:
        return nuke.thisNode().fullName()
    except Exception:
        return None


def is_gui():
    try:
        return bool(nuke.GUI)
    except Exception:
        return False


def add_before_render(callback):
    if nuke is None:
        return
    try:
        nuke.addBeforeRender(callback)
    except Exception:
        pass


def write_paths_with(tag):
    """(full_name, raw path) of Write nodes whose file path contains a version tag."""
    from .shotcheck import replace_version_in
    if nuke is None or not tag:
        return []
    result = []
    try:
        nodes = nuke.allNodes(recurseGroups=True)
    except Exception:
        return []
    for node in nodes:
        try:
            if node.Class() not in RENDER_WRITE_CLASSES:
                continue
            raw = _knob_value(node, "file", "") or ""
            if raw and replace_version_in(raw, tag, "\0") != raw:
                result.append((node.fullName(), raw))
        except Exception:
            continue
    return result


def save_script_as(path, write_updates=()):
    """Set new Write paths [(full_name, raw)] and save the script under a
    new name. Raises on failure."""
    if nuke is None:
        raise RuntimeError("Only available inside Nuke.")
    for full_name, raw in write_updates:
        node = _node(full_name)
        if node is not None and node.knob("file") is not None:
            node["file"].setValue(raw)
    nuke.scriptSaveAs(path)
