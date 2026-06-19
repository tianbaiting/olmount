from __future__ import annotations
from dataclasses import dataclass

KIND_DOC, KIND_FILE, KIND_FOLDER = "doc", "file", "folder"

@dataclass
class _Node:
    id: str; name: str; kind: str; parent: str | None = None
    doc_version: int | None = None

class RemoteTree:
    """Parses a joinProject `project` payload into a flat id->node and path->id map."""
    def __init__(self, project: dict):
        self.project = project
        self.nodes: dict[str, _Node] = {}
        self._by_path: dict[str, tuple[str, str]] = {}
        root = project["rootFolder"][0]
        self._root_id = root["_id"]
        self._walk_folder(root, None, "")

    def _walk_folder(self, folder: dict, parent_id: str | None, prefix: str):
        fid = folder["_id"]; name = folder.get("name", "")
        self.nodes[fid] = _Node(fid, name, KIND_FOLDER, parent_id)
        for d in folder.get("docs", []):
            self.nodes[d["_id"]] = _Node(d["_id"], d["name"], KIND_DOC, fid, d.get("version"))
            path = prefix + d["name"]; self._by_path[path] = (d["_id"], KIND_DOC)
        for f in folder.get("fileRefs", []):
            self.nodes[f["_id"]] = _Node(f["_id"], f["name"], KIND_FILE, fid)
            path = prefix + f["name"]; self._by_path[path] = (f["_id"], KIND_FILE)
        for sub in folder.get("folders", []):
            self._walk_folder(sub, fid, prefix + sub["name"] + "/")

    def root_folder_id(self) -> str: return self._root_id
    def doc_version(self, doc_id) -> int | None: return self.nodes[doc_id].doc_version
    def parent_folder_id(self, entity_id) -> str | None: return self.nodes[entity_id].parent
    def find_id_by_path(self, path) -> tuple[str, str] | None: return self._by_path.get(path)
    def find_path_by_id(self, entity_id) -> str | None:
        for p,(eid,_) in self._by_path.items():
            if eid == entity_id: return p
        return None
    def walk(self):
        for path,(eid,kind) in self._by_path.items(): yield path, (eid, kind)
