# `olmount` — Local-sync client for Overleaf (incl. self-hosted)

**Date:** 2026-06-19
**Status:** Design (awaiting implementation plan)
**Project root:** `overleaf_mount/`

## 1. Overview

`olmount` is a Python CLI that keeps a local directory two-way synchronized with an
Overleaf project, so you can edit your LaTeX project locally (in any editor) and have
changes flow to/from Overleaf. It works against **both** `www.overleaf.com **and**
self-hosted Overleaf / ShareLaTeX Community Edition** (the server URL is configurable).

It is **not** a FUSE mount and **not** a real-time collaborative client. It is a
**periodic, on-demand, version-aware sync tool** with git-style three-way merge for
conflict handling, plus an optional foreground `watch` daemon for live-ish sync.

### Goals
- Bidirectional sync of a local working tree with a remote Overleaf project.
- **Custom-domain / self-hosted support** — no hard-coded `overleaf.com`; per-server profiles.
- **Safe multi-user conflict handling** — explicit, mergeable, never silent data loss.
- Doc edits preserve Overleaf doc identity & history (stay editable/collaborative on Overleaf).
- On-demand commands (`pull`/`push`/`sync`/`status`) + optional `watch`.
- Server-side compile + PDF download.

### Non-goals (v1)
- FUSE filesystem mount.
- Persistent real-time Socket.IO backbone / live conflict-free OT streaming (Approach B).
- In-editor PDF preview / SyncTeX / chat (out of scope; use a local TeX install or web UI).
- Project CRUD beyond what sync needs (create/archive/delete/tag projects).

## 2. Key technical constraints (Overleaf API reality)

These shape the architecture and were verified against the three reference codebases
(`overleaf-sync`, `overleaf-sync-rs`, `Overleaf-Workshop`) and `Overleaf-Workshop/docs/webapi.md`:

1. **Reads are fully REST-accessible** (cookie-auth, custom-domain friendly):
   - project list: `GET {url}/project` → `<meta name="ol-projects">` / `ol-prefetchedProjectsBlob`.
   - full project content: `GET /project/{id}/download/zip`.
   - binary file content: `GET /project/{id}/file/{fileId}`.
   - compile: `POST /project/{id}/compile`; output via CLSI (CDN-aware).
   - auth/CSRF: `GET {url}/login` → `_csrf` input; `GET {url}/project` → `ol-*` meta.

2. **Writing doc (text) content has NO public REST endpoint.**
   `GET/POST /project/{id}/doc/{doc_id}` are both `privateApi` (`requirePrivateApiAuth`) —
   see `webapi.md:164-165`. Therefore the only correct way to update an existing Overleaf
   **doc** is **Socket.IO OT** (`applyOtUpdate`), which preserves the doc `_id`, version
   history, and live collaboration. The lossy alternatives (delete+re-create → loses id/
   history; upload-as-binary → makes the `.tex` an uneditable blob on Overleaf) are rejected.

3. **The file tree with `_id`s** (required for delete/rename/move/upload-to-subfolder)
   is obtained via **Socket.IO `joinProject`** (returns folders/docs/fileRefs with `_id`s
   and per-doc `version`). REST `/project/{id}/entities` returns only `{path, type}` (no ids).

**Conclusion — architecture "A2":** REST is the sync/merge *engine* and the transport for
all structural/binary/read operations; **Socket.IO is used only as an ephemeral, on-demand
API primitive** during a sync pass — `joinProject` (hydrate tree + ids + doc versions),
`joinDoc` (lazy doc content), `applyOtUpdate` (push doc edits). Connections open per pass
and close immediately. There is **no persistent live connection**, no reconnection/OT-stream
machinery. Conflicts are handled by the merge engine, not by OT.

## 3. Architecture decisions (recorded with rationale)

| Decision | Choice | Why |
|---|---|---|
| Mount semantics | Background sync + manual pull/push CLI | User choice; no FUSE, cross-platform |
| Language | Python | User choice; rich libs, matches `overleaf-sync` |
| Process model | On-demand commands + optional `watch` | User choice |
| Scope | File sync + compile/PDF | User choice |
| Sync engine | Version-aware periodic, **three-way snapshot diff** (base vs local vs remote), git-style merge | User choice (Approach A) + refinement: snapshot-based, not history-diff-based |
| Doc writes | Ephemeral Socket.IO OT | Forced by constraint #2; keeps docs editable on Overleaf |
| Conflict strategy | 3-way merge (text) / keep-both (binary) / preserve+warn (edit-delete) | User concern: explicit, mergeable, no data loss |
| Base content | **Stored locally** in `.olsync/base/` | User requirement; offline, no version/zip dependency |
| OT offsets | **UTF-16 code units**, surrogate-aware (NOT byte offsets) | User requirement; matches Overleaf/ShareLaTeX OT protocol |
| OT failure | **Re-fetch remote → full re-merge → regenerate ops → resend** | User requirement; converges under concurrent edits |

### Hard requirements (explicitly required by the user)
- **R1 — Base is always local.** `.olsync/base/` mirrors the last-synced state fully on
  disk. No `version/Vb/zip` dependency for merge. Refreshed only after a fully successful
  sync; never advanced on any failure.
- **R2 — OT offsets must be correct.** Positions are UTF-16 code-unit offsets (the unit the
  Overleaf Node server and JS client use). A surrogate-aware helper converts Python codepoint
  spans to UTF-16 `op.p`. `op.i`/`op.d` are literal substrings. Backed by an exhaustive test
  suite (BMP, astral/emoji, mixed, property test). Generation logic ported from
  `Overleaf-Workshop` `remoteFileSystemProvider.ts` `writeFile`.
- **R3 — `applyOtUpdate` failure ⇒ re-fetch remote ⇒ re-merge ⇒ regenerate ops ⇒ resend.**
  Bounded retries (`MAX_OT_RETRIES`, default 3). Stale ops are **never** resent.

## 4. Command surface

CLI via `click`; command `olmount`.

| Command | Purpose |
|---|---|
| `servers add NAME --url URL` | create a server profile |
| `servers list / remove NAME / set-default NAME` | manage profiles |
| `login [--server S] (--cookie STR \| --user EMAIL)` | authenticate (see §8) |
| `logout [--server S]` | clear stored credentials |
| `whoami [--server S]` | show session/user info |
| `list [--server S]` | list projects |
| `clone <id\|name> [--server S] [--into DIR]` | initial full download + init `.olsync/` |
| `status` | dry-run: changes + detected conflicts, no writes |
| `pull [--force]` | remote → local, one pass (with merge) |
| `push [--force]` | local → remote, one pass |
| `sync` | two-way reconciliation |
| `watch [--interval 5]` | foreground daemon (FS watch + remote poll) |
| `compile [--root FILE] [--draft] [--stop-on-first-error]` | server-side compile |
| `pdf [-o OUT]` | compile + download output PDF |

`pull`/`push` run the same engine restricted to one direction; `sync` runs it both ways;
`status` runs detection (steps 1–4) without executing.

## 5. Module layout

Small, focused, independently testable units:

```
src/olmount/
  __init__.py
  __main__.py
  cli.py                 # click dispatch
  config.py              # server profiles, paths (~/.config/olmount/)
  util.py
  api/
    http_client.py       # session: cookie + csrf + base URL; custom-domain aware; retries
    rest.py              # projects, download/zip, file/{id}, files/folders/docs CRUD, compile, pdf
    socketio.py          # EPHEMERAL: connect, joinProject (tree+ids+docVersions), joinDoc, applyOtUpdate, disconnect
    auth.py              # cookie login / optional password login / csrf extraction
  sync/
    tree.py              # in-memory project tree (folders/docs/fileRefs; ids; doc versions)
    state.py             # .olsync/state.json + .olsync/base/ management (atomic)
    ignore.py            # .olignore (pathspec/fnmatch)
    merge.py             # 3-way text merge (diff-match-patch) + binary/edit-delete conflict
    ot.py                # OT op generation: dmp(remoteNow→new) → {p,i,d} in UTF-16 units
    engine.py            # reconciliation: snapshot diff, classify (matrix), plan + execute
    watcher.py           # watchdog observer + remote poller for `watch`
  commands/
    {login,logout,whoami,servers,list,clone,status,pull,push,sync,watch,compile,pdf}.py
