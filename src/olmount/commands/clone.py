import click, pathlib
from olmount.config import Config
from olmount.api.http_client import HttpClient
from olmount.api.rest import OverleafREST
from olmount.api.socketio import EphemeralOLClient
from olmount.sync.state import ProjectState
from olmount.sync.engine import build_local_snapshot, build_remote_snapshot
from olmount.sync.tree import RemoteTree

@click.command()
@click.argument("project")
@click.option("--server")
@click.option("--into", default=".")
def clone_cmd(project, server, into):
    cfg = Config.load()
    name = server or cfg.default_server()
    prof = cfg.server(name)
    rest = OverleafREST(HttpClient(prof.url, prof.cookie, prof.csrf))
    pid = _resolve_id(rest, project)
    pname = _project_name(rest, pid)
    work = pathlib.Path(into) if into != "." else pathlib.Path(into) / pname
    work.mkdir(parents=True, exist_ok=True)
    zf = rest.download_zip(pid)
    for n in zf.namelist():
        if n.endswith("/"):
            continue
        out = work / n
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(zf.read(n))
    with EphemeralOLClient(prof.url, prof.cookie) as sock:
        tree_payload = sock.join_project(pid)
    root_id = tree_payload.get("rootDoc_id", "")
    st = ProjectState.init(work, server=name, projectId=pid, projectName=pname, rootDocId=root_id)
    tree = RemoteTree(tree_payload)
    remote_snap = build_remote_snapshot(zf, tree)
    local_snap = build_local_snapshot(work, lambda p: p.startswith(".olsync"))
    new_base = {}
    for p in local_snap:
        m = dict(local_snap[p])
        if p in remote_snap and "id" in remote_snap[p]:
            m["id"] = remote_snap[p]["id"]
        if p in remote_snap and "docVersion" in remote_snap[p]:
            m["docVersion"] = remote_snap[p]["docVersion"]
        new_base[p] = m
    st.advance(new_base, work, lambda p: p.startswith(".olsync"))
    click.echo(f"cloned '{pname}' ({pid}) -> {work}")

def _resolve_id(rest, project):
    for p in rest.list_projects():
        if p.get("id") == project or p.get("name") == project:
            return p["id"]
    raise click.ClickException(f"project '{project}' not found")

def _project_name(rest, pid):
    for p in rest.list_projects():
        if p.get("id") == pid:
            return p["name"]
    return pid
