# Phase 2 execution contract

Scope: local, single-owner project memory, separate from the webhook hot path.
Use the existing repository documentation and git history as the available real
corpus. GitHub history and Phase 1 digests can be imported when supplied; no remote
or populated database was present at execution start. Never fabricate history.

Ordered commits:
1. Scope and acceptance criteria (this document).
2. Source collection: GitHub export, docs, git history, persisted digests.
3. Content normalization and deduplication.
4. Validated metadata/provenance schema.
5. Bounded, section-aware chunks.
6. Local latent-semantic embeddings and persistent Chroma generations.
7. Vector retrieval and CLI.
8. Bounded context and extractive, cited answer generation.
9. At least 30 manually authored relevance-labeled queries with grouped splits.
10. Reproducible Recall@K, MRR, nDCG and latency baseline.
11. Chunk/overlap/K, hybrid BM25/RRF and reranking experiments.
12. Selection on development data, held-out validation and final review.

Acceptance: ingestion preserves source identities and relationships; rebuilding
removes stale chunks; unknown repositories fail closed; vector and lexical paths
search the same corpus; answers quote only retrieved evidence with source links;
no matching terms returns an explicit insufficient-evidence response. Test the
full pipeline using actual Chroma, not a fake vector store.

Evaluation target (provisional engineering threshold): document Recall@5 >= 0.8
and nDCG@5 >= 0.7 on development data; p95 retrieval < 250 ms locally. These are
not production guarantees. Report held-out results even if below target. Select
using development nDCG, recall, then latency. The small corpus and agent-authored
labels require maintainer review before any general quality claim.

Local LSA embeddings are a reproducible first model, not a pretrained semantic
model. Extractive answers provide verifiable historical passages, not untested
LLM synthesis. A neural embedding/reranker or generative provider requires its own
measured comparison. Data and indexes remain local; CLI access inherits filesystem
permissions. Do not expose this single-owner interface as a multi-user service.
