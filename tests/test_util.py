# tests/test_util.py
from olmount.util import sha1_hex, atomic_write_bytes, utf16_len
from pathlib import Path

def test_sha1_hex():
    assert sha1_hex(b"hello") == "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"

def test_atomic_write(tmp_path):
    p = tmp_path / "x" / "f.txt"
    atomic_write_bytes(p, b"data")
    assert p.read_bytes() == b"data"

def test_utf16_len():
    assert utf16_len("abc") == 3
    assert utf16_len("😀") == 2          # astral -> surrogate pair
    assert utf16_len("a😀b") == 4
