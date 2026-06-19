"""Loop 控制器 - 实现定期检查和持续改进

核心思想：
- 定期检查任务状态
- 自动重试失败的任务
- 持续改进和优化
"""

import asyncio
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LoopStatus(str, Enum):
    """循环状态"""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class LoopConfig(BaseModel):
    """循环配置"""
    interval: float = 60.0  # 间隔秒数
    max_iterations: int = 100  # 最大迭代次数
    max_duration: float = 3600.0  # 最大持续时间秒数
    auto_restart: bool = False  # 自动重启
    jitter: float = 0.1  # 抖动系数 0-1
    backoff_factor: float = 1.5  # 退避因子
    max_backoff: float = 300.0  # 最大退避时间


class LoopIteration(BaseModel):
    """循环迭代记录"""
    iteration: int
    start_time: datetime
    end_time: Optional[datetime] = None
    success: bool = False
    result: Any = None
    error: Optional[str] = None
    duration: float = 0.0


class LoopController:
    """Loop 控制器
    
    实现定期检查和持续改进的机制
    类似于 Claude Code 的 /loop 功能
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[LoopConfig] = None,
        check_func: Optional[Callable] = None,
        on_success: Optional[Callable] = None,
        on_failure: Optional[Callable] = None,
        on_complete: Optional[Callable] = None
    ):
        self.name = name
        self.config = config or LoopConfig()
        self.check_func = check_func
        self.on_success = on_success
        self.on_failure = on_failure
        self.on_complete = on_complete
        
        self.status = LoopStatus.IDLE
        self.iterations: List[LoopIteration] = []
        self.current_iteration = 0
        self.start_time: Optional[datetime] = None
        self.last_check_time: Optional[datetime] = None
        self.next_check_time: Optional[datetime] = None
        
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # 初始状态：未暂停
    
    async def start(self) -> None:
        """启动循环"""
        if self.status == LoopStatus.RUNNING:
            logger.warning(f"Loop {self.name} is already running")
            return
        
        self.status = LoopStatus.RUNNING
        self.start_time = datetime.now()
        self._stop_event.clear()
        self._pause_event.set()
        
        logger.info(f"Starting loop {self.name}")
        
        # 启动循环任务
        self._task = asyncio.create_task(self._run_loop())
    
    async def stop(self) -> None:
        """停止循环"""
        if self.status != LoopStatus.RUNNING:
            return
        
        logger.info(f"Stopping loop {self.name}")
        self._stop_event.set()
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        self.status = LoopStatus.STOPPED
        
        # 调用完成回调
        if self.on_complete:
            try:
                await self.on_complete(self.iterations)
            except Exception as e:
                logger.error(f"Complete callback failed: {e}")
    
    async def pause(self) -> None:
        """暂停循环"""
        if self.status != LoopStatus.RUNNING:
            return
        
        logger.info(f"Pausing loop {self.name}")
        self._pause_event.clear()
        self.status = LoopStatus.PAUSED
    
    async def resume(self) -> None:
        """恢复循环"""
        if self.status != LoopStatus.PAUSED:
            return
        
        logger.info(f"Resuming loop {self.name}")
        self._pause_event.set()
        self.status = LoopStatus.RUNNING
    
    async def _run_loop(self) -> None:
        """运行循环"""
        try:
            while not self._stop_event.is_set():
                # 检查是否达到最大迭代次数
                if self.current_iteration >= self.config.max_iterations:
                    logger.info(f"Loop {self.name} reached max iterations")
                    break
                
                # 检查是否达到最大持续时间
                if self.start_time:
                    elapsed = (datetime.now() - self.start_time).total_seconds()
                    if elapsed >= self.config.max_duration:
                        logger.info(f"Loop {self.name} reached max duration")
                        break
                
                # 等待暂停恢复
                await self._pause_event.wait()
                
                # 计算下次检查时间（带抖动）
                wait_time = self._calculate_wait_time()
                
                # 等待下次检查
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=wait_time
                    )
                    # 如果到这里说明收到了停止信号
                    break
                except asyncio.TimeoutError:
                    # 超时，继续执行检查
                    pass
                
                # 执行检查
                await self._execute_iteration()
                
        except asyncio.CancelledError:
            logger.info(f"Loop {self.name} cancelled")
        except Exception as e:
            logger.error(f"Loop {self.name} failed: {e}")
            self.status = LoopStatus.ERROR
        finally:
            if self.status == LoopStatus.RUNNING:
                self.status = LoopStatus.STOPPED
    
    def _calculate_wait_time(self) -> float:
        """计算等待时间（带抖动和退避）"""
        base_interval = self.config.interval
        
        # 如果有失败的迭代，应用退避策略
        recent_failures = self._count_recent_failures()
        if recent_failures > 0:
            backoff = min(
                base_interval * (self.config.backoff_factor ** recent_failures),
                self.config.max_backoff
            )
            base_interval = backoff
        
        # 添加抖动
        import random
        jitter = base_interval * self.config.jitter * (2 * random.random() - 1)
        
        return max(1.0, base_interval + jitter)
    
    def _count_recent_failures(self) -> int:
        """计算最近的失败次数"""
        if not self.iterations:
            return 0
        
        # 检查最近5次迭代
        recent = self.iterations[-5:]
        return sum(1 for i in recent if not i.success)
    
    async def _execute_iteration(self) -> None:
        """执行单次迭代"""
        self.current_iteration += 1
        iteration = LoopIteration(
            iteration=self.current_iteration,
            start_time=datetime.now()
        )
        
        try:
            logger.info(f"Loop {self.name} iteration {self.current_iteration}")
            
            # 执行检查函数
            if self.check_func:
                if asyncio.iscoroutinefunction(self.check_func):
                    result = await self.check_func()
                else:
                    result = self.check_func()
                
                iteration.success = True
                iteration.result = result
                
                # 调用成功回调
                if self.on_success:
                    try:
                        if asyncio.iscoroutinefunction(self.on_success):
                            await self.on_success(result)
                        else:
                            self.on_success(result)
                    except Exception as e:
                        logger.error(f"Success callback failed: {e}")
                
                # 检查是否应该停止
                if self._should_stop(result):
                    logger.info(f"Loop {self.name} check indicates completion")
                    self._stop_event.set()
                
            else:
                iteration.success = True
                iteration.result = "No check function defined"
            
        except Exception as e:
            logger.error(f"Loop {self.name} iteration {self.current_iteration} failed: {e}")
            iteration.success = False
            iteration.error = str(e)
            
            # 调用失败回调
            if self.on_failure:
                try:
                    if asyncio.iscoroutinefunction(self.on_failure):
                        await self.on_failure(e)
                    else:
                        self.on_failure(e)
                except Exception as callback_error:
                    logger.error(f"Failure callback failed: {callback_error}")
        
        finally:
            iteration.end_time = datetime.now()
            iteration.duration = (iteration.end_time - iteration.start_time).total_seconds()
            self.iterations.append(iteration)
            self.last_check_time = iteration.end_time
    
    def _should_stop(self, result: Any) -> bool:
        """检查是否应该停止循环"""
        # 子类可以覆盖此方法来实现自定义停止逻辑
        if isinstance(result, dict):
            # 如果结果中包含停止信号
            if result.get("stop") or result.get("complete") or result.get("done"):
                return True
            
            # 如果任务成功完成
            if result.get("success") and not result.get("continue"):
                return True
        
        return False
    
    def get_status(self) -> Dict[str, Any]:
        """获取循环状态"""
        return {
            "name": self.name,
            "status": self.status.value,
            "current_iteration": self.current_iteration,
            "max_iterations": self.config.max_iterations,
            "total_iterations": len(self.iterations),
            "successful_iterations": sum(1 for i in self.iterations if i.success),
            "failed_iterations": sum(1 for i in self.iterations if not i.success),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "last_check_time": self.last_check_time.isoformat() if self.last_check_time else None,
            "uptime": (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        }
    
    def get_iterations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取迭代历史"""
        recent = self.iterations[-limit:]
        return [
            {
                "iteration": i.iteration,
                "start_time": i.start_time.isoformat(),
                "end_time": i.end_time.isoformat() if i.end_time else None,
                "success": i.success,
                "duration": i.duration,
                "error": i.error
            }
            for i in recent
        ]


