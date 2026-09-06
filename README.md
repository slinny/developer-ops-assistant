# Developer Ops Assistant

A small Python application that turns signed GitHub issue and pull request webhooks
into structured task updates and per-event digests. Processing is inline in Phase 1.

## Local setup

Requires Python 3.11+.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
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

Tests use a fake LLM and temporary SQLite databases; no credentials or network required.
The OpenAI adapter uses the Responses API with JSON Schema structured outputs:
[official API guide](https://developers.openai.com/api/docs/guides/structured-outputs).
Model selection is configurable. Pricing will remain unknown unless explicitly configured.

## Reliability milestone

The provider boundary adds cancellable deadlines, bounded retries with backoff and
jitter, process-local capacity/rate limits and per-attempt usage accounting. Task
updates and event digests commit atomically after strict validation. JSON logs
correlate requests and delivery IDs. Duplicate deliveries cannot repeat LLM work.

- [Architecture, configuration and failure semantics](docs/reliability.md)
- [Ingestion baseline](docs/ingestion.md)
- [Failure-injection results](docs/failure-injection.md)
- [Streaming decision](docs/streaming.md)
- [Final local measurements](docs/milestone-results.json)

Use one worker and `--limit-concurrency 32` for the Phase 1 server. This is a local,
inline processor; queues, crash recovery, replay, RAG and deployment remain future
phases. All recorded timings use simulated providers, not live OpenAI requests.
