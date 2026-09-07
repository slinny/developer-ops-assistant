# Phase 2 execution report

Implemented project memory as an independent local batch pipeline and CLI, with
per-step commits. The measured milestone retrieves the historical reason for
rejecting streaming and returns the original source passage with a citation.
Live GitHub data and OpenAI synthesis quality remain unverified: no repository
remote, historical export, populated database or API credential was configured.

## Execution and commit progression

| Step | Commit | Result |
| --- | --- | --- |
| 1 Scope | 4ada887 | Local scope, quality/latency targets and acceptance contract |
| 2 Collection | a3f2987 | Docs, Git history, GitHub export and Phase 1 digest collectors |
| 3 Normalization | 1c165db | Conservative cleaning, content hashes, latest-version deduplication |
| 4 Metadata | 0b410f8 | Validated document/chunk provenance and access scope |
| 5 Chunking | 7c24ac3 | Heading-aware bounded word windows and source locations |
| 6 Embeddings/storage | daa679a | Local LSA embeddings and persistent Chroma generations |
| 7 Retrieval | 7300dc4 | Repository-scoped vector search and CLI |
| 8 Context/answers | be296ec | Bounded cited extractive answers |
| 9 Labels | 5d137a6 | 36 queries and a frozen 20-document historical corpus |
| 10 Baseline | 4b196e3 | Recall, MRR, nDCG, evidence coverage and latency |
| 11 Experiments | 698c84d | 54 chunk/overlap/K/hybrid/reranking configurations |
| Completion | c4d548e | Paginated live GitHub collector, optional structured synthesis and tests |
| 12 Final validation | This report's commit | Selected CLI defaults, review fixes and measured milestone |

## Measured selection

Corpus: 20 actual Phase 1 docs/commit messages from revision `4dacba1`.
Final index: 48 chunks, 48-dimensional corpus-fitted TF-IDF/LSA vectors, Chroma
cosine search. No fake source corpus or paid provider calls were used.

There are 24 development queries (21 answerable) and 12 held-out queries (9
answerable). Three unanswerable queries are included in each split. Paraphrase
families remain within a split. Labels specify preferred primary documents and
supporting passages; they are agent-authored and incomplete, pending maintainer
adjudication. The corpus and labels are committed for inspection.

Search grid: 128/256/512 words, 0/20% overlap, K=3/5/10, vector-only/hybrid/hybrid
with sentence-coverage reranking. Hybrid fuses BM25 and vector ranks with RRF
constant 60, then returns K chunks from 20 candidates. The reranker is a transparent
lexical sentence-coverage heuristic, not a neural cross-encoder.

Best development result within each search family:

| Configuration | Recall@10 | MRR@10 | nDCG@10 | Retrieval p95 |
| --- | ---: | ---: | ---: | ---: |
| Vector, 128 words, no overlap | 1.000 | 0.635 | 0.727 | 0.946 ms |
| Hybrid, 128 words, no overlap | 1.000 | 0.640 | 0.730 | 1.982 ms |
| Hybrid + reranker, same chunking | 1.000 | 0.625 | 0.718 | 2.943 ms |

Selected **128 words, no overlap, K=10, hybrid, 20 candidates, no reranker**.
Selection uses development nDCG, then recall, then latency, with the predefined
quality and latency gates. The tiny hybrid gain is not evidence of statistical
significance; the faster vector configuration is a credible operational alternative.
Reranking was measured and rejected because its best result was worse and slower.

Held-out comparison:

| Configuration | Recall@K | MRR@K | nDCG@K |
| --- | ---: | ---: | ---: |
| Initial vector baseline: 256 words, 26 overlap, K=5 | 0.889 | 0.639 | 0.703 |
| Selected hybrid: 128 words, no overlap, K=10 | 1.000 | 0.704 | 0.777 |

This improvement includes changes to chunking and K, not just hybrid search.
K means retrieved chunks; document IDs are deduplicated before scoring. These
queries usually have one preferred labeled document, so Recall is effectively
preferred-document hit rate. It does not prove complete coverage of all evidence.

