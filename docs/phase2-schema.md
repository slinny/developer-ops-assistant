# Memory schema v1

`memory.schema.Document` is the canonical validated record. Source IDs are stable
within a repository and type. SHA-256 hashes identify cleaned content. Source
version, original timestamps, author, labels, parent and related IDs remain
available in each corpus snapshot. Empty timestamps mean unknown, not ingestion
time. All nonempty timestamps must include a timezone.

`Chunk` preserves document identity, section heading, position, source URL and
start line. Chunk IDs include the content hash and chunk configuration. Size is
measured in whitespace-delimited words, explicitly not provider tokens.

All data is local-owner visibility. Repository is a required retrieval filter;
it is not a substitute for multi-user authorization. Do not host the CLI as an
unauthenticated service. Index manifests record corpus hash, build timestamp,
embedding version and chunk configuration. A new full snapshot replaces the
active generation; omit deleted documents to remove them from active retrieval.
