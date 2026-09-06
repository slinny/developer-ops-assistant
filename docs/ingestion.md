# Ingestion contract (Step 1)

`app.create_app.webhook` verifies HMAC SHA-256 over the original request bytes,
validates delivery ID and `WebhookPayload`, then calls `Processor.ingest`.
Only issues and pull_request events are processed; ping is acknowledged.

`Processor.ingest` inserts `Event` in its own transaction. Its delivery-ID primary
key is the concurrency-safe uniqueness boundary. The persisted payload contains
selected raw event fields (action, repository, item); unused GitHub fields and
headers are deliberately omitted. Duplicates return the durable event status
without additional LLM calls or writes.

`Processor.process` records `processing`, constructs bounded source context,
calls `LLMProvider.extract_task_update`, validates `TaskUpdate`, updates `Task`,
then calls `LLMProvider.generate_digest`, validates `DigestOutput`, and persists
`Digest` plus `completed`. `OpenAIProvider` alone knows the OpenAI SDK/API.
`Database` supplies async SQLAlchemy sessions over SQLite.

- Successful event: durable `completed`, a structured source task update, and a
  validated digest keyed by delivery ID. Webhook response is HTTP 200.
- Valid task update: Pydantic-validated summary and category; source repository,
  item number and state come from the signed webhook, never model instructions.
- Successful digest: validated event summary persisted with its event ID, readable
  from the authenticated `/digests` endpoint. This is not a scheduled rollup.
- Failed event: `failed` with a safe error category and HTTP 503. Invalid input is
  rejected before persistence/LLM use (401 or 422). Database unavailability can
  prevent even a failure record; it must remain an unsuccessful HTTP response.

States: `received → processing → completed | failed`. Failed duplicates are not
silently retried. Crash recovery, replay and queues are future work.

## Baseline gaps to close in subsequent steps

No application retry, deadline, capacity limit, usage accounting or structured
logs yet. Task persistence precedes digest generation, so digest failure can leave
a task update. Step 7 will close this transaction boundary. Process crashes can
leave nonterminal states; Phase 1 does not claim worker recovery.

## Baseline measurement

Run `.venv/bin/python scripts/measure.py`. `baseline.json` records 30 sequential
signed issue/PR requests using a zero-delay fake provider and temporary file-backed
SQLite. All 30 completed, with two calls each, no retries, and unknown usage/cost.
Latencies measure local application overhead only; no real-provider quality,
throughput or pricing claim follows from these results.
