"""按**工序角色**选「端点 + 模型」——让一条流水线上的各道工序跑在不同厂商的模型上。

两种接法都支持，可以混用：

1. **全挂在自建 One API 中转站后面**：端点只有一个，角色之间只换模型名。最省事，也是默认——
   不配任何东西时整条链路的行为与从前**逐字节一致**。
2. **直连各家官方端点**：每个 provider 自带 base_url + api_key。跨厂商的"独立性"要靠它才真
   （`.env.example` 里那对 ANTHROPIC_* 从前一处都没接上，现在接在这儿）。

为什么值得分层——判据是**"这一步错了代价多大"**，不是"这一步简不简单"：
  · `decompose` 只跑一次、token 最少，但拆错了后面全白干 → 该用最强的，省它毫无意义；
  · `implement` 是烧钱大头，也最吃能力，换弱模型省下的钱会被重试吃回去（还让人多等几倍）；
  · `review`/`verify` 是**异厂商最有价值**的地方，而理由是独立性不是强弱：同一个模型自己审
    自己写的实现，会系统性漏掉自己的盲区——它实现时认为对的地方，审的时候还是认为对。

安全底线（有回归测试钉住）：**一个 provider 的 key 绝不外借**。声明了端点却没给 key 的
provider 一律不可用，宁可回落默认也不把中转站的 key 发到另一家厂商的地址上。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "default"


@dataclass(frozen=True)
class ProviderSpec:
    """一个可用的模型端点。api_key 为空 = 不可用（见模块头的"key 绝不外借"）。"""

    name: str
    base_url: str
    api_key: str

    @property
    def usable(self) -> bool:
        return bool(self.base_url.strip() and self.api_key.strip())


@dataclass(frozen=True)
class RoleTarget:
    """某个工序角色最终要用的端点 + 模型。"""

    role: str
    provider: ProviderSpec
    model: str


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def providers() -> Dict[str, ProviderSpec]:
    """当前可见的端点表。`default` 恒在（即主 OPENAI_API_BASE/KEY，通常就是你的 One API）。

    另外两种登记方式：
      · `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` → 自动登记成 `anthropic`（沿用既有变量名）；
      · `VORTOCODE_PROVIDER_<NAME>_BASE` + `_KEY` → 登记成 `<name>`（小写），想加几家加几家。
    """
    from src.llm.client import DEFAULT_LLM_BASE_URL

    out: Dict[str, ProviderSpec] = {
        DEFAULT_PROVIDER: ProviderSpec(
            DEFAULT_PROVIDER,
            _env("OPENAI_API_BASE") or DEFAULT_LLM_BASE_URL,
            _env("OPENAI_API_KEY"),
        )
    }
    anthropic_base, anthropic_key = _env("ANTHROPIC_BASE_URL"), _env("ANTHROPIC_AUTH_TOKEN")
    if anthropic_base and anthropic_key:
        out["anthropic"] = ProviderSpec("anthropic", anthropic_base, anthropic_key)
    prefix, suffix = "VORTOCODE_PROVIDER_", "_BASE"
    for key in os.environ:
        if not (key.startswith(prefix) and key.endswith(suffix)):
            continue
        name = key[len(prefix):-len(suffix)].lower()
        if not name:
            continue
        out[name] = ProviderSpec(name, _env(key), _env(f"{prefix}{name.upper()}_KEY"))
    return out


def parse_role_models(raw: str) -> Dict[str, Tuple[Optional[str], str]]:
    """解析 `role=model` / `role=provider:model` 列表（逗号分隔）→ {role: (provider|None, model)}。

    纯函数、不读环境：将来换成从文件读同样能喂进来。无法解析的条目**跳过并记一条日志**，
    不让一处笔误把整张表打翻（静默吞掉才是这仓最恨的那种坏）。
    """
    out: Dict[str, Tuple[Optional[str], str]] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        role, sep, target = item.partition("=")
        role, target = role.strip().lower(), target.strip()
        if not sep or not role or not target:
            logger.warning("模型角色表里这条看不懂，已跳过：%r（要 role=model 或 role=provider:model）",
                           item)
            continue
        provider, psep, model = target.partition(":")
        if psep and provider.strip() and model.strip():
            out[role] = (provider.strip().lower(), model.strip())
        else:
            out[role] = (None, target)
    return out


def role_models() -> Dict[str, Tuple[Optional[str], str]]:
    """env `VORTOCODE_MODEL_ROLES` 里声明的角色表。没配 = 空表 = 全走默认。"""
    return parse_role_models(_env("VORTOCODE_MODEL_ROLES"))


def resolve(role: str) -> Optional[RoleTarget]:
    """这个角色该用哪个端点 + 哪个模型。没配 / 配错 → None，调用方沿用默认（零行为变化）。

    配错一律**回落默认并记警告**，不抛异常：模型路由是优化项，不该让一处拼错把整条流水线打死。
    """
    want = role_models().get((role or "").strip().lower())
    if want is None:
        return None
    provider_name, model = want
    table = providers()
    spec = table.get(provider_name or DEFAULT_PROVIDER)
    if spec is None:
        logger.warning("角色 %s 指定了没登记的端点 %s（已登记：%s），回落默认端点",
                       role, provider_name, ", ".join(sorted(table)))
        return None
    if not spec.usable:
        # 这里**绝不**拿 default 的 key 去补：把中转站的 key 发到另一家厂商的地址上是真事故。
        logger.warning("角色 %s 指定的端点 %s 没有可用凭据（base_url/api_key 缺一），回落默认端点——"
                       "不会借用其他端点的 key", role, spec.name)
        return None
    return RoleTarget(role=(role or "").strip().lower(), provider=spec, model=model)


_cache: Dict[Tuple[str, str, str], Any] = {}


def client_for_role(role: str) -> Optional[Any]:
    """角色专属 LLMClient（按端点+模型缓存，复用连接池）。没配该角色返回 None。

    返回 None 是**正常路径**：调用方照旧用自己的客户端，整条链路与从前一致。
    """
    target = resolve(role)
    if target is None:
        return None
    logger.info("工序 %s 走 %s 端点的 %s", target.role, target.provider.name, target.model)
    return _client(target.provider, target.model)


def _client(provider: ProviderSpec, model: str) -> Any:
    """按 (端点, 模型) 缓存客户端——同一目标复用同一个连接池。"""
    key = (provider.base_url, provider.api_key, model)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    from src.llm.client import LLMClient, LLMConfig

    client = LLMClient(LLMConfig(
        base_url=provider.base_url, api_key=provider.api_key, model=model))
    _cache[key] = client
    return client


def client_for_spec(spec: str) -> Optional[Any]:
    """给一条**显式**的 `provider:model` 造客户端（`.vortocode/agents/*.md` 的 `model:` 用）。

    不带 provider 前缀（只写模型名）返回 None——那种情况调用方用 `set_model()` 就够了，端点不变。
    端点没登记/没凭据同样返回 None 并记警告，绝不借别家的 key。
    """
    provider_name, sep, model = (spec or "").partition(":")
    provider_name, model = provider_name.strip().lower(), model.strip()
    if not sep or not provider_name or not model:
        return None
    table = providers()
    target = table.get(provider_name)
    if target is None or not target.usable:
        logger.warning("角色模型 %r 指定的端点不可用（未登记或缺凭据），回落默认端点——"
                       "不会借用其他端点的 key", spec)
        return None
    return _client(target, model)


def describe_roles() -> str:
    """一行人话：当前各工序分别跑在哪。给 doctor / 启动日志用，也便于真机核对。"""
    table = role_models()
    if not table:
        return "模型分层未启用（所有工序走默认模型）"
    parts = []
    for role in sorted(table):
        target = resolve(role)
        if target is None:
            parts.append(f"{role}=默认（配置未生效，见日志）")
        else:
            parts.append(f"{role}={target.provider.name}:{target.model}")
    return "；".join(parts)


def reset_cache() -> None:
    """清掉客户端缓存（测试用，或改了 env 想让新配置立刻生效）。"""
    _cache.clear()
