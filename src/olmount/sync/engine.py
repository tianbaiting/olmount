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
