# Phase 4 execution report

Implemented in staged commits: strict contracts, scoped read adapters, handwritten
stateful runtime, persistent budgets, durable investigation jobs, authenticated
read workflows, explicit local task writes, evaluation fixtures, and review fixes.
The optional LangGraph rebuild is deferred.

## Verified behavior

- The caching milestone uses actual local hybrid retrieval and SQLite, mocked
  GitHub responses and a scripted model. It returns architectural rationale,
  supporting PR evidence and an unresolved local task as three cited claims.
- The installed OpenAI SDK parses a strict decision through a mocked Responses
  endpoint. No paid model requests or live GitHub calls were made.
- Queue tests exercise claim, lease expiry/recovery, persisted reservations,
  cancellation and stale-worker rejection. Interrupted model reservations survive.
- Write tests exercise explicit confirmation, auth, audit payloads, concurrent
  identical retries, conflict rejection and visibility in task queries.
- Eight selection cases and an opt-in live grader are available. Offline grader
  tests use an oracle to verify grading mechanics, not model selection accuracy.

## Review findings fixed

1. Failed calls could repeat because only successful results were fingerprinted.
   Attempted normalized calls now participate in duplicate detection.
2. An expired deadline could enter the loop before cooperative cancellation ran.
   Explicit pre-dispatch deadline checks now stop without a model request.
3. Repository dot segments could normalize GitHub request paths. Those repository
   names are now rejected before any adapter executes.
4. Project knowledge incorrectly shared the event-only snapshot path. A dedicated
   `DOA_AGENT_MEMORY_PATH` now preserves the Phase 2 architecture/history index.
5. Unknown pricing appeared as zero reservation. It is now null; cost-limited
   requests fail before dispatch if pricing is unavailable.
6. PR upstream field validation and inline review evidence were incomplete. Both
   are covered, alongside bounded pagination and no arbitrary URL following.
7. Subprocess cancellation needed to drain pipes while reaping the child. A real
   noisy child-process timeout test now verifies cleanup.
8. Failure status/log rendering could claim success or remain running after an
   outer job failure. Job finalization and API output now distinguish these cases.
9. Type inference for heterogeneous schema classes failed strict mypy. Explicit
   registry typing resolves it.

## Limits of validation

Live credentials, repository access, production load, live model selection and
semantic entailment have not been validated. Citation membership and exact quotes
cannot prove every natural-language claim. Architectural rationale must exist in
the indexed corpus; task data can lag GitHub. Cost accounting is conservative and
configuration-based. Explicit task writes remain outside model-controlled runs.

The final machine-readable test result is in `phase4-validation.json`. The three
warnings also appeared in the baseline: two Starlette dependency deprecations and
an sklearn variance warning in an existing memory test. Ruff, mypy and git diff
whitespace checks pass.
