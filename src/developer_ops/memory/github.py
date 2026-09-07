"""Read-only GitHub REST/GraphQL snapshot export with complete pagination."""

import re
from datetime import UTC, datetime
from typing import Any

import httpx

COMMENT = "id url body createdAt updatedAt author { login }"
PAGE = "pageInfo { hasNextPage endCursor }"
DISCUSSIONS = """query($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    discussions(first:100, after:$cursor) { nodes {
      id number title url body createdAt updatedAt author { login }
    } pageInfo { hasNextPage endCursor } }
  }
}"""
COMMENTS = "query($id:ID!, $cursor:String) { node(id:$id) { ... on Discussion { " + (
    "comments(first:100, after:$cursor) { nodes { " + COMMENT + " } " + PAGE + " } } } }"
)
REPLIES = "query($id:ID!, $cursor:String) { node(id:$id) { ... on DiscussionComment { " + (
    "replies(first:100, after:$cursor) { nodes { " + COMMENT + " } " + PAGE + " } } } }"
)


class GitHubCollector:
    """Caller owns the HTTP client; errors abort export, never silently truncate it."""

    def __init__(self, client: httpx.Client, repository: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Expected owner/repository")
        self.client = client
        self.repository = repository

    def rest(self, endpoint: str, **params: str) -> list[dict[str, Any]]:
        result = []
        for page in range(1, 10001):
            response = self.client.get(
                f"https://api.github.com/repos/{self.repository}/{endpoint}",
                params={**params, "page": page, "per_page": 100},
                timeout=30,
            )
            response.raise_for_status()
            result.extend(response.json())
            if "next" not in response.links:
                return result
        raise ValueError("REST pagination limit exceeded; export is incomplete")

    def graphql(self, query: str, variables: dict[str, Any]) -> Any:
        response = self.client.post(
            "https://api.github.com/graphql",
            json={"query": query, "variables": variables},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors") or not body.get("data"):
            raise ValueError("GraphQL query failed; export is incomplete")
        return body["data"]

    def pages(
        self,
        query: str,
        variables: dict[str, Any],
        route: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        result = []
        cursor = None
        seen = set()
        for _ in range(10000):
            connection = self.graphql(query, {**variables, "cursor": cursor})
            for name in route:
                connection = connection[name]
            result.extend(connection["nodes"])
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                return result
            cursor = page["endCursor"]
            if not cursor or cursor in seen:
                raise ValueError("GraphQL pagination did not advance")
            seen.add(cursor)
        raise ValueError("GraphQL pagination limit exceeded; export is incomplete")

    def export(self) -> dict[str, Any]:
        items = []
        for issue in self.rest("issues", state="all", sort="created", direction="asc"):
            is_pr = bool(issue.get("pull_request"))
            number = issue["number"]
            issue["source_type"] = "pr" if is_pr else "issue"
            issue["comments"] = self.rest(f"issues/{number}/comments")
            for comment in issue["comments"]:
                comment["id"] = f"issue-comment-{comment['id']}"
            if is_pr:
                reviews = self.rest(f"pulls/{number}/reviews")
                for review in reviews:
                    review["id"] = f"review-{review['id']}"
                    review["created_at"] = review.get("submitted_at") or ""
                    review["updated_at"] = review.get("submitted_at") or ""
                issue["reviews"] = reviews
                comments = self.rest(f"pulls/{number}/comments")
                for comment in comments:
                    comment["id"] = f"review-comment-{comment['id']}"
                issue["comments"].extend(comments)
            items.append(issue)
        owner, name = self.repository.split("/")
        variables = {"owner": owner, "name": name}
        enabled = self.graphql(
            "query($owner:String!, $name:String!) { repository(owner:$owner, name:$name) "
            "{ hasDiscussionsEnabled } }",
            variables,
        )["repository"]["hasDiscussionsEnabled"]
        if enabled:
            for discussion in self.pages(DISCUSSIONS, variables, ("repository", "discussions")):
                discussion["source_type"] = "discussion"
                comments = self.pages(COMMENTS, {"id": discussion["id"]}, ("node", "comments"))
                all_comments = []
                for comment in comments:
                    all_comments.append(comment)
                    replies = self.pages(REPLIES, {"id": comment["id"]}, ("node", "replies"))
                    for reply in replies:
                        reply["related_ids"] = [comment["url"]]
                    all_comments.extend(replies)
                discussion["comments"] = all_comments
                items.append(discussion)
        return {
            "repository": self.repository,
            "complete": True,
            "collected_at": datetime.now(UTC).isoformat(),
            "items": items,
        }
