from __future__ import annotations
from diff_match_patch import diff_match_patch
from olmount.util import utf16_len

EQUAL, INSERT, DELETE = 0, 1, -1

def diff_ops(remote_now: str, new_content: str) -> list[dict]:
    """
    Overleaf/ShareLaTeX OT ops transforming remote_now -> new_content.
    op.p is a UTF-16 code-unit offset; op.i/op.d are literal substrings.
    Mirrors Overleaf-Workshop remoteFileSystemProvider.ts writeFile op generation,
    made explicit about UTF-16 units (the TS Buffer.from(x,'utf-8').toString('utf-8')
    is a no-op, so its effective unit is JS .length == UTF-16 units).
    """
    dmp = diff_match_patch()
    diffs = dmp.diff_main(remote_now, new_content)
    dmp.diff_cleanupSemantic(diffs)
    ops: list[dict] = []
    pos = 0  # UTF-16 offset into remote_now
    for op_type, text in diffs:
        if op_type == EQUAL:
            pos += utf16_len(text)
        elif op_type == INSERT:
            ops.append({"p": pos, "i": text})
        elif op_type == DELETE:
            ops.append({"p": pos, "d": text})
            pos += utf16_len(text)
    return ops

# ---- inverse, for property tests & (optionally) local apply of incoming ops ----
def _to_units(s: str) -> list[str]:
    units = []
    for c in s:
        cp = ord(c)
        if cp > 0xFFFF:
            cp -= 0x10000
            units.append(chr(0xD800 + (cp >> 10)))
            units.append(chr(0xDC00 + (cp & 0x3FF)))
        else:
            units.append(c)
    return units

def _from_units(units: list[str]) -> str:
    # encode with surrogatepass to allow lone surrogates through; the standard
    # decoder then recombines properly paired surrogates into astral chars.
    return "".join(units).encode("utf-16-le", errors="surrogatepass").decode("utf-16-le")

def apply_ops(ops: list[dict], content: str) -> str:
    """Apply a batch of ops (all op.p relative to the ORIGINAL content, ShareJS-style)."""
    units = _to_units(content)
    for op in sorted(ops, key=lambda o: o["p"], reverse=True):
        p = op["p"]
        if op.get("d") is not None:
            n = utf16_len(op["d"])
            del units[p:p + n]
        elif op.get("i") is not None:
            units[p:p] = _to_units(op["i"])
    return _from_units(units)
