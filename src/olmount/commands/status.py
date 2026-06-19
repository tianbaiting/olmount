import click
from olmount.commands._run import build_engine
from olmount.sync.engine import build_local_snapshot, build_remote_snapshot, classify_path, Action
from olmount.sync.tree import RemoteTree

@click.command()
def status_cmd():
    eng, sock = build_engine()
    try:
        tree = RemoteTree(sock.join_project(eng.project_id))
        zf = eng.rest.download_zip(eng.project_id)
        remote_snap = build_remote_snapshot(zf, tree)
        local_snap = build_local_snapshot(eng.working_root, eng.ignore)
        base = eng.state.data.get("base", {})
        any_change = False
        for p in sorted(set(base) | set(local_snap) | set(remote_snap)):
            if p.startswith(".olsync"):
                continue
            act = classify_path(p, base.get(p), local_snap.get(p), remote_snap.get(p))
            if act not in (Action.SKIP, Action.NOOP):
                click.echo(f"{act.value:12} {p}")
                any_change = True
        if not any_change:
            click.echo("up to date")
    finally:
        sock.disconnect()