```

## 6. Data model & storage

### 6.1 Global config — `~/.config/olmount/config.toml`
```toml
default_server = "official"

[servers.official]
url      = "https://www.overleaf.com"
cookie   = "overleaf_session2=..."   # or sharelatex.sid=... for self-hosted
csrf     = "..."
user_id  = "..."
email    = "..."

[servers.myhost]
url      = "https://ol.mylab.example.edu"
cookie   = "sharelatex.sid=..."
csrf     = "..."
user_id  = "..."
email    = "..."
```

### 6.2 Per-project metadata — `<project_dir>/.olsync/`
```
.olsync/
  state.json     # project binding + base snapshot metadata + lastSyncedVersion
  base/          # FULL local copy of last-synced content (R1) — the merge base
  watch.lock     # (only while `watch` runs) file lock
```

`state.json`:
```json
{
  "server": "myhost",
  "projectId": "65dbfff719ad65b54b9eaed4",
  "projectName": "paper",
  "rootDocId": "65dc00000000000000000001",
  "lastSyncedVersion": 42,
  "base": {
    "main.tex":            {"kind":"doc",  "id":"65dc0...", "docVersion": 7, "sha1":"...", "size": 1234},
    "sections/intro.tex":  {"kind":"doc",  "id":"65dc1...", "docVersion": 3, "sha1":"...", "size":  880},
    "figs/logo.png":       {"kind":"file", "id":"65dc2...",                  "sha1":"...", "size": 99000}
  }
}
```
`base/` mirrors exactly the same paths (the merge-base **content**); `base` in `state.json`
holds the metadata used for cheap change detection. `lastSyncedVersion` is retained for
diagnostics only (base content is authoritative and local — R1).

### 6.3 `.olignore` — project-root ignore file
`pathspec`-style (gitignore-like) patterns. Excludes paths from local scanning and from
pushing (e.g. `output/`, `*.aux`, `.git/`). `.olsync/` is always ignored.

## 7. The sync engine

### 7.1 Snapshot model (three-way)
Each of the three sides is a map `path → meta` where
`meta = { kind: 'doc'|'file', id?, docVersion?, sha1, size }`.
- **Base** — read from `state.json` (meta) + `.olsync/base/` (content). Local, always present (R1).
- **Remote** — fetched fresh each pass: `joinProject` → tree + ids + doc `version`s; binary
  file `sha1` computed from a single `download/zip` fetch (see flow step 2). Doc *content*
  is **not** fetched here (lazy).
- **Local** — computed by walking the working tree (respecting `.olignore`).

**Change detection (no history-diff dependency):**
- **Doc**: `docVersion` differs base↔remote  ⇒ changed. (No content fetch needed to detect.)
- **File (binary)**: `sha1` differs  ⇒ changed.
- Presence: in-base-and-present / absent → drives add/delete classification.

### 7.2 Classification matrix (per path)
`unchanged` = present in base and meta matches; `changed` = present but meta differs; `new` =
present but not in base; `absent` = in base but missing now.

| local \ remote | unchanged | changed / new | absent |
|---|---|---|---|
| **unchanged** | skip | **pull** (ff) | pull-delete |
| **changed / new** | **push** (ff) | **CONFLICT** (in-sync if equal) | CONFLICT (edit/delete) |
| **absent** | push-delete | CONFLICT (delete/edit) | no-op |

Renames are treated as delete+add in v1 (content-identity rename detection is a future enhancement).

### 7.3 Conflict resolution
- **Text / doc**: three-way merge (`diff-match-patch`, base+local+remote content).
  - Non-overlapping hunks → **auto-merge**: write merged result locally and push it. No conflict markers, no work lost.
  - Overlapping hunks → write file with `<<<<<<<` / `=======` / `>>>>>>>` markers, **do not push**, report in `status`, until user resolves and re-runs `sync`/`push`.
- **Binary** (`kind=file`): cannot merge → **keep-both**: `name.LOCAL.ext` + `name.REMOTE.ext`; never auto-overwrite; report.
- **Edit/delete & delete/edit**: preserve the modified variant, warn, prompt in interactive mode (auto-preserve-modified under `--force`).

### 7.4 Per-pass flow
1. **Read local** `state.json` + `.olsync/base/` meta.
2. **Fetch current remote**: ephemeral `joinProject` → tree + doc/file `_id`s + doc `version`s. For binary `sha1` (no cheap change signal), fetch current `download/zip` **once** and hash the files → remote snapshot. (Doc content is **not** fetched here — lazy.)
3. **Scan local** dir (`.olignore`) → local snapshot (`sha1`/`size`).
4. **Three-way classify** each path (matrix in §7.2).
5. **Lazy-load content** only for paths that need it:
   - remote doc content → `joinDoc(id)` (for pull or merge);
   - base content → read from local `.olsync/base/` (R1, instant, offline);
   - local content → read from disk.
6. **Execute**:
   - *pulls* → write to disk (atomic `.tmp`+rename).
   - *structural/file pushes* → REST: `POST /project/{id}/doc` (new doc), `POST /project/{id}/folder`, `POST /project/{id}/upload?folder_id=…` (file add/replace + delete old fileRef), `DELETE /project/{id}/{type}/{id}`, rename/move by id.
   - *doc content pushes* → OT (§7.5) via ephemeral Socket.IO.
7. **Advance (only on full success)**: refresh `.olsync/base/` from current state; rebuild
   `base` meta (docs from new `docVersion`, files from `sha1`); persist `state.json` atomically.
   On any failure → **do not advance base** (re-run is safe & idempotent).

### 7.5 Doc push: OT op generation (R2) and conflict repair (R3)
For each doc to push (new/merged content already computed):
```
remoteNow, remoteVersion = joinDoc(id)                 # server's CURRENT doc text + version
ops = ot.diff_ops(remoteNow, newContent)               # diff: CURRENT remote → new
#   each op = {p: utf16_unit_offset, i?: substr, d?: substr}   (NOT byte offsets — R2)
applyOtUpdate(id, {doc: id, v: remoteVersion, lastV: prevVersion, hash, op: ops})
```
**`ot.diff_ops`** uses `diff-match-patch.diff_main(remoteNow, newContent)` folded into
`{p, i, d}` ops, with positions accumulated as **UTF-16 code-unit lengths** (surrogate-aware
`utf16_len(s)` helper over the matched substrings). `op.i`/`op.d` are the literal substrings.
Logic ported from `Overleaf-Workshop` `remoteFileSystemProvider.ts` `writeFile`, corrected to
be explicit about UTF-16 units (the reference's `Buffer.from(s,'utf-8').toString('utf-8')` is a
no-op, so its real behavior is JS `.length` = UTF-16 units).

**On rejection** (returned `v` mismatch ⇒ concurrent edit during apply — R3):
1. re-`joinDoc(id)` ⇒ updated `remoteNow` + new `remoteVersion`;
2. **full re-merge**: base ⊗ local ⊗ newRemote (complete three-way merge, not a patch);
3. **regenerate ops**: `ot.diff_ops(newRemoteNow, remerged)`;
4. resend. Repeat up to `MAX_OT_RETRIES` (default 3); then mark the doc CONFLICT and
   **do not advance** it (stays retryable). **Stale ops are never resent.**

This guarantees the merge result is deterministic even if someone edits live during the merge:
a `push` converges rather than corrupting the document.

## 8. Authentication & custom-domain support
- **Profiles** in `~/.config/olmount/config.toml` (§6.1); `url` is fully configurable
  (`http`/`https`) → works for any self-hosted domain. **No hard-coded `overleaf.com`.**
- `login --cookie STR` (**universal**): validate via `GET {url}/project`, parse
  `ol-user_id` / `ol-usersEmail` / `ol-csrfToken` `<meta>` (Overleaf-Workshop `getUserId`).
  Works for official SSO/captcha **and** any self-hosted instance. Cookie source = browser
  dev-tools (`overleaf_session2=…` or `sharelatex.sid=…`).
- `login --user EMAIL` (**optional**, password): `POST {url}/login` with email+password+csrf.
  Only works where there is no SSO/captcha (typical CE self-host). On captcha/SSO → clear error
  directing to `--cookie`.
- **Cookie-expiry handling**: on 401 / 302→`/login`, clear profile credentials, print a
  re-login hint; never silently fail or loop.

## 9. `watch` mode
Foreground daemon. Two triggers, both debounced into a single reconciliation pass (~1 s quiet
period so we never sync mid-keystroke):
- **Local**: `watchdog` Observer on the working tree (ignores `.olignore` + `.olsync`).
- **Remote**: poll the remote snapshot every `--interval N` seconds (default 5).

`watch` takes an exclusive file lock `.olsync/watch.lock` (one watch per project); an in-process
mutex prevents re-entrant syncs. A `rich` live status line shows last-sync time, pending changes,
and conflicts. `Ctrl-C` → finish the current op, release the lock, exit.

## 10. `compile` / `pdf`
- `compile [--root FILE] [--draft] [--stop-on-first-error]`: `POST /project/{id}/compile` with
  `rootResourcePath` resolved from `--root` (or `rootDocId` from state); parse
  `CompileResponseSchema`; report `latexmk-errors`.
- `pdf [-o OUT]`: compile then download `output.pdf`, using `pdfDownloadDomain` /
  `clsiServerId` / `compileGroup` from the compile response (CDN-aware download, ported from
  Overleaf-Workshop `getFileFromClsi`); write to `-o` or the cwd.

## 11. Robustness & data safety
- **Network**: exponential backoff retries for 5xx/timeouts; no retry on 4xx auth.
- **Partial failure**: operations are path-by-path; **base is never advanced on failure**;
  re-running is idempotent (ops keyed by path+version; applied writes show as `unchanged` next pass).
- **Atomic local writes**: data files via `.tmp`+rename; `state.json` via temp+rename → a crash
  never corrupts state.
- **No data loss**: conflicts are explicit; binary files are never auto-overwritten; deletes
  require interactive confirmation or `--force`.

## 12. Testing strategy
R2 and R3 are first-class, heavily-tested concerns.

- **`ot.diff_ops` + UTF-16 offsets — exhaustive**: BMP-only, astral/emoji, mixed strings;
  **property test** `apply_ops(diff_ops(a, b), a) == b` holds under multi-byte content.
- **3-way merge**: auto-merge non-overlap / markers on overlap / idempotency.
- **Classification**: all 12 matrix cells.
- **Snapshot diffing**: base/local/remote → changesets (rename-as-add+delete).
- **OT conflict repair (R3)**: mock server rejects first send ⇒ assert re-fetch remote ⇒
  full re-merge ⇒ regenerate ops ⇒ eventual success.
- **Contract tests**: parse real-shape payloads (`ol-projects` / `ol-prefetchedProjectsBlob`,
  `CompileResponseSchema`, `joinProject` tree).
- **Integration**: mock Overleaf (REST + Socket.IO) running full sync passes, including
  conflicts and `watch` coalescing.
- **End-to-end (documented, manual)**: against a self-hosted Overleaf CE docker image
  (compatibility list in `Overleaf-Workshop/README.md`).

## 13. Dependencies
`requests` (HTTP), `python-socketio[client]` (ephemeral client), `diff-match-patch`
(merge + OT), `watchdog` (FS events), `click` (CLI), `rich` (output), `pathspec`
(`.olignore`), `tomli`/`tomllib` + `tomli-w` (config), `pytest` (tests).

## 14. Out of scope / future
- Optional `--live` Socket.IO/OT backend for conflict-free real-time collaboration (Approach B),
  behind the same engine interface.
- Content-identity rename detection.
- Project CRUD (create/archive/delete/tag).
- Incremental (per-file) remote content fetch instead of one zip per pass, for very large projects.
