"""量化配置加载器

从 config/quant.yaml 读取配置，提供给流水线、Agent、Hook 使用。
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FactorConfig(BaseModel):
    """因子配置"""
    default_factors: List[str] = Field(default_factory=lambda: [
        "momentum_20d", "reversal_5d", "low_vol_20d", "bias_20d",
    ])
    default_weighting: str = "ic"
    holding_period: int = 20


class ScheduleConfig(BaseModel):
    """调度配置"""
    news_interval: int = 1800
    sentiment_interval: int = 3600
    factor_interval: int = 86400
    review_interval: int = 86400
    # cron 风格时间点（HH:MM，24h 制），设置后优先于 interval
    factor_time: Optional[str] = None   # e.g. "15:30"
    review_time: Optional[str] = None   # e.g. "18:00"


class DingTalkConfig(BaseModel):
    """钉钉配置"""
    webhook: str = ""
    secret: str = ""


class NotificationsConfig(BaseModel):
    """通知配置"""
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)


class QuantConfig(BaseModel):
    """量化流水线完整配置"""
    api_base: str = "http://localhost:8000"
    markets: List[str] = Field(default_factory=lambda: ["astock"])
    watch_sectors: List[str] = Field(default_factory=list)
    watch_symbols: List[str] = Field(default_factory=list)
    factors: FactorConfig = Field(default_factory=FactorConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)


def load_quant_config(config_path: str = "config/quant.yaml") -> QuantConfig:
    """加载量化配置文件。

    文件不存在时返回默认配置（不报错）。
    """
    path = Path(config_path)
    if not path.exists():
        logger.warning("Quant config not found at %s, using defaults", config_path)
        return QuantConfig()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    quant_raw = raw.get("quant", raw)  # 兼容有无顶层 "quant:" 键
    return QuantConfig(**quant_raw)
