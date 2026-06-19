# `olmount` End-to-End Procedure Notes

> **Status:** this is the **manual** end-to-end test procedure against a real self-hosted Overleaf CE docker instance. We are **not** running it here — this document is the script to follow when validating on a live server. Fill in the **Results** section as you go.

**Goal:** validate that `olmount` round-trips correctly against a real Overleaf CE server: server config → login → list → clone → local edit → sync → web verify → web edit → sync → local verify → compile → pdf, plus a two-client conflict check.

---

## Prerequisites

1. **A running Overleaf (ShareLaTeX) Community Edition docker instance**, reachable over HTTP/HTTPS. The compatibility list of known-good CE images is maintained in [`../../../../Overleaf-Workshop/README.md`](../../../../Overleaf-Workshop/README.md) (section *Compatibility*). Verified-compatible images at time of writing include `sharelatex/sharelatex:5.0.4`, `4.2.4`, `4.1`, and `3.5`. Pick one of the verified tags to minimize protocol drift.
2. **A test account** on that instance (email + password), with SSO/captcha **disabled** (default for a plain CE docker setup) so both `--cookie` and `--user` login paths are exercisable.
3. **A test project** owned by that account, containing at least one `.tex` file and one binary asset (e.g. a `.png` figure), so both text-merge and binary paths get exercised.
4. **`olmount` installed** from this checkout: `pip install -e .[dev]` (or `pipx install .`). Confirm with `olmount --help`.
5. A browser logged in to the instance, for cookie extraction and web-UI verification.

> Use a **throwaway test project**. Do not run this against a project you cannot afford to lose until the procedure has been green once.

---

## Procedure

### Phase 1 — Server config & login

```bash
olmount servers add ce --url https://ol.test.local      # your CE base URL
olmount login --server ce --cookie "sharelatex.sid=…"   # cookie from browser devtools
olmount whoami --server ce                               # expect: <email> (id=…) @ <url>
```

- Cookie extraction: browser devtools → Application/Storage → Cookies → copy the `sharelatex.sid` value for a self-hosted CE instance (use `overleaf_session2` for overleaf.com).
- Optional alternative: `olmount login --server ce --user you@test.local` then enter the password (only works on non-SSO CE).
- **Expected:** `whoami` prints the account email and user id with no traceback.

### Phase 2 — List & clone

```bash
olmount list --server ce                                 # note a <project-id>
olmount clone <project-id> --server ce --into ./proj
cd proj
olmount status                                           # expect: in sync / clean
```

- **Expected:** `list` shows the project; `clone` creates `./proj` with the project files and a `.olsync/` metadata dir; `status` reports clean.

### Phase 3 — Local edit → sync → web verify

```bash
# edit a .tex file locally, e.g. append a comment line
olmount sync
```

- Then open the project in the **web UI** and confirm the local edit appears.
- **Expected:** `sync` reports pushed changes; web UI shows the new content; doc identity/history preserved (no duplicate file created).

### Phase 4 — Web edit → sync → local verify

- In the **web UI**, edit the same `.tex` file (different line than Phase 3) and save.
```bash
olmount sync
```
- **Expected:** `sync` pulls the remote edit; local file now contains both the Phase-3 local change and the Phase-4 web change (non-overlapping → auto-merge).

### Phase 5 — Compile & PDF

```bash
olmount compile                              # server-side compile
olmount pdf -o out.pdf                       # download the build output
```

- **Expected:** `compile` exits 0 (or reports compile errors from the TeX log if the doc is intentionally broken); `pdf` writes `out.pdf`.

---

## Phase 6 — Multi-user conflict check

Run **two** independent clones of the same project from two working copies (can be the same account, two directories):

```bash
olmount clone <project-id> --server ce --into ./projA
olmount clone <project-id> --server ce --into ./projB
```

**Case A — non-overlapping edits (auto-merge):**
1. In `projA`, edit line 1 of `main.tex`; in `projB`, edit a different line of `main.tex`.
2. `cd projA && olmount sync` then `cd projB && olmount sync`.
3. **Expected:** both edits merged cleanly into both clones; no conflict markers; both pushes succeed.

**Case B — overlapping edits (conflict markers):**
1. In `projA`, edit the **same line** of `main.tex`; in `projB`, edit that same line differently.
2. `cd projA && olmount sync` (pushes A's version).
3. `cd projB && olmount sync`.
4. **Expected:** `projB` reports a conflict; `main.tex` contains `<<<<<<< local` / `=======` / `>>>>>>> remote` markers. Edit `projB/main.tex`, remove the markers, keep the desired text, then `olmount sync` again. **Expected:** resolves and pushes cleanly.

**Case C — binary file (keep-both):**
1. Replace/modify the same binary asset (e.g. `figure.png`) in both `projA` and `projB`.
2. Sync both.
3. **Expected:** the losing side keeps both as `figure.LOCAL` and `figure.REMOTE` for manual selection; nothing is silently lost.

---

## Results

_(not yet run)_

Fill in when actually executed against a live server:

- CE image tested: _<e.g. sharelatex/sharelatex:5.0.4>_
- Phase 1 (login/whoami): _(pass/fail + notes)_
- Phase 2 (list/clone): _
- Phase 3 (local→sync→web): _
- Phase 4 (web→sync→local): _
- Phase 5 (compile/pdf): _
- Phase 6A (auto-merge): _
- Phase 6B (conflict markers): _
- Phase 6C (binary keep-both): _

---

## Known gaps to watch for

- **Socket.IO emit timing:** the `_emit` path currently uses a `sleep(0.01)` placeholder wait rather than a real acknowledgement. Under load or on a real server with higher latency, rapid multi-file bursts of `applyOtUpdate` may need re-validation. If `sync` reports doc-write failures, re-running `sync` (which re-fetches remote and regenerates ops per R3) should recover — but confirm this on a live server.
- **Password login scope:** `--user`/password works **only** on non-SSO self-hosted CE. On `overleaf.com` or any SSO/captcha-enabled deployment it will fail — use `--cookie`.
- **UTF-16 offsets:** OT doc writes use UTF-16 code-unit offsets to match the Overleaf protocol. ASCII/BMP text is correct; exercise surrogate-pair (astral-plane) content explicitly if relevant to your project.
- **No background daemon:** `olmount watch` is foreground-only; killing the terminal stops syncing.
- **Large/crucial projects:** this is early software. Keep an out-of-tree backup until the procedure above is green end-to-end on your specific CE version.