class PollingLoopController(LoopController):
    """轮询循环控制器
    
    用于定期轮询检查状态
    """
    
    def __init__(
        self,
        name: str,
        poll_func: Callable,
        condition_func: Optional[Callable] = None,
        **kwargs
    ):
        super().__init__(name, **kwargs)
        self.poll_func = poll_func
        self.condition_func = condition_func
        self.last_poll_result: Any = None
    
    async def _execute_iteration(self) -> None:
        """执行轮询"""
        self.current_iteration += 1
        iteration = LoopIteration(
            iteration=self.current_iteration,
            start_time=datetime.now()
        )
        
        try:
            # 执行轮询函数
            if asyncio.iscoroutinefunction(self.poll_func):
                result = await self.poll_func()
            else:
                result = self.poll_func()
            
            self.last_poll_result = result
            iteration.success = True
            iteration.result = result
            
            # 检查条件
            if self.condition_func:
                if asyncio.iscoroutinefunction(self.condition_func):
                    condition_met = await self.condition_func(result)
                else:
                    condition_met = self.condition_func(result)
                
                if condition_met:
                    logger.info(f"Polling loop {self.name} condition met")
                    self._stop_event.set()
            
        except Exception as e:
            logger.error(f"Polling loop {self.name} failed: {e}")
            iteration.success = False
            iteration.error = str(e)
        
        finally:
            iteration.end_time = datetime.now()
            iteration.duration = (iteration.end_time - iteration.start_time).total_seconds()
            self.iterations.append(iteration)
            self.last_check_time = iteration.end_time


