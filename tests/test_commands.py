from click.testing import CliRunner
from olmount.cli import main
from olmount.config import Config

def test_servers_add_and_whoami(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    monkeypatch.setattr("olmount.config.CONFIG_PATH", cfg)
    r = CliRunner().invoke(main, ["servers", "add", "myhost", "--url", "https://ol.lab.edu"])
    assert r.exit_code == 0, r.output

    import responses
    @responses.activate
    def go():
        responses.add(responses.GET, "https://ol.lab.edu/project", status=200,
                      body='<meta name="ol-user_id" content="u1"><meta name="ol-usersEmail" content="me@x.y"><meta name="ol-csrfToken" content="csrf">')
        rr = CliRunner().invoke(main, ["login", "--server", "myhost", "--cookie", "sharelatex.sid=x"])
        assert rr.exit_code == 0, rr.output
        rw = CliRunner().invoke(main, ["whoami", "--server", "myhost"])
        assert "u1" in rw.output or "me@x.y" in rw.output
    go()

def test_servers_list_and_set_default(tmp_path, monkeypatch):
    monkeypatch.setattr("olmount.config.CONFIG_PATH", tmp_path / "c.toml")
    CliRunner().invoke(main, ["servers", "add", "a", "--url", "https://a"])
    CliRunner().invoke(main, ["servers", "add", "b", "--url", "https://b"])
    CliRunner().invoke(main, ["servers", "set-default", "b"])
    r = CliRunner().invoke(main, ["servers", "list"])
    assert "b" in r.output and "https://b" in r.output
