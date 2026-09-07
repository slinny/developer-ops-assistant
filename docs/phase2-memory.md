# Project memory: usage and measured configuration

The local CLI collects historical sources, cleans and chunks them, fits a local
LSA embedding model, stores vectors in Chroma, and returns cited historical
passages. Optional OpenAI synthesis produces structured cited claims. Phase 1
webhook processing remains independent; memory refresh is an explicit batch job.

## Install and run

```sh
.venv/bin/pip install -e '.[memory,dev]'
# Reproduce the checked-in historical benchmark.
.venv/bin/doa-memory index --corpus evaluation/corpus.jsonl
.venv/bin/doa-memory ask 'Why did we decide against production streaming?' \
  --repository developer-ops-assistant
```

Defaults implement the measured selection: **128 words, zero overlap, hybrid
BM25 + vector reciprocal-rank fusion, K=10, 20 candidates, no reranker**. Word
counts are not model-token counts. Use `--mode vector`, `--k`, `--candidates` and
`--rerank` to compare retrieval. `search` returns ranked chunks and raw scores;
`ask` returns source excerpts with numbered citations. Scores are not confidence.

Build and retrieval flags are separate. Rebuild after changing chunk settings;
changing `--k` or `--mode` needs no rebuild. `evaluation/selected.json` records
the selected values; CLI defaults match that selection. Running new experiments
writes a new selection artifact but does not automatically modify CLI defaults.

## Collect current project history

```sh
.venv/bin/doa-memory collect --root . --repository OWNER/REPO \
  --revision HEAD --output .memory/corpus.jsonl
.venv/bin/doa-memory index --corpus .memory/corpus.jsonl
```

Collection reads tracked Markdown and commit messages from the specified Git
revision, including file provenance. It excludes evaluation data and Phase 2
reports. It never scans `.env` or untracked files. Commit sources use `git:SHA`
references: inspect with `git show SHA`. Local document sources are absolute
paths; their saved corpus text/version is authoritative if the checkout changes.
Historical commit diffs and external documentation sites are not collected.

For a configured GitHub repository, supply `DOA_GITHUB_TOKEN` in the environment:

```sh
.venv/bin/doa-memory fetch-github --repository OWNER/REPO --output .memory/github.json
.venv/bin/doa-memory collect --repository OWNER/REPO \
  --github-export .memory/github.json --digests-db developer_ops.db
.venv/bin/doa-memory index
```

Omit `--digests-db` if there is no populated Phase 1 database. Digest collection
opens SQLite read-only and filters by repository. GitHub export collects all
issues/PRs, issue comments, PR reviews and inline comments, discussions and nested
replies. Every REST and GraphQL connection is paginated. Errors fail the export;
an incomplete response is never published with `complete=true`. Full collection
is not a transactionally consistent GitHub snapshot: changes during pagination
may require rerunning. Large repositories incur many requests; rate-limit errors
abort and require rerunning. There is no incremental cursor scheduler yet.

Collectors follow the official [GitHub discussion API](https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions)
and [PR comment API](https://docs.github.com/en/rest/pulls/comments).
A prebuilt JSON export must contain `complete: true`, an optional matching
`repository`, and `items` with `source_type` (`issue`, `pr`, `discussion`), number,
title, body, source URL, and fully expanded comments/reviews. Prefer the built-in
exporter, which namespaces comment IDs and preserves reply references.

## Optional generated answers

```sh
# Supply DOA_OPENAI_API_KEY in the environment; model defaults to DOA_MODEL
# or the existing project's gpt-4.1-mini setting.
.venv/bin/doa-memory ask 'Why did we make writes atomic?' \
  --repository OWNER/REPO --generate --model YOUR_MODEL
```

Generation sends the question and at most 1,200 words of selected source content
to the provider. This bound is words, not a strict provider token budget. It uses
[Responses structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
`store=false`, a 30-second deadline, zero SDK retries and 2,000 output tokens.
Every claim must reference a supplied source and provide an exact quote. Invalid
references, fabricated quotations, incomplete outputs and contradictory abstention
states fail validation. Provider usage is returned when available; cost remains
unknown. Prompt instructions distinguish source data from instructions, require
abstention for unsupported premises, and ask for conflicting dated evidence.
Semantic entailment and prompt-injection resistance are not guaranteed by schema
or substring checks. This adapter has mock transport tests, not live quality results.

The default extractive mode stays local and makes no synthesized rationale claims.
It returns full retrieved passages so nearby explanations and negations survive.
It can still return related evidence for an unanswerable question: **only 1/3
held-out unanswerable queries abstained**. Treat excerpts as evidence for review,
not an automatically verified answer. Generated mode is not claimed to fix this
without a live evaluation.

## Index lifecycle and access

A generation contains the cleaned corpus, citation metadata, JSON vocabulary,
NPZ projection/IDF arrays, a manifest and a Chroma collection. No pickle loading or
implicit model download occurs. Generation publication atomically replaces
`CURRENT` only after all components have been written. Failed builds preserve the
active index. A full rebuild replaces active retrieval scope, including removed
documents; an empty snapshot removes the last active document.

Inactive generations remain on disk for inspection and in-flight readers. This is
logical deletion from active retrieval, not secure erasure. For complete removal,
stop all readers and remove the entire local index directory before rebuilding.
Use one ingestion writer. Publication is atomic but not a disk-failure durability
or multi-process coordination guarantee. LSA retrains on the whole corpus each
build; it is a small-project baseline rather than an incremental neural model.

All content is local-owner visibility and repository filtering is mandatory.
Filesystem permissions protect this CLI; there is no multi-user authorization
service. Both lexical and vector ranking operate within the requested repository,
but a shared embedding model is trained across the whole supplied corpus. Use
separate index directories for distinct access domains. Keep `.memory` out of Git.

## Reproduce evaluation

```sh
.venv/bin/python scripts/evaluate_memory.py
.venv/bin/python scripts/experiment_memory.py
.venv/bin/python scripts/validate_memory.py
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pip check
```

Run the baseline before experiments. Experiment selection uses development data
only; the test set is a final comparison. `validate_memory.py` uses the saved
selection without retuning, verifies the streaming-rationale milestone, and records
retrieval/answer metrics, build/load time, platform and corpus/query hashes.
`requirements-memory.lock` records the tested Python 3.13.1 macOS environment;
normal dependency installation resolves packages for other supported platforms.
Read [the execution report](phase2-execution-report.md) for results and limits.
