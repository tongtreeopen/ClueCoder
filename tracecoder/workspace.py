"""Application-level path boundary for model-facing file tools."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspacePolicy:
    root: Path

    def __post_init__(self):
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    def resolve(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"path outside workspace: {path}")
        return resolved

    def check_glob_pattern(self, pattern: str) -> None:
        path = Path(pattern)
        if path.is_absolute() or path.drive or ".." in path.parts:
            raise ValueError(f"path outside workspace: {pattern}")
