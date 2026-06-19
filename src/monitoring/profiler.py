"""性能分析器"""

import asyncio
import cProfile
import io
import logging
import pstats
import time
import tracemalloc
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FunctionProfile(BaseModel):
    """函数性能分析"""
    function_name: str
    module: str = ""
    calls: int = 0
    total_time: float = 0.0
    cumulative_time: float = 0.0
    per_call_time: float = 0.0
    filename: str = ""
    line_number: int = 0


class ProfileResult(BaseModel):
    """性能分析结果"""
    id: str
    name: str
    start_time: datetime
    end_time: datetime
    duration: float
    function_profiles: List[FunctionProfile] = Field(default_factory=list)
    memory_usage: Optional[Dict[str, Any]] = None
    cpu_usage: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Profiler:
    """性能分析器"""
    
    def __init__(self):
        self.profiles: List[ProfileResult] = []
        self.is_profiling = False
        self._profiler: Optional[cProfile.Profile] = None
        self._start_time: Optional[float] = None
    
    @contextmanager
    def profile(self, name: str = ""):
        """性能分析上下文管理器"""
        profile_id = f"profile_{len(self.profiles)}_{int(time.time())}"
        start_time = datetime.now()
        
        # 开始分析
        self._profiler = cProfile.Profile()
        self._profiler.enable()
        self._start_time = time.time()
        
        try:
            yield profile_id
        finally:
            # 停止分析
            self._profiler.disable()
            end_time = datetime.now()
            duration = time.time() - self._start_time
            
            # 处理结果
            result = self._process_profile(
                profile_id,
                name or profile_id,
                start_time,
                end_time,
                duration
            )
            
            self.profiles.append(result)
    
    def _process_profile(
        self,
        profile_id: str,
        name: str,
        start_time: datetime,
        end_time: datetime,
        duration: float
    ) -> ProfileResult:
        """处理分析结果"""
        function_profiles = []
        
        if self._profiler:
            # 获取统计信息
            stream = io.StringIO()
            stats = pstats.Stats(self._profiler, stream=stream)
            stats.sort_stats('cumulative')
            
            # 提取函数信息
            for func_stat in stats.stats.items():
                func, (cc, nc, tt, ct, callers) = func_stat
                
                filename, line_number, function_name = func
                
                function_profile = FunctionProfile(
                    function_name=function_name,
                    module=filename,
                    calls=nc,
                    total_time=tt,
                    cumulative_time=ct,
                    per_call_time=ct / nc if nc > 0 else 0,
                    filename=filename,
                    line_number=line_number
                )
                
                function_profiles.append(function_profile)
            
            # 按累计时间排序
            function_profiles.sort(key=lambda x: x.cumulative_time, reverse=True)
        
        return ProfileResult(
            id=profile_id,
            name=name,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            function_profiles=function_profiles[:50]  # 只保留前50个
        )
    
    async def profile_async(self, func: Callable, *args, **kwargs) -> Any:
        """异步函数性能分析"""
        profile_id = f"async_profile_{len(self.profiles)}_{int(time.time())}"
        start_time = datetime.now()
        
        # 开始分析
        self._profiler = cProfile.Profile()
        self._profiler.enable()
        self._start_time = time.time()
        
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            # 停止分析
            self._profiler.disable()
            end_time = datetime.now()
            duration = time.time() - self._start_time
            
            # 处理结果
            profile_result = self._process_profile(
                profile_id,
                func.__name__,
                start_time,
                end_time,
                duration
            )
            
            self.profiles.append(profile_result)
    
    def get_profile(self, profile_id: str) -> Optional[ProfileResult]:
        """获取分析结果"""
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        return None
    
    def list_profiles(self) -> List[Dict[str, Any]]:
        """列出所有分析结果"""
        return [
            {
                "id": p.id,
                "name": p.name,
                "duration": p.duration,
                "start_time": p.start_time.isoformat(),
                "function_count": len(p.function_profiles)
            }
            for p in self.profiles
        ]
    
    def get_slowest_functions(self, limit: int = 10) -> List[FunctionProfile]:
        """获取最慢的函数"""
        all_functions = []
        
        for profile in self.profiles:
            all_functions.extend(profile.function_profiles)
        
        # 按累计时间排序
        all_functions.sort(key=lambda x: x.cumulative_time, reverse=True)
        
        return all_functions[:limit]
    
    def clear_profiles(self) -> None:
        """清除所有分析结果"""
        self.profiles.clear()


