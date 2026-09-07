# Phase 2 evaluation data

36 agent-authored queries: 30 answerable, 6 unanswerable; 24 development and 12
held-out test queries. Related paraphrases share a group and split. Labels were
written against the frozen Phase 1 corpus before retrieval experiments. Each
answerable query identifies a preferred primary document (grade 2) and a short
support passage. Unlisted documents are unjudged and score zero; this is a known
incompleteness, not proof that alternative evidence is irrelevant. Maintainer
adjudication is still required. No LLM judge or synthetic source documents used.

`corpus.jsonl` contains 20 actual documents/commit messages collected at Phase 1
revision 4dacba1, before Phase 2 documentation was added. Do not ingest evaluation
queries, reports or selected settings into the corpus. The support substring is
an annotation for inspection, never supplied to retrieval or answer generation.

Metrics deduplicate chunk hits by document in first-seen order, then evaluate the
first K unique documents returned by K chunk retrieval. Recall denominator is all
labeled relevant documents. MRR uses the first relevant result; nDCG uses graded
2^relevance-1 gains and log2 discounts. Unanswerable queries are excluded from
retrieval metrics and separately scored for answer abstention. Results are local
small-corpus diagnostics, not a broad model benchmark.
