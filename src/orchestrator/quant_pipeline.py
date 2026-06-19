"""量化研究定时流水线

利用 LoopController 实现 7×24 调度：
- 新闻采集：每 30 分钟
- 情绪分析：每 1 小时
- 因子研究：每日收盘后（可配 cron 时间点）
- 每日复盘：每日 18:00（可配 cron 时间点）

支持从 config/quant.yaml 自动加载配置和注册钉钉通知。
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from .loop_controller import LoopController, LoopConfig, create_loop_controller
from .quant_config import QuantConfig, load_quant_config
from .quant_state import PipelineState
from ..memory.base import MemorySystem
from ..hooks.hook import HookEventType

logger = logging.getLogger(__name__)


def _seconds_until(target_hhmm: str) -> int:
    """计算距离今天 target_hhmm（HH:MM）还有多少秒。已过则算明天。"""
    now = datetime.now()
    parts = target_hhmm.split(":")
    target = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
    delta = (target - now).total_seconds()
    if delta <= 0:
        delta += 86400  # 明天
    return int(delta)


def _serialize_result(result: Any) -> str:
    """将 OrchestrationResult 序列化为 JSON 字符串（存入记忆用）。"""
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(), ensure_ascii=False, default=str)
    if hasattr(result, "__dict__"):
        return json.dumps(result.__dict__, ensure_ascii=False, default=str)
    return json.dumps(result, ensure_ascii=False, default=str)


class QuantPipeline:
    """7×24 量化研究流水线"""

    def __init__(
        self,
        engine: Any,
        memory: MemorySystem,
        hooks: Any,
        config: Optional[QuantConfig] = None,
        intervals: Optional[Dict[str, int]] = None,
        state: Optional[PipelineState] = None,
    ):
        self.engine = engine
        self.memory = memory
        self.hooks = hooks
        self.config = config or QuantConfig()
        self.state = state or PipelineState()

        # 合并 intervals 参数覆盖
        self._intervals = {
            "news": self.config.schedule.news_interval,
            "sentiment": self.config.schedule.sentiment_interval,
            "factor": self.config.schedule.factor_interval,
            "review": self.config.schedule.review_interval,
            **(intervals or {}),
        }
        self._loops: Dict[str, LoopController] = {}
        self._token_usage: Dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}

    async def start_all(self):
        """启动所有定时任务"""
        loop_defs = [
            ("news", self._run_news_collection, self._on_news_collected),
            ("sentiment", self._run_sentiment_analysis, self._on_sentiment_done),
            ("factor", self._run_factor_research, self._on_factor_done),
            ("review", self._run_daily_review, self._on_review_done),
        ]

        for name, check_func, on_success in loop_defs:
            interval = self._intervals[name]

            # cron 时间点模式
            cron_time = None
            if name == "factor" and self.config.schedule.factor_time:
                cron_time = self.config.schedule.factor_time
            elif name == "review" and self.config.schedule.review_time:
                cron_time = self.config.schedule.review_time

            if cron_time:
                interval = _seconds_until(cron_time)
                logger.info("[QuantPipeline] %s scheduled at %s (in %ds)", name, cron_time, interval)

            # 恢复持久化状态
            saved = self.state.get_loop_state(f"quant_{name}")
            saved_iters = saved.get("iterations", 0)

            loop = create_loop_controller(
                "basic",
                name=f"quant_{name}",
                config=LoopConfig(
                    interval=interval,
                    max_iterations=999999,
                    max_duration=86400 * 365,
                ),
                check_func=check_func,
                on_success=on_success,
                on_failure=self._on_error,
            )

            # 恢复迭代计数
            if saved_iters > 0:
                loop.current_iteration = saved_iters
                logger.info("[QuantPipeline] %s restored %d iterations", name, saved_iters)

            self._loops[name] = loop

        for name, loop in self._loops.items():
            await loop.start()
            logger.info("[QuantPipeline] Started loop: %s (interval=%ds)", name, self._intervals[name])

    async def stop_all(self):
        """停止所有定时任务"""
        for name, loop in self._loops.items():
            await loop.stop()
            logger.info("[QuantPipeline] Stopped loop: %s", name)

    def get_status(self) -> Dict[str, Any]:
        """获取所有循环状态"""
        return {
            name: {
                "status": loop.status.value,
                "iterations": loop.current_iteration,
                "interval": self._intervals[name],
                "last_check": str(loop.last_check_time) if loop.last_check_time else None,
            }
            for name, loop in self._loops.items()
        }

    def get_summary(self) -> Dict[str, Any]:
        """获取流水线摘要（含 token 用量和持久化状态）"""
        status = self.get_status()
        total_iters = sum(s["iterations"] for s in status.values())
        return {
            "loops": status,
            "total_iterations": total_iters,
            "token_usage": self._token_usage,
            "persisted_state": self.state.get_all(),
        }

    def _save_loop_state(self, name: str):
        """保存循环状态到磁盘"""
        loop = self._loops.get(name)
        if loop:
            self.state.save_loop_state(
                name=f"quant_{name}",
                iterations=loop.current_iteration,
                last_check=str(loop.last_check_time) if loop.last_check_time else None,
                status=loop.status.value,
            )

    def _track_tokens(self, result: Any):
        """从 OrchestrationResult 中提取 token 用量"""
        tokens = getattr(result, "tokens_used", 0)
        if tokens:
            self._token_usage["total"] += tokens

    def get_token_usage(self) -> Dict[str, int]:
        return self._token_usage.copy()

    # ---- 各阶段执行函数 ----

    def _build_context(self, stage: str) -> Dict[str, Any]:
        """构建流水线上下文，注入配置信息"""
        return {
            "pipeline_stage": stage,
            "watch_sectors": self.config.watch_sectors,
            "watch_symbols": self.config.watch_symbols,
            "factors": self.config.factors.model_dump(),
            "markets": self.config.markets,
        }

    async def _run_with_retry(self, name: str, func, max_retries: int = 1):
        """带重试的阶段执行"""
        for attempt in range(max_retries + 1):
            try:
                result = await func()
                return result
            except Exception as e:
                if attempt < max_retries:
                    wait = 30 * (attempt + 1)
                    logger.warning(
                        "[QuantPipeline] %s failed (attempt %d/%d), retrying in %ds: %s",
                        name, attempt + 1, max_retries + 1, wait, e,
                    )
                    await asyncio.sleep(wait)
                else:
                    raise

    async def _run_news_collection(self):
        sectors = "、".join(self.config.watch_sectors) if self.config.watch_sectors else "全市场"
        result = await self.engine.orchestrate(
            f"采集最新的 A 股市场新闻、公告、北向资金流向、龙虎榜数据。\n"
            f"重点关注板块：{sectors}\n"
            f"输出结构化 JSON 事件列表，每个事件包含：event_type, target, summary, sentiment, source, time。",
            context=self._build_context("news_collection"),
        )
        return result

    async def _run_sentiment_analysis(self):
        recent_news = await self.memory.retrieve("市场新闻", top_k=20)
        news_summary = "\n".join([m.content for m in recent_news[:10]])

        result = await self.engine.orchestrate(
            f"分析以下新闻的情绪影响，判断利好/利空/中性，给出评分(-1到1)。\n\n"
            f"最新新闻：\n{news_summary}\n\n"
            f"输出格式：事件卡片列表 JSON，每个包含 target, sentiment_score, impact_duration, reasoning, action_suggestion。",
            context=self._build_context("sentiment_analysis"),
        )
        return result

    async def _run_factor_research(self):
        factors = self.config.factors
        result = await self.engine.orchestrate(
            f"运行今日因子分析流水线：\n"
            f"1. 获取最新 A 股数据\n"
            f"2. 计算因子：{', '.join(factors.default_factors)}\n"
            f"3. 做 IC 体检（权重方式：{factors.default_weighting}）\n"
            f"4. 生成选股结果（持仓周期：{factors.holding_period}天）\n"
            f"5. 对比昨日结果，标注变化",
            context=self._build_context("factor_research"),
        )
        return result

    async def _run_daily_review(self):
        result = await self.engine.orchestrate(
            "生成今日量化复盘报告：\n"
            "1. 大盘概况（指数涨跌、成交额）\n"
            "2. 模拟盘表现（净值变化、持仓盈亏）\n"
            "3. 今日信号回顾\n"
            "4. 情绪面总结\n"
            "5. 明日关注点\n\n"
            "输出格式：结构化 Markdown 报告 + JSON 数据。",
            context=self._build_context("daily_review"),
        )
        return result

    # ---- 回调函数 ----

    async def _on_news_collected(self, result):
        self._save_loop_state("news")
        self._track_tokens(result)
        if hasattr(result, "success") and result.success:
            await self.memory.store(
                content=_serialize_result(result),
                memory_type="observation",
                importance=0.8,
                metadata={"stage": "news_collection", "timestamp": datetime.now().isoformat()},
            )

    async def _on_sentiment_done(self, result):
        self._save_loop_state("sentiment")
        self._track_tokens(result)
        if hasattr(result, "success") and result.success:
            await self.memory.store(
                content=_serialize_result(result),
                memory_type="thought",
                importance=0.9,
                metadata={"stage": "sentiment_analysis"},
            )

    async def _on_factor_done(self, result):
        self._save_loop_state("factor")
        self._track_tokens(result)
        if hasattr(result, "success") and result.success:
            await self.memory.store(
                content=_serialize_result(result),
                memory_type="result",
                importance=0.9,
                metadata={"stage": "factor_research"},
            )

    async def _on_review_done(self, result):
        self._save_loop_state("review")
        self._track_tokens(result)
        if hasattr(result, "success") and result.success:
            await self.memory.store(
                content=_serialize_result(result),
                memory_type="result",
                importance=1.0,
                metadata={"stage": "daily_review"},
            )
            await self.hooks.trigger(
                event_type=HookEventType.TASK_END,
                source="daily_review",
                data={"report": result.results},
            )

    async def _on_error(self, error: Exception):
        logger.error("[QuantPipeline] Pipeline error: %s", error)
        await self.hooks.trigger(
            event_type=HookEventType.ERROR,
            source="quant_pipeline",
            data={"error": str(error)},
        )


async def create_quant_pipeline(
    tool_manager: Optional[Any] = None,
    mcp_config_path: str = "config/mcp.yaml",
    quant_config_path: str = "config/quant.yaml",
    intervals: Optional[Dict[str, int]] = None,
    mock: bool = False,
) -> QuantPipeline:
    """一站式工厂：加载配置 → 创建引擎 → 注册 Hook → 创建流水线。"""
    from .quant_engine import create_quant_engine
    from ..hooks.executor import HookSystem
    from ..hooks.builtin.dingtalk_hook import create_dingtalk_hook

    config = load_quant_config(quant_config_path)
    memory = MemorySystem()
    engine = await create_quant_engine(
        tool_manager=tool_manager, mcp_config_path=mcp_config_path,
        memory=memory, mock=mock,
    )

    hooks = HookSystem()
    dt_hook = create_dingtalk_hook(
        webhook_url=config.notifications.dingtalk.webhook,
        secret=config.notifications.dingtalk.secret,
    )
    if dt_hook:
        hooks.register_hook(dt_hook)
        logger.info("Registered DingTalk hook")

    state = PipelineState()
    return QuantPipeline(
        engine=engine, memory=memory, hooks=hooks,
        config=config, intervals=intervals, state=state,
    )
