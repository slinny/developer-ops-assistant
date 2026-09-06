# Local milestone measurements

| Zero-delay fake provider, 30 sequential signed events | Baseline | Step 10 |
| --- | --- | --- |
| Completed | 30/30 | 30/30 |
| Provider calls | 60 | 60 |
| p50 application latency | 6.310 ms | 5.258 ms |
| p95 application latency | 8.674 ms | 12.252 ms |
| Retries in happy-path run | 0 | 0 |
| Token usage / estimated dollars | Unknown | Unknown |

The small timing difference is not evidence of a performance improvement or
regression: runs are local, sequential and unreplicated; code and transaction
boundaries changed. This is an overhead sanity check, not a load test or SLO.
Each final sample includes request/event IDs, latency, retries and persisted usage.

Retry overhead is bounded and verified deterministically: with maximum jitter and
a 0.75-second Retry-After fixture, the first two scheduled waits are 0.75 and 1.0
seconds. Tests replace the sleeper to verify scheduling without that wall time.
A Retry-After of 100 seconds with a 10ms test budget never reaches a second attempt.
Real cancellation tests use 5–20ms test deadlines and synchronization primitives.

Pricing tests use explicitly fictional fixture rates (input $1/M, cached $0.50/M,
output $2/M). 100 input tokens (10 cached) plus 20 output tokens yields $0.000135.
A preceding unknown-usage retry makes the full event cost unknown; that known
amount remains only a partial subtotal. These rates are not provider prices.

Step 10 verification: 65 tests passed; Ruff and strict mypy passed. Two deprecation
warnings originate in the installed Starlette TestClient compatibility layer.
`requirements-dev.lock` records the tested package versions on Python 3.13.1.
No live API, cloud deployment, load test or model-quality evaluation was performed.
