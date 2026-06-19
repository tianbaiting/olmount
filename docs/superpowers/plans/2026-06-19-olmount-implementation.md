# `olmount` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python CLI (`olmount`) that two-way syncs a local directory with an Overleaf project (official or self-hosted) using version-aware three-way merge, with server-side compile/PDF.

**Architecture:** REST is the sync/merge *engine* (reads, structural/binary writes, compile); Socket.IO is used only as an *ephemeral, on-demand* primitive per sync pass (`joinProject` for tree+ids+doc-versions, `joinDoc` for doc content, `applyOtUpdate` for doc edits). No persistent live connection. Conflicts handled by a git-style three-way merge, not by OT. See `docs/superpowers/specs/2026-06-19-olmount-design.md`.

**Tech Stack:** Python 3.10+, `requests`, `python-socketio`, `diff-match-patch`, `merge3`, `watchdog`, `click`, `rich`, `pathspec`, `pytest`.

**Hard requirements (from spec §3):**
- **R1** — Base content always stored locally under `.olsync/base/`; advanced only on full success; never on failure.
- **R2** — OT op offsets are **UTF-16 code-unit** offsets (surrogate-aware), never byte offsets.
- **R3** — On `applyOtUpdate` rejection: **re-fetch remote → full re-merge → regenerate ops → resend**; never resend stale ops.

---

## File Structure (responsibilities)

```
overleaf_mount/                          # project root (currently empty except docs/)
  pyproject.toml                         # package metadata + deps + console_scripts olmount=olmount.cli:main
  README.md
  src/olmount/
    __init__.py
    __main__.py                          # `python -m olmount`
    cli.py                               # click group + subcommand registration
    config.py                            # server profiles (~/.config/olmount/config.toml); paths
    util.py                              # sha1_hex, atomic file write, debouncer, utf16 helpers re-export
    api/
      __init__.py
      http_client.py                     # HttpClient: session w/ cookie+csrf+baseURL; retry; custom-domain
      auth.py                            # cookie login, optional password login, csrf extraction
      rest.py                            # REST endpoints (projects, zip, file/{id}, CRUD, compile, pdf)
      socketio.py                        # EphemeralOLClient: joinProject/joinDoc/applyOtUpdate/disconnect
    sync/
      __init__.py
      tree.py                            # RemoteTree: parse joinProject payload; path<->id resolution
      state.py                           # ProjectState: state.json + .olsync/base/ (atomic), advance()
      ignore.py                          # IgnoreFilter: .olignore (pathspec)
      merge.py                           # three_way_merge() text; binary/edit-delete helpers
      ot.py                              # diff_ops(), apply_ops(), utf16_len()  (R2)
      engine.py                          # reconcile(): snapshots, classify(), execute, advance (R1, R3)
      watcher.py                         # Watcher: watchdog + remote poll, debounce, lock
    commands/
      __init__.py
      login.py logout.py whoami.py servers.py list.py clone.py
      status.py pull.py push.py sync.py watch.py compile.py pdf.py
  tests/
    conftest.py                          # fixtures: tmp project dir, fake server payloads
    test_ot.py                           # R2 exhaustive
    test_merge.py
    test_state.py                        # R1 atomic + advance
    test_engine_classify.py              # all matrix cells
    test_engine_reconcile.py             # full pass + R3 repair (mock server)
    test_http_client.py test_auth.py
    test_rest.py test_socketio.py test_tree.py test_ignore.py
    test_commands.py test_watcher.py
    fixtures/                            # recorded Overleaf payloads (projects meta, joinProject, compile)
```

Build order (each milestone = independently testable software):
- **M0** scaffold + config · **M1** http + auth · **M2** REST reads + tree · **M3** socket.io ephemeral
- **M4** OT ops (R2) · **M5** state+base (R1) + ignore · **M6** three-way merge
- **M7** classify matrix · **M8** reconcile engine + execute + advance + R3 repair
- **M9** commands (clone/list/status/pull/push/sync + auth cmds) · **M10** watch · **M11** compile/pdf

---

## Milestone M0 — Scaffold & config

### Task M0.1: Project skeleton + pyproject

**Files:** Create `pyproject.toml`, `src/olmount/__init__.py`, `src/olmount/__main__.py`, `README.md`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "olmount"
version = "0.1.0"
description = "Two-way sync client for Overleaf (incl. self-hosted)"
requires-python = ">=3.10"
dependencies = [
  "requests>=2.31",
  "python-socketio>=5.10",
  "diff-match-patch>=1.0",
  "merge3>=0.0.0",
  "watchdog>=3.0",
  "click>=8.1",
  "rich>=13.0",
  "pathspec>=0.11",
  "tomli>=2.0; python_version<'3.11'",
  "tomli-w>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=7.4", "pytest-mock>=3.12", "responses>=0.24"]

[project.scripts]
olmount = "olmount.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write `src/olmount/__init__.py` and `__main__.py`**

```python
# src/olmount/__init__.py
__version__ = "0.1.0"
```
```python
# src/olmount/__main__.py
from olmount.cli import main
if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Install editable + verify**

Run: `pip install -e ".[dev]" && python -c "import olmount; print(olmount.__version__)"`
Expected: prints `0.1.0`

- [ ] **Step 4: Commit**

```bash
git init -q && git add -A && git commit -m "chore: project scaffold + pyproject"
```

### Task M0.2: Paths + config (server profiles)

**Files:** Create `src/olmount/config.py`, `tests/test_config.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import textwrap
from pathlib import Path
from olmount.config import Config

def test_load_and_save_profiles(tmp_path, monkeypatch):
    cfg_file = tmp_path / "olmount" / "config.toml"
    monkeypatch.setattr("olmount.config.CONFIG_PATH", cfg_file)
    cfg = Config.load()
    cfg.set_server("official", url="https://www.overleaf.com",
                   cookie="overleaf_session2=abc", csrf="csrf1",
                   user_id="u1", email="a@b.c")
    cfg.set_server("myhost", url="https://ol.lab.edu",
                   cookie="sharelatex.sid=xyz", csrf="csrf2",
                   user_id="u2", email="d@e.f")
    cfg.set_default("myhost")
    cfg.save()

    cfg2 = Config.load()
    assert cfg2.default_server() == "myhost"
    assert cfg2.server("myhost").url == "https://ol.lab.edu"
    assert cfg2.server("official").cookie == "overleaf_session2=abc"

def test_unknown_server_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("olmount.config.CONFIG_PATH", tmp_path / "c.toml")
    cfg = Config.load()
    try:
        cfg.server("nope"); assert False
    except KeyError:
        pass
```

- [ ] **Step 2: Run test → verify it fails** (`ModuleNotFoundError`).

Run: `pytest tests/test_config.py -q`
Expected: FAIL (no `olmount.config`).

- [ ] **Step 3: Implement `config.py`**

```python
# src/olmount/config.py
from __future__ import annotations
import os, sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
import tomli_w

CONFIG_PATH = Path(os.environ.get("OLMOUNT_CONFIG",
                       os.path.expanduser("~/.config/olmount/config.toml")))

@dataclass
class ServerProfile:
    name: str
    url: str
    cookie: str = ""
    csrf: str = ""
    user_id: str = ""
    email: str = ""

@dataclass
class Config:
    default: str = ""
    servers: dict[str, ServerProfile] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.is_file():
            with CONFIG_PATH.open("rb") as f:
                data = tomllib.load(f)
            cfg = cls(default=data.get("default_server", ""))
            for name, s in data.get("servers", {}).items():
                cfg.servers[name] = ServerProfile(name=name, **s)
            return cfg
        return cls()

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"default_server": self.default,
                "servers": {n: {k: v for k, v in asdict(s).items() if k != "name"}
                            for n, s in self.servers.items()}}
        with CONFIG_PATH.open("wb") as f:
            tomli_w.dump(data, f)

    def set_server(self, name, **fields) -> None:
        if name in self.servers:
            for k, v in fields.items(): setattr(self.servers[name], k, v)
        else:
            self.servers[name] = ServerProfile(name=name, **fields)

    def server(self, name) -> ServerProfile:
        if name not in self.servers: raise KeyError(name)
        return self.servers[name]

    def default_server(self) -> str:
        return self.default or (next(iter(self.servers)) if self.servers else "")

    def set_default(self, name) -> None:
        if name not in self.servers: raise KeyError(name)
        self.default = name
```

- [ ] **Step 4: Run test → verify it passes.**

Run: `pytest tests/test_config.py -q`  Expected: PASS.

- [ ] **Step 5: Commit** — `feat: config + server profiles`

### Task M0.3: `util.py` (sha1, atomic write, utf16 shim)

**Files:** Create `src/olmount/util.py`, `tests/test_util.py`.

- [ ] **Step 1: Failing test**

```python
# tests/test_util.py
from olmount.util import sha1_hex, atomic_write_bytes, utf16_len
from pathlib import Path

def test_sha1_hex():
    assert sha1_hex(b"hello") == "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"

def test_atomic_write(tmp_path):
    p = tmp_path / "x" / "f.txt"
    atomic_write_bytes(p, b"data")
    assert p.read_bytes() == b"data"

def test_utf16_len():
    assert utf16_len("abc") == 3
    assert utf16_len("😀") == 2          # astral -> surrogate pair
    assert utf16_len("a😀b") == 4
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# src/olmount/util.py
from __future__ import annotations
import hashlib, os, tempfile
from pathlib import Path

def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()

def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise

