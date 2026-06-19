from __future__ import annotations
from enum import Enum
import posixpath
import pathlib

from olmount.util import sha1_hex


class Action(Enum):
    SKIP = "skip"
    PULL = "pull"
    PULL_DELETE = "pull_delete"
    PUSH = "push"
    PUSH_DELETE = "push_delete"
    CONFLICT = "conflict"
    NOOP = "noop"


def _meta_eq(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return False
    # docs compare by sha1+size+docVersion; files by sha1+size
    key = lambda m: (m.get("sha1"), m.get("size"), m.get("docVersion"))
    return key(a) == key(b)


def classify_path(path: str, base_meta, local_meta, remote_meta) -> Action:
    # convergence: local and remote agree -> in sync (covers add/add-equal & both-changed-same)
    if local_meta is not None and remote_meta is not None and _meta_eq(local_meta, remote_meta):
        return Action.SKIP
    in_base = base_meta is not None
    in_local = local_meta is not None
    in_remote = remote_meta is not None

    if not in_local:
        lstate = "absent"
    elif not in_base:
        lstate = "changed"        # new locally
    elif _meta_eq(base_meta, local_meta):
        lstate = "unchanged"
    else:
        lstate = "changed"

    if not in_remote:
        rstate = "absent"
    elif not in_base:
        rstate = "changed"        # new remotely
    elif _meta_eq(base_meta, remote_meta):
        rstate = "unchanged"
    else:
        rstate = "changed"

    return {
        ("unchanged", "unchanged"): Action.SKIP,
        ("unchanged", "changed"):   Action.PULL,
        ("unchanged", "absent"):    Action.PULL_DELETE,
        ("changed",   "unchanged"): Action.PUSH,
        ("changed",   "changed"):   Action.CONFLICT,
        ("changed",   "absent"):    Action.CONFLICT,   # edit/delete
        ("absent",    "unchanged"): Action.PUSH_DELETE,
        ("absent",    "changed"):   Action.CONFLICT,   # delete/edit
        ("absent",    "absent"):    Action.NOOP,
    }[(lstate, rstate)]


def build_local_snapshot(root, ignore) -> dict[str, dict]:
    root = pathlib.Path(root)
    snap = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if ignore(rel):
            continue
        data = p.read_bytes()
        snap[rel] = {"kind": _guess_kind(rel), "sha1": sha1_hex(data), "size": len(data)}
    return snap


def build_remote_snapshot(zf, tree: "RemoteTree") -> dict[str, dict]:
    snap = {}
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        eid, kind = tree.find_id_by_path(name) or (None, _guess_kind(name))
        data = zf.read(name)
        meta = {"kind": kind or _guess_kind(name), "sha1": sha1_hex(data), "size": len(data)}
        if eid:
            meta["id"] = eid
        if kind == "doc" and eid is not None:
            meta["docVersion"] = tree.doc_version(eid)
        snap[name] = meta
    return snap


def _guess_kind(path: str) -> str:
    return "doc" if posixpath.splitext(path)[1].lower() in {
        ".tex", ".bib", ".cls", ".sty", ".txt", ".md", ".latexmkrc", ".tikz"} else "file"


from olmount.sync.ot import diff_ops
from olmount.sync.merge import three_way_merge
from olmount.sync.tree import RemoteTree
from olmount.util import atomic_write_bytes

MAX_OT_RETRIES = 3

class Engine:
    def __init__(self, state, rest, sock, project_id, working_root, ignore, on_event=print):
        self.state = state
        self.rest = rest
        self.sock = sock
        self.project_id = project_id
        self.working_root = pathlib.Path(working_root)
        self.ignore = ignore
        self.on_event = on_event

    def _local_content(self, rel):
        return (self.working_root / rel).read_bytes()

    def _base_content(self, rel):
        return self.state.base_content(rel)

    def _remote_doc_text(self, doc_id):
        res = self.sock.join_doc(doc_id)
        return "".join(res["docLines"]), res["version"]

    def reconcile(self, direction="both"):
        st = self.state
        tree = RemoteTree(self.sock.join_project(self.project_id))
        zf = self.rest.download_zip(self.project_id)
        base_meta = st.data.get("base", {})
        local_snap = build_local_snapshot(self.working_root, self.ignore)
        remote_snap = build_remote_snapshot(zf, tree)

        actions = {}
        for p in set(base_meta) | set(local_snap) | set(remote_snap):
            if p.startswith(".olsync"):
                continue
            actions[p] = classify_path(p, base_meta.get(p), local_snap.get(p), remote_snap.get(p))

        results = {"pulled": [], "pushed": [], "conflicts": [], "deleted": []}
        ok = True
        try:
            if direction in ("both", "pull"):
                self._do_pulls(actions, zf, results)
            if direction in ("both", "push"):
                self._do_pushes(actions, base_meta, local_snap, remote_snap, tree, zf, results)
        except Exception as e:
            ok = False
            self.on_event(f"sync error: {e}")
            raise
        finally:
            # R1 + C1: advance only on success AND with no unresolved conflicts.
            # Unresolved conflicts preserve the original base so the local side is never lost.
            if ok and not results["conflicts"]:
                self._advance()
        return results

    def _do_pulls(self, actions, zf, results):
        for p, act in actions.items():
            if act == Action.PULL:
                atomic_write_bytes(self.working_root / p, zf.read(p))
                results["pulled"].append(p)
            elif act == Action.PULL_DELETE:
                fp = self.working_root / p
                if fp.is_file():
                    fp.unlink()
                results["deleted"].append(p)

    def _do_pushes(self, actions, base_meta, local_snap, remote_snap, tree, zf, results):
        for p, act in actions.items():
            if act == Action.PUSH:
                self._push_path(p, base_meta, local_snap, remote_snap, tree, results)
            elif act == Action.PUSH_DELETE:
                found = tree.find_id_by_path(p)
                if found:
                    eid, kind = found
                    self.rest.delete_entity(self.project_id, kind, eid)
                results["deleted"].append(p)
            elif act == Action.CONFLICT:
                self._resolve_conflict(p, base_meta, local_snap, remote_snap, tree, zf, results)

    def _push_path(self, p, base_meta, local_snap, remote_snap, tree, results):
        kind = local_snap[p]["kind"]
        parent = self._ensure_parent(p, tree)
        data = self._local_content(p)
        if p not in remote_snap:
            if kind == "doc":
                self.rest.add_doc(self.project_id, parent, pathlib.Path(p).name)
            else:
                self.rest.upload_file(self.project_id, parent, pathlib.Path(p).name, data)
            results["pushed"].append(p)
        else:
            found = tree.find_id_by_path(p)
            if not found:
                # I3: surface missing id rather than silently dropping the push
                results["conflicts"].append(p)
                self.on_event(f"cannot push {p}: no remote id resolved")
                return
            eid, _rkind = found
            if kind == "doc":
                self._apply_doc_update(p, eid, base_meta, data.decode("utf-8"), results)
            else:
                self.rest.upload_file(self.project_id, parent, pathlib.Path(p).name, data)
                self.rest.delete_entity(self.project_id, "file", eid)
                results["pushed"].append(p)

    def _apply_doc_update(self, p, doc_id, base_meta, local_text, results):
        """R3: push `local_text` (the desired final content) via OT, generating ops from
        CURRENT remote. On rejection re-fetch + full re-merge + regenerate; never resend
        stale ops. If remote already equals target (converged), that's a no-op success."""
        base_text = self._base_content(p).decode("utf-8") if p in base_meta else local_text
        remote_now, remote_version = self._remote_doc_text(doc_id)
        target = local_text
        if remote_now != base_text:
            target, _conf = three_way_merge(base_text, local_text, remote_now)
        for _ in range(MAX_OT_RETRIES):
            ops = diff_ops(remote_now, target)  # from CURRENT remote -> target
            if not ops:
                # I4: nothing to push — remote already equals target (converged) -> success
                results["pushed"].append(p)
                return
            resp = self.sock.apply_ot_update(doc_id, {
                "doc": doc_id, "v": remote_version,
                "lastV": base_meta.get(p, {}).get("docVersion"),
                "op": ops}) or {}
            if resp.get("accepted"):
                results["pushed"].append(p)
                return
            # rejected: re-fetch remote, full re-merge, regenerate (R3 — never resend stale ops)
            remote_now, remote_version = self._remote_doc_text(doc_id)
            target, _ = three_way_merge(base_text, local_text, remote_now)
        results["conflicts"].append(p)
        self.on_event(f"OT conflict (could not converge): {p}")

    def _resolve_conflict(self, p, base_meta, local_snap, remote_snap, tree, zf, results):
        kind = (local_snap.get(p, {}) or {}).get("kind") or (remote_snap.get(p, {}) or {}).get("kind", "doc")
        if kind == "doc":
            local_text = self._local_content(p).decode("utf-8")
            # C1 protection: unresolved markers from a prior pass -> do not clobber
            if "<<<<<<< local" in local_text and ">>>>>>> remote" in local_text:
                results["conflicts"].append(p)
                self.on_event(f"unresolved conflict markers in {p}; edit to remove markers then re-run")
                return
            base_text = self._base_content(p).decode("utf-8") if p in base_meta else ""
            remote_text = zf.read(p).decode("utf-8") if zf is not None else None
            if remote_text is None:
                found = tree.find_id_by_path(p)
                remote_text, _ = self._remote_doc_text(found[0]) if found else ("", 0)
            merged, conflict = three_way_merge(base_text, local_text, remote_text)
            atomic_write_bytes(self.working_root / p, merged.encode("utf-8"))
            if conflict:
                results["conflicts"].append(p)
                self.on_event(f"conflict markers written: {p}; resolve and re-run")
            else:
                # C1 fix: clean auto-merge -> PUSH via OT so remote converges (no data loss)
                found = tree.find_id_by_path(p)
                if found:
                    self._apply_doc_update(p, found[0], base_meta, merged, results)
                else:
                    results["pushed"].append(p)
        else:
            local_data = self._local_content(p)
            remote_data = zf.read(p) if zf is not None else b""
            atomic_write_bytes(self.working_root / _suffixed(p, "LOCAL"), local_data)
            atomic_write_bytes(self.working_root / _suffixed(p, "REMOTE"), remote_data)
            results["conflicts"].append(p)
            self.on_event(f"binary conflict (keep-both): {p}")

    def _ensure_parent(self, p, tree):
        # I2 fix: accumulator (not parts.index, which breaks on repeated segment names)
        parts = p.split("/")[:-1]
        cur = tree.root_folder_id()
        acc = ""
        for part in parts:
            acc = f"{acc}{part}/" if acc else f"{part}/"
            found = tree.find_id_by_path(acc.rstrip("/"))
            if found:
                cur = found[0]
            else:
                # tree.find_id_by_path only indexes docs/files; also check tree.nodes for
                # an existing folder with this name under the current parent (so we don't
                # recreate folders that already exist remotely)
                fid = next((nid for nid, n in tree.nodes.items()
                            if n.kind == "folder" and n.parent == cur and n.name == part), None)
                if fid:
                    cur = fid
                else:
                    made = self.rest.add_folder(self.project_id, cur, part)
                    cur = made["_id"]
        return cur

    def _advance(self):
        # I1 fix: refresh the tree so docVersions reflect post-push state
        tree = RemoteTree(self.sock.join_project(self.project_id))
        zf = self.rest.download_zip(self.project_id)
        remote_snap = build_remote_snapshot(zf, tree)
        local_snap = build_local_snapshot(self.working_root, self.ignore)
        new_base = {}
        for p in set(remote_snap) | set(local_snap):
            if p.startswith(".olsync"):
                continue
            m = dict(local_snap.get(p) or remote_snap.get(p))
            if p in remote_snap and "id" in remote_snap[p]:
                m["id"] = remote_snap[p]["id"]
            if p in remote_snap and "docVersion" in remote_snap[p]:
                m["docVersion"] = remote_snap[p]["docVersion"]
            new_base[p] = m
        self.state.advance(new_base, self.working_root, self.ignore)


def _suffixed(p, tag):
    from pathlib import PurePosixPath
    pp = PurePosixPath(p)
    if pp.suffix:
        return str(pp.with_name(f"{pp.stem}.{tag}{pp.suffix}"))
    return f"{p}.{tag}"
