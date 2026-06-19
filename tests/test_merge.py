from olmount.sync.merge import three_way_merge

def test_no_conflict_disjoint_changes():
    base = "alpha\nbeta\ngamma\n"
    local = "ALPHA\nbeta\ngamma\n"      # changed line 1
    remote = "alpha\nbeta\nGAMMA\n"     # changed line 3
    merged, conflict = three_way_merge(base, local, remote)
    assert conflict is False
    assert merged == "ALPHA\nbeta\nGAMMA\n"

def test_conflict_overlapping():
    base = "alpha\nbeta\ngamma\n"
    local = "LOCAL\nbeta\ngamma\n"
    remote = "REMOTE\nbeta\ngamma\n"
    merged, conflict = three_way_merge(base, local, remote, "local", "remote")
    assert conflict is True
    assert "<<<<<<< local" in merged and "=======" in merged and ">>>>>>> remote" in merged
    assert "LOCAL" in merged and "REMOTE" in merged

def test_remote_only_change_is_taken():
    base = "a\nb\nc\n"; local = "a\nb\nc\n"; remote = "a\nB\nc\n"
    merged, conflict = three_way_merge(base, local, remote)
    assert merged == "a\nB\nc\n" and conflict is False
