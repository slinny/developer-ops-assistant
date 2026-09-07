# Phase 3 execution report

Implemented and validated locally on 2026-09-07. Each of the ten planned steps has
its own commit; an additional review-fix commit addresses issues found during validation.

## Delivered

- SQLite-backed durable jobs and attempts with additive legacy migration.
- Atomic webhook acceptance, 202 responses, stable job/idempotency IDs, authenticated status.
- One worker process with a bounded async pool, renewable leases and fenced writes.
- Validated, model/version-aware stage checkpoints and atomic task/digest/job completion.
- Coalesced, asynchronous event-digest memory builds with fenced CURRENT publication.
- Scheduler-owned retries, full-jitter backoff, Retry-After, attempt and elapsed budgets.
- Dead-letter inspection and explicit replay with durable replay/attempt history.
- Queue admission limits, correlated job logs and durable operational metrics.
- Migration, recovery and operating instructions in phase3-operations.md.

## Validation evidence

`pytest -q`: **129 passed**. `ruff check .`: passed. `mypy`: passed (30 source files).
`git diff --check`: passed.

Coverage includes concurrent duplicate acceptance, payload collisions, concurrent
claims, stale-owner rejection, retry scheduling, partial-stage checkpoint reuse,
model-change invalidation, replay history, legacy migration, provider 429s and real
async timeouts, database failure and commit acknowledgment loss, admission limits,
and memory coalescing/failure/publication-gap recovery.

Five tests start a separate worker process and send SIGKILL after a claim, before
a checkpoint, after a checkpoint, before the final database commit, and after that
commit. Recovery produces one digest and a succeeded job in every case. Committed
checkpoints avoid repeating their stage; a committed completed job avoids all new
provider work. Lease expiry is advanced after the kill to keep the suite fast.

The memory publication gap test injects failure immediately after the atomic rename,
then explicitly replays the failed job and verifies that the index is usable. It
is fault injection, not a memory-worker SIGKILL test. General worker SIGKILL tests
exercise actual event processing and SQLite persistence.

## Deliberate buildup and external failure

`scripts/measure_queue.py` submits 100 signed HTTP deliveries while workers are
paused: 80 unique jobs and 20 duplicates. It then starts four worker loops and
injects a 200 ms simulated 429 outage, with 10 ms fake provider operations.

Recorded in phase3-measurements.json:

- Peak ready depth: **80**.
- Acceptance latency p95: **2.82 ms**.
- Backlog drain time: **1.67 seconds**; drain throughput: **47.93 jobs/second**.
- Scheduled retries: **19**; provider calls: **179**.
- Processing latency p95, including queue wait: **1.70 seconds**.
- Final succeeded jobs and unique digests: **80**.
- Final ready/delayed/running/dead-letter counts: **0**.

The benchmark's acceptance checks were fixed before execution: no lost accepted jobs,
no duplicate digests, bounded retries, zero terminal failures after restored service,
and complete drain within 30 seconds. Memory work is disabled for this event-worker
throughput measurement; separate tests validate memory correctness. These figures
are simulated local measurements on Darwin arm64/Python 3.13.1, not live-provider
performance claims.

## Review fixes

Review identified and corrected missing cross-attempt usage aggregation, incomplete
replay auditing, process-local limit enforcement across worker processes, local
capacity errors being treated as permanent, elapsed retry budgets after downtime,
checkpoint compatibility after model changes, and worker logging initialization.
Startup schema creation is serialized. Status responses expose downstream memory
job IDs. The complete suite passed after these changes.

## Practical limits

The supported deployment is one Unix host, one worker process per SQLite database,
with an async pool. Provider calls can repeat after a crash before checkpointing.
The event memory index contains event digests and is separate from the existing
CLI docs/history index. Embedding is full LSA snapshot retraining and eventually
consistent. Canceled builds may leave unpublished generations for manual cleanup.
No live provider requests, deployment, or production database migration were run.

Three non-failing warnings remain: two existing test-client deprecations and a
scikit-learn explained-variance warning for the deliberately identical synthetic
memory documents. The published embeddings/index tests pass. Metrics and retained
job history are suitable for this local phase; large-scale retention and distributed
workers require additional design.
