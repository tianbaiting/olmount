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


import json as _json
import pathlib as _pl
from olmount.sync.engine import Engine
from olmount.sync.state import ProjectState
from olmount.sync.ot import apply_ops
from olmount.util import sha1_hex

_BASE = "line1\nline2\nline3\n"   # base main.tex content; docVersion 7 (matches fixture)

def _bootstrap(tmp_path, main_content=_BASE):
    ProjectState.init(tmp_path, server="x", projectId="p1", projectName="paper", rootDocId="d1")
    (tmp_path / "main.tex").write_text(main_content)
    st = ProjectState(tmp_path).load()
    data = main_content.encode()
    st.advance({"main.tex": {"kind": "doc", "id": "d1", "docVersion": 7,
                             "sha1": sha1_hex(data), "size": len(data)}},
               tmp_path, lambda p: p.startswith(".olsync"))
    return st.load()

class FakeREST:
    def __init__(self, zip_main):
        self._zip_main = zip_main
        self.created = []; self.deleted = []; self.uploaded = []
    def download_zip(self, pid):
        return _zip({"main.tex": self._zip_main})
    def add_doc(self, pid, pfid, n): self.created.append(("doc", n)); return {"_id": "newdoc", "_type": "doc"}
    def add_folder(self, pid, pfid, n): return {"_id": "newf", "name": n}
    def upload_file(self, pid, pfid, n, d): self.uploaded.append(n); return {"success": True}
    def delete_entity(self, pid, k, eid): self.deleted.append((k, eid))
    def rename_entity(self, *a): pass
    def move_entity(self, *a): pass
    def get_file(self, pid, fid): return b""

class FakeSock:
    def __init__(self, doc_content, doc_version, reject_once=False, concurrent_content=None):
        self.doc = {"d1": {"content": doc_content, "version": doc_version}}
        self.reject_once = reject_once
        self.concurrent_content = concurrent_content
        self.applies = []
    def join_project(self, pid):
        return _json.loads(_pl.Path("tests/fixtures/joinproject.json").read_text())
    def join_doc(self, did):
        d = self.doc[did]
        return {"docLines": d["content"].splitlines(keepends=True), "version": d["version"]}
    def apply_ot_update(self, did, upd):
        if self.reject_once:
            self.reject_once = False
            self.doc[did]["version"] += 1
            if self.concurrent_content is not None:
                self.doc[did]["content"] = self.concurrent_content
            return {"accepted": False, "error": "otupdate", "v": self.doc[did]["version"]}
        self.applies.append((did, upd))
        self.doc[did]["content"] = apply_ops(upd["op"], self.doc[did]["content"])
        self.doc[did]["version"] += 1
        return {"accepted": True, "v": self.doc[did]["version"]}

def _engine(tmp_path, st, rest, sock):
    return Engine(state=st, rest=rest, sock=sock, project_id="p1",
                  working_root=tmp_path, ignore=lambda p: p.startswith(".olsync"),
                  on_event=lambda *a: None)

def test_pull_creates_remote_new_content_locally(tmp_path):
    st = _bootstrap(tmp_path)  # local main.tex == base, unchanged
    rest = FakeREST(zip_main="REMOTE NEW CONTENT\n")          # remote changed
    sock = FakeSock(doc_content=_BASE, doc_version=7)
    _engine(tmp_path, st, rest, sock).reconcile(direction="both")
    assert (tmp_path / "main.tex").read_text() == "REMOTE NEW CONTENT\n"

def test_push_doc_edit_accepted_first_try(tmp_path):
    st = _bootstrap(tmp_path)
    (tmp_path / "main.tex").write_text("line1\nline2 CHANGED\nline3\n")  # local edit line 2
    rest = FakeREST(zip_main=_BASE)                          # remote == base (unchanged) -> PUSH
    sock = FakeSock(doc_content=_BASE, doc_version=7)
    r = _engine(tmp_path, st, rest, sock).reconcile(direction="push")
    assert sock.applies, "expected an apply"
    sent_op = sock.applies[0][1]["op"]
    assert apply_ops(sent_op, _BASE) == "line1\nline2 CHANGED\nline3\n"

