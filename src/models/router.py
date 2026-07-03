"""多模型路由器 - 智能选择最优模型"""

import logging
import time
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """任务类型"""
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    DOCUMENTATION = "documentation"
    ANALYSIS = "analysis"
    CHAT = "chat"
    TRANSLATION = "translation"
    MATH = "math"
    CREATIVE = "creative"


class ModelTier(str, Enum):
    """模型等级"""
    ECONOMY = "economy"      # 经济型 - 简单任务
    STANDARD = "standard"    # 标准型 - 一般任务
    PREMIUM = "premium"      # 高级型 - 复杂任务
    ULTRA = "ultra"          # 超级型 - 最高质量


class ModelConfig(BaseModel):
    """模型配置"""
    id: str
    name: str
    provider: str
    tier: ModelTier
    cost_per_1k_input: float = 0.0  # 每 1000 输入 token 成本
    cost_per_1k_output: float = 0.0  # 每 1000 输出 token 成本
    max_tokens: int = 4096
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    speed_rating: float = 1.0  # 1-10，越高越快
    quality_rating: float = 1.0  # 1-10，越高越好
    capabilities: List[str] = Field(default_factory=list)


class ModelUsage(BaseModel):
    """模型使用记录"""
    model_id: str
    task_type: TaskType
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    duration: float = 0.0
    success: bool = True
    timestamp: float = Field(default_factory=time.time)


