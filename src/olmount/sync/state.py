from __future__ import annotations
import json, os, shutil, tempfile
from pathlib import Path

class ProjectState:
    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.olsync = self.project_dir / ".olsync"
        self.state_path = self.olsync / "state.json"
        self.base_dir = self.olsync / "base"
        self.data: dict = {}

    @classmethod
    def init(cls, project_dir, *, server, projectId, projectName, rootDocId) -> "ProjectState":
        s = cls(project_dir)
        s.olsync.mkdir(parents=True, exist_ok=True)
        s.base_dir.mkdir(parents=True, exist_ok=True)
        s.data = {"server": server, "projectId": projectId, "projectName": projectName,
                  "rootDocId": rootDocId, "lastSyncedVersion": 0, "base": {}}
        s.save()
        return s

    def exists(self) -> bool:
        return self.state_path.is_file()

    def _recover_base(self) -> None:
        """Restore base/ if a crash interrupted the rename swap in advance()."""
        if not self.base_dir.exists():
            old = self.olsync / "base.old"
            staging = self.olsync / ".base-staging"
            if old.exists():
                old.rename(self.base_dir)
            elif staging.exists():
                staging.rename(self.base_dir)

    def load(self) -> "ProjectState":
        self._recover_base()
        self.data = json.loads(self.state_path.read_text())
        return self

    def save(self) -> None:
        """Atomic: write temp + os.replace. Never partially overwrites."""
        self.olsync.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.olsync), prefix="state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def base_content(self, relpath: str) -> bytes:
        return (self.base_dir / relpath).read_bytes()

    def advance(self, new_base_meta: dict, working_root: Path, ignore) -> None:
        """R1: rebuild .olsync/base/ from the working tree, then advance state.json.
        Uses a staging dir + rename swap so base/ is never half-written."""
        working_root = Path(working_root)
        staging = self.olsync / ".base-staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        mirrored = {}
        for relpath in new_base_meta:
            src = working_root / relpath
            if not src.is_file():
                continue
            dst = staging / relpath
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            mirrored[relpath] = new_base_meta[relpath]
        old = self.olsync / "base.old"
        if old.exists():
            shutil.rmtree(old)
        if self.base_dir.exists():
            self.base_dir.rename(old)
        staging.rename(self.base_dir)
        if old.exists():
            shutil.rmtree(old)
        self.data["base"] = mirrored
        self.save()
