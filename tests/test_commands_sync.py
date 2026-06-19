import responses, io, zipfile, pathlib, json
from click.testing import CliRunner
from olmount.cli import main
from olmount.config import Config

def _bootstrap_cfg(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    monkeypatch.setattr("olmount.config.CONFIG_PATH", cfg)
    c = Config.load()
    c.set_server("h", url="https://ol.lab.edu", cookie="c", csrf="x", user_id="u", email="e")
    c.set_default("h")
    c.save()

@responses.activate
def test_clone_downloads_and_inits_state(tmp_path, monkeypatch):
    _bootstrap_cfg(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("main.tex", "REMOTE")
    responses.add(responses.GET, "https://ol.lab.edu/project/p1/download/zip", status=200, body=buf.getvalue())
    responses.add(responses.GET, "https://ol.lab.edu/project", status=200,
                  body='<meta name="ol-prefetchedProjectsBlob" content=\'{"projects":[{"id":"p1","name":"paper"}]}\'>')
    joinpay = pathlib.Path("tests/fixtures/joinproject.json").read_text()
    import olmount.api.socketio as sio_mod
    monkeypatch.setattr(sio_mod.EphemeralOLClient, "join_project", lambda self, pid: json.loads(joinpay))
    monkeypatch.setattr(sio_mod.EphemeralOLClient, "connect", lambda self: None)
    monkeypatch.setattr(sio_mod.EphemeralOLClient, "disconnect", lambda self: None)
    work = tmp_path / "work"
    r = CliRunner().invoke(main, ["clone", "p1", "--server", "h", "--into", str(work)])
    assert r.exit_code == 0, r.output
    assert (work / "main.tex").read_text() == "REMOTE"
    assert (work / ".olsync" / "state.json").is_file()
    assert (work / ".olsync" / "base" / "main.tex").read_text() == "REMOTE"  # base mirrored

@responses.activate
def test_status_reports_local_change(tmp_path, monkeypatch):
    _bootstrap_cfg(tmp_path, monkeypatch)
    # clone first (reuse the clone path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("main.tex", "REMOTE")
    responses.add(responses.GET, "https://ol.lab.edu/project/p1/download/zip", status=200, body=buf.getvalue())
    responses.add(responses.GET, "https://ol.lab.edu/project", status=200,
                  body='<meta name="ol-prefetchedProjectsBlob" content=\'{"projects":[{"id":"p1","name":"paper"}]}\'>')
    joinpay = pathlib.Path("tests/fixtures/joinproject.json").read_text()
    import olmount.api.socketio as sio_mod
    monkeypatch.setattr(sio_mod.EphemeralOLClient, "join_project", lambda self, pid: json.loads(joinpay))
    monkeypatch.setattr(sio_mod.EphemeralOLClient, "connect", lambda self: None)
    monkeypatch.setattr(sio_mod.EphemeralOLClient, "disconnect", lambda self: None)
    work = tmp_path / "work"
    CliRunner().invoke(main, ["clone", "p1", "--server", "h", "--into", str(work)])
    # edit locally
    (work / "main.tex").write_text("EDITED")
    # status (run from work dir) should mention main.tex as a push
    monkeypatch.chdir(work)
    responses.add(responses.GET, "https://ol.lab.edu/project/p1/download/zip", status=200, body=buf.getvalue())
    r = CliRunner().invoke(main, ["status"])
    assert r.exit_code == 0, r.output
    assert "main.tex" in r.output
