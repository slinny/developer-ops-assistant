# Phase 3 execution contract

1. Durable SQLite queue on one host; event acceptance and enqueue share a transaction.
2. Versioned additive migration, jobs, attempts and explicit legacy reconciliation.
3. Webhooks return 202 after durable acceptance; authenticated status lookup.
4. Bounded worker pool, atomic claims, renewable leases and fenced writes.
5. Checkpoint validated stages; commit business effects and completion atomically.
6. Durable coalesced memory snapshot work with serialized publication.
7. Scheduler-owned bounded retries, persisted backoff and audited dead-letter replay.
8. Durable queue metrics and capacity limits.
9. Duplicate, crash, stale-owner, partial failure and provider failure tests.
10. Backlog benchmark, regression checks and operational report.

Execution is at least once. External provider calls may repeat after a crash before
checkpointing; committed business effects must not repeat. SQLite and local Chroma
limit this phase to one host. Memory is eventually consistent. No transaction is
held across external calls. Job success describes its own stage, not downstream
memory freshness. Retain existing provider-boundary tests as unit regressions.

Benchmark acceptance: zero lost acknowledged jobs, zero duplicate digests, bounded
attempts, all accepted jobs terminal after recovery, and backlog drains under restored
capacity. Timings are reported, not presented as production capacity guarantees.
