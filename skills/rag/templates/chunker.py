"""Recursive markdown chunker.

Patterns (see ../SKILL.md):
  - Splits on the document's own structure (headers) first.
  - Preserves the section path so each chunk knows where it lives.
  - Recursive fallback to paragraphs, then to fixed-size with overlap,
    only when the current chunk still exceeds the size budget.
  - Stable IDs from (source_uri, section_path, content_hash) — diffable
    across re-ingestion so unchanged chunks aren't re-embedded.

Usage:
    from chunker import chunk_markdown

    chunks = chunk_markdown(
        text=Path("docs/auth.md").read_text(),
        source_uri="docs/auth.md",
        max_chars=2000,
        overlap=200,
    )
    # each chunk: {id, source_uri, section_path, content, token_estimate, metadata}
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")


@dataclass
class Chunk:
    id: str
    source_uri: str
    section_path: list[str]
    content: str
    token_estimate: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_uri": self.source_uri,
            "section_path": self.section_path,
            "content": self.content,
            "token_estimate": self.token_estimate,
            "metadata": self.metadata,
        }


def _hash(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _estimate_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def _split_by_headers(text: str) -> list[tuple[list[str], str]]:
    """Return [(section_path, body)] pairs walking the doc top-to-bottom."""
    sections: list[tuple[list[str], str]] = []
    path: list[str] = []
    pos = 0
    matches = list(HEADER_RE.finditer(text))
    if not matches:
        return [([], text.strip())]

    if matches[0].start() > 0:
        body = text[: matches[0].start()].strip()
        if body:
            sections.append(([], body))

    for i, m in enumerate(matches):
        depth = len(m.group(1))
        title = m.group(2).strip()
        path = path[: depth - 1] + [title]
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections.append((list(path), body))
    return sections


def _recursive_split(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paras = PARAGRAPH_SPLIT.split(text)
    out: list[str] = []
    buf = ""
    for p in paras:
        if not buf:
            buf = p
        elif len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}"
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)

    # Final fallback: any remaining oversize pieces get fixed-size chunked.
    final: list[str] = []
    for piece in out:
        if len(piece) <= max_chars:
            final.append(piece)
            continue
        step = max(1, max_chars - overlap)
        for i in range(0, len(piece), step):
            final.append(piece[i : i + max_chars])
    return final


def chunk_markdown(
    text: str,
    source_uri: str,
    max_chars: int = 2000,
    overlap: int = 200,
    extra_metadata: dict[str, Any] | None = None,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    extra_metadata = extra_metadata or {}

    for section_path, body in _split_by_headers(text):
        if not body:
            continue
        for piece in _recursive_split(body, max_chars=max_chars, overlap=overlap):
            content_hash = _hash(piece)
            chunk_id = _hash(source_uri, " > ".join(section_path), content_hash)
            chunks.append(Chunk(
                id=chunk_id,
                source_uri=source_uri,
                section_path=section_path,
                content=piece,
                token_estimate=_estimate_tokens(piece),
                metadata={
                    "content_hash": content_hash,
                    "section_title": section_path[-1] if section_path else "",
                    "depth": len(section_path),
                    **extra_metadata,
                },
            ))
    return chunks


if __name__ == "__main__":
    sample = """# Auth Guide

Intro paragraph about authentication.

## Refresh tokens

Refresh tokens are long-lived...

### Rotation

Rotate tokens every 30 days. Reasons: blast radius, key compromise, compliance.
A second paragraph in this subsection with more detail about why rotation matters
and what the operational procedure looks like in practice.

## Error codes

- E-1042: invalid signature
- E-1043: expired token
"""
    import json
    for c in chunk_markdown(sample, source_uri="docs/auth.md", max_chars=400):
        print(json.dumps({
            "id": c.id,
            "section_path": c.section_path,
            "preview": c.content[:60] + ("…" if len(c.content) > 60 else ""),
            "tokens": c.token_estimate,
        }))
