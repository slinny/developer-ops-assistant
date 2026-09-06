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
