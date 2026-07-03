"""A2 · src/github/integration.py 的 GitHubClient 单测（此前 0 覆盖）。

不触网：monkeypatch GitHubClient._request（唯一的 HTTP 出口），验证各方法的**端点构造 + 载荷 + 解析**。
src/github 仍在用（/api/github/* 路由经 GitHubIntegration 调它），故补测而非退役。
"""
import pytest

from src.github import GitHubClient, Issue, IssueState


def _client_with_fake_request(monkeypatch, responses):
    """造一个 GitHubClient，把 _request 换成按 (method, endpoint) 返回预设的假实现，并记录调用。"""
    client = GitHubClient(token="t", repo="me/repo")
    calls = []

    async def fake_request(method, endpoint, data=None):
        calls.append({"method": method, "endpoint": endpoint, "data": data})
        for key, resp in responses.items():
            if key in endpoint:
                return resp
        return {}
    monkeypatch.setattr(client, "_request", fake_request)
    return client, calls


@pytest.mark.asyncio
async def test_get_issues_parses_and_builds_endpoint(monkeypatch):
    payload = [{"number": 1, "title": "bug", "body": "坏了", "state": "open",
                "labels": [{"name": "bug"}], "assignees": [{"login": "me"}],
                "html_url": "http://x/1", "user": {"login": "reporter"}}]
    client, calls = _client_with_fake_request(monkeypatch, {"/issues?": payload})
    issues = await client.get_issues(state=IssueState.OPEN, labels=["bug"], limit=10)
    assert len(issues) == 1 and isinstance(issues[0], Issue)
    assert issues[0].number == 1 and issues[0].labels == ["bug"] and issues[0].user == "reporter"
    ep = calls[0]["endpoint"]
    assert "/repos/me/repo/issues?" in ep and "state=open" in ep and "per_page=10" in ep and "labels=bug" in ep


@pytest.mark.asyncio
async def test_get_issue_single(monkeypatch):
    payload = {"number": 5, "title": "t", "body": "b", "state": "closed", "labels": [],
               "assignees": [], "html_url": "http://x/5", "user": {"login": "u"}}
    client, calls = _client_with_fake_request(monkeypatch, {"/issues/5": payload})
    issue = await client.get_issue(5)
    assert issue.number == 5 and issue.state == IssueState.CLOSED
    assert calls[0]["endpoint"] == "/repos/me/repo/issues/5"


@pytest.mark.asyncio
async def test_create_issue_comment_posts_body(monkeypatch):
    client, calls = _client_with_fake_request(monkeypatch, {"/comments": {"id": 1}})
    await client.create_issue_comment(3, "收到")
    assert calls[0]["method"] == "POST"
    assert calls[0]["endpoint"] == "/repos/me/repo/issues/3/comments"
    assert calls[0]["data"] == {"body": "收到"}


@pytest.mark.asyncio
async def test_update_issue_patches_state_and_labels(monkeypatch):
    client, calls = _client_with_fake_request(monkeypatch, {"/issues/2": {}})
    await client.update_issue(2, state=IssueState.CLOSED, labels=["done"])
    assert calls[0]["method"] == "PATCH"
    assert calls[0]["data"] == {"state": "closed", "labels": ["done"]}


@pytest.mark.asyncio
async def test_create_branch_success_and_failure(monkeypatch):
    client, _ = _client_with_fake_request(monkeypatch, {
        "/git/refs/heads/main": {"object": {"sha": "abc123"}}, "/git/refs": {}})
    assert await client.create_branch("vorto/x", "main") is True

    # base 分支查询抛错 → 优雅返回 False（不崩）
    client2 = GitHubClient(token="t", repo="me/repo")

    async def boom(*a, **k):
        raise Exception("404 not found")
    monkeypatch.setattr(client2, "_request", boom)
    assert await client2.create_branch("vorto/x", "nonexistent") is False


@pytest.mark.asyncio
async def test_request_raises_on_http_error(monkeypatch):
    """_request 遇到 >=400 应抛（上层据此降级）。用假 session 模拟 400。"""
    client = GitHubClient(token="t", repo="me/repo")

    class _Resp:
        status = 404

        async def text(self):
            return "not found"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def request(self, *a, **k):
            return _Resp()

    async def fake_session():
        return _Session()
    monkeypatch.setattr(client, "_get_session", fake_session)
    with pytest.raises(Exception, match="GitHub API error: 404"):
        await client._request("GET", "/repos/me/repo/issues/999")