def test_r3_ot_repair_remerges_and_resends(tmp_path):
    local_content = "line1 LOCAL\nline2\nline3\n"             # local changed line 1
    concurrent = "line1\nline2\nline3 REMOTE\n"               # concurrent edit on line 3 (line2 is shared anchor -> clean merge)
    st = _bootstrap(tmp_path)
    (tmp_path / "main.tex").write_text(local_content)
    rest = FakeREST(zip_main=_BASE)                          # remote initially == base -> PUSH
    sock = FakeSock(doc_content=_BASE, doc_version=7, reject_once=True, concurrent_content=concurrent)
    r = _engine(tmp_path, st, rest, sock).reconcile(direction="push")
    assert sock.applies, "expected a successful apply after repair"
    sent_op = sock.applies[0][1]["op"]
    # the FINAL op is relative to the post-rejection remote (concurrent); 3-way merge of
    # (base, local, concurrent) is non-conflicting (line1 local vs line3 remote, line2 shared)
    # -> "line1 LOCAL\nline2\nline3 REMOTE\n"
    expected_merged = "line1 LOCAL\nline2\nline3 REMOTE\n"
    assert apply_ops(sent_op, concurrent) == expected_merged

def test_conflict_overlapping_writes_markers_and_does_not_push(tmp_path):
    st = _bootstrap(tmp_path)
    (tmp_path / "main.tex").write_text("line1\nLINE2 LOCAL\nline3\n")   # local changed line 2
    rest = FakeREST(zip_main="line1\nLINE2 REMOTE\nline3\n")            # remote changed same line -> CONFLICT
    sock = FakeSock(doc_content="line1\nLINE2 REMOTE\nline3\n", doc_version=7)
    r = _engine(tmp_path, st, rest, sock).reconcile(direction="both")
    on_disk = (tmp_path / "main.tex").read_text()
    assert "<<<<<<< local" in on_disk and "=======" in on_disk and ">>>>>>> remote" in on_disk
    assert "LINE2 LOCAL" in on_disk and "LINE2 REMOTE" in on_disk
    assert not sock.applies, "conflict must not be pushed"


def test_push_force_overwrites_remote_conflict_with_local(tmp_path):
    st = _bootstrap(tmp_path)
    local = "line1\nLINE2 LOCAL\nline3\n"
    remote = "line1\nLINE2 REMOTE\nline3\n"
    (tmp_path / "main.tex").write_text(local)
    rest = FakeREST(zip_main=remote)
    sock = FakeSock(doc_content=remote, doc_version=7)

    r = _engine(tmp_path, st, rest, sock).reconcile(direction="push", force=True)

    assert "main.tex" in r["pushed"]
    assert sock.doc["d1"]["content"] == local
    assert "main.tex" not in r["conflicts"]


def test_pull_force_overwrites_local_conflict_with_remote(tmp_path):
    st = _bootstrap(tmp_path)
    remote = "line1\nLINE2 REMOTE\nline3\n"
    (tmp_path / "main.tex").write_text("line1\nLINE2 LOCAL\nline3\n")
    rest = FakeREST(zip_main=remote)
    sock = FakeSock(doc_content=remote, doc_version=7)

    r = _engine(tmp_path, st, rest, sock).reconcile(direction="pull", force=True)

    assert "main.tex" in r["pulled"]
    assert (tmp_path / "main.tex").read_text() == remote
    assert "main.tex" not in r["conflicts"]


