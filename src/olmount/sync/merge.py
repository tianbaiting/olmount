from __future__ import annotations

from merge3 import Merge3


def _lines(s: str) -> list[str]:
    return s.splitlines(keepends=True)


def _ensure_nl(lines: list[str]) -> list[str]:
    return [(l if l.endswith("\n") else l + "\n") for l in lines]


def three_way_merge(base: str, local: str, remote: str,
                    label_local: str = "local",
                    label_remote: str = "remote") -> tuple[str, bool]:
    """Line-oriented diff3 merge. Returns (merged_text, had_conflict)."""
    m3 = Merge3(_lines(base), _lines(local), _lines(remote))
    out: list[str] = []
    conflict = False
    for group in m3.merge_groups():
        tag = group[0]
        if tag == "unchanged":
            out.extend(group[1])
        elif tag == "a" or tag == "same":
            out.extend(group[1])
        elif tag == "b":
            out.extend(group[1])
        elif tag == "conflict":
            a_lines = group[2]
            b_lines = group[3]
            conflict = True
            out.append(f"<<<<<<< {label_local}\n")
            out.extend(_ensure_nl(a_lines))
            out.append("=======\n")
            out.extend(_ensure_nl(b_lines))
            out.append(f">>>>>>> {label_remote}\n")
    return "".join(out), conflict