The final validation run did not retune the configuration. It measured selected
retrieval p50 **1.664 ms**, p95 **1.871 ms**; local extractive end-to-end p50
**2.088 ms**, p95 **2.356 ms**. Build took **96.0 ms**, index load **4.09 ms**.
Timings are warm, sequential, repeated local measurements on Python 3.13.1/macOS,
not server throughput or real model latency. Exact runs are in
`evaluation/baseline.json`, `experiments.json` and `final-validation.json`.

## Milestone evidence

Question: **Why did we decide against production streaming?**

The selected pipeline retrieves `docs/streaming.md`, whose saved source states:

> Streaming exposes bytes earlier, but this webhook consumer cannot use a partial
> task/digest. It adds buffering, cancellation and interrupted-stream handling to
> an already complete-response consumer.

The CLI returns this passage with its document ID, chunk ID, original source path
and timestamp. `scripts/validate_memory.py` asserts the rationale is present in
returned evidence and saves the complete cited result. Extractive mode returns
source evidence, not a synthesized causal explanation. `ask --generate` adds
structured synthesis and validated quote/citation references, with mock HTTP tests.

## Review findings and fixes

- Compare timezone-aware instants during source deduplication, not timestamp strings.
- Use PR source URLs for PR digests and human-facing GitHub URLs instead of API URLs.
- Preserve distinct comment/review ID namespaces and paginate discussion replies.
- Read docs from the requested Git revision so dirty working files cannot inherit
  misleading committed provenance.
- Keep neighboring rationale and multiple relevant chunks from one document in
  context. Sentence-only extraction and document-level deduplication could omit
  the reason while retaining only a decision statement.
- Allow an empty snapshot to remove the last active document.
- Avoid a quadratic identity matrix when fitting a one-document embedding model.
- Replace absolute editable-package paths in the lock file and constrain numerical
  dependencies to versions compatible with declared Python 3.11 support. Actual
  tests ran on Python 3.13.1; other Python/platform combinations were not exercised.
- Correct one held-out evidence substring annotation. Document relevance labels,
  ranking metrics and selected configuration were unchanged. Corrected held-out
  supporting-passage recall is **8/9**, reported in final validation.

## Validation

- **108 tests passed**: all 70 existing Phase 1 tests plus 38 memory tests.
- Actual persistent Chroma tests cover scoped vector/hybrid/reranked search,
  reloading, updates/deletion, empty snapshots and failed-build publication.
- Source tests cover cleaning, timezone order, code preservation, comments/replies,
  pagination errors, SQLite read-only collection and repository boundaries.
- Answer tests cover bounded context, retained rationale, quote membership,
  fabricated/invalid citations, abstention and provider timeout.
- Known-ranking metric tests and label/split validation pass.
- Ruff lint and formatting, strict mypy (23 source files), compileall, pip check,
  editable package install, CLI entry point and Git whitespace check pass.
- Two pre-existing Starlette/TestClient deprecation warnings remain.

## Limits that remain

**Answer quality is not fully validated.** Exact quotation provenance is 100%,
but only **1/3 held-out unanswerable queries abstained** in extractive mode. Related
passages are sometimes returned for unsupported premises. A citation check proves
where a quotation came from, not whether the evidence entails a generated claim.
Live generated-answer correctness, abstention, injection robustness, model latency
and cost require credentialed evaluation. Do not claim these are solved by the
mock transport tests or by structured output validation.

The actual benchmark contains docs and commit messages, not live issues, PR
threads or digests. Those collectors are implemented and fixture-tested, but a
configured remote and credentials/data are needed to verify them on real history.
GitHub full collection can race with changes during pagination and has no durable
incremental checkpoint scheduler. Index refresh is manual, not webhook-driven.

Inactive index generations retain deleted content on disk. Active-snapshot
replacement is not a retention/erasure system. The interface is local and
single-owner; repository filters are not multi-user ACL enforcement. There is no
production deployment, distributed ingestion or production performance claim.