class ContinuousImprovementLoop(LoopController):
    """持续改进循环
    
    用于持续改进和优化任务
    """
    
    def __init__(
        self,
        name: str,
        task_func: Callable,
        evaluate_func: Callable,
        improve_func: Optional[Callable] = None,
        target_score: float = 0.9,
        **kwargs
    ):
        super().__init__(name, **kwargs)
        self.task_func = task_func
        self.evaluate_func = evaluate_func
        self.improve_func = improve_func
        self.target_score = target_score
        
        self.best_result: Any = None
        self.best_score: float = 0.0
        self.improvement_history: List[Dict[str, Any]] = []
    
    async def _execute_iteration(self) -> None:
        """执行改进迭代"""
        self.current_iteration += 1
        iteration = LoopIteration(
            iteration=self.current_iteration,
            start_time=datetime.now()
        )
        
        try:
            # 执行任务
            if asyncio.iscoroutinefunction(self.task_func):
                result = await self.task_func()
            else:
                result = self.task_func()
            
            # 评估结果
            if asyncio.iscoroutinefunction(self.evaluate_func):
                score = await self.evaluate_func(result)
            else:
                score = self.evaluate_func(result)
            
            iteration.success = True
            iteration.result = {"result": result, "score": score}
            
            # 记录改进历史
            self.improvement_history.append({
                "iteration": self.current_iteration,
                "score": score,
                "is_best": score > self.best_score
            })
            
            # 更新最佳结果
            if score > self.best_score:
                self.best_score = score
                self.best_result = result
                logger.info(f"New best score: {score}")
            
            # 检查是否达到目标
            if score >= self.target_score:
                logger.info(f"Reached target score: {score}")
                self._stop_event.set()
                return
            
            # 如果有改进函数，尝试改进
            if self.improve_func and score < self.target_score:
                try:
                    if asyncio.iscoroutinefunction(self.improve_func):
                        await self.improve_func(result, score)
                    else:
                        self.improve_func(result, score)
                except Exception as e:
                    logger.error(f"Improve function failed: {e}")
            
        except Exception as e:
            logger.error(f"Improvement loop {self.name} failed: {e}")
            iteration.success = False
            iteration.error = str(e)
        
        finally:
            iteration.end_time = datetime.now()
            iteration.duration = (iteration.end_time - iteration.start_time).total_seconds()
            self.iterations.append(iteration)
            self.last_check_time = iteration.end_time
    
    def get_improvement_summary(self) -> Dict[str, Any]:
        """获取改进总结"""
        if not self.improvement_history:
            return {
                "total_iterations": 0,
                "best_score": self.best_score,
                "target_score": self.target_score
            }
        
        scores = [h["score"] for h in self.improvement_history]
        
        return {
            "total_iterations": len(self.improvement_history),
            "best_score": self.best_score,
            "target_score": self.target_score,
            "average_score": sum(scores) / len(scores),
            "score_trend": scores[-10:],  # 最近10个分数
            "improvement_rate": self._calculate_improvement_rate()
        }
    
    def _calculate_improvement_rate(self) -> float:
        """计算改进率"""
        if len(self.improvement_history) < 2:
            return 0.0
        
        first_score = self.improvement_history[0]["score"]
        last_score = self.improvement_history[-1]["score"]
        
        if first_score == 0:
            return 0.0
        
        return (last_score - first_score) / first_score


def create_loop_controller(
    loop_type: str = "basic",
    **kwargs
) -> LoopController:
    """创建循环控制器工厂函数"""
    if loop_type == "polling":
        return PollingLoopController(**kwargs)
    elif loop_type == "improvement":
        return ContinuousImprovementLoop(**kwargs)
    else:
        return LoopController(**kwargs)
