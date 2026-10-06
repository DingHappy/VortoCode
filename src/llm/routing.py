"""按回合的模型调度：客户端选「自动」或指定模型，由这里决定本回合用哪个模型。

三档模型来自环境变量（Desktop 按用户配置注入，装配时读一次即可，但这里每次现读，
以便测试与运行时注入都生效）：
- ``LLM_MODEL_CHEAP``    快速档，短小的只读问答；
- ``LLM_MODEL_BALANCED`` 均衡档，默认；
- ``LLM_MODEL_POWERFUL`` 强力档，长任务、带图或明显的复杂工程任务。

不看 plan/build 模式：Desktop 已不再区分两者（每轮都按 build 发），拿模式当信号会让
「自动」永远落到强力档。
任一档没配就回落到 ``DEFAULT_MODEL``。

客户端点名的模型**只接受三档里已配置的**：WS 客户端不能借这个字段把请求打到任意模型上
（计费与能力边界由服务端配置决定，不由前端决定）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, Optional

AUTO = "auto"
_MAX_MODEL_CHARS = 128
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")

# 明显是"要动脑"的工程任务：命中任一就上强力档。
_COMPLEX_HINTS = (
    "重构", "架构", "设计", "调试", "排查", "定位", "性能", "迁移", "并发", "安全", "实现",
    "refactor", "architecture", "design", "debug", "investigate", "migrate", "performance",
    "concurrency", "security", "implement",
)
_LONG_TEXT = 400
_SHORT_TEXT = 80


@dataclass(frozen=True)
class TurnModel:
    model: str
    tier: str      # cheap / balanced / powerful / manual
    reason: str


def routing_tiers() -> Dict[str, str]:
    default = (os.getenv("DEFAULT_MODEL") or os.getenv("OPENAI_MODEL") or "").strip()
    tiers = {
        "cheap": (os.getenv("LLM_MODEL_CHEAP") or "").strip() or default,
        "balanced": (os.getenv("LLM_MODEL_BALANCED") or "").strip() or default,
        "powerful": (os.getenv("LLM_MODEL_POWERFUL") or "").strip() or default,
    }
    return {tier: model for tier, model in tiers.items() if model}


def clean_model_request(raw: object) -> Optional[str]:
    """协议字段 ``model`` 的形状校验：``auto`` 或一个形如模型 id 的短串；其余一律当没传。"""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if value == AUTO:
        return AUTO
    if not value or len(value) > _MAX_MODEL_CHARS or not _MODEL_RE.match(value):
        return None
    return value


def route_model(text: str, *, mode: str, has_media: bool = False, context_count: int = 0) -> Optional[TurnModel]:
    """「自动」：按任务形状挑一档。三档没配任何模型时返回 None（沿用当前模型）。"""
    tiers = routing_tiers()
    if not tiers:
        return None
    body = (text or "").strip()
    lowered = body.lower()
    if has_media:
        tier, reason = "powerful", "包含图片或音频"
    elif len(body) >= _LONG_TEXT or context_count >= 3:
        tier, reason = "powerful", "任务描述较长或引用了多个文件"
    elif any(hint in lowered for hint in _COMPLEX_HINTS):
        tier, reason = "powerful", "看起来是较复杂的工程任务"
    elif len(body) <= _SHORT_TEXT and context_count == 0:
        tier, reason = "cheap", "简短问答"
    else:
        tier, reason = "balanced", "常规任务"
    model = tiers.get(tier) or tiers.get("balanced") or next(iter(tiers.values()))
    return TurnModel(model=model, tier=tier, reason=reason)


def resolve_turn_model(requested: Optional[str], text: str, *, mode: str,
                       has_media: bool = False, context_count: int = 0) -> Optional[TurnModel]:
    """本回合用哪个模型。None = 不改（客户端没传、或点名了未配置的模型）。"""
    if requested is None:
        return None
    if requested == AUTO:
        return route_model(text, mode=mode, has_media=has_media, context_count=context_count)
    if requested in routing_tiers().values():
        return TurnModel(model=requested, tier="manual", reason="手动指定")
    return None