class MemoryProfiler:
    """内存分析器"""
    
    def __init__(self):
        self.snapshots: List[Dict[str, Any]] = []
        self.is_profiling = False
    
    def start(self) -> None:
        """开始内存分析"""
        tracemalloc.start()
        self.is_profiling = True
        logger.info("Memory profiling started")
    
    def stop(self) -> None:
        """停止内存分析"""
        tracemalloc.stop()
        self.is_profiling = False
        logger.info("Memory profiling stopped")
    
    def take_snapshot(self, name: str = "") -> Dict[str, Any]:
        """获取内存快照"""
        if not self.is_profiling:
            return {"error": "Memory profiling not started"}
        
        snapshot = tracemalloc.take_snapshot()
        
        # 统计信息
        stats = snapshot.statistics('lineno')
        
        total_size = sum(stat.size for stat in stats)
        total_count = sum(stat.count for stat in stats)
        
        # 按大小排序
        top_stats = stats[:20]
        
        snapshot_data = {
            "name": name or f"snapshot_{len(self.snapshots)}",
            "timestamp": datetime.now().isoformat(),
            "total_size": total_size,
            "total_count": total_count,
            "top_allocations": [
                {
                    "filename": stat.traceback[0].filename,
                    "lineno": stat.traceback[0].lineno,
                    "size": stat.size,
                    "count": stat.count
                }
                for stat in top_stats
            ]
        }
        
        self.snapshots.append(snapshot_data)
        return snapshot_data
    
    def compare_snapshots(self, snapshot1_idx: int, snapshot2_idx: int) -> Dict[str, Any]:
        """比较两个快照"""
        if snapshot1_idx >= len(self.snapshots) or snapshot2_idx >= len(self.snapshots):
            return {"error": "Invalid snapshot index"}
        
        snapshot1 = self.snapshots[snapshot1_idx]
        snapshot2 = self.snapshots[snapshot2_idx]
        
        size_diff = snapshot2["total_size"] - snapshot1["total_size"]
        count_diff = snapshot2["total_count"] - snapshot1["total_count"]
        
        return {
            "snapshot1": snapshot1["name"],
            "snapshot2": snapshot2["name"],
            "size_difference": size_diff,
            "count_difference": count_diff,
            "size_change_percent": (size_diff / snapshot1["total_size"] * 100) if snapshot1["total_size"] > 0 else 0
        }
    
    def get_snapshots(self) -> List[Dict[str, Any]]:
        """获取所有快照"""
        return self.snapshots
    
    def clear_snapshots(self) -> None:
        """清除所有快照"""
        self.snapshots.clear()


class CPUProfiler:
    """CPU分析器"""
    
    def __init__(self):
        self.measurements: List[Dict[str, Any]] = []
        self.is_profiling = False
        self._start_time: Optional[float] = None
        self._start_cpu_time: Optional[float] = None
    
    def start(self) -> None:
        """开始CPU分析"""
        import time
        self._start_time = time.time()
        self._start_cpu_time = time.process_time()
        self.is_profiling = True
        logger.info("CPU profiling started")
    
    def stop(self) -> Dict[str, Any]:
        """停止CPU分析"""
        if not self.is_profiling:
            return {"error": "CPU profiling not started"}
        
        import time
        end_time = time.time()
        end_cpu_time = time.process_time()
        
        wall_time = end_time - self._start_time
        cpu_time = end_cpu_time - self._start_cpu_time
        
        cpu_usage = (cpu_time / wall_time * 100) if wall_time > 0 else 0
        
        measurement = {
            "timestamp": datetime.now().isoformat(),
            "wall_time": wall_time,
            "cpu_time": cpu_time,
            "cpu_usage_percent": cpu_usage
        }
        
        self.measurements.append(measurement)
        self.is_profiling = False
        
        logger.info(f"CPU profiling stopped. CPU usage: {cpu_usage:.2f}%")
        return measurement
    
    def get_measurements(self) -> List[Dict[str, Any]]:
        """获取所有测量结果"""
        return self.measurements
    
    def get_average_cpu_usage(self) -> float:
        """获取平均CPU使用率"""
        if not self.measurements:
            return 0.0
        
        total_usage = sum(m["cpu_usage_percent"] for m in self.measurements)
        return total_usage / len(self.measurements)
    
    def clear_measurements(self) -> None:
        """清除所有测量结果"""
        self.measurements.clear()


class ProfilerManager:
    """性能分析管理器"""
    
    def __init__(self):
        self.profiler = Profiler()
        self.memory_profiler = MemoryProfiler()
        self.cpu_profiler = CPUProfiler()
    
    async def profile_function(self, func: Callable, *args, **kwargs) -> Dict[str, Any]:
        """分析函数性能"""
        # 内存分析
        self.memory_profiler.start()
        snapshot_before = self.memory_profiler.take_snapshot("before")
        
        # CPU分析
        self.cpu_profiler.start()
        
        # 函数分析
        result = await self.profiler.profile_async(func, *args, **kwargs)
        
        # 停止分析
        cpu_measurement = self.cpu_profiler.stop()
        snapshot_after = self.memory_profiler.take_snapshot("after")
        self.memory_profiler.stop()
        
        # 内存比较
        memory_comparison = self.memory_profiler.compare_snapshots(0, 1)
        
        return {
            "result": result,
            "cpu": cpu_measurement,
            "memory": {
                "before": snapshot_before,
                "after": snapshot_after,
                "comparison": memory_comparison
            },
            "profile": self.profiler.list_profiles()[-1] if self.profiler.list_profiles() else None
        }
    
    def get_report(self) -> Dict[str, Any]:
        """获取性能报告"""
        return {
            "profiles": self.profiler.list_profiles(),
            "memory_snapshots": self.memory_profiler.get_snapshots(),
            "cpu_measurements": self.cpu_profiler.get_measurements(),
            "slowest_functions": [
                {
                    "name": f.function_name,
                    "module": f.module,
                    "cumulative_time": f.cumulative_time,
                    "calls": f.calls
                }
                for f in self.profiler.get_slowest_functions(10)
            ],
            "average_cpu_usage": self.cpu_profiler.get_average_cpu_usage()
        }
    
    def clear_all(self) -> None:
        """清除所有数据"""
        self.profiler.clear_profiles()
        self.memory_profiler.clear_snapshots()
        self.cpu_profiler.clear_measurements()


def create_profiler_manager() -> ProfilerManager:
    """创建性能分析管理器工厂函数"""
    return ProfilerManager()