class ModelRouter:
    """模型路由器"""
    
    def __init__(self):
        self.models: Dict[str, ModelConfig] = {}
        self.usage_history: List[ModelUsage] = []
        self.task_model_performance: Dict[str, Dict[str, float]] = {}
        
        # 注册默认模型
        self._register_default_models()
    
    def _register_default_models(self):
        """注册默认模型"""
        default_models = [
            ModelConfig(
                id="mimo-v2.5",
                name="MiMo v2.5",
                provider="Xiaomi",
                tier=ModelTier.STANDARD,
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
                speed_rating=8,
                quality_rating=7,
                capabilities=["chat", "code", "analysis"]
            ),
            ModelConfig(
                id="gpt-4o-mini",
                name="GPT-4o Mini",
                provider="OpenAI",
                tier=ModelTier.ECONOMY,
                cost_per_1k_input=0.00015,
                cost_per_1k_output=0.0006,
                speed_rating=9,
                quality_rating=6,
                capabilities=["chat", "code"]
            ),
            ModelConfig(
                id="gpt-4o",
                name="GPT-4o",
                provider="OpenAI",
                tier=ModelTier.PREMIUM,
                cost_per_1k_input=0.005,
                cost_per_1k_output=0.015,
                speed_rating=6,
                quality_rating=9,
                supports_tools=True,
                supports_vision=True,
                capabilities=["chat", "code", "analysis", "vision"]
            ),
            ModelConfig(
                id="claude-3.5-sonnet",
                name="Claude 3.5 Sonnet",
                provider="Anthropic",
                tier=ModelTier.PREMIUM,
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
                speed_rating=7,
                quality_rating=9,
                supports_tools=True,
                capabilities=["chat", "code", "analysis"]
            ),
            ModelConfig(
                id="deepseek-chat",
                name="DeepSeek Chat",
                provider="DeepSeek",
                tier=ModelTier.ECONOMY,
                cost_per_1k_input=0.00014,
                cost_per_1k_output=0.00028,
                speed_rating=8,
                quality_rating=7,
                capabilities=["chat", "code"]
            ),
        ]
        
        for model in default_models:
            self.models[model.id] = model
    
    def register_model(self, model: ModelConfig):
        """注册模型"""
        self.models[model.id] = model
    
    def select_model(
        self,
        task_type: TaskType,
        tier: ModelTier = None,
        preferred_provider: str = None,
        requires_tools: bool = False,
        requires_vision: bool = False,
        max_cost: float = None
    ) -> ModelConfig:
        """选择最优模型"""
        candidates = list(self.models.values())
        
        # 过滤条件
        if tier:
            candidates = [m for m in candidates if m.tier == tier]
        
        if preferred_provider:
            candidates = [m for m in candidates if m.provider == preferred_provider]
        
        if requires_tools:
            candidates = [m for m in candidates if m.supports_tools]
        
        if requires_vision:
            candidates = [m for m in candidates if m.supports_vision]
        
        if max_cost:
            candidates = [m for m in candidates 
                         if m.cost_per_1k_input <= max_cost]
        
        if not candidates:
            # 回退到默认模型
            return self.models.get("mimo-v2.5") or list(self.models.values())[0]
        
        # 计算综合分数
        scored_candidates = []
        for model in candidates:
            score = self._calculate_score(model, task_type)
            scored_candidates.append((model, score))
        
        # 按分数排序
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        
        return scored_candidates[0][0]
    
    def _calculate_score(self, model: ModelConfig, task_type: TaskType) -> float:
        """计算模型分数"""
        # 基础分数
        base_score = (model.quality_rating * 0.4 + model.speed_rating * 0.3)
        
        # 确保 task_type 是 TaskType 枚举
        if isinstance(task_type, str):
            try:
                task_type = TaskType(task_type)
            except ValueError:
                task_type = TaskType.CHAT
        
        # 任务匹配加分
        task_capability_map = {
            TaskType.CODE_GENERATION: "code",
            TaskType.CODE_REVIEW: "code",
            TaskType.DOCUMENTATION: "chat",
            TaskType.ANALYSIS: "analysis",
            TaskType.CHAT: "chat",
        }
        
        required_cap = task_capability_map.get(task_type, "chat")
        if required_cap in model.capabilities:
            base_score += 0.2
        
        # 成本惩罚（越贵分数越低）
        cost_penalty = model.cost_per_1k_output * 100
        base_score -= cost_penalty
        
        # 历史表现加成
        perf_key = f"{model.id}:{task_type.value}"
        if perf_key in self.task_model_performance:
            avg_perf = sum(self.task_model_performance[perf_key].values()) / len(self.task_model_performance[perf_key])
            base_score += avg_perf * 0.1
        
        return max(0, base_score)
    
    def record_usage(self, usage: ModelUsage):
        """记录使用"""
        self.usage_history.append(usage)
        
        # 更新性能记录
        perf_key = f"{usage.model_id}:{usage.task_type.value}"
        if perf_key not in self.task_model_performance:
            self.task_model_performance[perf_key] = {}
        
        # 计算性能分数（0-1）
        if usage.success:
            score = 1.0
            if usage.duration > 0:
                # 速度越快分数越高
                speed_score = min(1.0, 10.0 / usage.duration)
                score = (score + speed_score) / 2
            self.task_model_performance[perf_key][time.time()] = score
    
    def get_model_stats(self) -> Dict[str, Any]:
        """获取模型统计"""
        stats = {}
        
        for model_id in self.models:
            usage = [u for u in self.usage_history if u.model_id == model_id]
            
            stats[model_id] = {
                "total_calls": len(usage),
                "total_input_tokens": sum(u.input_tokens for u in usage),
                "total_output_tokens": sum(u.output_tokens for u in usage),
                "total_cost": sum(u.cost for u in usage),
                "avg_duration": sum(u.duration for u in usage) / len(usage) if usage else 0,
                "success_rate": sum(1 for u in usage if u.success) / len(usage) if usage else 0
            }
        
        return stats
    
    def get_recommendations(self, task_description: str) -> List[Dict[str, Any]]:
        """获取模型推荐"""
        # 分析任务
        task_type = self._classify_task(task_description)
        
        # 获取推荐
        recommendations = []
        for tier in ModelTier:
            model = self.select_model(task_type, tier=tier)
            if model:
                recommendations.append({
                    "model": model.name,
                    "tier": tier.value,
                    "provider": model.provider,
                    "estimated_cost": self._estimate_cost(model, task_description),
                    "quality": model.quality_rating,
                    "speed": model.speed_rating
                })
        
        return recommendations
    
    def _classify_task(self, description: str) -> TaskType:
        """分类任务"""
        desc_lower = description.lower()
        
        if any(kw in desc_lower for kw in ["code", "implement", "function", "class"]):
            return TaskType.CODE_GENERATION
        elif any(kw in desc_lower for kw in ["review", "check", "audit"]):
            return TaskType.CODE_REVIEW
        elif any(kw in desc_lower for kw in ["document", "readme", "explain"]):
            return TaskType.DOCUMENTATION
        elif any(kw in desc_lower for kw in ["analyze", "research", "investigate"]):
            return TaskType.ANALYSIS
        else:
            return TaskType.CHAT
    
    def _estimate_cost(self, model: ModelConfig, description: str) -> float:
        """估算成本"""
        # 简单估算：每 100 字约 150 tokens
        word_count = len(description.split())
        estimated_tokens = word_count * 1.5
        
        input_cost = (estimated_tokens / 1000) * model.cost_per_1k_input
        output_cost = (estimated_tokens * 2 / 1000) * model.cost_per_1k_output  # 假设输出是输入的 2 倍
        
        return input_cost + output_cost


