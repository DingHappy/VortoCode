"""LLMClient.list_models（OpenAI 兼容 GET /models）的行为测试。

全程 127.0.0.1 临时 aiohttp 服务器，离线确定性；不打任何真实端点。
"""
import pytest
from aiohttp import web

from src.llm.client import LLMClient, LLMConfig


class _ModelsServer:
    """一个只服务 GET /v1/models 的本地假中转站。"""

    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        app = web.Application()
        app.router.add_get("/v1/models", self._handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}/v1"
        return self

    async def __aexit__(self, *exc):
        await self._runner.cleanup()


def _client(base_url: str, api_key: str = "test-key") -> LLMClient:
    return LLMClient(LLMConfig(base_url=base_url, api_key=api_key))


@pytest.mark.asyncio
async def test_list_models_parses_ids_in_server_order_and_dedupes():
    """标准 OpenAI 格式：按服务端顺序取 id、去重；非 dict 项与空 id 跳过；兼容 model/name 键。"""
    async def handler(request):
        return web.json_response({"object": "list", "data": [
            {"id": "mimo-v2.5-pro"},
            {"id": "mimo-v2.5"},
            {"id": "mimo-v2.5-pro"},          # 重复 → 去重
            "garbage",                         # 非 dict → 跳过
            {"id": ""},                        # 空 id → 跳过
            {"model": "by-model-key"},         # 兼容 model 键（部分网关这么报）
            {"name": "by-name-key"},           # 兼容 name 键
        ]})

    async with _ModelsServer(handler) as server:
        models = await _client(server.base_url).list_models()
    assert models == ["mimo-v2.5-pro", "mimo-v2.5", "by-model-key", "by-name-key"]


@pytest.mark.asyncio
async def test_list_models_sends_bearer_auth():
    """配了 key 必须带 Bearer 头——中转站的 /models 是鉴权接口，不带头拿到的是空列表或 401。"""
    seen = []

    async def handler(request):
        seen.append(request.headers.get("Authorization", ""))
        return web.json_response({"data": [{"id": "m1"}]})

    async with _ModelsServer(handler) as server:
        await _client(server.base_url, api_key="sk-abc").list_models()
    assert seen == ["Bearer sk-abc"]


@pytest.mark.asyncio
async def test_list_models_raises_on_http_error():
    """非 200 抛错（由调用方决定回落）——静默吞错会把「key 失效」伪装成「服务端没模型」。"""
    async def handler(request):
        return web.Response(status=500, text="boom")

    async with _ModelsServer(handler) as server:
        with pytest.raises(Exception, match="HTTP 500"):
            await _client(server.base_url).list_models()


@pytest.mark.asyncio
async def test_list_models_unknown_payload_is_empty_not_error():
    """200 但结构不认识 → 空列表（≠出错）：服务端就是没报任何模型，调用方按空回落。"""
    async def handler(request):
        return web.json_response({"object": "list"})

    async with _ModelsServer(handler) as server:
        assert await _client(server.base_url).list_models() == []
