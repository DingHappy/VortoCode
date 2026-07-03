"""制品（artifacts）能力的单测：存储库 + 工具 + Web 路由 + 鉴权透传。

复刻 Claude Code artifact 的本地版：发布自包含 HTML → 可分享/实时更新的网页。
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from src.web.artifacts import (
    ArtifactStore, artifact_url, build_artifact_tools, render_markdown_doc, max_bytes,
)


# --------------------------------------------------------------- ArtifactStore
def test_publish_creates_files_and_meta(tmp_path):
    store = ArtifactStore(str(tmp_path))
    meta = store.publish("My Page", "<h1>hi</h1>")
    d = tmp_path / ".vortocode" / "artifacts" / meta["id"]
    assert (d / "index.html").read_text(encoding="utf-8") == "<h1>hi</h1>"
    assert meta["version"] == 1 and meta["title"] == "My Page"
    assert meta["bytes"] == len("<h1>hi</h1>")
    assert meta["id"].startswith("my-page-")            # slug + 随机后缀（ASCII）
    assert store.html(meta["id"]) == "<h1>hi</h1>"


def test_publish_same_id_bumps_version_keeps_created(tmp_path):
    store = ArtifactStore(str(tmp_path))
    first = store.publish("Dash", "<p>v1</p>")
    second = store.publish("Dash", "<p>v2</p>", artifact_id=first["id"])
    assert second["id"] == first["id"]
    assert second["version"] == 2
    assert second["created_at"] == first["created_at"]  # created 保留
    assert store.html(first["id"]) == "<p>v2</p>"        # 内容原地更新


def test_cjk_title_yields_ascii_id(tmp_path):
    store = ArtifactStore(str(tmp_path))
    meta = store.publish("中文标题", "<i>x</i>")
    assert meta["id"].startswith("artifact-")           # 纯 CJK → slug 退化为 artifact
    assert meta["title"] == "中文标题"                   # 标题仍保留中文
    assert store.exists(meta["id"])


def test_safe_id_blocks_traversal(tmp_path):
    store = ArtifactStore(str(tmp_path))
    for bad in ["../evil", "a/b", "..", "x" * 200, ""]:
        assert store.meta(bad) is None
        assert store.html(bad) is None
        assert store.exists(bad) is False


def test_list_sorted_by_updated_desc(tmp_path):
    store = ArtifactStore(str(tmp_path))
    a = store.publish("A", "x")
    b = store.publish("B", "y")
    ids = [m["id"] for m in store.list()]
    assert set(ids) == {a["id"], b["id"]} and len(ids) == 2


def test_list_empty_when_no_dir(tmp_path):
    assert ArtifactStore(str(tmp_path)).list() == []


def test_artifact_url_env(monkeypatch):
    monkeypatch.delenv("VORTOCODE_WEB_BASE", raising=False)
    assert artifact_url(None, "abc") == "http://127.0.0.1:8080/artifact/abc"
    monkeypatch.setenv("VORTOCODE_WEB_BASE", "https://x.dev/")
    assert artifact_url(None, "abc") == "https://x.dev/artifact/abc"
    assert artifact_url("http://h:9/", "abc") == "http://h:9/artifact/abc"


# --------------------------------------------------------------- 工具封装
@pytest.mark.asyncio
async def test_publish_tool_requires_html(tmp_path):
    pub = {t.name: t for t in build_artifact_tools(str(tmp_path))}["publish_artifact"]
    assert not pub.read_only
    out = await pub.handler({"title": "t"})
    assert "需要 html" in out


@pytest.mark.asyncio
async def test_publish_tool_returns_url_and_updates(tmp_path):
    seen = {}
    tools = {t.name: t for t in build_artifact_tools(
        str(tmp_path), base_url="http://h:1", on_published=lambda m, u: seen.update(m=m, u=u))}
    out = await tools["publish_artifact"].handler({"title": "Rep", "html": "<b>1</b>"})
    assert "http://h:1/artifact/" in out and "v1" in out
    aid = seen["m"]["id"]
    out2 = await tools["publish_artifact"].handler({"title": "Rep", "html": "<b>2</b>", "id": aid})
    assert "更新" in out2 and "v2" in out2
    listed = await tools["list_artifacts"].handler({})
    assert aid in listed


@pytest.mark.asyncio
async def test_publish_tool_confirm_only_on_create(tmp_path):
    calls = []

    async def confirm(preview, is_update):
        calls.append(is_update)
        return True

    tools = {t.name: t for t in build_artifact_tools(str(tmp_path), confirm=confirm)}
    pub = tools["publish_artifact"]
    r1 = await pub.handler({"title": "P", "html": "<i>a</i>"})
    aid = r1.split("id=")[1].split(",")[0]
    await pub.handler({"title": "P", "html": "<i>b</i>", "id": aid})   # 更新：不应再问
    assert calls == [False]                                            # 仅首次确认


@pytest.mark.asyncio
async def test_publish_tool_cancel(tmp_path):
    async def deny(preview, is_update):
        return False

    pub = {t.name: t for t in build_artifact_tools(str(tmp_path), confirm=deny)}["publish_artifact"]
    out = await pub.handler({"title": "P", "html": "<i>a</i>"})
    assert "取消" in out
    assert ArtifactStore(str(tmp_path)).list() == []                  # 未落盘


# --------------------------------------------------------------- Web 路由
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                  # 路由用 os.getcwd() 定位制品库
    from src.web.server import app
    return TestClient(app), tmp_path


def test_routes_list_view_raw_meta(client):
    c, root = client
    meta = ArtifactStore(str(root)).publish("Title", "<h1>Body</h1><script>1</script>")
    aid = meta["id"]

    lst = c.get("/api/artifacts").json()["artifacts"]
    assert any(m["id"] == aid for m in lst) and lst[0]["url"].endswith(f"/artifact/{aid}")

    mj = c.get(f"/api/artifacts/{aid}").json()
    assert mj["version"] == 1 and mj["title"] == "Title"

    raw = c.get(f"/artifact/{aid}/raw")
    assert raw.status_code == 200 and "<h1>Body</h1>" in raw.text
    csp = raw.headers["content-security-policy"]
    assert "default-src 'none'" in csp                # 禁外联
    assert "frame-ancestors 'self'" in csp            # 只许同源查看页嵌、禁外站 iframe

    view = c.get(f"/artifact/{aid}")
    assert view.status_code == 200
    assert 'sandbox="allow-scripts"' in view.text     # iframe 隔离
    assert aid in view.text and "Title" in view.text
    assert "frame-ancestors 'none'" in view.headers["content-security-policy"]  # 查看页禁被嵌


def test_missing_artifact_404(client):
    c, _ = client
    assert c.get("/artifact/nope/raw").status_code == 404
    assert c.get("/api/artifacts/nope").status_code == 404
    assert c.get("/artifact/nope").status_code == 404


def test_gallery_and_empty_list_ok(client):
    c, _ = client
    assert c.get("/artifacts").status_code == 200          # 画廊页（无 500）
    assert c.get("/api/artifacts").json() == {"artifacts": []}


def test_view_escapes_title(client):
    c, root = client
    meta = ArtifactStore(str(root)).publish("<x>&", "<p>ok</p>")
    html = c.get(f"/artifact/{meta['id']}").text
    assert "&lt;x&gt;&amp;" in html and "<x>&" not in html.replace("<x>&amp;", "")


# --------------------------------------------------------------- 鉴权：Cookie（token 已移出 URL，审计 P0#4）
def test_artifact_api_auth_via_cookie(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTODEV_API_TOKEN", "secret")
    from src.web.server import app
    c = TestClient(app)
    aid = ArtifactStore(str(tmp_path)).publish("T", "<p>x</p>")["id"]
    # 无凭证 → 401；?token= 已不再被接受 → 仍 401；登录种 Cookie 后 → 放行
    assert c.get(f"/api/artifacts/{aid}").status_code == 401
    assert c.get(f"/api/artifacts/{aid}?token=secret").status_code == 401     # 移出 URL
    c.post("/api/auth/login", json={"token": "secret"})
    assert c.get(f"/api/artifacts/{aid}").status_code == 200                  # Cookie 鉴权
    assert c.get(f"/api/artifacts/{aid}", headers={"Authorization": "Bearer secret"}).status_code == 200


# --------------------------------------------------------------- 体积上限
def test_publish_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_ARTIFACT_MAX_BYTES", "100")
    assert max_bytes() == 100
    store = ArtifactStore(str(tmp_path))
    with pytest.raises(ValueError):
        store.publish("big", "x" * 101)
    assert store.publish("ok", "x" * 100)["bytes"] == 100   # 边界内可发


@pytest.mark.asyncio
async def test_publish_tool_oversize_message(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_ARTIFACT_MAX_BYTES", "50")
    pub = {t.name: t for t in build_artifact_tools(str(tmp_path))}["publish_artifact"]
    out = await pub.handler({"title": "t", "html": "y" * 999})
    assert "发布失败" in out and "上限" in out
    assert ArtifactStore(str(tmp_path)).list() == []        # 未落盘


def test_max_bytes_default_and_bad_env(monkeypatch):
    monkeypatch.delenv("VORTOCODE_ARTIFACT_MAX_BYTES", raising=False)
    assert max_bytes() == 16 * 1024 * 1024
    monkeypatch.setenv("VORTOCODE_ARTIFACT_MAX_BYTES", "notint")
    assert max_bytes() == 16 * 1024 * 1024                  # 坏值回退默认


# --------------------------------------------------------------- markdown
def test_render_markdown_doc():
    html = render_markdown_doc("标题", "# Hello\n\n- a\n- b\n")
    assert "<h1>Hello</h1>" in html
    assert "<li>a</li>" in html and "<li>b</li>" in html
    assert "标题" in html and html.lstrip().startswith("<!doctype html>")


@pytest.mark.asyncio
async def test_publish_tool_markdown_kind(tmp_path):
    pub = {t.name: t for t in build_artifact_tools(str(tmp_path))}["publish_artifact"]
    out = await pub.handler({"title": "报告", "markdown": "# 标题\n正文"})
    assert "markdown" in out
    m = ArtifactStore(str(tmp_path)).list()[0]
    assert m["kind"] == "markdown"
    assert "<h1>标题</h1>" in ArtifactStore(str(tmp_path)).html(m["id"])


@pytest.mark.asyncio
async def test_publish_tool_needs_html_or_markdown(tmp_path):
    pub = {t.name: t for t in build_artifact_tools(str(tmp_path))}["publish_artifact"]
    out = await pub.handler({"title": "t"})
    assert "html 或 markdown" in out


# --------------------------------------------------------------- 删除
def test_store_delete(tmp_path):
    store = ArtifactStore(str(tmp_path))
    aid = store.publish("D", "<p>x</p>")["id"]
    assert store.exists(aid)
    assert store.delete(aid) is True
    assert not store.exists(aid)
    assert store.delete(aid) is False           # 再删不存在
    assert store.delete("../evil") is False      # 非法 id


@pytest.mark.asyncio
async def test_delete_tool_confirm_and_cancel(tmp_path):
    calls = []

    async def confirm_del(preview):
        calls.append(preview["id"])
        return preview["id"].startswith("keep") is False   # 取消以 keep 开头的

    tools = {t.name: t for t in build_artifact_tools(str(tmp_path), confirm_delete=confirm_del)}
    store = ArtifactStore(str(tmp_path))
    aid = store.publish("X", "<p>x</p>")["id"]
    assert "delete_artifact" in tools and not tools["delete_artifact"].read_only
    out = await tools["delete_artifact"].handler({"id": aid})
    assert "已删除" in out and not store.exists(aid)
    assert calls == [aid]
    # 不存在的 id
    assert "没有" in await tools["delete_artifact"].handler({"id": "nope"})
    # 缺 id
    assert "需要 id" in await tools["delete_artifact"].handler({})


@pytest.mark.asyncio
async def test_delete_tool_respects_cancel(tmp_path):
    async def deny(preview):
        return False

    tools = {t.name: t for t in build_artifact_tools(str(tmp_path), confirm_delete=deny)}
    store = ArtifactStore(str(tmp_path))
    aid = store.publish("X", "<p>x</p>")["id"]
    out = await tools["delete_artifact"].handler({"id": aid})
    assert "取消" in out and store.exists(aid)   # 取消则保留


def test_delete_route(client):
    c, root = client
    aid = ArtifactStore(str(root)).publish("R", "<p>x</p>")["id"]
    assert c.delete(f"/api/artifacts/{aid}").json() == {"deleted": aid}
    assert c.get(f"/api/artifacts/{aid}").status_code == 404   # 已删
    assert c.delete("/api/artifacts/nope").status_code == 404  # 删不存在 → 404


# --------------------------------------------------------------- 版本历史 + pin
def test_publish_keeps_version_snapshots(tmp_path):
    store = ArtifactStore(str(tmp_path))
    a = store.publish("V", "<p>one</p>")
    b = store.publish("V", "<p>two</p>", artifact_id=a["id"])
    assert b["version"] == 2 and b["pinned"] is None
    assert [x["v"] for x in store.versions(a["id"])] == [1, 2]
    assert store.html(a["id"], 1) == "<p>one</p>"      # 历史版本快照可取
    assert store.html(a["id"], 2) == "<p>two</p>"
    assert store.html(a["id"]) == "<p>two</p>"         # 当前=最新


def test_pin_and_unpin(tmp_path):
    store = ArtifactStore(str(tmp_path))
    a = store.publish("V", "<p>one</p>")
    store.publish("V", "<p>two</p>", artifact_id=a["id"])
    m = store.pin(a["id"], 1)
    assert m["pinned"] == 1 and store.html(a["id"]) == "<p>one</p>"   # 当前指向 pin 的 v1
    m2 = store.pin(a["id"], None)
    assert m2["pinned"] is None and store.html(a["id"]) == "<p>two</p>"  # 取消→回最新
    assert store.pin(a["id"], 99) is None and store.pin("nope", 1) is None  # 非法


def test_publish_clears_pin(tmp_path):
    store = ArtifactStore(str(tmp_path))
    a = store.publish("V", "<p>one</p>")
    store.publish("V", "<p>two</p>", artifact_id=a["id"])
    store.pin(a["id"], 1)
    c = store.publish("V", "<p>three</p>", artifact_id=a["id"])
    assert c["pinned"] is None and store.html(a["id"]) == "<p>three</p>"  # 再发布清 pin


def test_versions_pin_raw_routes(client):
    c, root = client
    store = ArtifactStore(str(root))
    a = store.publish("R", "<p>1</p>")
    store.publish("R", "<p>2</p>", artifact_id=a["id"])
    aid = a["id"]
    vj = c.get(f"/api/artifacts/{aid}/versions").json()
    assert vj["current"] == 2 and vj["pinned"] is None and len(vj["versions"]) == 2
    assert "<p>1</p>" in c.get(f"/artifact/{aid}/raw?v=1").text       # 历史版本
    assert "<p>2</p>" in c.get(f"/artifact/{aid}/raw").text          # 当前
    assert c.post(f"/api/artifacts/{aid}/pin?version=1").json()["pinned"] == 1
    assert "<p>1</p>" in c.get(f"/artifact/{aid}/raw").text          # pin 后当前=v1
    assert c.post(f"/api/artifacts/{aid}/pin").json()["pinned"] is None   # 取消 pin
    assert c.post(f"/api/artifacts/{aid}/pin?version=99").status_code == 400
    assert c.get("/api/artifacts/nope/versions").status_code == 404
