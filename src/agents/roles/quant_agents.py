"""量化研究 Agent 角色

4 个专用 Agent，通过 ToolManager 调用 MCP 工具与 quant-platform 交互：
- NewsCollectorAgent  : 新闻/公告/资金流采集
- SentimentAgent      : 新闻情绪分析（引用历史情绪对比）
- FactorResearchAgent : 因子分析与回测（带重试 + 结果缓存）
- DailyReviewAgent    : 每日复盘报告（引用往期报告对比趋势）
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from ..base import Agent, AgentConfig, AgentResult

logger = logging.getLogger(__name__)


class _QuantAgentBase(Agent):
    """量化 Agent 基类 — 注入 ToolManager + Memory"""

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        llm_client: Optional[Any] = None,
        tool_manager: Optional[Any] = None,
        memory: Optional[Any] = None,
    ):
        super().__init__(config, llm_client=llm_client)
        self.tool_manager = tool_manager
        self.memory = memory  # MemorySystem 实例，用于检索历史

    async def _call_tool(self, tool_name: str, arguments: dict) -> Any:
        """通过 ToolManager 调用 MCP 工具，返回解析后的数据。"""
        if self.tool_manager is None:
            raise RuntimeError(
                f"{self.role} 未注入 tool_manager，无法调用工具 {tool_name}"
            )
        result = await self.tool_manager.execute_tool(
            tool_name, arguments, agent_role=self.role
        )
        if hasattr(result, "output"):
            output = result.output
        elif isinstance(result, dict):
            output = result.get("output", result)
        else:
            output = result

        # MCP 内容数组格式: [{"type": "text", "text": "<json>"}]
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, dict) and first.get("type") == "text":
                output = first.get("text", "")

        if isinstance(output, str):
            try:
                return json.loads(output)
            except (json.JSONDecodeError, TypeError):
                return output
        return output

    async def _call_tool_with_retry(
        self, tool_name: str, arguments: dict, max_retries: int = 2, timeout: float = 30.0
    ) -> Any:
        """带重试 + 超时的工具调用"""
        last_err = None
        for attempt in range(max_retries + 1):
            try:
                return await asyncio.wait_for(
                    self._call_tool(tool_name, arguments),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                last_err = TimeoutError(f"Tool {tool_name} timed out after {timeout}s")
                logger.warning("[%s] Tool %s timed out (attempt %d/%d)", self.role, tool_name, attempt + 1, max_retries + 1)
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "[%s] Tool %s failed (attempt %d/%d), retrying in %ds: %s",
                        self.role, tool_name, attempt + 1, max_retries + 1, wait, e,
                    )
                    await asyncio.sleep(wait)
        raise last_err

    async def _get_memory_context(self, query: str, top_k: int = 5) -> str:
        """从记忆系统检索相关历史，拼成上下文字符串。"""
        if self.memory is None:
            return ""
        try:
            items = await self.memory.retrieve(query, top_k=top_k)
            if not items:
                return ""
            lines = []
            for item in items:
                ts = item.timestamp.strftime("%m-%d %H:%M") if hasattr(item, "timestamp") else ""
                lines.append(f"[{ts}] {item.content[:200]}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug("[%s] Memory retrieval failed: %s", self.role, e)
            return ""

    async def _llm_analyze(self, prompt: str, fallback: Any, system: str = "") -> Any:
        """LLM 分析，失败时返回 fallback（原始数据）而非抛异常。"""
        try:
            return await self._complete_json(prompt, system=system or self.SYSTEM, default=fallback)
        except Exception as e:
            logger.warning("[%s] LLM failed, returning raw data: %s", self.role, e)
            return fallback


class NewsCollectorAgent(_QuantAgentBase):
    """新闻采集 Agent — 采集市场新闻、公告、资金流"""

    SYSTEM = (
        "你是专业的量化新闻分析师。\n"
        "你的任务是对采集到的市场新闻事件进行分类、去重和摘要。\n"
        "输出必须是 JSON 数组，每个元素包含：\n"
        "  - event_type: 事件类型（policy/industry/company/fund_flow/announcement）\n"
        "  - target: 相关标的（股票代码或板块名称）\n"
        "  - summary: 一句话摘要\n"
        "  - sentiment: 利好/利空/中性\n"
        "  - source: 来源\n"
        "  - time: 时间\n"
        "去重：与历史新闻对比，只输出新事件。"
    )

    def __init__(self, config=None, llm_client=None, tool_manager=None, memory=None):
        default_config = AgentConfig(
            role="news_collector",
            name="News Collector Agent",
            description="采集 A 股市场新闻、公告、北向资金、龙虎榜",
            capabilities=["news_collection", "market_data_fetch"],
            tools=["akshare_news", "quant_market_overview", "quant_market_movers"],
            temperature=0.2,
        )
        super().__init__(config or default_config, llm_client=llm_client, tool_manager=tool_manager, memory=memory)

    async def execute(self, task: str, **kwargs) -> AgentResult:
        try:
            ctx = kwargs.get("context", {})

            # 1. 采集新闻
            raw_news = await self._call_tool_with_retry("akshare_news", {"type": "news", "limit": 30})

            # 2. 采集北向资金
            try:
                northbound = await self._call_tool("akshare_news", {"type": "northbound", "limit": 10})
            except Exception:
                northbound = []

            # 3. 检索历史新闻用于去重
            history = await self._get_memory_context("市场新闻", top_k=3)

            # 4. LLM 分类和摘要
            prompt = (
                f"对以下新闻事件进行分类和情绪标注，输出 JSON 数组。\n"
                f"去重：与历史新闻对比，只输出新事件。\n\n"
                f"新闻数据：\n{json.dumps(raw_news, ensure_ascii=False, default=str)[:4000]}\n\n"
                f"北向资金：\n{json.dumps(northbound, ensure_ascii=False, default=str)[:1000]}"
            )
            if history:
                prompt += f"\n\n历史新闻（用于去重）：\n{history[:1000]}"

            analysis = await self._llm_analyze(prompt, fallback=raw_news)

            return AgentResult(
                success=True,
                output=analysis,
                metadata={"role": "news_collector", "raw_count": len(raw_news) if isinstance(raw_news, list) else 0, "llm_used": analysis is not raw_news},
            )
        except Exception as e:
            logger.exception("NewsCollectorAgent failed")
            return AgentResult(success=False, error=str(e))


class SentimentAgent(_QuantAgentBase):
    """情绪分析 Agent — 分析新闻情绪，引用历史对比"""

    SYSTEM = (
        "你是专业的市场情绪分析师。\n"
        "根据新闻事件和历史情绪数据，判断每个事件对市场/个股的影响方向和强度。\n"
        "输出 JSON 数组，每个元素包含：\n"
        "  - target: 标的\n"
        "  - sentiment_score: -1 到 1 的情绪评分\n"
        "  - impact_duration: 预计影响时长（short/medium/long）\n"
        "  - reasoning: 判断理由\n"
        "  - action_suggestion: 建议操作（buy/sell/hold/watch）\n"
        "参考历史情绪：如果近期同类标的已有情绪信号，评估是否延续或反转。"
    )

    def __init__(self, config=None, llm_client=None, tool_manager=None, memory=None):
        default_config = AgentConfig(
            role="sentiment",
            name="Sentiment Agent",
            description="分析市场新闻情绪，判断利好利空",
            capabilities=["sentiment_analysis", "event_classification"],
            tools=["akshare_news"],
            temperature=0.3,
        )
        super().__init__(config or default_config, llm_client=llm_client, tool_manager=tool_manager, memory=memory)

    async def execute(self, task: str, **kwargs) -> AgentResult:
        try:
            ctx = kwargs.get("context", {})
            artifacts = ctx.get("artifacts", {})

            # 读取前序 Agent 的新闻产出
            news_data = artifacts.get("news_collector", [])
            if not news_data:
                news_data = await self._call_tool_with_retry("akshare_news", {"type": "news", "limit": 20})

            # 检索历史情绪分析
            history = await self._get_memory_context("情绪分析 sentiment", top_k=5)

            prompt = (
                f"分析以下新闻事件的市场情绪影响。\n\n"
                f"新闻事件：\n{json.dumps(news_data, ensure_ascii=False, default=str)[:5000]}\n\n"
                f"任务：{task}"
            )
            if history:
                prompt += f"\n\n历史情绪分析（参考对比）：\n{history[:2000]}"

            analysis = await self._llm_analyze(prompt, fallback=news_data)

            return AgentResult(
                success=True,
                output=analysis,
                metadata={"role": "sentiment", "llm_used": analysis is not news_data},
            )
        except Exception as e:
            logger.exception("SentimentAgent failed")
            return AgentResult(success=False, error=str(e))


# ---- FactorResearchAgent 结果缓存 ----
_factor_cache: Dict[str, Any] = {}
_factor_cache_time: float = 0
_FACTOR_CACHE_TTL = 3600  # 1 小时


class FactorResearchAgent(_QuantAgentBase):
    """因子研究 Agent — 带重试 + 结果缓存"""

    SYSTEM = (
        "你是专业的量化因子研究员。\n"
        "你的任务是运行因子分析流水线，生成 IC 体检报告和选股结果。\n"
        "输出 JSON 对象，包含：\n"
        "  - factor_ic: 各因子 IC 值\n"
        "  - top_stocks: 选出的 top N 标的\n"
        "  - backtest_summary: 回测摘要\n"
        "  - recommendations: 操作建议\n"
        "参考历史因子研究：对比往期 IC 变化，标注趋势。"
    )

    def __init__(self, config=None, llm_client=None, tool_manager=None, memory=None):
        default_config = AgentConfig(
            role="factor_researcher",
            name="Factor Research Agent",
            description="运行因子分析、IC 体检、选股和回测",
            capabilities=["factor_analysis", "backtesting", "stock_screening"],
            tools=["quant_factor_run", "quant_screen", "quant_backtest"],
            temperature=0.3,
        )
        super().__init__(config or default_config, llm_client=llm_client, tool_manager=tool_manager, memory=memory)

    async def execute(self, task: str, **kwargs) -> AgentResult:
        global _factor_cache, _factor_cache_time
        try:
            ctx = kwargs.get("context", {})
            artifacts = ctx.get("artifacts", {})

            # 从流水线上下文读取因子配置
            factors_cfg = ctx.get("factors", {})
            factor_list = factors_cfg.get("default_factors", ["momentum_20d", "reversal_5d"])
            weighting = factors_cfg.get("default_weighting", "ic")
            holding = factors_cfg.get("holding_period", 20)

            # 缓存检查：同日内相同因子配置不重复计算
            cache_key = json.dumps({"factors": sorted(factor_list), "w": weighting, "h": holding})
            now = time.time()
            if cache_key in _factor_cache and (now - _factor_cache_time) < _FACTOR_CACHE_TTL:
                logger.info("[factor_researcher] Using cached result")
                factor_result = _factor_cache[cache_key]["factor"]
                screen_result = _factor_cache[cache_key]["screen"]
            else:
                # 1. 运行因子流水线（带重试）
                factor_result = await self._call_tool_with_retry("quant_factor_run", {
                    "market": "astock",
                    "factors": factor_list,
                    "weighting": weighting,
                    "holding_period": holding,
                })

                # 2. 横截面选股（带重试）
                screen_result = await self._call_tool_with_retry("quant_screen", {
                    "market": "astock",
                    "factors": {f: 1 for f in factor_list[:2]},
                    "top_n": 20,
                })

                # 写入缓存
                _factor_cache[cache_key] = {"factor": factor_result, "screen": screen_result}
                _factor_cache_time = now

            # 3. 检索历史因子研究
            history = await self._get_memory_context("因子研究 factor", top_k=3)

            # 4. LLM 综合分析
            sentiment_data = artifacts.get("sentiment", {})
            prompt = (
                f"综合因子分析结果和情绪数据，生成投资建议。\n\n"
                f"因子分析：\n{json.dumps(factor_result, ensure_ascii=False, default=str)[:3000]}\n\n"
                f"选股结果：\n{json.dumps(screen_result, ensure_ascii=False, default=str)[:2000]}\n\n"
                f"情绪数据：\n{json.dumps(sentiment_data, ensure_ascii=False, default=str)[:2000]}\n\n"
                f"任务：{task}"
            )
            if history:
                prompt += f"\n\n历史因子研究（对比趋势）：\n{history[:1500]}"

            fallback = {"factor_result": factor_result, "screen_result": screen_result, "analysis": "LLM 不可用，返回原始数据"}
            analysis = await self._llm_analyze(prompt, fallback=fallback)

            return AgentResult(
                success=True,
                output={
                    "factor_result": factor_result,
                    "screen_result": screen_result,
                    "analysis": analysis,
                },
                metadata={"role": "factor_researcher", "llm_used": analysis is not fallback},
            )
        except Exception as e:
            logger.exception("FactorResearchAgent failed")
            return AgentResult(success=False, error=str(e))


class DailyReviewAgent(_QuantAgentBase):
    """每日复盘 Agent — 引用往期报告对比趋势"""

    SYSTEM = (
        "你是专业的量化投资复盘分析师。\n"
        "根据今日行情、持仓、信号、情绪数据，生成结构化复盘报告。\n"
        "输出 Markdown 格式报告，包含：\n"
        "1. 大盘概况\n"
        "2. 模拟盘表现\n"
        "3. 今日信号回顾\n"
        "4. 情绪面总结\n"
        "5. 明日关注点\n"
        "参考往期报告：对比近期趋势，给出连续性分析。"
    )

    def __init__(self, config=None, llm_client=None, tool_manager=None, memory=None):
        default_config = AgentConfig(
            role="daily_review",
            name="Daily Review Agent",
            description="每日复盘，生成投资报告",
            capabilities=["report_generation", "portfolio_analysis"],
            tools=["quant_paper_status", "quant_paper_signals", "quant_market_overview"],
            temperature=0.4,
        )
        super().__init__(config or default_config, llm_client=llm_client, tool_manager=tool_manager, memory=memory)

    async def execute(self, task: str, **kwargs) -> AgentResult:
        try:
            ctx = kwargs.get("context", {})
            artifacts = ctx.get("artifacts", {})

            # 1. 获取模拟盘状态
            paper_status = await self._call_tool_with_retry("quant_paper_status", {})
            paper_signals = await self._call_tool_with_retry("quant_paper_signals", {"limit": 20})

            # 2. 获取大盘行情
            market_overview = await self._call_tool_with_retry("quant_market_overview", {})

            # 3. 读取前序产出
            factor_data = artifacts.get("factor_researcher", {})
            sentiment_data = artifacts.get("sentiment", {})

            # 4. 检索往期复盘报告
            history = await self._get_memory_context("复盘报告 daily_review", top_k=3)

            # 5. LLM 生成报告
            prompt = (
                f"生成今日量化复盘报告。\n\n"
                f"大盘行情：\n{json.dumps(market_overview, ensure_ascii=False, default=str)[:2000]}\n\n"
                f"模拟盘状态：\n{json.dumps(paper_status, ensure_ascii=False, default=str)[:2000]}\n\n"
                f"今日信号：\n{json.dumps(paper_signals, ensure_ascii=False, default=str)[:2000]}\n\n"
                f"因子研究：\n{json.dumps(factor_data, ensure_ascii=False, default=str)[:2000]}\n\n"
                f"情绪分析：\n{json.dumps(sentiment_data, ensure_ascii=False, default=str)[:2000]}\n\n"
                f"任务：{task}"
            )
            if history:
                prompt += f"\n\n往期复盘报告（对比趋势）：\n{history[:2000]}"

            try:
                report = await self._complete(prompt, system=self.SYSTEM)
            except Exception as e:
                logger.warning("[daily_review] LLM failed, generating raw report: %s", e)
                report = (
                    f"# 今日复盘（LLM 不可用，原始数据）\n\n"
                    f"## 大盘行情\n{json.dumps(market_overview, ensure_ascii=False, default=str)[:1000]}\n\n"
                    f"## 模拟盘状态\n{json.dumps(paper_status, ensure_ascii=False, default=str)[:1000]}\n\n"
                    f"## 今日信号\n{json.dumps(paper_signals, ensure_ascii=False, default=str)[:1000]}\n\n"
                    f"## 因子研究\n{json.dumps(factor_data, ensure_ascii=False, default=str)[:1000]}\n\n"
                    f"## 情绪分析\n{json.dumps(sentiment_data, ensure_ascii=False, default=str)[:1000]}"
                )

            return AgentResult(
                success=True,
                output=report,
                metadata={
                    "role": "daily_review",
                    "paper_status": paper_status,
                    "market_overview": market_overview,
                },
            )
        except Exception as e:
            logger.exception("DailyReviewAgent failed")
            return AgentResult(success=False, error=str(e))
