"""Conservative cleaning: preserve quotes, code and contradictory evidence."""

import hashlib
import html
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

NORMALIZATION_VERSION = "clean-v1"


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    # Do not strip HTML inside fenced code examples.
    parts = re.split(r"(```[\s\S]*?```|~~~[\s\S]*?~~~)", text)
    for index in range(0, len(parts), 2):
        part = re.sub(r"<!--[\s\S]*?-->", "", parts[index])
        parts[index] = html.unescape(part)
    text = "".join(parts)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(x.rstrip() for x in text.splitlines())).strip()


def normalize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Latest version per source ID; never merge independent corroborating sources."""
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        row = dict(record)
        row["text"] = clean(row["text"])
        row["content_hash"] = hashlib.sha256(row["text"].encode()).hexdigest()
        row["normalization_version"] = NORMALIZATION_VERSION
        old = latest.get(row["id"])

        def instant(value: str) -> datetime:
            if not value:
                return datetime.min.replace(tzinfo=UTC)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Timestamps require timezone")
            return parsed.astimezone(UTC)

        if old and instant(old.get("updated_at", "")) > instant(row.get("updated_at", "")):
            continue
        if old and instant(old.get("updated_at", "")) == instant(row.get("updated_at", "")):
            if old["content_hash"] != row["content_hash"]:
                raise ValueError(f"Conflicting versions at same timestamp: {row['id']}")
        latest[row["id"]] = row
    return [row for _, row in sorted(latest.items()) if row["text"]]
