from __future__ import annotations
from pathlib import Path
import pathspec

class IgnoreFilter:
    def __init__(self, patterns: list[str] | None = None):
        self.spec = pathspec.PathSpec.from_lines("gitignore", patterns or [])

    @classmethod
    def from_file(cls, path: Path | None) -> "IgnoreFilter":
        if path is None or not Path(path).is_file():
            return cls([])
        return cls(Path(path).read_text().splitlines())

    def is_ignored(self, relpath_posix: str) -> bool:
        return self.spec.match_file(relpath_posix)
