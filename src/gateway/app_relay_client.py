"""手机网关 → 常驻助理的本机转发（ADR-012 阶段 A）。网关自己不执行回合，只转。

``VORTOCODE_APP_RELAY_URL`` 只接受回环地址（``http://127.0.0.1:<端口>`` / ``http://localhost:<端口>``），
专用令牌 ``VORTOCODE_APP_RELAY_TOKEN`` 因此不会被配置错发到别的机器。两项都没配 = 对话未开通。
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit


class RelayUnavailable(RuntimeError):
    """对话转发没开通，或常驻助理暂时连不上。"""


def _target() -> tuple[str, str]:
    url = os.getenv("VORTOCODE_APP_RELAY_URL", "").strip().rstrip("/")
    token = os.getenv("VORTOCODE_APP_RELAY_TOKEN", "").strip()
    parts = urlsplit(url)
    if (not url or not token or parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1")
            or parts.path or parts.query or parts.username):
        raise RelayUnavailable("这台助理还没开通手机对话")
    return url, token


async def relay(path: str, body: dict, *, client=None) -> tuple[int, dict]:
    """POST 给常驻助理的 app-relay 接口，返回 (状态码, JSON)。连不上抛 RelayUnavailable。"""
    import httpx

    url, token = _target()
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, trust_env=False)
    try:
        response = await client.post(url + path, json=body, headers={"Authorization": "Bearer " + token})
    except httpx.HTTPError as error:
        raise RelayUnavailable("暂时连不上常驻助理") from error
    finally:
        if own:
            await client.aclose()
    try:
        data = response.json()
    except ValueError:
        data = {}
    return response.status_code, data if isinstance(data, dict) else {}