def test_c1_clean_auto_merge_is_pushed_no_data_loss_two_passes(tmp_path):
    # non-overlapping changes: local edits line2, remote edits line4 -> clean 3-way merge.
    # C1 fix: the merged result MUST be pushed, so a second pass converges (SKIP) and the
    # local edit (LINE2) is preserved. Without the fix, pass 2 would pull remote and lose LINE2.
    base = "line1\nline2\nline3\nline4\n"
    local = "line1\nLINE2\nline3\nline4\n"        # local: line2
    remote = "line1\nline2\nline3\nLINE4\n"        # remote: line4
    merged = "line1\nLINE2\nline3\nLINE4\n"
    ProjectState.init(tmp_path, server="x", projectId="p1", projectName="paper", rootDocId="d1")
    (tmp_path / "main.tex").write_text(base)
    st = ProjectState(tmp_path).load()
    bd = base.encode()
    st.advance({"main.tex": {"kind": "doc", "id": "d1", "docVersion": 7,
                             "sha1": sha1_hex(bd), "size": len(bd)}}, tmp_path,
               lambda p: p.startswith(".olsync"))
    st.load()
    (tmp_path / "main.tex").write_text(local)       # local edit
    sock = FakeSock(doc_content=remote, doc_version=7)   # remote has line4 edit
    rest = FakeREST(zip_main=remote)
    # make download_zip reflect the (pushed) doc content so a 2nd pass sees convergence
    rest.download_zip = lambda pid: _zip({"main.tex": sock.doc["d1"]["content"]})
    eng = Engine(state=st, rest=rest, sock=sock, project_id="p1", working_root=tmp_path,
                 ignore=lambda p: p.startswith(".olsync"), on_event=lambda *a: None)
    r1 = eng.reconcile(direction="both")
    assert "main.tex" in r1["pushed"], "clean auto-merge must be pushed (C1)"
    assert (tmp_path / "main.tex").read_text() == merged
    # pass 2: base==local==remote==merged -> SKIP, no pull/conflict, LINE2 preserved
    st2 = ProjectState(tmp_path).load()
    eng2 = Engine(state=st2, rest=rest, sock=sock, project_id="p1", working_root=tmp_path,
                  ignore=lambda p: p.startswith(".olsync"), on_event=lambda *a: None)
    r2 = eng2.reconcile(direction="both")
    assert "main.tex" not in r2["pulled"] and "main.tex" not in r2["conflicts"]
    assert (tmp_path / "main.tex").read_text() == merged   # LINE2 preserved (no data loss)


def test_ensure_parent_handles_repeated_segment_names(tmp_path):
    # build a tree whose root has a folder "a"; pushing a/a/file.tex must create a NESTED a
    import olmount.sync.tree as treemod
    project = {"rootFolder": [{"_id": "root0", "name": "root", "docs": [], "fileRefs": [],
                               "folders": [{"_id": "A1", "name": "a", "docs": [], "fileRefs": [], "folders": []}]}],
               "rootDoc_id": ""}
    tree = treemod.RemoteTree(project)
    created = []
    class REST:
        def add_folder(self, pid, pfid, n): created.append((pfid, n)); return {"_id": f"new-{n}", "name": n}
    st = ProjectState.init(tmp_path, server="x", projectId="p1", projectName="p", rootDocId="")
    eng = Engine(state=st, rest=REST(), sock=None, project_id="p1", working_root=tmp_path,
                 ignore=lambda p: False, on_event=lambda *a: None)
    parent = eng._ensure_parent("a/a/file.tex", tree)
    # must have created a nested "a" under A1 (the existing top-level a), not returned A1
    assert ("A1", "a") in created, "expected a nested folder 'a' created under the existing 'a'"


def test_apply_doc_update_empty_ops_is_success_not_conflict(tmp_path):
    # I4: if remote already equals target, there's nothing to push -> success, not conflict
    base = "line1\nline2\nline3\n"
    ProjectState.init(tmp_path, server="x", projectId="p1", projectName="p", rootDocId="d1")
    (tmp_path / "main.tex").write_text(base)
    st = ProjectState(tmp_path).load()
    bd = base.encode()
    st.advance({"main.tex": {"kind": "doc", "id": "d1", "docVersion": 7, "sha1": sha1_hex(bd), "size": len(bd)}},
               tmp_path, lambda p: p.startswith(".olsync"))
    st.load()
    sock = FakeSock(doc_content=base, doc_version=7)   # remote == local == base
    rest = FakeREST(zip_main=base)
    rest.download_zip = lambda pid: _zip({"main.tex": sock.doc["d1"]["content"]})
    eng = Engine(state=st, rest=rest, sock=sock, project_id="p1", working_root=tmp_path,
                 ignore=lambda p: p.startswith(".olsync"), on_event=lambda *a: None)
    # force a PUSH-classified path whose target already equals remote -> empty ops -> success
    eng._apply_doc_update("main.tex", "d1", st.data["base"], base, results := {"pulled": [], "pushed": [], "conflicts": [], "deleted": []})
    assert "main.tex" in results["pushed"] and "main.tex" not in results["conflicts"]


def test_remote_doc_text_reconstructs_overleaf_line_array(tmp_path):
    class Sock:
        def join_doc(self, doc_id):
            return {"docLines": ["line1", "line2", ""], "version": 3}

    eng = Engine(state=None, rest=None, sock=Sock(), project_id="p1",
                 working_root=tmp_path, ignore=lambda p: False, on_event=lambda *a: None)

    assert eng._remote_doc_text("d1") == ("line1\nline2\n", 3)
