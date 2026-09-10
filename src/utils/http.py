"""aiohttp 会话工厂——**每个调用点必须表态：目的地在公网，还是在本机/局域网。**

## 为什么需要它

`aiohttp.ClientSession()` 默认 `trust_env=False`，**不读 `HTTP_PROXY`/`HTTPS_PROXY`**
（requests / httpx / openai SDK 都读，所以很容易以为它也读）。在"纯环境变量代理、没有 TUN"
的机器上，这意味着一切出网调用直接失败——而报错是超时，不是"没配代理"，人会往网络故障上排查。

真机 2026-09-10：生产机 192.168.10.97 正是这种机型（`HTTP_PROXY=http://192.168.10.99:7890`，
无 TUN）。钉钉一直能用，只是因为 `api.dingtalk.com` 在国内直连就通——这个坑被掩盖了几个月，
直到要接 Telegram 才暴露：同一台机器上 `curl` 走代理 302、aiohttp 直连 8 秒超时。

`doctor.py` 早就单独踩过一次并就地修了（`_check_relay` 用 `trust_env=True`，注释写着
"真机 doctor 首跑就踩了这个"）。但只修一处的后果更糟：**doctor 说"LLM 接口可达"，
真正发请求的 `llm/client.py` 却直连失败**——体检报告和实际情况说的不是一回事。

## 为什么是两个函数而不是一个开关

因为"要不要吃代理"不是全局设置，是**每个目的地各自的事实**：

* 公网目的地（api.telegram.org / api.github.com / 中转站）→ 必须吃代理，否则出不去；
* 本机目的地（127.0.0.1:8080 的 serve）→ **绝不能吃**：`NO_PROXY` 没配全时，
  127.0.0.1 走代理会被误路由，把在跑的服务误报成不可达（doctor 的 `_check_serve`
  注释里记着这条）。

一个布尔开关会让人在调用点随手抄一个默认值。两个具名函数逼人回答那个问题，
而"127.0.0.1 走代理"从结构上不可能再发生。

`trust_env=True` 同时让 aiohttp 读 `~/.netrc`（无显式 auth 时用作凭据来源）。
对**目的地在公网**这个前提来说这是 netrc 本来的用途；正因如此，本机/局域网那档不开它。
"""
from __future__ import annotations

from typing import Any


def outbound_session(**kwargs: Any):
    """目的地在**公网**的会话：吃 `HTTP(S)_PROXY`，也吃 `NO_PROXY` 例外。

    局域网里的自建服务（如指到 `192.168.x.x` 的中转站）不会因此被推进代理——
    那是 `NO_PROXY` 的职责，不该靠这里少开一个开关来凑。
    """
    import aiohttp
    kwargs.setdefault("trust_env", True)
    return aiohttp.ClientSession(**kwargs)


def local_session(**kwargs: Any):
    """目的地在**本机/局域网**的会话：刻意不吃代理环境。

    `NO_PROXY` 没配全是常态，而把 127.0.0.1 推进代理的后果是"服务明明在跑却报不可达"——
    宁可这一档永远直连。
    """
    import aiohttp
    kwargs.setdefault("trust_env", False)
    return aiohttp.ClientSession(**kwargs)
