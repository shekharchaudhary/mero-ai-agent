"""Build the retrieval block of an agent prompt with numbered chunks and
citation validation.

Patterns (see ../SKILL.md):
  - Retrieved chunks go AFTER the cached system prompt — never embed them
    in the cached layer, or the cache invalidates on every query.
  - Number chunks [1], [2], ... so the model can cite by index.
  - Header each chunk with its source + section path so the citation tells
    the user where to look.
  - Place the top-1 chunk LAST in the block; attention is strongest at the
    ends of a long sequence.
  - Validate citations against the retrieved set before returning.

Usage:
    block = build_retrieval_block(hits)
    # ...send to model with cache_control on the layers above this block...
    answer = model_call(...).text
    validation = validate_citations(answer, hits)
    if not validation.ok:
        # regenerate with stricter prompt, or surface validation.unknown_citations
        ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from retriever import Hit

CITATION_RE = re.compile(r"\[(\d+)\]")

CITATION_INSTRUCTION = (
    "Cite the chunk index for every factual claim, like [3]. "
    "If no chunk supports a claim, say so explicitly and do not cite. "
    "Do not invent chunk indices."
)


def build_retrieval_block(
    hits: list[Hit],
    *,
    title: str = "Retrieved context",
    place_best_last: bool = True,
) -> str:
    """Format hits into a numbered, header-tagged block to append to a prompt.

    Ordering: by default the top-scored hit is placed LAST so the model
    sees it most recently before generating. The index assigned to each
    chunk is independent of position, so citations remain meaningful.
    """
    if not hits:
        return f"<{title}>\n(no relevant chunks retrieved)\n</{title}>"

    indexed = list(enumerate(hits, start=1))
    if place_best_last:
        # hits are already sorted best→worst; reverse so best is last.
        indexed = list(reversed(indexed))

    parts = [f"<{title}>"]
    parts.append(CITATION_INSTRUCTION)
    parts.append("")
    for idx, h in indexed:
        section = " > ".join(h.section_path) or "(root)"
        parts.append(f"[{idx}] source: {h.source_uri}, section: {section}")
        parts.append(h.content.strip())
        parts.append("")
    parts.append(f"</{title}>")
    return "\n".join(parts)


@dataclass
class CitationValidation:
    ok: bool
    cited: list[int]
    unknown_citations: list[int]
    uncited_share: float  # fraction of sentences with no citation


def validate_citations(answer: str, hits: list[Hit]) -> CitationValidation:
    """Check that every citation refers to a chunk in `hits`.

    Returns:
      - cited: indices the model used
      - unknown_citations: indices that don't exist in `hits`
      - uncited_share: rough share of sentences with no citation, as a smell test
    """
    valid_indices = set(range(1, len(hits) + 1))
    cited_raw = [int(m.group(1)) for m in CITATION_RE.finditer(answer)]
    cited = sorted(set(cited_raw))
    unknown = sorted(set(cited_raw) - valid_indices)

    sentences = re.split(r"(?<=[.!?])\s+", answer.strip())
    sentences = [s for s in sentences if s.strip()]
    if not sentences:
        uncited_share = 0.0
    else:
        with_cite = sum(1 for s in sentences if CITATION_RE.search(s))
        uncited_share = 1.0 - with_cite / len(sentences)

    return CitationValidation(
        ok=not unknown,
        cited=cited,
        unknown_citations=unknown,
        uncited_share=round(uncited_share, 3),
    )


if __name__ == "__main__":
    sample_hits = [
        Hit(
            id="a1", content="Rotate refresh tokens every 30 days.", score=0.9,
            source_uri="docs/auth.md", section_path=["Auth Guide", "Refresh tokens"],
        ),
        Hit(
            id="b2", content="E-1042 means invalid signature.", score=0.7,
            source_uri="docs/auth.md", section_path=["Auth Guide", "Error codes"],
        ),
    ]
    print(build_retrieval_block(sample_hits))
    print()
    fake_answer = (
        "Rotate refresh tokens monthly [1]. Error E-1042 is invalid signature [2]. "
        "Tokens never expire [4]."
    )
    v = validate_citations(fake_answer, sample_hits)
    print(v)
