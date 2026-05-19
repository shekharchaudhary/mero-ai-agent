---
name: rag
description: Scaffold retrieval-augmented generation for AI agents — chunking with metadata, hybrid retrieval with re-ranking, prompt assembly that caches well and cites sources, and eval of retrieval separately from generation. Use when an agent needs to ground answers in a corpus larger than the context window.
---

# rag

RAG is the right tool when an agent must answer from a corpus larger than the context window and the answer must be grounded in specific sources. It's the wrong tool when the corpus fits in a cached prefix (just put it in context), when the data is structured (use SQL/an API), or when the agent needs to *reason* about the data rather than look it up. This skill codifies the patterns that make RAG actually work in production. Pairs with `prompt-engineering` (retrieved context belongs in the cache layout), `evals` (retrieval and generation are evaluated separately), `cost-tracking` (embedding cost is a real line item), and `logging` (log every query, the retrieved chunks, and which chunks the model cited).

## When to trigger

- User types `/rag`.
- User asks to "make the agent answer from these docs", "give it knowledge of our codebase", "ground responses in source X."
- User has tried stuffing a long context and is hitting context-length errors or paying for it.
- An existing RAG setup has poor recall, hallucinated citations, or unbounded latency.

## Decide before you build

Before reaching for RAG, ask:

| Situation | Better answer |
| --- | --- |
| Corpus fits in <100k tokens, mostly static | Put it in the prompt cache — no retrieval, no vector store. |
| Corpus is structured (DB rows, tickets, metrics) | Tool call to a real query API. RAG over a structured DB is a regression. |
| Answers require multi-step reasoning over the corpus | RAG retrieves; the agent reasons. Plan for tool use over RAG results, not RAG alone. |
| Corpus changes faster than you can re-index | Reconsider the ingestion model, or shift to live API calls. |
| Single-source, single-question, one-shot | Just include the source in the prompt. RAG is overkill. |

RAG earns its complexity at: large corpus + frequent queries + need for citations + acceptable index lag.

## Chunking

Chunk boundaries are the most common cause of bad retrieval. A good chunk:

- **Stands alone semantically.** A reader who sees only the chunk can understand it.
- **Carries metadata.** `source`, `section`, `title`, `last_modified`, `doc_type` — used for filtering and citations.
- **Has stable IDs.** Hash of (source_uri, position, content). Stable IDs make re-indexing diffable.

Strategies, in order of usual preference:

1. **Structural chunks.** Use the document's own structure: markdown headers, code blocks, HTML sections, function definitions, JSON objects. The author already drew the boundaries.
2. **Recursive split.** Try paragraph → sentence → fixed-size, falling through only when needed. Preserves coherence when structural cues are weak.
3. **Fixed-size with overlap.** Last resort. ~500-1000 tokens with ~10-15% overlap. Use only on unstructured text.

Common mistakes:

- **Splitting code mid-function.** A chunk that ends in the middle of `def foo()` is worse than one that includes the whole function plus its preceding docstring.
- **Splitting tables across chunks.** Either keep the whole table in one chunk or convert to a more retrievable format (one row per chunk with the schema row repeated).
- **Stripping headers.** A chunk that starts with `# Authentication` carries more signal than one that doesn't. Include the section path.

## Embeddings

- Pick an embedding model and **commit to it for the corpus**. Re-embedding is expensive; switching embeddings means re-embedding everything.
- Match query and document embeddings — they must be from the same model.
- Cache embeddings keyed by `(model_id, content_hash)`. Re-ingestion shouldn't re-pay for unchanged chunks.
- Embedding cost goes in the cost ledger like model cost (see `cost-tracking`).

## Retrieval

Pure semantic search wins benchmarks; **hybrid** search wins production. Use both:

- **Semantic (dense)**: catches paraphrases, synonyms, conceptual matches.
- **Lexical (BM25 / sparse)**: catches exact tokens — names, error codes, API identifiers, version numbers. Always wins on "what does error E-1042 mean."
- **Combine** via reciprocal rank fusion (RRF) or a weighted blend. RRF is simpler and robust.

Then **re-rank** the top ~30 candidates with a cross-encoder or a model judge. Re-ranking gains usually beat embedding upgrades on the same budget.

Filters > re-ranking when applicable:

- **Metadata filter first.** If the user is asking about `2025 docs`, filter to that before retrieving, don't filter after.
- **Recency boosts** for time-sensitive corpora. A small recency weight on the score prevents stale answers.

## Query transformation

Often the user's query isn't a good retrieval query. Cheap transforms that help:

- **Query decomposition.** "How do I auth and then upload a file?" → two queries.
- **HyDE-lite.** Have the model write a hypothetical *answer* to the query, embed that, and retrieve against it. Surprisingly effective for vague queries.
- **Conversational rewrite.** Multi-turn queries often reference earlier turns ("what about for the v2 API?"). Rewrite to a standalone query before retrieval.

Don't over-engineer this — every transform adds latency and cost. Measure with retrieval evals before adopting.

