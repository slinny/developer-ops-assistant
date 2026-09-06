# Failure injection results

Step 9: 65 tests passed locally with deterministic fake providers, mocked HTTP
transport and temporary file-backed SQLite. No live-provider faults were induced.

| Injected condition | Verified outcome |
| --- | --- |
| Transient timeout, connection failure, 429, selected 5xx | Recovery on next attempt; one atomic task/digest |
| Persistent transient failure | Three-attempt cap, failed event, no task/digest |
| Attempt timeout | Coroutine cancelled on each of three attempts |
| Deadline during extraction or digest | Controlled deadline failure; no task/digest |
| Retry-After longer than budget | Retry deadline expires before another attempt |
| Authentication, authorization, invalid request | Not retried |
| Malformed JSON, wrong schema, blank/extra fields | Not retried; no task/digest |
| Capacity wait exhausted | Durable capacity failure; permits reusable afterward |
| Request budget exhausted during digest | Neither task nor digest committed |
| Database error inserting digest | Earlier task upsert rolled back; failed event recorded |
| Five concurrent duplicate deliveries | One event processor, two provider calls, one task/digest |
| Four concurrent unique deliveries for one item | Four digests, one task, distinct correlated contexts |
| Same delivery ID with different content | HTTP 409, no additional provider calls |
| Older source timestamp | Does not overwrite newer task state |
| Failed delivery repeated | Existing failure returned; no repeated provider spending |
| Unexpected provider exception with sensitive message | Safe internal category, no message disclosure |

The wire-contract test uses the real OpenAI SDK with `httpx.MockTransport`, verifies
strict JSON Schema request construction and response usage extraction. It does not
prove account/model availability or provider-side schema acceptance.

Remaining limits: hard process kills, disk failure during failure recording,
ambiguous commit acknowledgment, distributed limits, durable queue/redelivery and
real-provider measurements need separate validation. Phase 3 owns worker crash
recovery; this milestone must not claim it.
