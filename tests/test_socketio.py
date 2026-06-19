import pytest
import threading
from olmount.api.socketio import EphemeralOLClient, _V09Transport

class FakeSock:
    def __init__(self):
        self.calls = []; self._responses = {}
    def on(self, ev, cb): pass
    def emit(self, ev, data, callback=None):
        self.calls.append((ev, data))
        if callback and ev in self._responses: callback(*self._responses[ev])
    def disconnect(self): self.calls.append(("disconnect", None))
    def connect(self): pass
    def sleep(self, secs): pass

def make(monkeypatch, responses):
    fake = FakeSock(); fake._responses = responses
    monkeypatch.setattr("olmount.api.socketio._new_transport", lambda *a, **k: fake)
    return EphemeralOLClient("https://ol.lab.edu/", "sharelatex.sid=x"), fake

def test_join_project(monkeypatch):
    c, fake = make(monkeypatch, {"joinProject": (None, {"rootFolder":[{"_id":"r"}]}, None, None)})
    proj = c.join_project("p1")
    assert fake.calls[0][0] == "joinProject"
    assert proj["rootFolder"][0]["_id"] == "r"

def test_join_doc(monkeypatch):
    c, fake = make(monkeypatch, {"joinDoc": (None, {"docLines":["a","b"],"version":5}, None, None)})
    res = c.join_doc("d1")
    assert res["version"] == 5 and res["docLines"] == ["a","b"]

def test_apply_ot_update_returns_accepted(monkeypatch):
    c, fake = make(monkeypatch, {"applyOtUpdate": ({"accepted": True, "v": 8},)})
    out = c.apply_ot_update("d1", {"doc":"d1","v":7,"op":[{"p":0,"i":"x"}]})
    assert out["accepted"] is True

def test_apply_ot_update_normalizes_multiarg(monkeypatch):
    # real servers may return (error, data) tuples; apply_ot_update must surface the dict, not the tuple
    c, fake = make(monkeypatch, {"applyOtUpdate": (None, {"accepted": False, "error": "otupdate", "v": 9})})
    out = c.apply_ot_update("d1", {"doc":"d1","v":7,"op":[]})
    assert isinstance(out, dict)
    assert out["accepted"] is False and out["v"] == 9

def test_emit_times_out_if_no_ack(monkeypatch):
    class NoAckSock:
        def on(self, ev, cb): pass
        def emit(self, ev, data, callback=None): pass  # never calls callback
        def disconnect(self): pass
        def connect(self): pass
    monkeypatch.setattr("olmount.api.socketio._new_transport", lambda *a, **k: NoAckSock())
    monkeypatch.setattr("olmount.api.socketio.EMIT_TIMEOUT", 0.1)
    c = EphemeralOLClient("https://ol.lab.edu/", "c")
    with pytest.raises(TimeoutError):
        c.join_project("p1")  # via _emit

def test_v09_detection_surfaces_legacy_transport_failure(monkeypatch):
    import olmount.api.socketio as sio_mod

    class Response:
        status_code = 200
        text = "abc:60:60:websocket,xhr-polling"

    class BrokenV09Transport:
        def __init__(self, *args, **kwargs): pass
        def connect(self): raise ModuleNotFoundError("No module named 'websocket'")

    v4_called = False

    class V4Transport:
        def connect(self, *args, **kwargs):
            nonlocal v4_called
            v4_called = True

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(sio_mod, "_V09Transport", BrokenV09Transport)
    monkeypatch.setattr(sio_mod, "_new_transport", lambda *args, **kwargs: V4Transport())

    c = EphemeralOLClient("https://ol.lab.edu/", "sharelatex.sid=x")
    with pytest.raises(ConnectionError) as exc:
        c.connect()

    assert "Socket.IO v0.9" in str(exc.value)
    assert v4_called is False

def test_connect_passes_project_id_to_v09_transport(monkeypatch):
    import olmount.api.socketio as sio_mod

    class Response:
        status_code = 200
        text = "abc:60:60:websocket,xhr-polling"

    seen = []

    class V09Transport:
        def __init__(self, *args, **kwargs): pass
        def connect(self, project_id=None):
            seen.append(project_id)

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(sio_mod, "_V09Transport", V09Transport)

    c = EphemeralOLClient("https://ol.lab.edu/", "sharelatex.sid=x")
    c.connect(project_id="p1")

    assert seen == ["p1"]

def test_v09_join_doc_normalizes_positional_ack():
    class V09Transport:
        def __init__(self):
            self.calls = []
        def emit_with_ack(self, event, args, timeout=None):
            self.calls.append((event, args))
            return [None, ["a", "b"], 5, [], {}]

    c = EphemeralOLClient("https://ol.lab.edu/", "sharelatex.sid=x")
    c._v09 = V09Transport()

    res = c.join_doc("d1")

    assert c._v09.calls == [("joinDoc", ["d1"])]
    assert res == {"docLines": ["a", "b"], "version": 5, "ops": [], "ranges": {}, "type": None}

def test_v09_apply_ot_update_accepts_empty_success_ack():
    class V09Transport:
        def __init__(self):
            self.calls = []
        def emit_with_ack(self, event, args, timeout=None):
            self.calls.append((event, args))
            return [None]

    c = EphemeralOLClient("https://ol.lab.edu/", "sharelatex.sid=x")
    c._v09 = V09Transport()

    res = c.apply_ot_update("d1", {"v": 7, "op": []})

    assert c._v09.calls == [("applyOtUpdate", ["d1", {"v": 7, "op": [], "doc": "d1"}])]
    assert res == {"accepted": True}

def test_v09_transport_handles_empty_ack_frame():
    transport = _V09Transport("https://ol.lab.edu/", "sharelatex.sid=x")
    box = {}
    done = threading.Event()
    transport._pending["3"] = (box, done)

    transport._handle("6:::3")

    assert done.is_set()
    assert box["res"] == []

def test_v09_join_project_uses_cached_join_project_response():
    class V09Transport:
        join_project_response = {
            "project": {"rootFolder": [{"_id": "root"}]},
            "permissionsLevel": "owner",
            "protocolVersion": 2,
        }
        def emit_with_ack(self, event, args, timeout=None):
            raise AssertionError("joinProjectResponse should avoid a legacy joinProject emit")

    c = EphemeralOLClient("https://ol.lab.edu/", "sharelatex.sid=x")
    c._v09 = V09Transport()

    assert c.join_project("p1") == {"rootFolder": [{"_id": "root"}]}

def test_v09_join_project_waits_for_async_join_project_response():
    class V09Transport:
        def __init__(self):
            self.join_project_response = None
            self._join_project_response_ready = threading.Event()
            self._connected_project_id = "p1"
            self._saw_connection_accepted = False
        def emit_with_ack(self, event, args, timeout=None):
            raise AssertionError("official joinProjectResponse should avoid a legacy joinProject emit")

    c = EphemeralOLClient("https://ol.lab.edu/", "sharelatex.sid=x")
    c._v09 = V09Transport()

    def set_response():
        c._v09.join_project_response = {"project": {"rootFolder": [{"_id": "root"}]}}
        c._v09._join_project_response_ready.set()

    threading.Timer(0.01, set_response).start()

    assert c.join_project("p1") == {"rootFolder": [{"_id": "root"}]}