class CostTracker:
    """成本追踪器"""
    
    def __init__(self):
        self.entries: List[Dict[str, Any]] = []
        self.budgets: Dict[str, float] = {}
        self.alerts: List[Dict[str, Any]] = []
    
    def track(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        task: str = "",
        agent: str = ""
    ):
        """记录成本"""
        entry = {
            "timestamp": time.time(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "task": task,
            "agent": agent
        }
        self.entries.append(entry)
        
        # 检查预算
        self._check_budget(agent, cost)
    
    def set_budget(self, agent: str, budget: float):
        """设置预算"""
        self.budgets[agent] = budget
    
    def _check_budget(self, agent: str, cost: float):
        """检查预算"""
        if agent in self.budgets:
            total_cost = sum(e["cost"] for e in self.entries if e["agent"] == agent)
            if total_cost > self.budgets[agent]:
                self.alerts.append({
                    "type": "budget_exceeded",
                    "agent": agent,
                    "budget": self.budgets[agent],
                    "actual": total_cost,
                    "timestamp": time.time()
                })
    
    def get_report(self, period: str = "daily") -> Dict[str, Any]:
        """获取报告"""
        now = time.time()
        
        if period == "daily":
            cutoff = now - 86400
        elif period == "weekly":
            cutoff = now - 604800
        else:
            cutoff = 0
        
        period_entries = [e for e in self.entries if e["timestamp"] > cutoff]
        
        total_cost = sum(e["cost"] for e in period_entries)
        total_input = sum(e["input_tokens"] for e in period_entries)
        total_output = sum(e["output_tokens"] for e in period_entries)
        
        # 按模型分组
        by_model = {}
        for entry in period_entries:
            model = entry["model"]
            if model not in by_model:
                by_model[model] = {"cost": 0, "calls": 0}
            by_model[model]["cost"] += entry["cost"]
            by_model[model]["calls"] += 1
        
        # 按 Agent 分组
        by_agent = {}
        for entry in period_entries:
            agent = entry.get("agent", "unknown")
            if agent not in by_agent:
                by_agent[agent] = {"cost": 0, "calls": 0}
            by_agent[agent]["cost"] += entry["cost"]
            by_agent[agent]["calls"] += 1
        
        return {
            "period": period,
            "total_cost": total_cost,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_calls": len(period_entries),
            "by_model": by_model,
            "by_agent": by_agent,
            "alerts": self.alerts[-10:]
        }
    
    def get_optimization_suggestions(self) -> List[str]:
        """获取优化建议"""
        suggestions = []
        
        # 分析模型使用
        model_usage = {}
        for entry in self.entries:
            model = entry["model"]
            if model not in model_usage:
                model_usage[model] = {"cost": 0, "calls": 0, "tasks": set()}
            model_usage[model]["cost"] += entry["cost"]
            model_usage[model]["calls"] += 1
            model_usage[model]["tasks"].add(entry.get("task", ""))
        
        # 检查是否可以使用更便宜的模型
        for model, usage in model_usage.items():
            if usage["cost"] > 10:  # 成本超过 $10
                suggestions.append(
                    f"Consider using a cheaper model for {model} "
                    f"(current cost: ${usage['cost']:.2f})"
                )
        
        # 检查重复调用
        task_calls = {}
        for entry in self.entries:
            task = entry.get("task", "")
            if task:
                task_calls[task] = task_calls.get(task, 0) + 1
        
        for task, count in task_calls.items():
            if count > 5:
                suggestions.append(
                    f"Task '{task[:50]}' has been called {count} times. "
                    f"Consider caching results."
                )
        
        return suggestions
