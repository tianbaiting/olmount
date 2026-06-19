from __future__ import annotations
import json
import threading
import socketio  # python-socketio client

EMIT_TIMEOUT = 30.0

def _new_transport(base_url, cookie):
    s = socketio.Client()
    return s

def _have_websocket_transport() -> bool:
    try:
        import websocket  # noqa: F401  (websocket-client)
        return True
    except ImportError:
        return False

def _looks_like_v09_handshake(text: str) -> bool:
    """Socket.IO v0.9 handshake response: 'sid:hb:close:transports'."""
    parts = text.split(":")
    if len(parts) < 4:
        return False
    transports = parts[3]
    return any(t in transports for t in ("websocket", "xhr-polling", "jsonp-polling"))


class _V09Transport:
    """Minimal Socket.IO v0.9 client over websocket-client.

    Legacy ShareLaTeX servers speak Socket.IO v0.9, which uses a
    completely different wire protocol from the Engine.IO v4 that
    ``python-socketio`` (>=5.x) implements.  This transport performs the
    v0.9 handshake (``GET /socket.io/1/``) and carries emit/ack traffic
    over a raw WebSocket connection.
    """

    def __init__(self, base_url: str, cookie: str):
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie
        self._ws = None
        self._ack_counter = 0
        self._pending: dict[str, tuple[dict, threading.Event]] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._closed = False
        self._ready = threading.Event()

    # -- connection -------------------------------------------------------

    def connect(self) -> None:
        import requests
        import websocket

        # v0.9 handshake: GET /socket.io/1/ -> "sid:hb:close:transports"
        r = requests.get(
            f"{self.base_url}/socket.io/1/",
            headers={"Cookie": self.cookie},
            timeout=10,
        )
        r.raise_for_status()
        if not _looks_like_v09_handshake(r.text):
            raise ConnectionError(f"unexpected v0.9 handshake response: {r.text!r}")
        sid = r.text.split(":")[0]

        # websocket transport
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url += f"/socket.io/1/websocket/{sid}"
        self._ws = websocket.create_connection(
            ws_url,
            header=[f"Cookie: {self.cookie}"],
            origin=self.base_url,
            timeout=30,
        )

        # background reader dispatches acks and server events
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        # heartbeat: v0.9 expects client heartbeats within the timeout window
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat.start()

        # wait for connectionAccepted (or plain connect frame) before returning
        if not self._ready.wait(timeout=15):
            self.disconnect()
            raise ConnectionError("v0.9: timed out waiting for connectionAccepted")

    def disconnect(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        # wake up any pending ack callers so they don't hang
        with self._lock:
            for _, (_box, done) in self._pending.items():
                done.set()
            self._pending.clear()

    # -- background loops -------------------------------------------------

    def _read_loop(self) -> None:
        ws = self._ws
        try:
            while not self._closed and ws is not None:
                try:
                    msg = ws.recv()
                except Exception:
                    break
                if not msg:
                    break
                self._handle(msg)
        finally:
            self._closed = True
            with self._lock:
                for _, (_box, done) in self._pending.items():
                    done.set()
                self._pending.clear()

    def _heartbeat_loop(self) -> None:
        import time
        # wait until the connection is ready before starting heartbeats
        self._ready.wait(timeout=15)
        while not self._closed:
            time.sleep(50)  # server heartbeat timeout is typically 60s
            if self._closed:
                break
            try:
                self._send("2::")
            except Exception:
                break

    def _handle(self, msg: str) -> None:
        # v0.9 frame:  type : id : endpoint : data
        parts = msg.split(":", 3)
        ptype = parts[0]
        if ptype == "0":  # disconnect
            self._closed = True
        elif ptype == "1":  # connect
            self._ready.set()
        elif ptype == "2":  # heartbeat
            try:
                self._send("2::")
            except Exception:
                pass
        elif ptype == "5":  # event
            data_str = parts[3] if len(parts) > 3 else ""
            try:
                evt = json.loads(data_str)
                if evt.get("name") == "connectionAccepted":
                    self._ready.set()
            except Exception:
                pass
        elif ptype == "6":  # ack
            data_str = parts[3] if len(parts) > 3 else ""
            # format: "<ack_id>+[json_array]"
            plus = data_str.find("+")
            if plus < 0:
                return
            ack_id = data_str[:plus]
            try:
                ack_data = json.loads(data_str[plus + 1:])
            except Exception:
                ack_data = None
            with self._lock:
                entry = self._pending.pop(ack_id, None)
            if entry is not None:
                box, done = entry
                box["res"] = ack_data
                done.set()

    # -- emit -------------------------------------------------------------

    def _send(self, frame: str) -> None:
        with self._send_lock:
            if self._ws is not None:
                self._ws.send(frame)

    def emit_with_ack(self, event: str, data, timeout: float | None = None):
        if timeout is None:
            timeout = EMIT_TIMEOUT
        with self._lock:
            self._ack_counter += 1
            ack_id = str(self._ack_counter)
            box: dict = {}
            done = threading.Event()
            self._pending[ack_id] = (box, done)

        payload = json.dumps({"name": event, "args": [data]})
        # v0.9 event-with-ack frame:  5:<id>+::<json}
        frame = f"5:{ack_id}+::{payload}"
        try:
            self._send(frame)
        except Exception as exc:
            with self._lock:
                self._pending.pop(ack_id, None)
            raise ConnectionError(f"v0.9 send failed for '{event}': {exc}") from exc

        if not done.wait(timeout):
            with self._lock:
                self._pending.pop(ack_id, None)
            raise TimeoutError(
                f"socket.io v0.9 '{event}' ack timed out after {timeout}s"
            )

        res = box.get("res")
        # Overleaf positional callbacks: (err, payload, ...) -> take payload ([1])
        if isinstance(res, list) and len(res) >= 2:
            return res[1]
        return res


class EphemeralOLClient:
    """Short-lived Socket.IO client used only inside a sync pass.

    Supports both modern Overleaf (Socket.IO v4 via ``python-socketio``)
    and legacy ShareLaTeX (Socket.IO v0.9 via raw websocket).  The
    appropriate protocol is auto-detected at connect time.
    """

    def __init__(self, base_url: str, cookie: str):
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie
        self._s = None          # python-socketio Client (v4 path)
        self._v09 = None        # _V09Transport (v0.9 path)

    def __enter__(self): self.connect(); return self
    def __exit__(self, *a): self.disconnect()

    def connect(self):
        if self._try_v09_connect():
            return
        self._connect_v4()

    def _try_v09_connect(self) -> bool:
        """Probe for Socket.IO v0.9 (legacy ShareLaTeX).  Returns True on success."""
        try:
            import requests
            r = requests.get(
                f"{self.base_url}/socket.io/1/",
                headers={"Cookie": self.cookie},
                timeout=5,
            )
            if r.status_code != 200 or not _looks_like_v09_handshake(r.text):
                return False
        except Exception:
            return False
        try:
            self._v09 = _V09Transport(self.base_url, self.cookie)
            self._v09.connect()
            return True
        except Exception:
            self._v09 = None
            return False

    def _connect_v4(self):
        origin = self.base_url
        self._s = _new_transport(self.base_url, self.cookie)
        transports = ["websocket", "polling"] if _have_websocket_transport() else ["polling"]
        self._s.connect(origin, headers={"Origin": origin, "Cookie": self.cookie},
                        transports=transports)

    def disconnect(self):
        if self._v09 is not None:
            self._v09.disconnect()
            self._v09 = None
        if self._s is not None:
            try:
                self._s.disconnect()
            except Exception:
                pass
            self._s = None

    def _emit(self, event, data, timeout=None):
        if timeout is None:
            timeout = EMIT_TIMEOUT
        if self._v09 is not None:
            return self._v09.emit_with_ack(event, data, timeout)
        # v4 path (python-socketio)
        if self._s is None:
            self._s = _new_transport(self.base_url, self.cookie)
        box = {}
        done = threading.Event()
        def cb(*args):
            if len(args) == 0:
                box["res"] = None
            elif len(args) == 1:
                box["res"] = args[0]
            else:
                # Overleaf positional callbacks: (err, payload, ...) -> take payload ([1])
                box["res"] = args[1]
            done.set()
        self._s.emit(event, data, callback=cb)
        if not done.wait(timeout):
            raise TimeoutError(f"socket.io '{event}' ack timed out after {timeout}s")
        return box.get("res")

    def join_project(self, project_id) -> dict:
        return self._emit("joinProject", {"project_id": project_id})

    def join_doc(self, doc_id) -> dict:
        return self._emit("joinDoc", {"doc_id": doc_id, "ranges": []})

    def apply_ot_update(self, doc_id, update: dict) -> dict:
        update = dict(update); update["doc"] = doc_id
        return self._emit("applyOtUpdate", update) or {}
