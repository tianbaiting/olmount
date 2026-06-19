# olmount

`olmount` is a two-way sync client for [Overleaf](https://www.overleaf.com/) — including self-hosted / custom-domain Overleaf Community Edition servers. It is **not** a FUSE mount: it is a set of on-demand CLI commands (`clone`, `pull`, `push`, `sync`, `compile`, `pdf`, …) backed by a version-aware three-way merge, plus an optional foreground `watch` daemon that re-syncs on local file changes. Each Overleaf project is synced into an ordinary local directory with a small `.olsync/` metadata mirror, so you can edit with your own editor, build offline, and push back.

## Install

```bash
pipx install .          # recommended (isolated)
# or, for development:
pip install -e .[dev]
```

Requires Python 3.10+. Run `olmount --help` to verify.

## Quick start

Custom-domain / self-hosted Overleaf is the headline use case:

```bash
olmount servers add myhost --url https://ol.mylab.edu
olmount login --server myhost --cookie "sharelatex.sid=..."   # from browser devtools
olmount list --server myhost
olmount clone <project-id> --server myhost --into ./proj
cd proj
olmount status
olmount sync
olmount watch            # optional foreground daemon
olmount compile
olmount pdf -o out.pdf
```

For the official `overleaf.com` server, add it with `--url https://www.overleaf.com` and use the `overleaf_session2=…` cookie instead.

### Getting the `--cookie`

`olmount login` needs a session cookie extracted from a browser that is already logged in to your Overleaf instance:

1. Open the Overleaf site in your browser and log in normally.
2. Open the browser devtools → **Application** (Chrome/Edge) or **Storage** (Firefox) → **Cookies** → your site.
3. Copy the session cookie value:
   - `overleaf.com`: the cookie named **`overleaf_session2`** → pass `--cookie "overleaf_session2=<value>"`.
   - self-hosted CE: the cookie named **`sharelatex.sid`** → pass `--cookie "sharelatex.sid=<value>"`.
4. `olmount login --server <name> --cookie "<name>=<value>"` validates the cookie, extracts your user id/email/CSRF token, and stores the profile.

`--user <email>` + interactive password is also supported, but **only on self-hosted instances without SSO/captcha** (typical of a plain Overleaf CE docker setup). On `overleaf.com` and SSO-protected deployments, use `--cookie`.

## `.olignore`

Place a `.olignore` file at the root of a synced project to exclude paths from sync, using [gitignore](https://git-scm.com/docs/gitignore)-style patterns (`.gitignore` semantics). The `.olsync/` metadata directory is **always** ignored and never uploaded. Common entries:

```
output/
*.aux
*.log
*.fls
*.fdb_latexmk
```

## Conflict resolution

Sync uses a **three-way merge** against the last-known-shared state stored locally in `.olsync/base/`:

- **Non-overlapping changes** between local and remote are **auto-merged** automatically.
- **Overlapping text edits** produce conflict markers you resolve by hand:
  ```
  <<<<<<< local
  your local lines
  =======
  remote lines
  >>>>>>> remote
  ```
  Edit the file, remove the markers, and re-run `olmount sync`.
- **Binary files** cannot be text-merged: both sides are kept as `<file>.LOCAL` and `<file>.REMOTE` for you to pick.

Clean auto-merges are pushed to the server. When a conflict cannot be auto-resolved, the local base mirror is preserved so nothing is lost — resolve the markers and sync again.

## How it works

REST is the sync/merge *engine*: it handles reads, structural changes (file/folder create/delete/rename), binary uploads, and server-side compile/PDF. Socket.IO is used only **ephemerally, per sync pass** — `joinProject` fetches the file tree + doc ids/versions, `joinDoc` fetches doc content, and `applyOtUpdate` writes doc edits while preserving doc identity/history. There is no persistent live connection. Each sync diffs the current local tree and the remote snapshot against the version-aware base mirror (`.olsync/base/`) and reconciles the three. See `docs/superpowers/specs/` and `docs/superpowers/plans/` for the full design.

## Project layout

```
src/olmount/
  cli.py               click group + subcommand registration
  config.py            server profiles (~/.config/olmount/config.toml)
  api/                 http_client, auth, rest, socketio (ephemeral OT client)
  sync/                tree, state, ignore, merge, engine (reconcile)
  commands/            one module per CLI subcommand
tests/                 pytest suite (run: .venv/bin/python -m pytest -q)
docs/superpowers/
  specs/               design spec
  plans/               implementation plan + e2e procedure notes
```

## Limitations / status

Early/experimental. **Field-test on a throwaway project before trusting it with large or crucial work** — keep a backup outside the synced tree. Known constraints:

- OT doc writes use **UTF-16 code-unit offsets** to match the Overleaf protocol; pure-ASCII and BMP text is handled correctly, but exotic surrogate-pair edge cases deserve care.
- The Socket.IO emit path currently uses a short placeholder wait; real-server timing under load should be validated before relying on it for large multi-file bursts.
- Password login works only on non-SSO self-hosted instances; use `--cookie` everywhere else.
- No background daemon: `olmount watch` runs in the foreground.
