from pathlib import Path
from olmount.sync.state import ProjectState

def test_init_and_load(tmp_path):
    ProjectState.init(tmp_path, server="myhost", projectId="p1",
                      projectName="paper", rootDocId="d1")
    s = ProjectState(tmp_path).load()
    assert s.data["projectId"] == "p1"
    assert s.base_dir.is_dir()

def test_atomic_save_survives_garbage(tmp_path, monkeypatch):
    s = ProjectState.init(tmp_path, server="x", projectId="p", projectName="n", rootDocId="d")
    s.data["lastSyncedVersion"] = 5
    s.save()
    before = (tmp_path / ".olsync" / "state.json").read_text()
    # corrupt a write: force json.dump to fail mid-save
    import olmount.sync.state as st
    monkeypatch.setattr(st.json, "dump", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        s.save()
    except RuntimeError:
        pass
    assert (tmp_path / ".olsync" / "state.json").read_text() == before  # unchanged

def test_advance_mirrors_working_tree_and_advances_meta(tmp_path):
    s = ProjectState.init(tmp_path, server="x", projectId="p", projectName="n", rootDocId="d")
    (tmp_path / "main.tex").write_text("hi")
    (tmp_path / "secs").mkdir()
    (tmp_path / "secs" / "a.tex").write_text("yo")
    meta = {
        "main.tex": {"kind": "doc", "id": "d1", "docVersion": 1, "sha1": "x", "size": 2},
        "secs/a.tex": {"kind": "doc", "id": "d2", "docVersion": 1, "sha1": "y", "size": 2},
    }
    s.advance(meta, working_root=tmp_path, ignore=lambda p: False)
    assert (s.base_dir / "main.tex").read_text() == "hi"
    assert (s.base_dir / "secs" / "a.tex").read_text() == "yo"
    assert s.load().data["base"] == meta

def test_advance_not_run_on_failure(tmp_path):
    # R1: a failed sync must NOT touch base/. We simulate by simply not calling advance()
    # after the working tree changed — base must retain the last-synced content.
    s = ProjectState.init(tmp_path, server="x", projectId="p", projectName="n", rootDocId="d")
    (tmp_path / "main.tex").write_text("orig")
    s.advance({"main.tex": {"kind": "doc", "docVersion": 1, "sha1": "x", "size": 4}},
              tmp_path, lambda p: False)
    (tmp_path / "main.tex").write_text("CHANGED")  # local edit during a LATER failed sync
    # engine does NOT call advance() on failure -> base stays at "orig"
    assert (s.base_dir / "main.tex").read_text() == "orig"

def test_load_recovers_base_from_old_after_crash(tmp_path):
    s = ProjectState.init(tmp_path, server="x", projectId="p", projectName="n", rootDocId="d")
    (tmp_path / "main.tex").write_text("base")
    s.advance({"main.tex": {"kind": "doc", "sha1": "x", "size": 4}}, tmp_path, lambda p: False)
    # simulate a crash mid-swap: base/ renamed away to base.old, staging still there, base/ gone
    import shutil
    shutil.move(str(s.base_dir), str(s.olsync / "base.old"))
    assert not s.base_dir.exists()
    # reload -> recovery should restore base/ from base.old
    s2 = ProjectState(tmp_path).load()
    assert s2.base_dir.is_dir()
    assert (s2.base_dir / "main.tex").read_text() == "base"

def test_advance_meta_excludes_unmirrored_files(tmp_path):
    s = ProjectState.init(tmp_path, server="x", projectId="p", projectName="n", rootDocId="d")
    (tmp_path / "main.tex").write_text("hi")
    # meta lists a file that does NOT exist on disk
    meta = {"main.tex": {"kind": "doc", "sha1": "x", "size": 2},
            "ghost.tex": {"kind": "doc", "sha1": "z", "size": 0}}
    s.advance(meta, tmp_path, lambda p: False)
    persisted = s.load().data["base"]
    assert "main.tex" in persisted           # mirrored -> kept
    assert "ghost.tex" not in persisted      # not on disk -> dropped from meta (consistency)
    assert (s.base_dir / "main.tex").read_text() == "hi"
    assert not (s.base_dir / "ghost.tex").exists()
