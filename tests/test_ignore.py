from olmount.sync.ignore import IgnoreFilter

def test_basic_ignore(tmp_path):
    (tmp_path / ".olignore").write_text("output/\n*.aux\n.olsync/\n")
    ig = IgnoreFilter.from_file(tmp_path / ".olignore")
    assert ig.is_ignored("output/main.pdf")
    assert ig.is_ignored("main.aux")
    assert ig.is_ignored(".olsync/state.json")
    assert not ig.is_ignored("main.tex")
    assert not ig.is_ignored("secs/intro.tex")
