# tests/test_config.py
import os
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

def test_save_restricts_file_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr("olmount.config.CONFIG_PATH", tmp_path / "c.toml")
    cfg = Config.load()
    cfg.set_server("s", url="https://x", cookie="c", csrf="x", user_id="u", email="e")
    cfg.save()
    mode = os.stat(tmp_path / "c.toml").st_mode & 0o777
    assert mode == 0o600
