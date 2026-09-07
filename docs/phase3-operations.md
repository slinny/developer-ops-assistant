# Async event processing

## Start and inspect

Install `.[memory,dev]` as described in the README. Start the web server and one
worker process from the same directory with the same DOA_DATABASE_URL:

```sh
.venv/bin/uvicorn developer_ops.app:create_app --factory --host 127.0.0.1
# In another terminal:
.venv/bin/python -m developer_ops.worker
```

The web server requires webhook and API secrets, but no provider key. The worker
requires the provider key. `doa-worker` and `doa-queue` are also available after
refreshing the editable installation. One advisory process lock per database
ensures provider concurrency/rate limits are shared by the complete async pool.
This is a single-host Unix deployment using local SQLite, not a distributed queue.

Valid webhooks return **202** only after the event and job commit together. The
response includes `event_id`, `job_id`, `idempotency_key`, `status`, and `duplicate`.
A duplicate returns the same job, even if it failed. Conflicting delivery content
returns 409. Signature/payload checks remain synchronous. GET `/jobs/{job_id}` and
GET `/queue/metrics` require `Authorization: Bearer <DOA_API_TOKEN>`.

The job lifecycle is queued → running → succeeded. Retryable failures return to
queued with a durable available_at time. Exhausted and permanent failures become
failed (dead letters). The legacy event status completed corresponds to event-job
succeeded; downstream memory completion is separate.

## Retry, recovery and replay

The queue scheduler owns retries. Each worker provider operation has one bounded
attempt; the standalone LLMBoundary retains its retry behavior for other callers.
429, selected 5xx, connection errors, timeouts, temporary SQLite contention, and
local capacity/rate limits retry. Invalid structured output, authentication,
authorization, integrity failures and unknown internal errors fail closed.
Exponential backoff uses full jitter and respects Retry-After, with both attempt
and elapsed budgets. The elapsed budget starts at the first claim, not enqueue.

Each claim has a token and renewable lease. SIGTERM stops new claims and drains
active work within the processing deadline. SIGKILL leaves jobs reclaimable after
lease expiry. The current token and an unexpired lease are required for checkpoints,
publication, final effects and failure transitions. Exhausted crash recovery goes
to dead letters. A provider response lost before checkpointing may be requested
again and billed again; there is no exactly-once external API guarantee.

Validated extraction and summary outputs are checkpointed with immutable input,
model and processor-version fingerprints. Task update, digest, downstream enqueue
and event-job completion commit together. Equal source timestamps use delivery ID
lexical order as a deterministic tie-breaker, not a claim about GitHub chronology.
Usage from durable earlier attempts is aggregated. Calls interrupted before a
checkpoint may have unknown usage; consult provider billing for reconciliation.

```sh
.venv/bin/python -m developer_ops.queue_cli dead-letters
.venv/bin/python -m developer_ops.queue_cli replay JOB_ID
```

Replay keeps the same logical job and idempotency key, resets its retry budget,
preserves compatible checkpoints, and records a job_replays audit entry. Fix the
underlying issue before replay. Attempts and prior error records are retained.

## Memory publication

Memory is enabled by default; install the memory dependencies. Set
DOA_MEMORY_ENABLED=false for an intentional event/digest-only deployment.
The event worker atomically creates a memory job. A memory worker snapshots all
committed event digests and coalesces covered queued memory jobs. It builds local
LSA embeddings in a thread without blocking the event loop. Only a live fenced
owner may atomically replace CURRENT. A crash between rename and job commit safely
rebuilds an equal or newer snapshot. Failed builds leave the previous index usable.

`DOA_MEMORY_PATH` defaults to a **dedicated event digest index**. This does not
replace the CLI's separately collected docs/history corpus. Do not point both
writers at the same directory. Complete corpus retraining is appropriate for the
existing LSA model; it is not incremental embedding. Canceled builds may finish in
the background and leave unpublished generations, but cannot publish them.
Generation cleanup is manual while workers are stopped; retain CURRENT's generation.

## Capacity and measurements

DOA_WORKER_CONCURRENCY controls the pool; DOA_MAX_CONCURRENCY and
DOA_REQUESTS_PER_WINDOW cap provider calls across that process. The default job
lease is 30 seconds, renewed every third of the lease interval. Tune the lease
above expected local scheduling/DB stalls. No external calls hold write transactions.

DOA_QUEUE_MAX_PENDING bounds acceptance against queued/running jobs, including
memory jobs. Overflow returns 503 with Retry-After before persisting a new event;
duplicates remain readable. Downstream jobs for already accepted events are always
persisted, even if that temporarily exceeds the admission limit. Monitor disk usage
and retain a backup/retention policy: completed jobs, attempts, dead letters and
raw event payloads are not automatically deleted. This is an admission bound,
not a total database-size quota. Ensure rejected deliveries are redelivered by
an operator or upstream mechanism; no acknowledgment is sent for them.

Metrics include ready/delayed/running/dead-letter depth, oldest queued age,
last-60-second completion throughput, retries and failures by cause, lease recovery,
latency distributions, and pending memory work. Metrics read durable history and
are intended for this bounded local phase; large retained histories will require
SQL aggregation or an external metrics backend. IDs appear in logs, not metric
labels. Logs omit payloads, secrets and provider exception text.

Reproduce the offline burst/outage benchmark:

```sh
.venv/bin/python scripts/measure_queue.py
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy
```

See phase3-measurements.json for measured values. The provider is simulated, not a
live capacity or cost estimate. The benchmark deliberately accumulates 80 jobs,
redelivers 20, injects an initial 429 outage, and requires the backlog to drain with
80 unique digests, zero dead letters and bounded retries within 30 seconds.

## Migration and rollback

Stop the old inline server and back up the SQLite database before upgrading. Startup
runs an additive, versioned migration under a SQLite write lock. Completed events
receive succeeded jobs; received/processing events are queued for recovery; failed
events become inspectable dead letters requiring explicit replay. Migration is
repeatable and does not re-run successful historical provider calls. Historical
completed events do not automatically trigger memory rebuilds; the next memory
snapshot includes their digests.

Do not run the old inline server concurrently with the queue workers. To roll back,
stop all new processes and restore the pre-upgrade backup. An old binary cannot
safely operate new queued events. Accepted events after the backup would need to be
retained/exported and redelivered before discarding that newer database.
