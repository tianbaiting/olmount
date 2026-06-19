from __future__ import annotations
import socketio  # python-socketio client

def _new_transport(base_url, cookie):
    s = socketio.Client()
    return s

class EphemeralOLClient:
    """Short-lived Socket.IO client used only inside a sync pass."""
    def __init__(self, base_url: str, cookie: str):
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie
        self._s = None

    def __enter__(self): self.connect(); return self
    def __exit__(self, *a): self.disconnect()

    def connect(self):
        origin = self.base_url
        self._s = _new_transport(self.base_url, self.cookie)
        self._s.connect(origin, headers={"Origin": origin, "Cookie": self.cookie},
                        transports=["websocket"])
    def disconnect(self):
        if self._s is not None:
            try: self._s.disconnect()
            except Exception: pass
            self._s = None

    def _emit(self, event, data):
        if self._s is None:
            self._s = _new_transport(self.base_url, self.cookie)
        result = {}
        def cb(*args):
            result["res"] = args[0] if len(args) == 1 else args
        self._s.emit(event, data, callback=cb)
        self._s.sleep(0.01)  # let callback fire; engine callers run single-threaded
        return result.get("res")

    def join_project(self, project_id) -> dict:
        res = self._emit("joinProject", {"project_id": project_id})
        if isinstance(res, tuple):
            res = res[1] if len(res) > 1 else res[0]
        return res

    def join_doc(self, doc_id) -> dict:
        res = self._emit("joinDoc", {"doc_id": doc_id, "ranges": []})
        if isinstance(res, tuple):
            res = res[1] if len(res) > 1 else res[0]
        return res

    def apply_ot_update(self, doc_id, update: dict) -> dict:
        update = dict(update); update["doc"] = doc_id
        return self._emit("applyOtUpdate", update) or {}
