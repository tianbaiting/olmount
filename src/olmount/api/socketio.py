from __future__ import annotations
import json
import threading
import time
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

def _v09_payload_frames(payload: str) -> list[str]:
    if not payload:
        return []
    if not payload.startswith("\ufffd"):
        return [payload]
    frames: list[str] = []
    i = 0
    while i < len(payload):
        if payload[i] != "\ufffd":
            break
        j = payload.find("\ufffd", i + 1)
        if j < 0:
            break
        try:
            n = int(payload[i + 1:j])
        except ValueError:
            break
        start = j + 1
        frames.append(payload[start:start + n])
        i = start + n
    return frames

def _as_args(args) -> list:
    if args is None:
        return []
    if isinstance(args, tuple):
        return list(args)
    if isinstance(args, list):
        return args
    return [args]

def _error_message(error) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)


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
        self._session = None
        self._ws = None
        self._poll_url = None
        self._ack_counter = 0
        self._pending: dict[str, tuple[dict, threading.Event]] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._closed = False
        self._ready = threading.Event()
        self.join_project_response = None
        self._join_project_response_ready = threading.Event()
        self._connected_project_id = None
        self._saw_connection_accepted = False

    # -- connection -------------------------------------------------------

    def connect(self, project_id: str | None = None) -> None:
        import requests

        self._closed = False
        self._connected_project_id = project_id
        self._session = requests.Session()
        params = {"projectId": project_id} if project_id else None
        r = self._session.get(
            f"{self.base_url}/socket.io/1/",
            headers={"Cookie": self.cookie},
            params=params,
            timeout=10,
        )
        r.raise_for_status()
        if not _looks_like_v09_handshake(r.text):
            raise ConnectionError(f"unexpected v0.9 handshake response: {r.text!r}")
        sid, _hb, _close, transports_s = r.text.split(":", 3)
        transports = {t.strip() for t in transports_s.split(",")}

        websocket_error = None
        if "websocket" in transports:
            try:
                self._connect_websocket(sid)
            except Exception as exc:
                websocket_error = exc
                self._ws = None
                self._closed = False
            else:
                self._start_background()
                self._wait_ready()
                return

        if "xhr-polling" in transports:
            self._connect_xhr_polling(sid)
            self._start_background()
            self._wait_ready()
            return

        if websocket_error is not None:
            raise ConnectionError(f"v0.9 websocket failed: {websocket_error}") from websocket_error
        raise ConnectionError(f"v0.9 server offered no supported transport: {transports_s}")

    def _connect_websocket(self, sid: str) -> None:
        import websocket
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url += f"/socket.io/1/websocket/{sid}"
        self._ws = websocket.create_connection(
            ws_url,
            header=[f"Cookie: {self._combined_cookie()}"],
            origin=self.base_url,
            timeout=30,
        )

    def _connect_xhr_polling(self, sid: str) -> None:
        self._poll_url = f"{self.base_url}/socket.io/1/xhr-polling/{sid}"

    def _start_background(self) -> None:
        target = self._read_loop if self._ws is not None else self._poll_loop
        self._reader = threading.Thread(target=target, daemon=True)
        self._reader.start()

        self._heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat.start()

    def _wait_ready(self) -> None:
        if not self._ready.wait(timeout=15):
            self.disconnect()
            raise ConnectionError("v0.9: timed out waiting for connectionAccepted")

    def _combined_cookie(self) -> str:
        if self._session is None:
            return self.cookie
        extra = "; ".join(f"{k}={v}" for k, v in self._session.cookies.get_dict().items())
        if self.cookie and extra:
            return f"{self.cookie}; {extra}"
        return self.cookie or extra

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"Cookie": self._combined_cookie(), "Origin": self.base_url}
        if extra:
            headers.update(extra)
        return headers

    def disconnect(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._poll_url = None
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

    def _poll_loop(self) -> None:
        import requests

        try:
            while not self._closed and self._poll_url is not None:
                try:
                    r = self._session.get(
                        self._poll_url,
                        headers=self._headers(),
                        params={"t": int(time.time() * 1000)},
                        timeout=70,
                    )
                except requests.exceptions.ReadTimeout:
                    continue
                except Exception:
                    break
                if r.status_code != 200:
                    break
                for msg in _v09_payload_frames(r.text):
                    if self._closed:
                        break
                    self._handle(msg)
        finally:
            self._closed = True
            with self._lock:
                for _, (_box, done) in self._pending.items():
                    done.set()
                self._pending.clear()

    def _heartbeat_loop(self) -> None:
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
                    self._saw_connection_accepted = True
                    self._ready.set()
                elif evt.get("name") == "joinProjectResponse":
                    args = evt.get("args") or []
                    self.join_project_response = args[0] if args else None
                    self._join_project_response_ready.set()
            except Exception:
                pass
        elif ptype == "6":  # ack
            data_str = parts[3] if len(parts) > 3 else ""
            # format: "<ack_id>+[json_array]"
            plus = data_str.find("+")
            if plus < 0:
                ack_id = data_str
                ack_data = []
            else:
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
        elif ptype == "7":  # error
            self._closed = True
            with self._lock:
                for _, (box, done) in self._pending.items():
                    box["res"] = [{"message": data_str or "Socket.IO v0.9 error"}]
                    done.set()
                self._pending.clear()

    # -- emit -------------------------------------------------------------

    def _send(self, frame: str) -> None:
        with self._send_lock:
            if self._ws is not None:
                self._ws.send(frame)
            elif self._poll_url is not None and self._session is not None:
                r = self._session.post(
                    self._poll_url,
                    headers=self._headers({"Content-Type": "text/plain;charset=UTF-8"}),
                    params={"t": int(time.time() * 1000)},
                    data=frame,
                    timeout=10,
                )
                r.raise_for_status()
            else:
                raise ConnectionError("v0.9 transport is not connected")

    def emit_with_ack(self, event: str, args, timeout: float | None = None):
        if timeout is None:
            timeout = EMIT_TIMEOUT
        args = _as_args(args)
        with self._lock:
            self._ack_counter += 1
            ack_id = str(self._ack_counter)
            box: dict = {}
            done = threading.Event()
            self._pending[ack_id] = (box, done)

        payload = json.dumps({"name": event, "args": args})
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

        return box.get("res") or []


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

    def connect(self, project_id: str | None = None):
        if self._try_v09_connect(project_id=project_id):
            return
        self._connect_v4(project_id=project_id)

    def _try_v09_connect(self, project_id: str | None = None) -> bool:
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
            self._v09.connect(project_id=project_id)
            return True
        except Exception as exc:
            self._v09 = None
            raise ConnectionError(
                "Socket.IO v0.9 server detected, but the legacy transport failed: "
                f"{exc}"
            ) from exc

    def _connect_v4(self, project_id: str | None = None):
        origin = self.base_url
        self._s = _new_transport(self.base_url, self.cookie)
        transports = ["websocket", "polling"] if _have_websocket_transport() else ["polling"]
        url = origin if project_id is None else f"{origin}?projectId={project_id}"
        self._s.connect(url, headers={"Origin": origin, "Cookie": self.cookie},
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

    def _emit_args(self, event, args=None, timeout=None):
        if timeout is None:
            timeout = EMIT_TIMEOUT
        args = _as_args(args)
        if self._v09 is not None:
            return self._v09.emit_with_ack(event, args, timeout)
        # v4 path (python-socketio)
        if self._s is None:
            self._s = _new_transport(self.base_url, self.cookie)
        box = {}
        done = threading.Event()
        def cb(*args):
            box["res"] = list(args)
            done.set()
        data = tuple(args)
        self._s.emit(event, data, callback=cb)
        if not done.wait(timeout):
            raise TimeoutError(f"socket.io '{event}' ack timed out after {timeout}s")
        return box.get("res") or []

    def _emit(self, event, data, timeout=None):
        args = self._emit_args(event, [data], timeout)
        if args and args[0] is None:
            return args[1] if len(args) > 1 else None
        return args[0] if args else None

    def _payloads_or_raise(self, event: str, args: list) -> list:
        if args and args[0] is not None:
            raise ConnectionError(f"socket.io '{event}' failed: {_error_message(args[0])}")
        return args[1:] if args else []

    def join_project(self, project_id) -> dict:
        if self._v09 is not None and self._v09.join_project_response:
            return self._v09.join_project_response["project"]
        if (
            self._v09 is not None
            and self._v09._connected_project_id == project_id
            and not self._v09._saw_connection_accepted
            and self._v09._join_project_response_ready.wait(timeout=10)
            and self._v09.join_project_response
        ):
            return self._v09.join_project_response["project"]
        payloads = self._payloads_or_raise(
            "joinProject",
            self._emit_args("joinProject", [{"project_id": project_id}]),
        )
        if payloads and isinstance(payloads[0], dict) and "project" in payloads[0]:
            return payloads[0]["project"]
        if payloads and isinstance(payloads[0], dict):
            return payloads[0]
        raise ConnectionError("socket.io 'joinProject' returned no project payload")

    def join_doc(self, doc_id) -> dict:
        payloads = self._payloads_or_raise(
            "joinDoc",
            self._emit_args("joinDoc", [doc_id]),
        )
        if payloads and isinstance(payloads[0], dict):
            res = dict(payloads[0])
            if "message" in res and "docLines" not in res:
                raise ConnectionError(f"socket.io 'joinDoc' failed: {res['message']}")
            res.setdefault("ops", [])
            res.setdefault("ranges", {})
            res.setdefault("type", None)
            return res
        if len(payloads) >= 2 and isinstance(payloads[0], list):
            return {
                "docLines": payloads[0],
                "version": payloads[1],
                "ops": payloads[2] if len(payloads) > 2 else [],
                "ranges": payloads[3] if len(payloads) > 3 else {},
                "type": payloads[4] if len(payloads) > 4 else None,
            }
        raise ConnectionError("socket.io 'joinDoc' returned an unsupported payload")

    def apply_ot_update(self, doc_id, update: dict) -> dict:
        update = dict(update); update["doc"] = doc_id
        args = self._emit_args("applyOtUpdate", [doc_id, update])
        if not args:
            return {"accepted": True}
        if args[0] is not None:
            if isinstance(args[0], dict) and "accepted" in args[0]:
                return args[0]
            return {"accepted": False, "error": _error_message(args[0])}
        payloads = args[1:]
        if not payloads:
            return {"accepted": True}
        if isinstance(payloads[0], dict):
            res = dict(payloads[0])
            res.setdefault("accepted", True)
            return res
        return {"accepted": True}
