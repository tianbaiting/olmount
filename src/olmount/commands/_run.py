import pathlib
from olmount.config import Config
from olmount.api.http_client import HttpClient
from olmount.api.rest import OverleafREST
from olmount.api.socketio import EphemeralOLClient
from olmount.sync.state import ProjectState
from olmount.sync.ignore import IgnoreFilter
from olmount.sync.engine import Engine

def build_engine(server=None):
    cfg = Config.load()
    name = server or cfg.default_server()
    prof = cfg.server(name)
    work = pathlib.Path.cwd()
    st = ProjectState(work)
    if not st.exists():
        raise SystemExit("not an olmount project (no .olsync/ here)")
    st.load()
    rest = OverleafREST(HttpClient(prof.url, prof.cookie, prof.csrf))
    sock = EphemeralOLClient(prof.url, prof.cookie)
    sock.connect()
    ig = IgnoreFilter.from_file(work / ".olignore")
    eng = Engine(state=st, rest=rest, sock=sock, project_id=st.data["projectId"],
                 working_root=work,
                 ignore=lambda p: p.startswith(".olsync") or ig.is_ignored(p),
                 on_event=lambda *a: None)
    return eng, sock
