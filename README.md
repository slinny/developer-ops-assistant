# Developer Ops Assistant

A small Python application that turns signed GitHub issue and pull request webhooks
into structured task updates and per-event digests. Webhooks now enqueue durable jobs; Phase 3 workers process them asynchronously.

## Local setup

Requires Python 3.11+.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[memory,dev]'
cp .env.example .env
# Set separate webhook/API secrets and your OpenAI key in .env.
.venv/bin/uvicorn developer_ops.app:create_app --factory --host 127.0.0.1
```

Configure GitHub to send issues and pull_request events to `/webhooks/github`,
using `DOA_WEBHOOK_SECRET`. Read up to 100 digests with `GET /digests` and
`Authorization: Bearer <DOA_API_TOKEN>`. The digest is an event summary, not a
scheduled cross-project rollup. No GitHub API writes occur.

```sh
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy
```

Tests use fake LLMs and temporary SQLite/Chroma stores; no credentials or network required.
The OpenAI adapter uses the Responses API with JSON Schema structured outputs:
[official API guide](https://developers.openai.com/api/docs/guides/structured-outputs).
Model selection is configurable. Pricing will remain unknown unless explicitly configured.

## Reliability milestone

The provider boundary adds cancellable deadlines, bounded retries with backoff and
jitter, process-local capacity/rate limits and per-attempt usage accounting. Task
updates and event digests commit atomically after strict validation. JSON logs
correlate requests and delivery IDs. Duplicate deliveries share one job; crash recovery may repeat uncheckpointed provider calls.

- [Architecture, configuration and failure semantics](docs/reliability.md)
- [Ingestion baseline](docs/ingestion.md)
- [Failure-injection results](docs/failure-injection.md)
- [Streaming decision](docs/streaming.md)
- [Final local measurements](docs/milestone-results.json)

Phase 3 adds a durable SQLite queue, a separate bounded worker pool, recoverable
leases, stage checkpoints, retry scheduling, dead letters, and asynchronous memory
snapshots. Follow the [worker and recovery guide](docs/phase3-operations.md).
The earlier Phase 1 reports above are historical inline-processing measurements.
[Phase 3 measurements](docs/phase3-measurements.json) use a simulated provider.

## Project memory / RAG

Phase 2 adds a separate local `doa-memory` CLI with GitHub/docs/history/digest
collection, Chroma, local LSA embeddings, vector and hybrid retrieval, reranking
experiments, cited excerpts and optional validated OpenAI synthesis.

Install `.venv/bin/pip install -e '.[memory,dev]'`, then follow the
[usage guide](docs/phase2-memory.md). The [execution report](docs/phase2-execution-report.md)
records 36 labeled queries, 54 retrieval configurations, held-out results and
known answer-quality limits. Live GitHub ingestion and generated-answer quality
remain unverified; the benchmark uses actual local Phase 1 history.
