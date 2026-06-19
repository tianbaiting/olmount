from __future__ import annotations
import threading, time, os
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class Watcher:
    def __init__(self, working_root, interval, debounce, do_reconcile):
        self.working_root = Path(working_root)
        self.interval = interval
        self.debounce = debounce
        self.do_reconcile = do_reconcile
        self._lock_path = self.working_root / ".olsync" / "watch.lock"
        self._timer = None
        self._stop = threading.Event()
        self._mutex = threading.Lock()
        self._reconcile_lock = threading.Lock()

    def acquire_lock(self):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
        except FileExistsError:
            if self._pid_alive(self._read_lock_pid()):
                raise RuntimeError("another `watch` already runs in this project")
            # stale lock from a dead process -> reclaim
            try:
                self._lock_path.unlink()
            except FileNotFoundError:
                pass
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)

    def _read_lock_pid(self):
        try:
            return int(self._lock_path.read_text().strip())
        except (ValueError, OSError):
            return None

    @staticmethod
    def _pid_alive(pid):
        if pid is None:
            return True   # unknown -> be safe, treat as alive
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except OSError:
            return False

    def release_lock(self):
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass

    def _on_local_event(self):
        with self._mutex:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._trigger)
            self._timer.daemon = True
            self._timer.start()

    def _trigger(self):
        self._safe_reconcile()

    def _safe_reconcile(self):
        if self._reconcile_lock.acquire(blocking=False):
            try:
                self.do_reconcile()
            finally:
                self._reconcile_lock.release()

    def run(self):
        self.acquire_lock()
        try:
            handler = FileSystemEventHandler()
            handler.on_any_event = lambda e: (self._on_local_event() if not self._ignored(e.src_path) else None)
            obs = Observer()
            obs.schedule(handler, str(self.working_root), recursive=True)
            obs.start()
            last = time.time()
            while not self._stop.wait(0.2):
                if time.time() - last >= self.interval:
                    last = time.time()
                    self._safe_reconcile()
        finally:
            with self._mutex:
                if self._timer:
                    self._timer.cancel()
                    self._timer = None
            if "obs" in locals():
                try:
                    obs.stop(); obs.join()
                except Exception:
                    pass
            self.release_lock()

    def stop(self):
        self._stop.set()
        with self._mutex:
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _ignored(self, path):
        return ".olsync" in Path(path).parts
