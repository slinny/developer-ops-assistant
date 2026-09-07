# Phase 4 developer agent

The adapter uses the [official structured-output interface](https://developers.openai.com/api/docs/guides/structured-outputs).
The handwritten agent loop selects one bounded tool per step, accumulates evidence,
and emits claims with validated source IDs and exact supporting quotations. The
caching milestone passes against a controlled fixture using real Chroma/LSA
retrieval and SQLite, mocked GitHub HTTP responses and a scripted model.
This proves orchestration, not live model selection or semantic answer quality.

## Setup

Install `.[memory,dev]` as described in the README. Configure these in `.env`:

```dotenv
DOA_AGENT_REPOSITORIES=["OWNER/REPO"]
DOA_AGENT_MEMORY_PATH=./.memory/index
DOA_AGENT_SYNC_CONCURRENCY=2
# Existing settings: DOA_API_TOKEN, DOA_OPENAI_API_KEY, DOA_MODEL, DOA_DATABASE_URL
# Optional for private repositories: DOA_GITHUB_TOKEN (read-only repository access)
```

Repository names must use owner/repository syntax. An empty allowlist disables
agent access. The API token identifies the single local owner; this is not a
multi-tenant authorization system. All tools inherit the request's fixed scope;
the model cannot pass a different repository or an arbitrary network URL.

Collect historical docs/commits and optional GitHub exports using the
[Phase 2 guide](phase2-memory.md), with the same OWNER/REPO used in requests:

```sh
.venv/bin/doa-memory collect --root . --repository OWNER/REPO \
  --revision HEAD --output .memory/corpus.jsonl
.venv/bin/doa-memory index --corpus .memory/corpus.jsonl
```

The agent knowledge index is separate from Phase 3's event-only
`DOA_MEMORY_PATH`. Rebuild project knowledge explicitly when its source history
changes. Task queries use the current local task database; PR reads use GitHub.
Task ingestion can lag GitHub and is labeled as a snapshot.

Start the API and the existing worker in separate terminals:

```sh
.venv/bin/uvicorn developer_ops.app:create_app --factory --host 127.0.0.1
.venv/bin/doa-worker
```

## Read-only workflows

`POST /agent/investigations` queues a durable `investigate` job (HTTP 202):

```json
{
  "repository": "OWNER/REPO",
  "question": "Why did we change the caching architecture, and are there unresolved tasks?",
  "limits": {
    "max_steps": 12,
    "max_tokens": 100000,
    "timeout_seconds": 180,
    "tool_timeout_seconds": 20,
    "output_tokens": 1500
  }
}
```

Every agent endpoint requires `Authorization: Bearer <DOA_API_TOKEN>`.
Poll `GET /agent/investigations/{job_id}` for job status, partial evidence,
validated claims, answer, limitations and usage counters. Cancel with
`DELETE /agent/investigations/{job_id}`. Cancellation invalidates the worker lease
immediately; active read/model work stops on its next checkpoint, heartbeat or
deadline. A provider may still bill an already dispatched request.

`POST /agent/run` accepts the same body for synchronous execution, requiring
`limits.timeout_seconds <= 30`. It has a separate bounded admission count and
returns 429 when full. The default 180-second request belongs on the queue.
Long jobs share the existing worker concurrency and queue capacity with ingestion.
Use synchronous runs for small investigations and the queue for the milestone.

## Tool contracts and policies

Schemas are in `developer_ops.agent.schema`; all reject unknown fields, type
coercion and nonfinite numbers. Result schemas are validated again at the loop
boundary, including fake/custom adapters.

| Tool | Arguments | Execution and result |
| --- | --- | --- |
| `search_project_knowledge` | query, limit 1–10 | Synchronous hybrid search in a killable subprocess; cited document excerpts |
| `query_tasks` | state open/closed/all, query, offset, limit 1–50 | Synchronous parameterized DB read; `next_page` is next offset |
| `get_pull_request` | number, page 1–100 | Synchronous bounded GitHub reads; 10 records per page for comments, reviews, inline comments and files; `next_page` is next page |
| `create_task` | title, description, idempotency_key | Synchronous explicit write endpoint; local task and audit event in one transaction |
| `investigate` | repository, question, limits | Queued orchestration; not a recursive model tool |

PR bodies and patches are bounded excerpts, not guaranteed complete diffs.
Responses over 1 MB are rejected; redirects and arbitrary pagination URLs are
never followed. Missing sources, HTTP errors and invalid outputs become structured
history errors. Tools are not automatically retried. A repeated normalized call,
including a previously failed one, stops the run. Changing arguments can recover
from a bad query within the remaining step budget.

## Limits and recovery

The loop checks steps, deadline, token reservations and optional cost reservations
before each model dispatch. Input reservation uses serialized ASCII JSON bytes,
schema/instructions and framing allowance as a conservative byte-BPE token bound,
plus the enforced output-token cap. This intentionally stops earlier than an
actual-token budget. Reported `actual_tokens` covers observed successful responses;
interrupted/invalid provider responses may have unknown actual usage.

For a cost limit, configure `DOA_AGENT_USD_PER_MILLION_UPPER_BOUND` to at least the
maximum input/output rate for the configured model and provide `max_cost_usd` in
the request. No pricing is guessed. Missing pricing stops cost-limited requests
before dispatch; `reserved_cost_usd` is null when pricing is unknown. The amount
is a conservative reservation estimate, not an invoice or an account-wide cap.
Reverify rates when changing the model. Hidden SDK retries are disabled.

Queued runs checkpoint reservations before model dispatch and state after tool
execution. Crash recovery keeps the original deadline, step count, reservations
and completed evidence. An uncheckpointed read may repeat, but its interrupted
model call retains the full reservation. Saves and finalization require the live
lease. Queue claim/recovery and cancellation use the existing SQLite transaction
fences. Explicit queue replay never resets agent budgets or terminal state; submit
a new investigation when a fresh budget is intended.

Stopping due to limits, duplication, invalid citations or model failure returns
partial evidence with a terminal reason. Citation membership and exact quotation
are checked locally; semantic entailment and completeness still require evaluation.
The current agent refuses writes even if a prompt or retrieved text requests them.

## First write action

Use `POST /agent/tasks` after explicitly deciding to create a local task:

```json
{
  "repository": "OWNER/REPO",
  "confirmed": true,
  "arguments": {
    "title": "Add cache invalidation monitoring",
    "description": "Track stale reads after the architecture change.",
    "idempotency_key": "cache-monitoring-v1"
  },
  "investigation_id": null
}
```

An optional investigation ID attaches repository-checked provenance. The exact
request is stored in an audit event referenced by the task. Confirmation must be
a boolean true. The same scoped key and identical request returns the same task,
including concurrent retries; different content returns HTTP 409. Created tasks
appear in `query_tasks` as open local tasks, with `local-task:<id>` references.
This endpoint does not create a GitHub issue. Model-controlled writes are deferred;
the first write remains a concrete explicit request.

## Validation and evaluation

```sh
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy
# Optional paid live selection evaluation:
.venv/bin/python -m developer_ops.agent.evaluate --live
```

`evaluation/agent-cases.json` defines eight selection cases: architectural history,
open/closed tasks, task filtering, PR lookup, write/scope boundaries and the
milestone's first step. The grader accepts schema defaults and checks expected
argument subsets. The live command exits nonzero on failure and reports each
case; it does not invoke real tools. Its successful execution is not claimed here.

The offline milestone uses a controlled caching ADR, PR #42 and open task #43;
it checks three source types and three cited claims. Regression tests cover scope,
strict validation, pagination, malformed results, duplicate success/failure calls,
limits, deadline expiry, interrupted reservations, SDK parsing, queue recovery,
cancellation, API authentication, and concurrent idempotent writes.

LangGraph is deliberately deferred. The adapter, tool contracts and state are
separate so a future comparison can run the same evals against both orchestrators.
