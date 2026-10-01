import os
import tempfile

from nuke_text_editor import nkparse

SCRIPT = r"""#! /usr/local/Nuke13.2v5/libnuke-13.2.5.so -nx
version 13.2 v5
define_window_layout_xml {<?xml version="1.0" encoding="UTF-8"?>
<layout version="1.0">
    <window x="0" y="0" w="1920" h="1080" screen="0">
    </window>
</layout>
}
Root {
 inputs 0
 name /jobs/sh010/sh010_comp_v012.nk
 first_frame 1001
 last_frame 1100
}
Read {
 inputs 0
 file /jobs/sh010/plate/sh010_plate.####.exr
 name Read1
 xpos 100
 ypos -200
}
Grade {
 white {1.2 1.1 1 1}
 gamma {{curve x1001 1 x1050 1.2}}
 name Grade1
 xpos 100
}
Group {
 name Group1
}
 Input {
  inputs 0
  name Input1
 }
 Blur {
  size 4
  name Blur1
 }
 Output {
  name Output1
 }
end_group
set N1234 [stack 0]
Write {
 file "[python {nuke.script_directory()}]/render/out.%04d.exr"
 name Write1
}
"""


def test_parse_nodes():
    nodes = nkparse.parse_nk(SCRIPT)
    names = [n.full_name for n in nodes]
    assert names == ["Root", "Read1", "Grade1", "Group1", "Group1.Input1", "Group1.Blur1",
                     "Group1.Output1", "Write1"]
    grade = nodes[2]
    assert grade.cls == "Grade"
    assert grade.knobs["white"] == "1.2 1.1 1 1"
    assert grade.knobs["gamma"] == "{curve x1001 1 x1050 1.2}"
    assert SCRIPT.split("\n")[grade.line].strip() == "Grade {"
    assert nodes[5].knobs["size"] == "4"
    assert nodes[7].knobs["file"].startswith("[python")


def test_compare():
    old = nkparse.parse_nk(SCRIPT)
    new_text = (SCRIPT.replace(" size 4", " size 8")
                .replace(" xpos 100\n ypos -200", " xpos 300\n ypos -200")
                .replace("Write {", "Dot {\n name Dot1\n}\nWrite {")
                .replace("Grade {\n white {1.2 1.1 1 1}\n gamma {{curve x1001 1 x1050 1.2}}\n name Grade1\n xpos 100\n}\n", ""))
    result = nkparse.compare_nodes(old, nkparse.parse_nk(new_text))
    assert [n.full_name for n in result["added"]] == ["Dot1"]
    assert [n.full_name for n in result["removed"]] == ["Grade1"]
    changed = dict((n.full_name, d) for n, d in result["changed"])
    assert changed == {"Group1.Blur1": [("size", "4", "8")]}  # xpos ignored


def test_file_paths_and_status():
    folder = tempfile.mkdtemp()
    for frame in (1001, 1002):
        open(os.path.join(folder, "plate.{}.exr".format(frame)), "w").close()
    assert nkparse.path_status(os.path.join(folder, "plate.####.exr")) == "ok"
    assert nkparse.path_status(os.path.join(folder, "plate.%04d.exr")) == "ok"
    assert nkparse.path_status(os.path.join(folder, "other.%04d.exr")) == "missing"
    assert nkparse.path_status("[python x]/a.exr") == "expression"
    assert nkparse.path_status("plate.####.exr", script_dir=folder) == "ok"
    paths = nkparse.file_paths(nkparse.parse_nk(SCRIPT))
    assert [(n.name, k) for n, k, _p in paths] == [("Read1", "file"), ("Write1", "file")]
