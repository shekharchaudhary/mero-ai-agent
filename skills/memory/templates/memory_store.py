"""Minimal file-based memory store for an AI agent.

Memory layout:

    ~/.agent-memory/<project>/
    ├── MEMORY.md          # index, one line per entry
    ├── <name>.md          # one memory per file, with YAML frontmatter

Frontmatter fields: name, description, type (user|feedback|project|reference).

Usage:
    store = MemoryStore("my-agent")
    store.write(MemoryEntry(
        name="user_role",
        description="senior data engineer, prefers Polars",
        type="user",
        body="User is a senior data engineer...",
    ))
    matches = store.search("polars")
    store.rebuild_index()
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

VALID_TYPES = {"user", "feedback", "project", "reference"}
_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(s: str) -> str:
    return _SLUG.sub("_", s.lower()).strip("_")


@dataclass
class MemoryEntry:
    name: str
    description: str
    type: str
    body: str

    def __post_init__(self) -> None:
        if self.type not in VALID_TYPES:
            raise ValueError(f"type must be one of {VALID_TYPES}, got {self.type!r}")
        if len(self.description) > 200:
            raise ValueError("description should be a single short line (<=200 chars)")

    @property
    def filename(self) -> str:
        return f"{_slugify(self.name)}.md"

    def render(self) -> str:
        return (
            "---\n"
            f"name: {self.name}\n"
            f"description: {self.description}\n"
            f"type: {self.type}\n"
            "---\n\n"
            f"{self.body.rstrip()}\n"
        )


class MemoryStore:
    def __init__(self, project: str, root: Path | None = None) -> None:
        root = root or (Path.home() / ".agent-memory")
        self.dir = root / _slugify(project)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index = self.dir / "MEMORY.md"

    def write(self, entry: MemoryEntry) -> Path:
        path = self.dir / entry.filename
        path.write_text(entry.render(), encoding="utf-8")
        self.rebuild_index()
        return path

    def read(self, name: str) -> MemoryEntry | None:
        path = self.dir / f"{_slugify(name)}.md"
        if not path.exists():
            return None
        return self._parse(path)

    def delete(self, name: str) -> bool:
        path = self.dir / f"{_slugify(name)}.md"
        if not path.exists():
            return False
        path.unlink()
        self.rebuild_index()
        return True

    def all(self) -> list[MemoryEntry]:
        entries = []
        for p in sorted(self.dir.glob("*.md")):
            if p.name == "MEMORY.md":
                continue
            parsed = self._parse(p)
            if parsed:
                entries.append(parsed)
        return entries

    def search(self, query: str) -> list[MemoryEntry]:
        q = query.lower()
        return [e for e in self.all() if q in e.description.lower() or q in e.body.lower()]

    def rebuild_index(self) -> None:
        lines = ["# Memory index", ""]
        for e in self.all():
            lines.append(f"- [{e.name}]({e.filename}) — {e.description}")
        self.index.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _parse(path: Path) -> MemoryEntry | None:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return None
        _, fm, body = text.split("---\n", 2)
        meta: dict[str, str] = {}
        for line in fm.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        try:
            return MemoryEntry(
                name=meta["name"],
                description=meta["description"],
                type=meta["type"],
                body=body.strip(),
            )
        except (KeyError, ValueError):
            return None
