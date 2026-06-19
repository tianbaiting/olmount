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
