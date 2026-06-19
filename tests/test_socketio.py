import pytest
from olmount.api.socketio import EphemeralOLClient

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