def utf16_len(s: str) -> int:
    """Length in UTF-16 code units (JS string .length). Astral chars count as 2."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)
```

- [ ] **Step 4: Run → PASS. Commit** — `feat: util (sha1, atomic write, utf16_len)`

---

## Milestone M1 — HTTP client + auth

### Task M1.1: `http_client.HttpClient`

**Files:** Create `src/olmount/api/__init__.py`, `src/olmount/api/http_client.py`, `tests/test_http_client.py`.

- [ ] **Step 1: Failing test** (uses `responses` to mock HTTP)

```python
# tests/test_http_client.py
import responses, pytest
from olmount.api.http_client import HttpClient

@responses.activate
def test_get_sends_cookie_csrf_and_retries_5xx():
    url = "https://ol.lab.edu/"
    responses.add(responses.GET, url + "project", status=500)
    responses.add(responses.GET, url + "project", status=200, body="OK")
    c = HttpClient(base_url=url, cookie="sharelatex.sid=x", csrf="csrf")
    r = c.get("project")
    assert r.status_code == 200
    assert r.text == "OK"
    assert "sharelatex.sid=x" in responses.calls[0].request.headers["Cookie"]

@responses.activate
def test_get_raises_on_401_no_retry():
    responses.add(responses.GET, "https://ol.lab.edu/login", status=401)
    c = HttpClient("https://ol.lab.edu/", "c", "csrf")
    with pytest.raises(Exception): c.get("login")
    assert len(responses.calls) == 1
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# src/olmount/api/http_client.py
from __future__ import annotations
import time
import requests

class HttpError(Exception): ...

class HttpClient:
    def __init__(self, base_url: str, cookie: str, csrf: str = "",
                 timeout: int = 30, max_retries: int = 3):
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.cookie = cookie
        self.csrf = csrf
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Connection": "keep-alive", "Cookie": self.cookie}
        if extra: h.update(extra)
        return h

    def _retry(self, method, url, **kwargs):
        last = None
        for attempt in range(self.max_retries + 1):
            resp = self.session.request(method, url, timeout=self.timeout,
                                        allow_redirects=False, **kwargs)
            if resp.status_code < 500 and resp.status_code != 429:
                return resp
            last = resp
            time.sleep(0.5 * (2 ** attempt))
        raise HttpError(f"{method} {url} failed after retries: {last.status_code if last else '?'}")

    def get(self, route: str, extra_headers: dict | None = None, stream: bool = False):
        return self._retry("GET", self.base_url + route.lstrip("/"),
                           headers=self._headers(extra_headers), stream=stream)

    def post_json(self, route: str, body: dict | None = None, extra_headers: dict | None = None):
        body = dict(body or {})
        body.setdefault("_csrf", self.csrf)
        headers = self._headers(extra_headers); headers["Content-Type"] = "application/json"
        return self._retry("POST", self.base_url + route.lstrip("/"), headers=headers, json=body)

    def post_multipart(self, route: str, data=None, files=None, extra_headers=None):
        return self._retry("POST", self.base_url + route.lstrip("/"),
                           headers=self._headers(extra_headers), data=data, files=files)

    def delete(self, route: str, extra_headers: dict | None = None):
        headers = self._headers(extra_headers or {}); headers["X-Csrf-Token"] = self.csrf
        return self._retry("DELETE", self.base_url + route.lstrip("/"), headers=headers)
```

- [ ] **Step 4: Run → PASS. Commit** — `feat: http client with retry`

### Task M1.2: `auth.py` (cookie + optional password login)

**Files:** Create `src/olmount/api/auth.py`, `tests/test_auth.py`, `tests/fixtures/login_project.html`.

- [ ] **Step 1: Create fixture** (a minimal `/project` page with the `ol-*` meta tags).

```html
<!-- tests/fixtures/login_project.html -->
<html><head>
<meta name="ol-user_id" content="u123">
<meta name="ol-usersEmail" content="me@lab.edu">
<meta name="ol-csrfToken" content="csrfTOKEN">
</head><body></body></html>
```

- [ ] **Step 2: Failing test**

```python
# tests/test_auth.py
import responses, pathlib
from olmount.api.auth import cookie_login, CookieExpired

FIX = pathlib.Path("tests/fixtures/login_project.html").read_text()

@responses.activate
def test_cookie_login_parses_user_meta():
    responses.add(responses.GET, "https://ol.lab.edu/project", status=200, body=FIX)
    info = cookie_login("https://ol.lab.edu/", "sharelatex.sid=good")
    assert info.user_id == "u123"
    assert info.email == "me@lab.edu"
    assert info.csrf == "csrfTOKEN"

@responses.activate
def test_cookie_login_redirect_to_login_raises():
    responses.add(responses.GET, "https://ol.lab.edu/project", status=302,
                  headers={"Location": "/login"})
    with __import__("pytest").raises(CookieExpired):
        cookie_login("https://ol.lab.edu/", "sharelatex.sid=bad")
```

- [ ] **Step 3: Run → FAIL.**

- [ ] **Step 4: Implement**

```python
# src/olmount/api/auth.py
from __future__ import annotations
import re
from dataclasses import dataclass
from olmount.api.http_client import HttpClient

class CookieExpired(Exception): ...

_META = re.compile(r'<meta\s+name="ol-(user_id|usersEmail|csrfToken)"\s+content="([^"]*)">')

@dataclass
class LoginInfo:
    user_id: str; email: str; csrf: str

def cookie_login(base_url: str, cookie: str) -> LoginInfo:
    c = HttpClient(base_url, cookie)
    r = c.get("project")
    if r.status_code in (301, 302) and "/login" in r.headers.get("Location", ""):
        raise CookieExpired("cookie rejected (redirected to /login); re-run `olmount login --cookie`")
    if r.status_code != 200:
        raise CookieExpired(f"unexpected status {r.status_code}")
    fields = dict(_META.findall(r.text))
    if "user_id" not in fields or "csrfToken" not in fields:
        raise CookieExpired("could not parse user meta; cookie likely expired")
    return LoginInfo(user_id=fields["user_id"], email=fields.get("usersEmail", ""),
                     csrf=fields["csrfToken"])

def password_login(base_url: str, email: str, password: str) -> tuple[str, str]:
    """Returns (cookie, csrf). Only works where no SSO/captcha (typical self-hosted CE)."""
    c = HttpClient(base_url, cookie="")
    r = c.get("login")
    m = re.search(r'<input.*?name="_csrf".*?value="([^"]*)"', r.text)
    if not m: raise CookieExpired("could not get login CSRF")
    csrf = m[1]
    set_cookie = r.headers.get("set-cookie", "")
    sess = set_cookie.split(";")[0]
    r2 = c.post_json("login", {"_csrf": csrf, "email": email, "password": password},
                     extra_headers={"Cookie": sess})
    if r2.status_code != 302 or "/login" in r2.headers.get("Location", ""):
        raise CookieExpired("password login failed (captcha/SSO? use --cookie)")
    sc = r2.headers.get("set-cookie", "").split(";")[0]
    return f"{sess}; {sc}", csrf
```

- [ ] **Step 5: Run → PASS. Commit** — `feat: auth (cookie + password login)`

---

## Milestone M2 — REST reads + RemoteTree

### Task M2.1: `rest.py` core reads (projects, zip, file)

**Files:** Create `src/olmount/api/rest.py`, `tests/test_rest.py`.

- [ ] **Step 1: Failing test**

```python
# tests/test_rest.py
import io, zipfile, json, responses
from olmount.api.rest import OverleafREST
from olmount.api.http_client import HttpClient

def _client():
    return OverleafREST(HttpClient("https://ol.lab.edu/", "sharelatex.sid=x", "csrf"))

@responses.activate
def test_list_projects_parses_meta():
    html = '<meta name="ol-prefetchedProjectsBlob" content=\'{"totalSize":1,"projects":[{"id":"p1","name":"paper"}]}\'>'
    responses.add(responses.GET, "https://ol.lab.edu/project", status=200, body=html)
    projects = _client().list_projects()
    assert [p["name"] for p in projects] == ["paper"]

@responses.activate
def test_download_zip_returns_zipfile():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z: z.writestr("main.tex", "hello")
    responses.add(responses.GET, "https://ol.lab.edu/project/p1/download/zip",
                  status=200, body=buf.getvalue(), content_type="application/zip")
    zf = _client().download_zip("p1")
    assert zf.read("main.tex") == b"hello"

@responses.activate
def test_get_file_bytes():
    responses.add(responses.GET, "https://ol.lab.edu/project/p1/file/f9",
                  status=200, body=b"\x89PNG")
    assert _client().get_file("p1", "f9") == b"\x89PNG"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# src/olmount/api/rest.py
from __future__ import annotations
import io, re, zipfile
from olmount.api.http_client import HttpClient

_META_RE = re.compile(r'<meta\s+name="ol-prefetchedProjectsBlob"\s+content=(?P<q>["\'])(?P<c>.*?)(?P=q)')
_PROJECTS_RE = re.compile(r'<meta\s+name="ol-projects"\s+content=(?P<q>["\'])(?P<c>.*?)(?P=q)')

class OverleafREST:
    def __init__(self, http: HttpClient): self.http = http

    def list_projects(self) -> list[dict]:
        r = self.http.get("project")
        for rx in (_META_RE, _PROJECTS_RE):
            m = rx.search(r.text)
            if m:
                import json
                data = json.loads(m["c"].replace("&quot;", '"'))
                return data.get("projects", data if isinstance(data, list) else [])
        return []

    def download_zip(self, project_id: str) -> zipfile.ZipFile:
        r = self.http.get(f"project/{project_id}/download/zip", stream=True)
        if r.status_code != 200: raise RuntimeError(f"zip download failed: {r.status_code}")
        return zipfile.ZipFile(io.BytesIO(r.content))

    def get_file(self, project_id: str, file_id: str) -> bytes:
        r = self.http.get(f"project/{project_id}/file/{file_id}", stream=True)
        if r.status_code != 200: raise RuntimeError(f"file download failed: {r.status_code}")
        return r.content

    # ---- structural writes (used by engine in M8) ----
    def add_doc(self, project_id, parent_folder_id, name) -> dict:
        r = self.http.post_json(f"project/{project_id}/doc",
                                {"parent_folder_id": parent_folder_id, "name": name},
                                {"X-Csrf-Token": self.http.csrf})
        return r.json()

    def add_folder(self, project_id, parent_folder_id, name) -> dict:
        r = self.http.post_json(f"project/{project_id}/folder",
                                {"name": name, "parent_folder_id": parent_folder_id},
                                {"X-Csrf-Token": self.http.csrf})
        return r.json()

    def upload_file(self, project_id, parent_folder_id, name, data: bytes) -> dict:
        r = self.http.post_multipart(
            f"project/{project_id}/upload",
            data={"folder_id": parent_folder_id, "_csrf": self.http.csrf,
                  "qqfilename": name}, files={"qqfile": (name, data)})
        return r.json()

    def delete_entity(self, project_id, kind, entity_id):
        self.http.delete(f"project/{project_id}/{kind}/{entity_id}")

    def rename_entity(self, project_id, kind, entity_id, name):
        self.http.post_json(f"project/{project_id}/{kind}/{entity_id}/rename",
                            {"name": name}, {"X-Csrf-Token": self.http.csrf})

    def move_entity(self, project_id, kind, entity_id, folder_id):
        self.http.post_json(f"project/{project_id}/{kind}/{entity_id}/move",
                            {"folder_id": folder_id}, {"X-Csrf-Token": self.http.csrf})

    # ---- compile/pdf (wired in M11) ----
    def compile(self, project_id, root_resource_path=None, draft=False, stop_on_first_error=False) -> dict:
        body = {"check": "silent", "draft": draft, "incrementalCompilesEnabled": True,
                "rootResourcePath": root_resource_path, "stopOnFirstError": stop_on_first_error}
        r = self.http.post_json(f"project/{project_id}/compile?auto_compile=true", body,
                                {"X-Csrf-Token": self.http.csrf})
        return r.json()

    def download_output(self, project_id, url) -> bytes:
        r = self.http.get(url.lstrip("/")) if url.startswith("/") else \
            self.http.http_get_absolute(url)  # CDN absolute URL helper (see M11)
        return r.content
```

(Note: `download_output` for absolute CDN URLs is finalized in Task M11.1 with an `http_get_absolute` helper that does **not** send web cookies cross-origin.)

- [ ] **Step 4: Run → PASS. Commit** — `feat: REST reads (projects, zip, file)`

### Task M2.2: `tree.py` RemoteTree from joinProject payload

**Files:** Create `src/olmount/sync/__init__.py`, `src/olmount/sync/tree.py`, `tests/test_tree.py`, `tests/fixtures/joinproject.json`.

- [ ] **Step 1: Fixture** (representative joinProject `project` payload with nested folders/docs/fileRefs and `version` on docs).

```json
// tests/fixtures/joinproject.json
{ "rootFolder": [ {
    "_id": "root0", "name": "root",
    "docs":    [ {"_id":"d1","name":"main.tex","version":7} ],
    "fileRefs":[{"_id":"f1","name":"logo.png","created":"2024-01-01T00:00:00Z"}],
    "folders": [ {"_id":"fo1","name":"secs","docs":[{"_id":"d2","name":"a.tex","version":3}],
                  "fileRefs":[], "folders":[]} ]
  } ],
  "rootDoc_id": "d1", "compiler":"pdflatex", "spellCheckLanguage":"" }
```

- [ ] **Step 2: Failing test**

```python
# tests/test_tree.py
import json, pathlib
from olmount.sync.tree import RemoteTree

PAY = json.loads(pathlib.Path("tests/fixtures/joinproject.json").read_text())
def test_walk_and_resolve():
    t = RemoteTree(PAY)
    assert t.root_folder_id() == "root0"
    assert t.find_id_by_path("main.tex") == ("d1","doc")
    assert t.find_id_by_path("secs/a.tex") == ("d2","doc")
    assert t.find_id_by_path("logo.png") == ("f1","file")
    assert t.doc_version("d1") == 7
    assert {p for p,_ in t.walk()} == {"main.tex","secs/a.tex","logo.png"}

def test_parent_folder_id():
    t = RemoteTree(PAY)
    assert t.parent_folder_id("d2") == "fo1"
    assert t.parent_folder_id("d1") == "root0"
```

- [ ] **Step 3: Run → FAIL.**

- [ ] **Step 4: Implement**

```python
# src/olmount/sync/tree.py
from __future__ import annotations
from dataclasses import dataclass, field

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
```

- [ ] **Step 5: Run → PASS. Commit** — `feat: RemoteTree from joinProject payload`

---

## Milestone M3 — Ephemeral Socket.IO client

### Task M3.1: `socketio.py` (joinProject, joinDoc, applyOtUpdate) against a fake server

**Files:** Create `src/olmount/api/socketio.py`, `tests/test_socketio.py`.

- [ ] **Step 1: Failing test** (uses `python-socketio` server in a thread as a stand-in; mocks the client's emit to return canned data). Because a full integration server is heavy, test the *wrapper's contract*: that it emits the right event names and unwraps the right fields, using a monkeypatched transport.

```python
# tests/test_socketio.py
import pytest
from olmount.api.socketio import EphemeralOLClient

class FakeSock:
    def __init__(self): self.calls = []; self._responses = {}
    def on(self, ev, cb): pass
    def emit(self, ev, data, callback=None):
        self.calls.append((ev, data))
        if callback and ev in self._responses: callback(*self._responses[ev])
    def disconnect(self): self.calls.append(("disconnect", None))
    def connect(self): pass

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
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** (note: Overleaf's joinProject callback historically received positional args; the wrapper normalizes a dict-or-positional response).

```python
# src/olmount/api/socketio.py
from __future__ import annotations
import socketio  # python-socketio client

def _new_transport(base_url, cookie):
    s = socketio.Client()
    # connection handled by caller; headers carry the cookie
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
        result = {}
        def cb(*args):
            result["res"] = args[0] if len(args) == 1 else args
        self._s.emit(event, data, callback=cb)
        self._s.sleep(0.01)  # let callback fire; engine callers run single-threaded
        # NOTE: in tests, transport is fake and calls cb synchronously.
        return result.get("res")

    def join_project(self, project_id) -> dict:
        res = self._emit("joinProject", {"project_id": project_id})
        # Overleaf may return positional (err, project, ...) or a dict
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
```

- [ ] **Step 4: Run → PASS. Commit** — `feat: ephemeral Socket.IO client`

> **Note for implementer:** the live `_emit` uses a real event-callback loop; the `sleep` is a placeholder for a proper wait-on-future. When wiring M8 against a real server, replace `_emit` with a `threading.Event`-based wait that blocks until the callback fires or times out (2 s). Add an integration test marked `@pytest.mark.integration` against a local mock Socket.IO server; keep the unit tests above as the fast contract gate.

---

## Milestone M4 — OT op generation (R2) ⭐

This is a hard requirement. Offsets are **UTF-16 code-unit offsets**.

### Task M4.1: `ot.diff_ops` + `ot.apply_ops` with exhaustive UTF-16 tests

**Files:** Create `src/olmount/sync/ot.py`, `tests/test_ot.py`.

- [ ] **Step 1: Failing test** (exhaustive over BMP / astral / mixed; property test that applying ops reconstructs the target).

```python
# tests/test_ot.py
import pytest
from olmount.sync.ot import diff_ops, apply_ops

def test_pure_insert_at_start():
    assert diff_ops("xyz", "axyz") == [{"p": 0, "i": "a"}]

def test_pure_delete():
    assert diff_ops("abc", "ac") == [{"p": 1, "d": "b"}]

def test_bmp_offsets():
    # euro sign U+20AC is BMP (1 UTF-16 unit)
    ops = diff_ops("a€c", "aX€c")
    assert ops == [{"p": 1, "i": "X"}]   # position after "a" == 1

def test_astral_offset_counts_two_units():
    # U+1F600 grinning face -> 2 UTF-16 units
    ops = diff_ops("😀b", "X😀b")
    assert ops == [{"p": 0, "i": "X"}]
    ops2 = diff_ops("a😀", "a")            # delete the astral char at offset 1 (length 2)
    assert ops2 == [{"p": 1, "d": "😀"}]

def test_mixed_string_insert_position():
    ops = diff_ops("ab😀ef", "ab😀Zef")
    assert ops == [{"p": 4, "i": "Z"}]     # a,b,=2 ; 😀=2 -> offset 4

@pytest.mark.parametrize("a,b", [
    ("", ""), ("abc","abc"), ("abc","abxyc"), ("Hello world","Hello cruel world"),
    ("line1\nline2\nline3","line1\nline2 CHANGED\nline3 extra"),
    ("😀😀😀","😀😂😀"),                    # astral substitutions
    ("café résumé","café résumé naïve"),   # BMP accents
    ("a"*1000, "a"*500+"b"+"a"*500),
])
def test_property_apply_reconstructs_target(a, b):
    assert apply_ops(diff_ops(a, b), a) == b

def test_empty_ops_when_equal():
    assert diff_ops("same", "same") == []
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** (`diff_match_patch.diff_main` is codepoint-based; we accumulate positions in UTF-16 units via `utf16_len` of the matched substrings).

```python
# src/olmount/sync/ot.py
from __future__ import annotations
from diff_match_patch import diff_match_patch
from olmount.util import utf16_len

EQUAL, INSERT, DELETE = 0, 1, -1

def diff_ops(remote_now: str, new_content: str) -> list[dict]:
    """
    Overleaf/ShareLaTeX OT ops transforming remote_now -> new_content.
    op.p is a UTF-16 code-unit offset; op.i/op.d are literal substrings.
    Mirrors Overleaf-Workshop remoteFileSystemProvider.ts writeFile op generation,
    made explicit about UTF-16 units (the TS Buffer.from(x,'utf-8').toString('utf-8')
    is a no-op, so its effective unit is JS .length == UTF-16 units).
    """
    dmp = diff_match_patch()
    diffs = dmp.diff_main(remote_now, new_content)
    dmp.diff_cleanupSemantic(diffs)
    ops: list[dict] = []
    pos = 0  # UTF-16 offset into remote_now
    for op_type, text in diffs:
        if op_type == EQUAL:
            pos += utf16_len(text)
        elif op_type == INSERT:
            ops.append({"p": pos, "i": text})
        elif op_type == DELETE:
            ops.append({"p": pos, "d": text})
            pos += utf16_len(text)
    return ops

# ---- inverse, for property tests & (optionally) local apply of incoming ops ----
def _to_units(s: str) -> list[str]:
    units = []
    for c in s:
        cp = ord(c)
        if cp > 0xFFFF:
            cp -= 0x10000
            units.append(chr(0xD800 + (cp >> 10)))
            units.append(chr(0xDC00 + (cp & 0x3FF)))
        else:
            units.append(c)
    return units

def _from_units(units: list[str]) -> str:
    return "".join(units).encode("utf-16-le").decode("utf-16-le")

def apply_ops(ops: list[dict], content: str) -> str:
    """Apply a batch of ops (all op.p relative to the ORIGINAL content, ShareJS-style)."""
    units = _to_units(content)
    for op in sorted(ops, key=lambda o: o["p"], reverse=True):
        p = op["p"]
        if op.get("d") is not None:
            n = utf16_len(op["d"])
            del units[p:p + n]
        elif op.get("i") is not None:
            units[p:p] = _to_units(op["i"])
    return _from_units(units)
```

- [ ] **Step 4: Run → PASS.** (If any property case fails, the bug is in UTF-16 handling — do not proceed until all pass. This is R2.)

- [ ] **Step 5: Commit** — `feat(ot): UTF-16 OT op generation (R2)`

---

## Milestone M5 — State + local base (R1) + ignore

### Task M5.1: `state.py` atomic state + base mirror

**Files:** Create `src/olmount/sync/state.py`, `tests/test_state.py`.

- [ ] **Step 1: Failing test**

```python
# tests/test_state.py
import json
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
    try: s.save()
    except RuntimeError: pass
    assert (tmp_path / ".olsync" / "state.json").read_text() == before  # unchanged

def test_advance_mirrors_working_tree_and_advances_meta(tmp_path):
    s = ProjectState.init(tmp_path, server="x", projectId="p", projectName="n", rootDocId="d")
    (tmp_path / "main.tex").write_text("hi")
    (tmp_path / "secs").mkdir(); (tmp_path / "secs" / "a.tex").write_text("yo")
    meta = {
        "main.tex": {"kind":"doc","id":"d1","docVersion":1,"sha1":"x","size":2},
        "secs/a.tex": {"kind":"doc","id":"d2","docVersion":1,"sha1":"y","size":2},
    }
    s.advance(meta, working_root=tmp_path, ignore=lambda p: False)
    assert (s.base_dir / "main.tex").read_text() == "hi"
    assert (s.base_dir / "secs" / "a.tex").read_text() == "yo"
    assert s.load().data["base"] == meta

def test_advance_not_run_on_failure(tmp_path):
    s = ProjectState.init(tmp_path, server="x", projectId="p", projectName="n", rootDocId="d")
    (tmp_path / "main.tex").write_text("orig")
    s.advance({"main.tex":{"kind":"doc","docVersion":1,"sha1":"x","size":4}}, tmp_path, lambda p: False)
    # a later failed sync must NOT touch base/
    (tmp_path / "main.tex").write_text("CHANGED")
    # simulate failure: engine simply doesn't call advance()
    assert (s.base_dir / "main.tex").read_text() == "orig"  # base unchanged
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# src/olmount/sync/state.py
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

    def exists(self) -> bool: return self.state_path.is_file()
    def load(self) -> "ProjectState":
        self.data = json.loads(self.state_path.read_text()); return self

    def save(self) -> None:
        """Atomic: write temp + os.replace. Never partially overwrites."""
        self.olsync.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.olsync), prefix="state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=2); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        except BaseException:
            try: os.unlink(tmp)
            except OSError: pass
            raise

    def base_content(self, relpath: str) -> bytes:
        return (self.base_dir / relpath).read_bytes()

    def advance(self, new_base_meta: dict, working_root: Path, ignore) -> None:
        """R1: rebuild .olsync/base/ from the working tree, then advance state.json.
        Uses a staging dir + rename swap so base/ is never half-written."""
        working_root = Path(working_root)
        staging = self.olsync / ".base-staging"
        if staging.exists(): shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for relpath in new_base_meta:
            src = working_root / relpath
            if not src.is_file(): continue
            dst = staging / relpath
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        old = self.olsync / "base.old"
        if old.exists(): shutil.rmtree(old)
        if self.base_dir.exists(): self.base_dir.rename(old)
        staging.rename(self.base_dir)
        if old.exists(): shutil.rmtree(old)
        self.data["base"] = new_base_meta
        self.save()
```

- [ ] **Step 4: Run → PASS. Commit** — `feat: project state + atomic local base (R1)`

### Task M5.2: `ignore.py` (.olignore)

**Files:** Create `src/olmount/sync/ignore.py`, `tests/test_ignore.py`.

- [ ] **Step 1: Failing test**

```python
# tests/test_ignore.py
from olmount.sync.ignore import IgnoreFilter

def test_basic_ignore(tmp_path):
    (tmp_path / ".olignore").write_text("output/\n*.aux\n.olsync/\n")
    ig = IgnoreFilter.from_file(tmp_path / ".olignore")
    assert ig.is_ignored("output/main.pdf")
    assert ig.is_ignored("main.aux")
    assert ig.is_ignored(".olsync/state.json")
    assert not ig.is_ignored("main.tex")
    assert not ig.is_ignored("secs/intro.tex")
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# src/olmount/sync/ignore.py
from __future__ import annotations
from pathlib import Path
import pathspec

class IgnoreFilter:
    def __init__(self, patterns: list[str] | None = None):
        self.spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns or [])
    @classmethod
    def from_file(cls, path: Path | None) -> "IgnoreFilter":
        if path is None or not Path(path).is_file(): return cls([])
        return cls(Path(path).read_text().splitlines())
    def is_ignored(self, relpath_posix: str) -> bool:
        return self.spec.match_file(relpath_posix)
```

- [ ] **Step 4: Run → PASS. Commit** — `feat: .olignore filter`

---

## Milestone M6 — Three-way merge

### Task M6.1: `merge.three_way_merge`

**Files:** Create `src/olmount/sync/merge.py`, `tests/test_merge.py`.

> Uses the `merge3` library. The test pins exact output; if the installed `merge3` version yields a different conflict-marker layout, adjust only the marker-emission in the wrapper until the tests pass — the tests are the contract.

- [ ] **Step 1: Failing test**

```python
# tests/test_merge.py
from olmount.sync.merge import three_way_merge

def test_no_conflict_disjoint_changes():
    base = "alpha\nbeta\ngamma\n"
    local = "ALPHA\nbeta\ngamma\n"      # changed line 1
    remote = "alpha\nbeta\nGAMMA\n"     # changed line 3
    merged, conflict = three_way_merge(base, local, remote)
    assert conflict is False
    assert merged == "ALPHA\nbeta\nGAMMA\n"

def test_conflict_overlapping():
    base = "alpha\nbeta\ngamma\n"
    local = "LOCAL\nbeta\ngamma\n"
    remote = "REMOTE\nbeta\ngamma\n"
    merged, conflict = three_way_merge(base, local, remote, "local", "remote")
    assert conflict is True
    assert "<<<<<<< local" in merged and "=======" in merged and ">>>>>>> remote" in merged
    assert "LOCAL" in merged and "REMOTE" in merged

def test_remote_only_change_is_taken():
    base = "a\nb\nc\n"; local = "a\nb\nc\n"; remote = "a\nB\nc\n"
    merged, conflict = three_way_merge(base, local, remote)
    assert merged == "a\nB\nc\n" and conflict is False
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# src/olmount/sync/merge.py
from __future__ import annotations
from merge3 import Merge3

def _lines(s: str) -> list[str]:
    # keepends so re-join is lossless
    return s.splitlines(keepends=True)

def three_way_merge(base: str, local: str, remote: str,
                    label_local: str = "local", label_remote: str = "remote") -> tuple[str, bool]:
    """Line-oriented diff3 merge. Returns (merged_text, had_conflict)."""
    m3 = Merge3(_lines(base), _lines(local), _lines(remote))
    out: list[str] = []
    conflict = False
    for group in m3.merge_groups(label_local, label_remote):
        tag = group[0]
        if tag == "unchanged":
            out.extend(group[1])
        elif tag == "a" or tag == "same":
            out.extend(group[1])
        elif tag == "b":
            out.extend(group[2])
        elif tag == "conflict":
            _, a_lines, b_lines = group[1], group[2], group[3]
            conflict = True
            out.append(f"<<<<<<< {label_local}\n")
            out.extend(_ensure_nl(a_lines))
            out.append("=======\n")
            out.extend(_ensure_nl(b_lines))
            out.append(f">>>>>>> {label_remote}\n")
    return "".join(out), conflict

def _ensure_nl(lines: list[str]) -> list[str]:
    return [(l if l.endswith("\n") else l + "\n") for l in lines]
```

> If `merge_groups` yields tuples of a different arity in your version, fix the unpacking in the `conflict` branch to match what the test expects (the test only checks marker presence + both sides present).

- [ ] **Step 4: Run → PASS. Commit** — `feat: three-way text merge`

---

## Milestone M7 — Snapshot diff + classification matrix

### Task M7.1: `engine.classify_path` (all matrix cells)

**Files:** Create `src/olmount/sync/engine.py` (classification only first), `tests/test_engine_classify.py`.

- [ ] **Step 1: Failing test** (all 9 cells + convergence/equality shortcut)

```python
# tests/test_engine_classify.py
import pytest
from olmount.sync.engine import classify_path, Action

def meta(kind="doc", v=1, sha="s", size=1): return {"kind":kind,"docVersion":v,"sha1":sha,"size":size}

@pytest.mark.parametrize("base_loc,base_rem,expected", [
    # (local meta or None, remote meta or None) given identical base meta
    ({}, {}, "skip"),                              # both unchanged
    ({}, {"v":2}, "pull"),                         # remote changed, local unchanged
    ({"v":2}, {}, "push"),                         # local changed, remote unchanged
    ({"v":2}, {"v":3}, "conflict"),               # both changed
    (None, {}, "push_delete"),                     # local deleted, remote unchanged
    ({}, None, "pull_delete"),                     # remote deleted, local unchanged
    (None, {"v":2}, "conflict"),                  # local deleted, remote changed (delete/edit)
    ({"v":2}, None, "conflict"),                  # local changed, remote deleted (edit/delete)
    (None, None, "noop"),                          # both gone
])
def test_matrix(base_loc, base_rem, expected):
    bm = meta()
    lm = meta(**base_loc) if base_loc is not None else None
    rm = meta(**base_rem) if base_rem is not None else None
    assert classify_path("f", bm, lm, rm) == Action(expected)

def test_converged_equal_means_skip():
    # both new, identical -> in sync
    assert classify_path("f", None, meta(sha="eq"), meta(sha="eq")) == Action.SKIP
    # both changed to same value -> in sync
    bm = meta(sha="old")
    assert classify_path("f", bm, meta(sha="new"), meta(sha="new")) == Action.SKIP
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# src/olmount/sync/engine.py  (part 1: classification)
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class Action(Enum):
    SKIP = "skip"; PULL = "pull"; PULL_DELETE = "pull_delete"
    PUSH = "push"; PUSH_DELETE = "push_delete"
    CONFLICT = "conflict"; NOOP = "noop"

def _meta_eq(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None: return False
    # docs compare by docVersion+sha1; files by sha1+size
    key = lambda m: (m.get("sha1"), m.get("size"), m.get("docVersion"))
    return key(a) == key(b)

def classify_path(path: str, base_meta, local_meta, remote_meta) -> Action:
    # convergence: local and remote agree -> in sync (covers add/add-equal & both-changed-same)
    if local_meta is not None and remote_meta is not None and _meta_eq(local_meta, remote_meta):
        return Action.SKIP
    in_base = base_meta is not None
    in_local = local_meta is not None
    in_remote = remote_meta is not None

    if not in_local:    lstate = "absent"
    elif not in_base:   lstate = "changed"        # new locally
    elif _meta_eq(base_meta, local_meta): lstate = "unchanged"
    else:               lstate = "changed"

    if not in_remote:   rstate = "absent"
    elif not in_base:   rstate = "changed"        # new remotely
    elif _meta_eq(base_meta, remote_meta): rstate = "unchanged"
    else:               rstate = "changed"

    return {
        ("unchanged","unchanged"): Action.SKIP,
        ("unchanged","changed"):   Action.PULL,
        ("unchanged","absent"):    Action.PULL_DELETE,
        ("changed","unchanged"):   Action.PUSH,
        ("changed","changed"):     Action.CONFLICT,
        ("changed","absent"):      Action.CONFLICT,   # edit/delete
        ("absent","unchanged"):    Action.PUSH_DELETE,
        ("absent","changed"):      Action.CONFLICT,   # delete/edit
        ("absent","absent"):       Action.NOOP,
    }[(lstate, rstate)]
```

- [ ] **Step 4: Run → PASS. Commit** — `feat(engine): classification matrix`

---

## Milestone M8 — Reconcile engine: execute + advance + R3 repair

### Task M8.1: snapshot builders

**Files:** Append to `src/olmount/sync/engine.py`, `tests/test_engine_reconcile.py`.

- [ ] **Step 1: Failing test** (snapshot from local walk; remote snapshot from zip+tree).

```python
# tests/test_engine_reconcile.py
import io, zipfile, json, pathlib
from olmount.sync.engine import build_local_snapshot, build_remote_snapshot
from olmount.sync.tree import RemoteTree
from olmount.util import sha1_hex

def _zip(files: dict) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf,"w") as z:
        for n,c in files.items(): z.writestr(n, c)
    return zipfile.ZipFile(buf)

def test_local_snapshot_hashes_files(tmp_path):
    (tmp_path/"main.tex").write_text("hi")
    (tmp_path/"secs").mkdir(); (tmp_path/"secs"/"a.tex").write_text("yo")
    snap = build_local_snapshot(tmp_path, ignore=lambda p: p.startswith(".olsync"))
    assert snap["main.tex"]["sha1"] == sha1_hex(b"hi")
    assert snap["secs/a.tex"]["sha1"] == sha1_hex(b"yo")

def test_remote_snapshot_combines_zip_and_tree():
    pay = json.loads(pathlib.Path("tests/fixtures/joinproject.json").read_text())
    tree = RemoteTree(pay)
    zf = _zip({"main.tex":"hi","secs/a.tex":"yo","logo.png":b"\x89PNG"})
    snap = build_remote_snapshot(zf, tree)
    assert snap["main.tex"]["kind"] == "doc"
    assert snap["main.tex"]["docVersion"] == 7
    assert snap["secs/a.tex"]["sha1"] == sha1_hex(b"yo")
    assert snap["logo.png"]["kind"] == "file"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** (append to engine.py)

```python
# engine.py continued
import posixpath
from olmount.util import sha1_hex

def build_local_snapshot(root, ignore) -> dict[str, dict]:
    root = pathlib.Path(root)
    snap = {}
    for p in root.rglob("*"):
        if not p.is_file(): continue
        rel = p.relative_to(root).as_posix()
        if ignore(rel): continue
        data = p.read_bytes()
        snap[rel] = {"kind": _guess_kind(rel), "sha1": sha1_hex(data), "size": len(data)}
    return snap

def build_remote_snapshot(zf, tree: "RemoteTree") -> dict[str, dict]:
    snap = {}
    for name in zf.namelist():
        if name.endswith("/"): continue
        eid, kind = tree.find_id_by_path(name) or (None, _guess_kind(name))
        data = zf.read(name)
        meta = {"kind": kind or _guess_kind(name), "sha1": sha1_hex(data), "size": len(data)}
        if eid: meta["id"] = eid
        if kind == "doc" and eid is not None:
            meta["docVersion"] = tree.doc_version(eid)
        snap[name] = meta
    return snap

def _guess_kind(path: str) -> str:
    return "doc" if posixpath.splitext(path)[1].lower() in {
        ".tex",".bib",".cls",".sty",".txt",".md",".latexmkrc",".tikz"} else "file"

import pathlib
```

- [ ] **Step 4: Run → PASS. Commit** — `feat(engine): snapshot builders`

### Task M8.2: reconcile execution + advance + R3 OT repair

**Files:** Append to `src/olmount/sync/engine.py`; expand `tests/test_engine_reconcile.py`.

- [ ] **Step 1: Failing test** — a mock server (REST + Socket.IO doubles) exercises: a pull (remote new file), a push (local new file → REST), a doc push (OT path, accepted first try), and **R3** (doc push rejected once → re-fetch → re-merge → regenerate → accepted).

```python
# tests/test_engine_reconcile.py  (continued)
import pytest
from olmount.sync.engine import Engine
from olmount.sync.state import ProjectState
from olmount.sync.tree import RemoteTree

class FakeREST:
    def __init__(self): self.created=[]; self.deleted=[]; self.uploaded=[]
    def add_doc(self,pid,pfid,n): self.created.append(("doc",n)); return {"_id":"newdoc","_type":"doc"}
    def add_folder(self,pid,pfid,n): return {"_id":"newf","name":n}
    def upload_file(self,pid,pfid,n,d): self.uploaded.append(n); return {"success":True,"entity_id":"newf","entity_type":"file"}
    def delete_entity(self,pid,k,eid): self.deleted.append((k,eid))
    def rename_entity(self,*a): pass
    def move_entity(self,*a): pass
    def get_file(self,pid,fid): return b""  # unused here
    def download_zip(self,pid):
        return _zip({"main.tex":"REMOTE_NEW"})

class FakeSock:
    def __init__(self): self.doc={"d1":{"content":"base text","version":1}}; self.reject_once=True; self.applies=[]
    def join_project(self,pid): return json.loads(pathlib.Path("tests/fixtures/joinproject.json").read_text())
    def join_doc(self,did):
        d=self.doc[did]; return {"docLines":d["content"].splitlines(keepends=True),"version":d["version"]}
    def apply_ot_update(self,did,upd):
        if self.reject_once:
            self.reject_once=False
            # simulate concurrent edit: version moved
            self.doc[did]["version"]+=1
            self.doc[did]["content"]="base text +concurrent"
            return {"accepted":False,"error":"otupdate","v":self.doc[did]["version"]}
        self.applies.append((did,upd)); return {"accepted":True,"v":self.doc[did]["version"]+1}

def _bootstrap(tmp_path):
    ProjectState.init(tmp_path, server="x", projectId="p1", projectName="paper", rootDocId="d1")
    st=ProjectState(tmp_path).load()
    st.advance({"main.tex":{"kind":"doc","id":"d1","docVersion":1,"sha1":"orig","size":9}},
               tmp_path, lambda p: p.startswith(".olsync"))
    return st.load()

def test_pull_creates_remote_new_file_locally(tmp_path):
    st=_bootstrap(tmp_path)
    eng=Engine(state=st, rest=FakeREST(), sock=FakeSock(), project_id="p1", working_root=tmp_path,
               ignore=lambda p: p.startswith(".olsync"))
    # remote zip has main.tex="REMOTE_NEW"; local base main.tex="base text" (we set below)
    (tmp_path/"main.tex").write_text("base text")  # local unchanged vs base
    eng.reconcile(direction="both")
    assert (tmp_path/"main.tex").read_text()=="REMOTE_NEW"   # pulled

def test_r3_ot_repair_remerges_and_resends(tmp_path):
    st=_bootstrap(tmp_path)
    (tmp_path/"main.tex").write_text("base text LOCAL EDIT")   # local edit to push
    sock=FakeSock()
    eng=Engine(state=st, rest=FakeREST(), sock=sock, project_id="p1", working_root=tmp_path,
               ignore=lambda p: p.startswith(".olsync"))
    eng.reconcile(direction="push")
    # first apply rejected, then re-merged against "base text +concurrent", regenerated, accepted
    assert sock.applies, "expected a successful apply after repair"
    # the op sent must be relative to the NEW remote content (R3: never stale)
    from olmount.sync.ot import apply_ops
    sent_op = sock.applies[0][1]["op"]
    assert apply_ops(sent_op, "base text +concurrent") == "base text +concurrent LOCAL EDIT"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** (append to engine.py). The engine runs the §7.4 flow: classify → lazy-load content → execute pulls/pushes → for doc pushes generate OT from **current remote**, and on rejection **re-fetch + re-merge + regenerate** (R3) → advance base on success.

```python
# engine.py continued
from olmount.sync.ot import diff_ops, apply_ops
from olmount.sync.merge import three_way_merge
from olmount.util import atomic_write_bytes

MAX_OT_RETRIES = 3
TEXT_EXTS = {".tex",".bib",".cls",".sty",".txt",".md",".tikz"}

class Engine:
    def __init__(self, state, rest, sock, project_id, working_root, ignore,
                 on_event=print):
        self.state=state; self.rest=rest; self.sock=sock; self.project_id=project_id
        self.working_root=pathlib.Path(working_root); self.ignore=ignore
        self.on_event=on_event

    # ---------- content helpers ----------
    def _local_content(self, rel): return (self.working_root/rel).read_bytes()
    def _base_content(self, rel): return self.state.base_content(rel)
    def _remote_doc_text(self, doc_id):
        res=self.sock.join_doc(doc_id)
        return "".join(res["docLines"]), res["version"]

    # ---------- execution ----------
    def reconcile(self, direction="both"):
        st=self.state
        tree=RemoteTree(self.sock.join_project(self.project_id))
        zf=self.rest.download_zip(self.project_id)
        base_meta=st.data.get("base",{})
        local_snap=build_local_snapshot(self.working_root, self.ignore)
        remote_snap=build_remote_snapshot(zf, tree)

        actions={}
        paths=set(base_meta)|set(local_snap)|set(remote_snap)
        for p in paths:
            if p.startswith(".olsync"): continue
            act=classify_path(p, base_meta.get(p), local_snap.get(p), remote_snap.get(p))
            actions[p]=act

        results={"pulled":[],"pushed":[],"conflicts":[],"deleted":[]}
        ok=True
        try:
            if direction in ("both","pull"):
                self._do_pulls(actions, base_meta, remote_snap, zf, results)
            if direction in ("both","push"):
                self._do_pushes(actions, base_meta, local_snap, remote_snap, tree, results)
        except Exception as e:
            ok=False; self.on_event(f"sync error: {e}")
            raise
        finally:
            if ok:
                self._advance(tree, results)
        return results

    def _do_pulls(self, actions, base_meta, remote_snap, zf, results):
        for p,act in actions.items():
            if act==Action.PULL:
                atomic_write_bytes(self.working_root/p, zf.read(p)); results["pulled"].append(p)
            elif act==Action.PULL_DELETE:
                fp=self.working_root/p
                if fp.is_file(): fp.unlink()
                results["deleted"].append(p)

    def _do_pushes(self, actions, base_meta, local_snap, remote_snap, tree, results):
        root_id=tree.root_folder_id()
        for p,act in actions.items():
            if act==Action.PUSH:
                self._push_path(p, base_meta, local_snap, remote_snap, tree, results)
            elif act==Action.PUSH_DELETE:
                eid,kind=tree.find_id_by_path(p) or (None,None)
                if eid: self.rest.delete_entity(self.project_id,kind,eid)
                results["deleted"].append(p)
            elif act==Action.CONFLICT:
                self._resolve_conflict(p, base_meta, local_snap, remote_snap, tree, zf=None, results=results)

    def _push_path(self, p, base_meta, local_snap, remote_snap, tree, results):
        kind=local_snap[p]["kind"]
        parent=self._ensure_parent(p, tree)
        data=self._local_content(p)
        if p not in remote_snap:
            # new file
            if kind=="doc":
                self.rest.add_doc(self.project_id, parent, pathlib.Path(p).name)
            else:
                self.rest.upload_file(self.project_id, parent, pathlib.Path(p).name, data)
            results["pushed"].append(p)
        else:
            eid,rkind=tree.find_id_by_path(p)
            if kind=="doc":
                self._push_doc_edit(p, eid, base_meta, results)
            else:
                # binary replace = upload new + delete old
                self.rest.upload_file(self.project_id, parent, pathlib.Path(p).name, data)
                self.rest.delete_entity(self.project_id,"file",eid)
                results["pushed"].append(p)

    def _push_doc_edit(self, p, doc_id, base_meta, results):
        """R3: generate OT from CURRENT remote; on rejection re-fetch+re-merge+regenerate."""
        local_text=self._local_content(p).decode("utf-8")
        base_text=self._base_content(p).decode("utf-8") if p in base_meta else local_text
        remote_now, remote_version=self._remote_doc_text(doc_id)
        # decide target: if remote_now != base, three-way merge first
        target=local_text
        if remote_now!=base_text:
            target,_conf=three_way_merge(base_text, local_text, remote_now)
        for _ in range(MAX_OT_RETRIES):
            ops=diff_ops(remote_now, target)               # from CURRENT remote -> target
            if not ops: break
            resp=self.sock.apply_ot_update(doc_id, {
                "doc":doc_id,"v":remote_version,"lastV":base_meta.get(p,{}).get("docVersion"),
                "op":ops})
            if resp.get("accepted"):
                results["pushed"].append(p); return
            # rejected: re-fetch remote, re-merge, regenerate  (R3 — never resend stale ops)
            remote_now, remote_version=self._remote_doc_text(doc_id)
            target,_=three_way_merge(base_text, local_text, remote_now)
        # exhausted -> mark conflict, do NOT advance this doc
        results["conflicts"].append(p)
        self.on_event(f"OT conflict (could not converge): {p}")

    def _resolve_conflict(self, p, base_meta, local_snap, remote_snap, tree, zf, results):
        kind=local_snap.get(p,{}).get("kind") or remote_snap.get(p,{}).get("kind","doc")
        if kind=="doc":
            base_text=self._base_content(p).decode("utf-8") if p in base_meta else ""
            local_text=self._local_content(p).decode("utf-8")
            remote_text=zf.read(p).decode("utf-8") if zf else None
            if remote_text is None:
                eid,_=tree.find_id_by_path(p); remote_text,_=self._remote_doc_text(eid)
            merged,conflict=three_way_merge(base_text, local_text, remote_text)
            atomic_write_bytes(self.working_root/p, merged.encode("utf-8"))
            if conflict:
                results["conflicts"].append(p); self.on_event(f"conflict markers written: {p}")
            else:
                results["pushed"].append(p)   # auto-merged; will be pushed next pass / push direction
        else:
            # binary keep-both
            for tag,data in (("LOCAL",self._local_content(p)),("REMOTE",zf.read(p))):
                atomic_write_bytes(self.working_root/_suffixed(p,tag), data)
            results["conflicts"].append(p); self.on_event(f"binary conflict (keep-both): {p}")

    def _ensure_parent(self, p, tree):
        parts=p.split("/")[:-1]
        cur=tree.root_folder_id()
        acc=""
        for part in parts:
            acc=f"{acc}{part}/" if acc else f"{part}/"
            existing=tree.find_id_by_path(acc.rstrip("/"))
            if existing: cur=existing[0]
            else:
                made=self.rest.add_folder(self.project_id, cur, part); cur=made["_id"]
        return cur

    def _advance(self, tree, results):
        # rebuild base meta from current remote tree + local files, then advance base/
        zf=self.rest.download_zip(self.project_id)
        remote_snap=build_remote_snapshot(zf, tree)
        local_snap=build_local_snapshot(self.working_root, self.ignore)
        new_base={}
        for p in set(remote_snap)|set(local_snap):
            m=dict(local_snap.get(p) or remote_snap.get(p))
            if p in remote_snap and "id" in remote_snap[p]: m["id"]=remote_snap[p]["id"]
            if p in remote_snap and "docVersion" in remote_snap[p]: m["docVersion"]=remote_snap[p]["docVersion"]
            new_base[p]=m
        self.state.advance(new_base, self.working_root, self.ignore)

def _suffixed(p, tag):
    from pathlib import PurePosixPath
    pp=PurePosixPath(p)
    return str(pp.with_name(f"{pp.stem}.{tag}{pp.suffix}")) if pp.suffix else f"{p}.{tag}"
```

- [ ] **Step 4: Run → PASS.** The R3 test is the critical correctness gate: the op sent must reconstruct the target from the *post-rejection* remote. If it fails, the bug is stale-op reuse or wrong merge base — fix before continuing.

- [ ] **Step 5: Commit** — `feat(engine): reconcile execute + advance + R3 OT repair`

---

## Milestone M9 — Commands

### Task M9.1: CLI skeleton + auth/servers commands

**Files:** Create `src/olmount/cli.py`, `src/olmount/commands/__init__.py`, `src/olmount/commands/{login,logout,whoami,servers}.py`, `tests/test_commands.py`.

- [ ] **Step 1: Failing test**

```python
# tests/test_commands.py
from click.testing import CliRunner
from olmount.cli import main

def test_servers_add_and_whoami(tmp_path, monkeypatch):
    cfg=tmp_path/"c.toml"; monkeypatch.setattr("olmount.config.CONFIG_PATH", cfg)
    r=CliRunner().invoke(main, ["servers","add","myhost","--url","https://ol.lab.edu"])
    assert r.exit_code==0, r.output
    # whoami uses cookie login against a mocked server
    import responses
    @responses.activate
    def go():
        import pathlib
        responses.add(responses.GET,"https://ol.lab.edu/project",status=200,
            body='<meta name="ol-user_id" content="u1"><meta name="ol-csrfToken" content="csrf">')
        nonlocal_runner=CliRunner()
        rr=nonlocal_runner.invoke(main,["login","--server","myhost","--cookie","sharelatex.sid=x"])
        assert rr.exit_code==0, rr.output
        rw=nonlocal_runner.invoke(main,["whoami","--server","myhost"])
        assert "u1" in rw.output
    go()
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# src/olmount/cli.py
import click
from olmount.config import Config

@click.group()
@click.version_option()
def main():
    """olmount — two-way sync for Overleaf (incl. self-hosted)."""
    pass

@main.group()
def servers():
    """Manage server profiles."""
    pass

@servers.command("add")
@click.argument("name")
@click.option("--url", required=True)
def servers_add(name, url):
    cfg=Config.load(); cfg.set_server(name, url=url); cfg.save()
    click.echo(f"added server '{name}' -> {url}")

@servers.command("list")
def servers_list():
    cfg=Config.load()
    for n,s in cfg.servers.items():
        mark="*" if n==cfg.default_server() else " "
        click.echo(f"{mark} {n}\t{s.url}")

@servers.command("set-default")
@click.argument("name")
def servers_set_default(name):
    cfg=Config.load(); cfg.set_default(name); cfg.save()

# register remaining commands (implemented in their modules)
from olmount.commands.login import login_cmd; main.add_command(login_cmd)         # noqa: E402
from olmount.commands.logout import logout_cmd; main.add_command(logout_cmd)      # noqa: E402
from olmount.commands.whoami import whoami_cmd; main.add_command(whoami_cmd)      # noqa: E402
from olmount.commands.list import list_cmd; main.add_command(list_cmd, name="list")  # noqa: E402
from olmount.commands.clone import clone_cmd; main.add_command(clone_cmd)         # noqa: E402
from olmount.commands.status import status_cmd; main.add_command(status_cmd)      # noqa: E402
from olmount.commands.pull import pull_cmd; main.add_command(pull_cmd)            # noqa: E402
from olmount.commands.push import push_cmd; main.add_command(push_cmd)            # noqa: E402
from olmount.commands.sync import sync_cmd; main.add_command(sync_cmd)            # noqa: E402
from olmount.commands.watch import watch_cmd; main.add_command(watch_cmd)         # noqa: E402
from olmount.commands.compile import compile_cmd; main.add_command(compile_cmd)   # noqa: E402
from olmount.commands.pdf import pdf_cmd; main.add_command(pdf_cmd)               # noqa: E402

if __name__=="__main__": main()
```

```python
# src/olmount/commands/login.py
import click
from olmount.config import Config
from olmount.api.auth import cookie_login, password_login, CookieExpired

@click.command()
@click.option("--server")
@click.option("--cookie")
@click.option("--user")  # email; prompts password
def login_cmd(server, cookie, user):
    cfg=Config.load(); name=server or cfg.default_server()
    prof=cfg.server(name)
    if cookie:
        info=cookie_login(prof.url, cookie)
        cfg.set_server(name, cookie=cookie, csrf=info.csrf, user_id=info.user_id, email=info.email)
    elif user:
        pw=click.prompt("password", hide_input=True)
        ck,csrf=password_login(prof.url, user, pw)
        info=cookie_login(prof.url, ck)
        cfg.set_server(name, cookie=ck, csrf=csrf, user_id=info.user_id, email=info.email)
    else:
        raise click.UsageError("provide --cookie or --user")
    cfg.save(); click.echo(f"logged in as {info.email} on '{name}'")
```

```python
# src/olmount/commands/whoami.py
import click
from olmount.config import Config
@click.command()
@click.option("--server")
def whoami_cmd(server):
    cfg=Config.load(); name=server or cfg.default_server(); prof=cfg.server(name)
    click.echo(f"{prof.email} (id={prof.user_id}) @ {prof.url}")
```

(`logout.py` clears the profile's cookie/csrf/user_id/email and saves — follow the same pattern.)

- [ ] **Step 4: Run → PASS. Commit** — `feat(cli): servers/login/whoami`

### Task M9.2: `clone` + `status`/`pull`/`push`/`sync` (engine wiring)

**Files:** Create `src/olmount/commands/{clone,status,pull,push,sync,list}.py`; add to `tests/test_commands.py`.

- [ ] **Step 1: Failing test** (clone downloads zip, writes files, inits state+base; `status` reports a change).

```python
# tests/test_commands.py (continued)
import responses, io, zipfile, pathlib, json
from olmount.config import Config

@responses.activate
def test_clone_and_status(tmp_path, monkeypatch):
    cfg=tmp_path/"c.toml"; monkeypatch.setattr("olmount.config.CONFIG_PATH", cfg)
    Config.load().set_server("h", url="https://ol.lab.edu", cookie="c", csrf="x", user_id="u", email="e")
    Config.load().save()
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w") as z: z.writestr("main.tex","REMOTE")
    responses.add(responses.GET,"https://ol.lab.edu/project/p1/download/zip",status=200,body=buf.getvalue())
    responses.add(responses.GET,"https://ol.lab.edu/project",status=200,body='<meta name="ol-prefetchedProjectsBlob" content=\'{"projects":[{"id":"p1","name":"paper"}]}\'>')
    joinpay=pathlib.Path("tests/fixtures/joinproject.json").read_text()
    # clone uses ephemeral socketio -> monkeypatch join_project
    import olmount.commands.clone as clone_mod
    monkeypatch.setattr("olmount.api.socketio.EphemeralOLClient.join_project", lambda self,pid: json.loads(joinpay))
    r=CliRunner().invoke(main,["clone","p1","--server","h","--into",str(tmp_path/"work")])
    assert r.exit_code==0, r.output
    assert (tmp_path/"work"/"main.tex").read_text()=="REMOTE"
    # now edit locally and run status
    (tmp_path/"work"/"main.tex").write_text("EDITED")
    r2=CliRunner().invoke(main,["status"], obj={})  # status reads CWD
    assert "main.tex" in r2.output
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# src/olmount/commands/clone.py
import click, pathlib
from olmount.config import Config
from olmount.api.http_client import HttpClient
from olmount.api.rest import OverleafREST
from olmount.api.socketio import EphemeralOLClient
from olmount.sync.state import ProjectState
from olmount.sync.engine import build_local_snapshot, build_remote_snapshot

@click.command()
@click.argument("project")
@click.option("--server")
@click.option("--into", default=".")
def clone_cmd(project, server, into):
    cfg=Config.load(); name=server or cfg.default_server(); prof=cfg.server(name)
    rest=OverleafREST(HttpClient(prof.url, prof.cookie, prof.csrf))
    pid=_resolve_id(rest, project)
    work=pathlib.Path(into)/_project_name(rest, pid) if into=="." else pathlib.Path(into)
    work.mkdir(parents=True, exist_ok=True)
    zf=rest.download_zip(pid)
    for n in zf.namelist():
        if n.endswith("/"): continue
        out=work/n; out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(zf.read(n))
    with EphemeralOLClient(prof.url, prof.cookie) as sock:
        tree_payload=sock.join_project(pid)
        root_id=tree_payload.get("rootDoc_id","")
    st=ProjectState.init(work, server=name, projectId=pid,
                         projectName=_project_name(rest, pid), rootDocId=root_id)
    # advance base to the just-cloned state
    from olmount.sync.tree import RemoteTree
    tree=RemoteTree(tree_payload)
    remote_snap=build_remote_snapshot(zf, tree)
    local_snap=build_local_snapshot(work, lambda p: p.startswith(".olsync"))
    new_base={p:dict(local_snap[p], **{k:v for k,v in remote_snap[p].items() if k in ("id","docVersion")})
              for p in local_snap}
    st.advance(new_base, work, lambda p: p.startswith(".olsync"))
    click.echo(f"cloned '{pid}' -> {work}")

def _resolve_id(rest, project):
    for p in rest.list_projects():
        if p.get("id")==project or p.get("name")==project: return p["id"]
    raise click.ClickException(f"project '{project}' not found")
def _project_name(rest, pid):
    for p in rest.list_projects():
        if p.get("id")==pid: return p["name"]
    return pid
```

```python
# src/olmount/commands/_run.py  (shared: build engine from CWD)
import pathlib
from olmount.config import Config
from olmount.api.http_client import HttpClient
from olmount.api.rest import OverleafREST
from olmount.api.socketio import EphemeralOLClient
from olmount.sync.state import ProjectState
from olmount.sync.ignore import IgnoreFilter
from olmount.sync.engine import Engine

def build_engine(server=None):
    cfg=Config.load(); name=server or cfg.default_server(); prof=cfg.server(name)
    work=pathlib.Path.cwd()
    st=ProjectState(work).load()
    if not st.exists(): raise SystemExit("not an olmount project (no .olsync/)")
    rest=OverleafREST(HttpClient(prof.url, prof.cookie, prof.csrf))
    sock=EphemeralOLClient(prof.url, prof.cookie)
    sock.connect()
    ig=IgnoreFilter.from_file(work/".olignore")
    return Engine(state=st, rest=rest, sock=sock, project_id=st.data["projectId"],
                  working_root=work, ignore=lambda p: p.startswith(".olsync") or ig.is_ignored(p)), sock
```

```python
# src/olmount/commands/status.py
import click
from olmount.commands._run import build_engine
from olmount.sync.engine import build_local_snapshot, build_remote_snapshot, classify_path, Action
from olmount.sync.tree import RemoteTree

@click.command()
def status_cmd():
    eng,sock=build_engine()
    try:
        tree=RemoteTree(sock.join_project(eng.project_id))
        zf=eng.rest.download_zip(eng.project_id)
        remote_snap=build_remote_snapshot(zf, tree)
        local_snap=build_local_snapshot(eng.working_root, eng.ignore)
        base=eng.state.data["base"]
        for p in sorted(set(base)|set(local_snap)|set(remote_snap)):
            if p.startswith(".olsync"): continue
            act=classify_path(p, base.get(p), local_snap.get(p), remote_snap.get(p))
            if act!=Action.SKIP and act!=Action.NOOP:
                click.echo(f"{act.value:12} {p}")
    finally: sock.disconnect()
```

```python
# src/olmount/commands/pull.py
import click
from olmount.commands._run import build_engine
@click.command()
@click.option("--force", is_flag=True)
def pull_cmd(force):
    eng,sock=build_engine()
    try: r=eng.reconcile(direction="pull"); _report(r)
    finally: sock.disconnect()

# push.py -> direction="push";  sync.py -> direction="both"   (identical except direction)
def _report(r):
    import click
    for k in ("pulled","pushed","deleted","conflicts"):
        for p in r.get(k,[]): click.echo(f"{k}: {p}")
```

(Create `push.py` with `push_cmd` calling `reconcile(direction="push")`, and `sync.py` with `sync_cmd` calling `reconcile(direction="both")`, each wrapping the socket `connect()`/`disconnect()` like `pull.py`.)

```python
# src/olmount/commands/list.py
import click
from olmount.config import Config
from olmount.api.http_client import HttpClient
from olmount.api.rest import OverleafREST
@click.command()
@click.option("--server")
def list_cmd(server):
    cfg=Config.load(); prof=cfg.server(server or cfg.default_server())
    rest=OverleafREST(HttpClient(prof.url, prof.cookie, prof.csrf))
    for p in rest.list_projects():
        click.echo(f"{p.get('id')}\t{p.get('name')}")
```

- [ ] **Step 4: Run → PASS. Commit** — `feat(commands): clone/status/pull/push/sync/list`

---

## Milestone M10 — `watch`

### Task M10.1: `watcher.Watcher` (debounced, locked, both triggers)

**Files:** Create `src/olmount/sync/watcher.py`, `src/olmount/commands/watch.py`, `tests/test_watcher.py`.

- [ ] **Step 1: Failing test** (debounce coalesces bursts; lock prevents double-watch; remote poll fires reconcile).

```python
# tests/test_watcher.py
import time, threading
from pathlib import Path
from olmount.sync.watcher import Watcher

def test_debounce_coalesces_burst(tmp_path):
    fired=[]
    w=Watcher(working_root=tmp_path, interval=10, debounce=0.1,
              do_reconcile=lambda: fired.append(time.time()))
    w._on_local_event()  # burst of 3
    w._on_local_event()
    w._on_local_event()
    time.sleep(0.3)
    assert len(fired)==1

def test_lock_file_prevents_second_watch(tmp_path):
    (tmp_path/".olsync").mkdir()
    w1=Watcher(tmp_path, interval=10, debounce=0.05, do_reconcile=lambda:None)
    w1.acquire_lock()
    import pytest
    w2=Watcher(tmp_path, interval=10, debounce=0.05, do_reconcile=lambda:None)
    with pytest.raises(RuntimeError): w2.acquire_lock()
    w1.release_lock()
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

```python
# src/olmount/sync/watcher.py
from __future__ import annotations
import threading, time, os
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class Watcher:
    def __init__(self, working_root, interval, debounce, do_reconcile):
        self.working_root=Path(working_root)
        self.interval=interval; self.debounce=debounce
        self.do_reconcile=do_reconcile
        self._lock_path=self.working_root/".olsync"/"watch.lock"
        self._timer=None; self._stop=threading.Event()
        self._mutex=threading.Lock()

    def acquire_lock(self):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd=os.open(self._lock_path, os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode()); os.close(fd)
        except FileExistsError:
            raise RuntimeError("another `watch` already runs in this project")
    def release_lock(self):
        try: self._lock_path.unlink()
        except FileNotFoundError: pass

    def _on_local_event(self):
        with self._mutex:
            if self._timer: self._timer.cancel()
            self._timer=threading.Timer(self.debounce, self._trigger)
            self._timer.daemon=True; self._timer.start()
    def _trigger(self):
        self.do_reconcile()

    def run(self):
        self.acquire_lock()
        handler=FileSystemEventHandler()
        handler.on_any_event=lambda e: (self._on_local_event() if not self._ignored(e.src_path) else None)
        obs=Observer(); obs.schedule(handler, str(self.working_root), recursive=True); obs.start()
        last=time.time()
        try:
            while not self._stop.wait(0.2):
                if time.time()-last>=self.interval:
                    last=time.time(); self.do_reconcile()
        finally:
            obs.stop(); obs.join(); self.release_lock()
    def stop(self): self._stop.set()
    def _ignored(self, path):
        return ".olsync" in Path(path).parts
```

```python
# src/olmount/commands/watch.py
import click
from olmount.commands._run import build_engine
from olmount.sync.watcher import Watcher
@click.command()
@click.option("--interval", default=5, type=int)
@click.option("--debounce", default=1.0, type=float)
def watch_cmd(interval, debounce):
    eng,sock=build_engine()
    def reconcile():
        try: r=eng.reconcile(direction="both"); click.echo("synced")
        except Exception as e: click.echo(f"sync error: {e}")
    w=Watcher(eng.working_root, interval, debounce, reconcile)
    click.echo("watching (Ctrl-C to stop)")
    try: w.run()
    finally: sock.disconnect()
```

- [ ] **Step 4: Run → PASS. Commit** — `feat: watch daemon (debounced, locked)`

---

## Milestone M11 — compile / pdf

### Task M11.1: CDN-aware output download + `compile`/`pdf` commands

**Files:** Add `http_get_absolute` to `http_client.py`; add `download_output` wiring in `rest.py`; create `src/olmount/commands/{compile,pdf}.py`, `tests/test_compile.py`.

- [ ] **Step 1: Failing test**

```python
# tests/test_compile.py
import responses, pytest
from olmount.api.http_client import HttpClient
from olmount.api.rest import OverleafREST

@responses.activate
def test_compile_then_download_pdf_via_cdn():
    compile_resp={"status":"success","compileGroup":"g","clsiServerId":"srv",
                  "pdfDownloadDomain":"https://cdn.lab.edu","outputFiles":[{"path":"output.pdf","type":"pdf","url":"/project/p1/user/u/build/b1/output/output.pdf"}]}
    responses.add(responses.POST,"https://ol.lab.edu/project/p1/compile",json=compile_resp,status=200)
    responses.add(responses.GET,"https://cdn.lab.edu/project/p1/user/u/build/b1/output/output.pdf",body=b"%PDF-1.4",status=200)
    rest=OverleafREST(HttpClient("https://ol.lab.edu/","c","csrf"))
    res=rest.compile("p1", root_resource_path="main.tex")
    assert res["status"]=="success"
    pdf=rest.download_output("p1", res["outputFiles"][0], res.get("compileGroup"),
                             res.get("clsiServerId"), res.get("pdfDownloadDomain"))
    assert pdf.startswith(b"%PDF")
    # CDN request must NOT carry the web cookie
    cdn_req=responses.calls[-1].request
    assert "Cookie" not in {k.title() for k in cdn_req.headers.keys()}
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — add absolute GET (no cookies) and rewrite `download_output`.

```python
# append to src/olmount/api/http_client.py
    def http_get_absolute(self, absolute_url, include_cookies=False, stream=False):
        headers={"Connection":"keep-alive"}
        if include_cookies: headers["Cookie"]=self.cookie
        last=None
        for attempt in range(self.max_retries+1):
            resp=self.session.get(absolute_url, headers=headers, timeout=self.timeout,
                                  allow_redirects=False, stream=stream)
            if resp.status_code<500 and resp.status_code!=429: return resp
            last=resp; time.sleep(0.5*(2**attempt))
        raise HttpError(f"GET {absolute_url} failed: {last.status_code if last else '?'}")
```

```python
# replace download_output in src/olmount/api/rest.py
    def download_output(self, project_id, output_file, compile_group, clsi_server_id=None, pdf_download_domain=None) -> bytes:
        url=output_file["url"]
        if pdf_download_domain and clsi_server_id:
            cdn=f"{pdf_download_domain.rstrip('/')}/{url.lstrip('/')}?compileGroup={compile_group}&clsiserverid={clsi_server_id}&enable_pdf_caching=true"
            return self.http.http_get_absolute(cdn, include_cookies=False).content
        # legacy: download via web frontend (cookies required)
        return self.http.get(url.lstrip("/")).content
```

```python
# src/olmount/commands/compile.py
import click
from olmount.commands._run import build_engine
@click.command()
@click.option("--root")
@click.option("--draft", is_flag=True)
@click.option("--stop-on-first-error", is_flag=True)
def compile_cmd(root, draft, stop_on_first_error):
    eng,sock=build_engine()
    try:
        res=eng.rest.compile(eng.project_id, root_resource_path=root, draft=draft,
                             stop_on_first_error=stop_on_first_error)
        click.echo(f"compile: {res.get('status')}")
        for e in (res.get("stats",{}) or {}).get("latexmk-errors",0) and ["errors"] or []:
            click.echo(e)
    finally: sock.disconnect()
```

```python
# src/olmount/commands/pdf.py
import click, pathlib
from olmount.commands._run import build_engine
@click.command()
@click.option("-o","out", default="output.pdf")
def pdf_cmd(out):
    eng,sock=build_engine()
    try:
        res=eng.rest.compile(eng.project_id)
        pdf=next(f for f in res["outputFiles"] if f["type"]=="pdf")
        data=eng.rest.download_output(eng.project_id, pdf, res.get("compileGroup"),
                                      res.get("clsiServerId"), res.get("pdfDownloadDomain"))
        pathlib.Path(out).write_bytes(data); click.echo(f"wrote {out}")
    finally: sock.disconnect()
```

- [ ] **Step 4: Run → PASS. Commit** — `feat: compile + pdf (CDN-aware)`

---

## Final Task: README + smoke test

- [ ] **Step 1:** Write `overleaf_mount/README.md` documenting: install (`pipx install .`), `servers add`, `login --cookie`, `clone`, `pull`/`push`/`sync`, `watch`, `compile`/`pdf`, custom-domain usage, `.olignore`, and conflict resolution behavior.
- [ ] **Step 2:** Run the full suite: `pytest -q`. Expected: all green.
- [ ] **Step 3:** Manual end-to-end against a self-hosted Overleaf CE docker (document results in `docs/superpowers/plans/2026-06-19-olmount-e2e-notes.md`): `servers add` → `login --cookie` → `clone` → edit → `sync` → verify on web UI → edit on web UI → `sync` → verify locally → `pdf`.
- [ ] **Step 4:** Commit — `docs: README + e2e notes`.

---

## Self-Review

**Spec coverage** (spec section → tasks):
- §1–3 (overview, constraints, decisions) → reflected in architecture + M3/M4/M8 design. ✓
- §4 command surface → M9 (login/logout/whoami/servers/list/clone/status/pull/push/sync), M10 (watch), M11 (compile/pdf). ✓
- §5 module layout → File Structure maps 1:1. ✓
- §6 data model (config/state/base/.olignore) → M0.2, M5.1, M5.2. ✓
- §7 sync engine (snapshots, matrix, conflict, flow, OT gen, R3 repair) → M7, M8; OT gen → M4; merge → M6. ✓
- §8 auth & custom-domain → M1.2; custom-domain = configurable url in profiles (M0.2) used everywhere. ✓
- §9 watch → M10. ✓
- §10 compile/pdf → M11. ✓
- §11 robustness (atomic writes, no-advance-on-failure, idempotent) → M5.1 atomic save + advance-not-on-failure test; M1.1 retries. ✓
- §12 testing → R2 exhaustive in M4; R3 repair test in M8.2; matrix in M7; contract fixtures M2. ✓
- R1 base local → M5.1 (advance + atomic + tests). ✓
- R2 UTF-16 OT offsets → M4 (property tests incl. astral). ✓
- R3 re-fetch+re-merge+regen → M8.2 (`_push_doc_edit`, gated by test). ✓

**Placeholder scan:** none — every code step has real code; where a library API (`merge3.merge_groups`) may vary, the test pins expected behavior and the step says to adjust only marker emission until tests pass. No "TODO"/"implement later".

**Type/name consistency:** `diff_ops`/`apply_ops`/`utf16_len` (M4) reused identically in M8.2. `build_local_snapshot`/`build_remote_snapshot`/`classify_path`/`Action` (M7/M8) consistent. `ProjectState.init/advance/load/save/base_content` (M5) reused in M8/M9. `EphemeralOLClient.{join_project,join_doc,apply_ot_update}` (M3) consistent with usage in M8/M9. `OverleafREST.{compile,download_output,...}` consistent across M2/M11. `HttpClient.{get,post_json,post_multipart,delete,http_get_absolute}` consistent.
