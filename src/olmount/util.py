# src/olmount/util.py
from __future__ import annotations
import hashlib, os, tempfile
from pathlib import Path

def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()

def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise

def utf16_len(s: str) -> int:
    """Length in UTF-16 code units (JS string .length). Astral chars count as 2."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)
