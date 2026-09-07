import json

import httpx
import pytest

from developer_ops.memory.collect import github_export
from developer_ops.memory.github import GitHubCollector


def test_full_export_paginates_rest_and_discussion_replies(tmp_path):
    requests = []

    def connection(nodes, cursor=None):
        return dict(nodes=nodes, pageInfo=dict(hasNextPage=bool(cursor), endCursor=cursor))

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            path = request.url.path
            if path.endswith("/issues"):
                if request.url.params["page"] == "1":
                    return httpx.Response(
                        200,
                        headers={"Link": '<https://api.github.com/next>; rel="next"'},
                        json=[
                            dict(
                                number=1,
                                title="Change",
                                body="why",
                                pull_request={},
                                html_url="https://github.com/a/b/issues/1",
                            )
                        ],
                    )
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[])
        body = json.loads(request.content)
        query, variables = body["query"], body["variables"]
        if "hasDiscussionsEnabled" in query:
            data = dict(repository=dict(hasDiscussionsEnabled=True))
        elif "discussions(first" in query:
            data = dict(
                repository=dict(
                    discussions=connection(
                        [
                            dict(
                                id="d1",
                                number=3,
                                title="Decision",
                                body="Rationale",
                                url="https://github.com/a/b/discussions/3",
                            )
                        ]
                    )
                )
            )
        elif "comments(first" in query:
            data = dict(
                node=dict(
                    comments=connection(
                        [
                            dict(
                                id="c1",
                                body="Original comment",
                                url="https://github.com/a/b/discussions/3#c1",
                            )
                        ]
                    )
                )
            )
        else:
            if variables["cursor"] is None:
                data = dict(
                    node=dict(
                        replies=connection(
                            [
                                dict(
                                    id="r1",
                                    body="First reply",
                                    url="https://github.com/a/b/discussions/3#r1",
                                )
                            ],
                            "next",
                        )
                    )
                )
            else:
                data = dict(
                    node=dict(
                        replies=connection(
                            [
                                dict(
                                    id="r2",
                                    body="Second reply",
                                    url="https://github.com/a/b/discussions/3#r2",
                                )
                            ]
                        )
                    )
                )
        return httpx.Response(200, json=dict(data=data))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = GitHubCollector(client, "a/b").export()
    assert result["complete"]
    assert len(result["items"][1]["comments"]) == 3
    path = tmp_path / "export.json"
    path.write_text(json.dumps(result))
    docs = github_export(path, "a/b")
    assert any(d["text"] == "Second reply" for d in docs)
    assert all("mutation" not in r.content.decode() for r in requests)
    with pytest.raises(ValueError, match="repository"):
        github_export(path, "other/repo")


def test_graphql_errors_never_produce_complete_snapshot():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"errors": [{"message": "rate limited"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="incomplete"):
            GitHubCollector(client, "a/b").export()


def test_repeated_cursor_is_rejected():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "data": {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "same"}}
                },
            )
        )
    ) as client:
        with pytest.raises(ValueError, match="advance"):
            GitHubCollector(client, "a/b").pages("query", {}, ())
