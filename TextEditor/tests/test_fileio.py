import os
import stat
import tempfile

from nuke_text_editor import fileio


def _tmp():
    return tempfile.mkdtemp()


def test_round_trips_keep_bytes():
    folder = _tmp()
    cases = [
        ("utf8_lf", "h\u00e9llo\nw\u00f6rld\n".encode("utf-8"), "utf-8", "\n"),
        ("utf8_crlf", "a\r\nb\r\n".encode("utf-8"), "utf-8", "\r\n"),
        ("cp1252", "caf\u00e9 \u20ac\n".encode("cp1252"), "cp1252", "\n"),
        ("bom", b"\xef\xbb\xbfhi\n", "utf-8-sig", "\n"),
        ("old_mac", b"a\rb\r", "utf-8", "\r"),
    ]
    for name, raw, encoding, newline in cases:
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(raw)
        text, enc, nl = fileio.read_text_file(path)
        assert (enc, nl) == (encoding, newline), name
        assert "\r" not in text
        fileio.write_text_file(path, text, enc, nl)
        with open(path, "rb") as handle:
            assert handle.read() == raw, name


def test_encoding_falls_back_to_utf8():
    path = os.path.join(_tmp(), "x.txt")
    used = fileio.write_text_file(path, "emoji \U0001F600", "cp1252")
    assert used == "utf-8"
    with open(path, "rb") as handle:
        assert handle.read().decode("utf-8") == "emoji \U0001F600"


def test_atomic_write_keeps_mode_and_symlink():
    folder = _tmp()
    path = os.path.join(folder, "run.sh")
    with open(path, "w") as handle:
        handle.write("x")
    os.chmod(path, 0o755)
    fileio.write_text_file(path, "y")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o755
    assert not [f for f in os.listdir(folder) if f.endswith(".tmp")]
    if hasattr(os, "symlink"):
        real = os.path.join(folder, "real.txt")
        link = os.path.join(folder, "link.txt")
        with open(real, "w") as handle:
            handle.write("a")
        os.symlink(real, link)
        fileio.write_text_file(link, "b")
        assert os.path.islink(link)
        with open(real) as handle:
            assert handle.read() == "b"


def test_strip_body_font():
    html = ('<html><body style=" font-family:\'Consolas\'; font-size:12pt; font-weight:400;">'
            '<p>x</p></body></html>')
    out = fileio.strip_body_font(html)
    assert "font-size" not in out and "font-family" not in out
    assert "font-weight:400" in out and "<p>x</p>" in out


def test_html_to_text():
    html = "<html><body><p>Hello &amp; <b>bye</b></p><p>- [ ] task</p></body></html>"
    assert fileio.html_to_text(html).strip().split("\n") == ["Hello & bye", "- [ ] task"]


def test_relativize_images_copies_into_files_folder():
    folder = _tmp()
    image = os.path.join(_tmp(), "shot.jpg")
    with open(image, "wb") as handle:
        handle.write(b"jpg")
    note = os.path.join(folder, "notes.tnote")
    html = '<p><img src="{}" width="100" /></p>'.format(image)
    out = fileio.relativize_images(html, note)
    assert 'src="notes_files/shot.jpg"' in out
    assert os.path.isfile(os.path.join(folder, "notes_files", "shot.jpg"))
    # Relative / missing sources are left alone.
    assert fileio.relativize_images('<img src="rel.png">', note) == '<img src="rel.png">'
    # Save As into another folder: relative images follow the note.
    other = os.path.join(_tmp(), "copy.tnote")
    moved = fileio.relativize_images(out, other, base_dir=folder)
    assert 'src="copy_files/shot.jpg"' in moved
    assert os.path.isfile(os.path.join(os.path.dirname(other), "copy_files", "shot.jpg"))


def test_helpers():
    assert fileio.to_list(None) == [] and fileio.to_list("a") == ["a"]
    assert fileio.to_bool("false", True) is False and fileio.to_bool(None, True) is True
    assert fileio.is_rich_path("/a/b.TNOTE") and not fileio.is_rich_path("/a/b.txt")
    assert fileio.language_for_path("x.gizmo") == "nuke"
    assert fileio.language_for_path(None) == "text"
    assert fileio.pid_alive(os.getpid())
    assert not fileio.pid_alive(-5)
