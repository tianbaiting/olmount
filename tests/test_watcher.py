import os, time, threading
from pathlib import Path
from olmount.sync.watcher import Watcher

def test_debounce_coalesces_burst(tmp_path):
    fired = []
    w = Watcher(working_root=tmp_path, interval=10, debounce=0.1,
                do_reconcile=lambda: fired.append(time.time()))
    w._on_local_event()  # burst of 3
    w._on_local_event()
    w._on_local_event()
    time.sleep(0.3)
    assert len(fired) == 1

def test_lock_file_prevents_second_watch(tmp_path):
    (tmp_path / ".olsync").mkdir()
    w1 = Watcher(tmp_path, interval=10, debounce=0.05, do_reconcile=lambda: None)
    w1.acquire_lock()
    import pytest
    w2 = Watcher(tmp_path, interval=10, debounce=0.05, do_reconcile=lambda: None)
    with pytest.raises(RuntimeError):
        w2.acquire_lock()
    w1.release_lock()


def test_stop_cancels_pending_debounce(tmp_path):
    fired = []
    w = Watcher(tmp_path, interval=10, debounce=0.2, do_reconcile=lambda: fired.append(1))
    w._on_local_event()           # schedule a debounce
    w.stop()                      # cancel before it fires
    time.sleep(0.4)
    assert fired == []            # pending timer was cancelled, did not fire


def test_reconcile_not_reentrant(tmp_path):
    in_call = threading.Event()
    done = threading.Event()
    overlap = {"n": 0}
    def reconcile():
        overlap["n"] += 1
        if overlap["n"] > 1:
            assert False, "reconcile ran concurrently"
        in_call.set()
        done.wait(2.0)            # hold the lock until released
    w = Watcher(tmp_path, interval=10, debounce=0.0, do_reconcile=reconcile)
    # first trigger on a background thread -> enters reconcile, holds the lock
    bg = threading.Thread(target=w._trigger)
    bg.start()
    assert in_call.wait(2.0)      # bg is now inside reconcile (lock held)
    # second trigger on THIS thread -> must SKIP (lock busy, non-reentrant)
    w._trigger()
    done.set()                    # release the background reconcile
    bg.join()
    assert overlap["n"] == 1      # second was skipped because the first was running


def test_stale_lock_reclaimed_if_process_dead(tmp_path):
    (tmp_path / ".olsync").mkdir()
    w1 = Watcher(tmp_path, interval=10, debounce=0.05, do_reconcile=lambda: None)
    w1.acquire_lock()
    # simulate a dead process: write a PID that does not exist
    (tmp_path / ".olsync" / "watch.lock").write_text("999999")
    # a new watcher should reclaim the stale lock (process 999999 presumed dead)
    w2 = Watcher(tmp_path, interval=10, debounce=0.05, do_reconcile=lambda: None)
    w2.acquire_lock()             # should NOT raise (reclaimed)
    w2.release_lock()
