"""Read-only collectors. GitHub input is a complete JSON export, not a webhook."""

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, encoding="utf-8"
    ).strip()


def documents(root: Path, repository: str, revision: str = "HEAD") -> list[dict[str, Any]]:
    """Only tracked Markdown; never traverse secrets or generated evaluation files."""
    result = []
    for name in git(root, "ls-tree", "-r", "--name-only", "-z", revision).split("\0"):
        if not name.endswith(".md") or name.startswith(("evaluation/", "docs/phase2")):
            continue
        path = root / name
        if path.is_symlink():
            continue
        result.append(
            dict(
                id=f"{repository}:doc:{name}",
                repository=repository,
                source_type="doc",
                title=name,
                url=str(path.resolve()),
                text=git(root, "show", f"{revision}:{name}"),
                updated_at=git(root, "log", "-1", "--format=%cI", revision, "--", name),
                version=git(root, "log", "-1", "--format=%H", revision, "--", name),
            )
        )
    return result


def history(root: Path, repository: str, revision: str = "HEAD") -> list[dict[str, Any]]:
    result = []
    for sha in git(root, "rev-list", revision).splitlines():
        title, author, timestamp, body = git(
            root, "show", "-s", "--format=%s%n%an%n%cI%n%B", sha
        ).split("\n", 3)
        result.append(
            dict(
                id=f"{repository}:commit:{sha}",
                repository=repository,
                source_type="commit",
                title=title,
                author=author,
                created_at=timestamp,
                updated_at=timestamp,
                version=sha,
                url=f"git:{sha}",
                text=body,
            )
        )
    return result


def github_export(path: Path, repository: str) -> list[dict[str, Any]]:
    """Array of issue/pr/discussion objects; comments/reviews must be fully expanded.

    Accepts gh-style camelCase or REST-style snake_case fields. Export metadata
    must explicitly attest pagination completeness, so partial exports fail closed.
    """
    export = json.loads(path.read_text())
    if export.get("complete") is not True:
        raise ValueError("GitHub export must attest complete=true (including comment pagination)")
    if export.get("repository", repository) != repository:
        raise ValueError("Export repository does not match requested repository")
    result = []
    for item in export["items"]:
        kind = item["source_type"]
        if kind not in {"issue", "pr", "discussion"}:
            raise ValueError("Unsupported GitHub source type")
        identity = f"{repository}:{kind}:{item['number']}"
        url = item.get("html_url") or item["url"]
        base = dict(
            repository=repository,
            source_type=kind,
            title=item["title"],
            created_at=item.get("createdAt", item.get("created_at", "")),
            updated_at=item.get("updatedAt", item.get("updated_at", "")),
            author=(item.get("author") or item.get("user") or {}).get("login", ""),
            labels=[x["name"] if isinstance(x, dict) else x for x in item.get("labels", [])],
            state=item.get("state", ""),
            related_ids=item.get("related_ids", []),
        )
        result.append(dict(base, id=identity, url=url, text=item.get("body") or ""))
        # Each comment retains its own anchor and parent; replies may be flattened
        # by the exporter, with their parent_id retained in related_ids.
        for comment in item.get("comments", []) + item.get("reviews", []):
            anchor = comment.get("html_url") or comment.get("url")
            if not anchor:
                raise ValueError("Every comment/review requires a source URL")
            result.append(
                dict(
                    base,
                    id=f"{identity}:comment:{comment['id']}",
                    parent_id=identity,
                    related_ids=comment.get("related_ids", []),
                    url=anchor,
                    text=comment.get("body") or "",
                    author=(comment.get("author") or comment.get("user") or {}).get("login", ""),
                    created_at=comment.get("createdAt", comment.get("created_at", "")),
                    updated_at=comment.get("updatedAt", comment.get("updated_at", "")),
                )
            )
    return result


def digests(path: Path, repository: str) -> list[dict[str, Any]]:
    """Read existing Phase 1 SQLite data without creating or mutating a database."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT d.event_id, d.summary, e.payload FROM digests d "
            "JOIN events e ON e.id = d.event_id WHERE e.status = 'completed'"
        ).fetchall()
    finally:
        connection.close()
    result = []
    for event_id, summary, raw in rows:
        payload = json.loads(raw)
        repo = payload["repository"]["full_name"]
        if repo != repository:
            continue
        item = payload.get("issue") or payload.get("pull_request")
        kind = "issues" if payload.get("issue") else "pull"
        url = item.get("html_url") or f"https://github.com/{repo}/{kind}/{item['number']}"
        result.append(
            dict(
                id=f"{repository}:digest:{event_id}",
                repository=repository,
                source_type="digest",
                title=f"Event digest {event_id}",
                text=summary,
                url=url,
                updated_at=item["updated_at"],
                related_ids=[url],
            )
        )
    return result


def write_snapshot(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records))
