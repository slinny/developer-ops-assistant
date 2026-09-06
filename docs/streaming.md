# Streaming experiment

Production remains non-streaming. `scripts/streaming_experiment.py` is an isolated
simulated transport comparison with no database or provider access. Both paths
receive identical JSON in eight-character chunks at two-millisecond intervals.
The streaming path observes the first chunk; both paths wait for the complete
JSON and Pydantic validation before producing useful structured output.

Run `.venv/bin/python scripts/streaming_experiment.py`; the recorded local run is
in `streaming-results.json`. These are simulated timings, not OpenAI measurements.
No credentials were needed or live requests made. Tests verify complete validation
and rejection of a truncated stream.

Streaming exposes bytes earlier, but this webhook consumer cannot use a partial
task/digest. It adds buffering, cancellation and interrupted-stream handling to
an already complete-response consumer. Structured validation still happens at the
end. The authenticated digest endpoint reads persisted complete summaries.

Decision: no production streaming path. Revisit for a future user-facing digest
preview if users can benefit from provisional text. A real-provider A/B test is
still needed before making claims about provider latency or streaming overhead.
