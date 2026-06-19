import pytest
from olmount.sync.engine import classify_path, Action

def meta(kind="doc", v=1, sha="s", size=1):
    return {"kind": kind, "docVersion": v, "sha1": sha, "size": size}

@pytest.mark.parametrize("base_loc,base_rem,expected", [
    # (local meta overrides or {}, remote meta overrides or {}) given identical base meta
    ({}, {}, "skip"),                              # both unchanged
    ({}, {"v": 2}, "pull"),                        # remote changed, local unchanged
    ({"v": 2}, {}, "push"),                        # local changed, remote unchanged
    ({"v": 2}, {"v": 3}, "conflict"),              # both changed
    (None, {}, "push_delete"),                     # local deleted, remote unchanged
    ({}, None, "pull_delete"),                     # remote deleted, local unchanged
    (None, {"v": 2}, "conflict"),                  # local deleted, remote changed (delete/edit)
    ({"v": 2}, None, "conflict"),                  # local changed, remote deleted (edit/delete)
    (None, None, "noop"),                          # both gone
])
def test_matrix(base_loc, base_rem, expected):
    bm = meta()
    lm = meta(**base_loc) if base_loc is not None else None
    rm = meta(**base_rem) if base_rem is not None else None
    assert classify_path("f", bm, lm, rm) == Action(expected)

def test_converged_equal_means_skip():
    # both new, identical -> in sync
    assert classify_path("f", None, meta(sha="eq"), meta(sha="eq")) == Action.SKIP
    # both changed to same value -> in sync
    bm = meta(sha="old")
    assert classify_path("f", bm, meta(sha="new"), meta(sha="new")) == Action.SKIP
