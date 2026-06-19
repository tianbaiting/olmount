import io, zipfile, json, pathlib
from olmount.sync.engine import build_local_snapshot, build_remote_snapshot
from olmount.sync.tree import RemoteTree
from olmount.util import sha1_hex

def _zip(files: dict) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    return zipfile.ZipFile(buf)

def test_local_snapshot_hashes_files(tmp_path):
    (tmp_path / "main.tex").write_text("hi")
    (tmp_path / "secs").mkdir()
    (tmp_path / "secs" / "a.tex").write_text("yo")
    snap = build_local_snapshot(tmp_path, ignore=lambda p: p.startswith(".olsync"))
    assert snap["main.tex"]["sha1"] == sha1_hex(b"hi")
    assert snap["secs/a.tex"]["sha1"] == sha1_hex(b"yo")

def test_remote_snapshot_combines_zip_and_tree():
    pay = json.loads(pathlib.Path("tests/fixtures/joinproject.json").read_text())
    tree = RemoteTree(pay)
    zf = _zip({"main.tex": "hi", "secs/a.tex": "yo", "logo.png": b"\x89PNG"})
    snap = build_remote_snapshot(zf, tree)
    assert snap["main.tex"]["kind"] == "doc"
    assert snap["main.tex"]["docVersion"] == 7
    assert snap["secs/a.tex"]["sha1"] == sha1_hex(b"yo")
    assert snap["logo.png"]["kind"] == "file"
