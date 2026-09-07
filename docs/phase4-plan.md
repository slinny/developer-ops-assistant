# Phase 4 execution plan

1. Contracts: strict schemas, scoped tool policies and milestone acceptance criteria.
2. Read tools: repository knowledge, paginated DB tasks and bounded GitHub PR evidence.
3. Runtime: handwritten selection/execution loop, persistent state and citation validation.
4. Limits: steps, conservative token/cost reservations, deadlines and duplicate detection.
5. Jobs: existing durable queue, fenced checkpoints, recovery and cancellation.
6. Workflows: authenticated synchronous and queued read-only investigations.
7. Writes: explicitly authorized, idempotent local task creation with audit provenance.
8. Evals: deterministic milestone, tool selection and adversarial/failure regression cases.
9. Review: full tests, lint, typing, fixes and documented operating limits.

Each numbered implementation stage is committed separately. LangGraph comparison is
optional and deferred until there is measured benefit over this baseline.

Acceptance: a controlled caching-architecture scenario retrieves RAG rationale,
GitHub PR evidence and open database tasks, then returns validated cited claims.
Live answer quality is separate from deterministic orchestration correctness.