## Prompt assembly

How retrieved chunks land in the prompt matters as much as which chunks were retrieved.

- **Cache the stable layers** (see `prompt-engineering`): system prompt → tool defs → static framing. Retrieved chunks change per query and go *after* the cache breakpoint.
- **Number every chunk** (`[1]`, `[2]`, …) so the model can cite by index. Citation alignment becomes verifiable.
- **Include metadata in the chunk header**:
  ```
  [3] source: docs/auth.md, section: "Refresh tokens", updated: 2026-04
  <chunk body>
  ```
- **Instruct on citation discipline.** "Cite the chunk index for every factual claim. If no chunk supports the claim, say so explicitly."
- **Order matters.** Models attend more reliably to the start and end. Place the top-1 chunk last, surrounded by relevant context.
- **Cap the budget.** Retrieve top-K (e.g. 5-10) and truncate to a token budget. More chunks ≠ better answers past a point; they dilute attention and inflate cost.

## Citations the agent can't fake

The cheapest hallucination to detect is a citation pointing at a chunk that doesn't exist or doesn't say what was claimed. Build verification in:

1. Retrieve chunks; assign indices.
2. Generate the answer with citations.
3. **Validate every citation** against the chunk set before returning to the user. Unparseable or unknown citations → reject and regenerate, or surface as a warning.
4. Optional but worth the cost: for high-stakes responses, re-prompt the model with "Does chunk [N] actually support claim X?" and gate on yes.

Log every (query, retrieved_ids, cited_ids, answer) tuple. The cite-vs-retrieve diff is your hallucination smoke alarm.

## Evaluation

Evaluate retrieval and generation **separately**. A great generator over bad retrieval is a confident liar.

| Layer | Metric | Notes |
| --- | --- | --- |
| Retrieval | `recall@k`, `MRR` against a labeled golden set | Build the golden set from real user queries. ~50-200 cases. |
| Re-ranking | `nDCG@10`, position of the correct chunk | Compare candidate sets before and after re-rank. |
| Generation | Answer-faithfulness (LLM judge with the chunks), citation correctness | Use the rubric pattern from `evals`. |
| End-to-end | Cost-per-correct-answer | Ties cost, retrieval, and generation together. |

Refresh the golden set quarterly from new user queries. Old golden sets get gamed by your own optimization choices.

## Freshness and ingestion

- **Pull, not push, where possible.** A cron that re-ingests known sources is more robust than relying on systems-of-record to push events.
- **Diff before re-embedding.** Hash chunk content; re-embed only changed chunks. Saves cost and avoids unnecessary index churn.
- **Soft delete then sweep.** When a source goes away, mark its chunks deleted; sweep on a schedule. Sudden hard deletes break in-flight queries.
- **Track ingestion lag** as a metric. "Last ingested" per source; alert when any source goes stale.

## Common failure modes

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Retrieves the right doc but wrong section | Chunks too big — whole-doc signal beats section-specific | Smaller chunks; structural splitting |
| Misses exact identifiers (error codes, API names) | Pure semantic search | Add BM25 / hybrid |
| Right docs retrieved, wrong answer | Generation prompt or token budget for context | Verify chunks reach the model; tighten citation discipline |
| Confident citations to non-existent chunks | No citation validation step | Validate against retrieved set before returning |
| Latency p95 spikes intermittently | Vector store cold cache or noisy neighbor | Warm the index; isolate the vector store |
| Cost spike with no traffic change | Cache miss on retrieved context (chunks change every call, prefix invalidated upstream) | Move retrieved chunks after the cache breakpoint |

## Behavior when invoked

1. Confirm RAG is the right tool given the corpus shape and access pattern.
2. Pick a chunking strategy that matches the corpus structure.
3. Set up ingestion with content-hash-based dedup and metadata capture.
4. Wire hybrid retrieval (semantic + BM25) with a re-ranker hook.
5. Build the prompt assembly with numbered chunks, citation instructions, and the cache breakpoint correctly placed.
6. Add a citation validation step before responses go to the user.
7. Scaffold a retrieval eval set with ~50 queries and labeled relevant chunks.

## What this skill will NOT do

- Use RAG when structured data and a tool call would do.
- Ship without citation validation. Confident-and-wrong citations destroy trust faster than refusals.
- Use pure semantic search alone for corpora with proper nouns, codes, or identifiers.
- Skip retrieval evals because generation looks good in spot checks.
- Stuff retrieved chunks into the cached system prompt — that blows the cache on every call.
- Re-embed the whole corpus on every ingestion. Content-hash diff first.

## Templates

- `templates/chunker.py` — recursive markdown chunker that splits on headers, preserves the section path in metadata, and emits stable content-hash IDs.
- `templates/retriever.py` — hybrid retrieval interface (semantic + BM25 via RRF) with re-ranker hook and metadata filtering.
- `templates/prompt_assembly.py` — builds the retrieval block with numbered, header-tagged chunks; validates citations against the retrieved set; places chunks after the cache breakpoint.
