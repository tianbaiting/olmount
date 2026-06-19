import time, threading
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
