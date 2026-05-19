"""Hybrid retriever: dense (semantic) + sparse (BM25) fused via reciprocal
rank fusion, with optional re-ranking and metadata filtering.

This is a scaffold — wire in your vector store (Chroma, pgvector, Pinecone,
Weaviate, ...) and BM25 backend (rank_bm25, Tantivy, OpenSearch, ...) by
implementing the two `Backend` protocols.

Patterns (see ../SKILL.md):
  - Hybrid > pure semantic in production.
  - Filter on metadata BEFORE retrieval, not after.
  - Re-rank top ~30 candidates rather than tuning k upward.
  - Return chunk-level results with stable IDs the prompt assembly can cite.

Usage:
    retriever = HybridRetriever(dense=my_dense, sparse=my_sparse, reranker=my_reranker)
    results = retriever.retrieve(
        query="how do I rotate refresh tokens?",
        k=5,
        candidate_pool=30,
        metadata_filter={"doc_type": "docs", "min_updated": "2026-01-01"},
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class Hit:
    id: str
    content: str
    score: float
    source_uri: str
    section_path: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


class DenseBackend(Protocol):
    def search(
        self, query: str, k: int, metadata_filter: dict[str, Any] | None
    ) -> list[Hit]: ...


class SparseBackend(Protocol):
    def search(
        self, query: str, k: int, metadata_filter: dict[str, Any] | None
    ) -> list[Hit]: ...


Reranker = Callable[[str, list[Hit]], list[Hit]]


def rrf_fuse(
    *result_lists: list[Hit],
    k_const: int = 60,
) -> list[Hit]:
    """Reciprocal rank fusion. score = sum(1 / (k_const + rank)) across lists.

    Robust default for hybrid retrieval — no per-backend weight tuning needed.
    """
    by_id: dict[str, Hit] = {}
    scores: dict[str, float] = {}
    for results in result_lists:
        for rank, hit in enumerate(results):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k_const + rank + 1)
            if hit.id not in by_id:
                by_id[hit.id] = hit
    fused = [
        Hit(
            id=h.id,
            content=h.content,
            score=scores[h.id],
            source_uri=h.source_uri,
            section_path=h.section_path,
            metadata=h.metadata,
        )
        for h in by_id.values()
    ]
    fused.sort(key=lambda h: h.score, reverse=True)
    return fused


@dataclass
class HybridRetriever:
    dense: DenseBackend
    sparse: SparseBackend
    reranker: Reranker | None = None
    candidate_pool: int = 30

    def retrieve(
        self,
        query: str,
        k: int = 5,
        candidate_pool: int | None = None,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[Hit]:
        pool = candidate_pool or self.candidate_pool
        dense_hits = self.dense.search(query, k=pool, metadata_filter=metadata_filter)
        sparse_hits = self.sparse.search(query, k=pool, metadata_filter=metadata_filter)
        fused = rrf_fuse(dense_hits, sparse_hits)
        if self.reranker is not None:
            top_for_rerank = fused[:pool]
            return self.reranker(query, top_for_rerank)[:k]
        return fused[:k]


# --- Tiny in-memory backends for tests / examples ---


@dataclass
class _InMemoryHit:
    chunk: dict[str, Any]


class InMemorySparse(SparseBackend):
    """Toy BM25-ish: token overlap. Replace with rank_bm25 / OpenSearch in prod."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks

    @staticmethod
    def _tokens(s: str) -> set[str]:
        return {t.lower() for t in s.replace(",", " ").split() if t}

    def search(self, query: str, k: int, metadata_filter: dict[str, Any] | None) -> list[Hit]:
        q = self._tokens(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for c in self.chunks:
            if metadata_filter and not _matches(c, metadata_filter):
                continue
            overlap = len(q & self._tokens(c["content"]))
            if overlap > 0:
                scored.append((float(overlap), c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            Hit(
                id=c["id"], content=c["content"], score=s,
                source_uri=c["source_uri"], section_path=c.get("section_path", []),
                metadata=c.get("metadata", {}),
            )
            for s, c in scored[:k]
        ]


def _matches(chunk: dict[str, Any], filt: dict[str, Any]) -> bool:
    meta = {**chunk.get("metadata", {}), "source_uri": chunk["source_uri"]}
    for key, want in filt.items():
        if key.startswith("min_"):
            base = key[len("min_") :]
            if str(meta.get(base, "")) < str(want):
                return False
        elif meta.get(key) != want:
            return False
    return True
