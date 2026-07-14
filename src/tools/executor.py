"""工具执行器"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from .mcp_client import MCPClient, MCPToolCall
from .registry import Tool, ToolRegistry
from .permission import ToolPermissionManager

logger = logging.getLogger(__name__)


class ToolExecutionResult(BaseModel):
    """工具执行结果"""
    success: bool
    tool_name: str
    output: Any = None
    error: Optional[str] = None
    execution_time: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolExecutor:
    """工具执行器"""
    
    def __init__(
        self,
        tool_registry: ToolRegistry,
        permission_manager: Optional[ToolPermissionManager] = None,
        confirm: Optional[Any] = None
    ):
        self.tool_registry = tool_registry
        self.permission_manager = permission_manager or ToolPermissionManager()
        self.execution_history: List[ToolExecutionResult] = []
        self.mcp_clients: Dict[str, MCPClient] = {}  # server_name -> client
        # ASK 规则的确认门：async (message) -> bool。**缺省 fail-closed**（ASK 判定为拒绝，
        # 且如实说明原因）——绝不能因为没接确认门就把"要问人"降级成"直接放行"。
        self.confirm = confirm
    
    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        agent_role: str = "",
        timeout: float = 30.0
    ) -> ToolExecutionResult:
        """执行工具"""
        start_time = time.time()
        
        # 检查权限
        permission_result = self.permission_manager.check_permission(
            tool_name, agent_role, arguments, self.tool_registry
        )
        
        # ASK 规则：**真的去问人**。此前这段是死的——check_permission 对 ASK 返回
        # allowed=False + requires_confirmation=True，而上面的 `if not allowed` 先 return 了，
        # 下面那句 logger.warning 永远执行不到。于是 config/mcp.yaml 里写的 `action: ask`
        # 规则（filesystem-write / git-operations，以及两条默认 ASK 规则）**全都是静默硬拒**，
        # 从来不"问"——配置在撒谎。
        # 修法**只往安全一侧靠**：有确认门就问、同意才放行；**没有确认门仍然拒绝**（fail-closed），
        # 只是把理由说清楚，绝不把"要问人"降级成"直接放行"。
        if permission_result.requires_confirmation:
            if self.confirm is None:
                return ToolExecutionResult(
                    success=False,
                    tool_name=tool_name,
                    error=(f"Permission denied: 规则要求人工确认（ask），但当前入口没有接确认门 "
                           f"—— {permission_result.reason}"),
                    execution_time=time.time() - start_time
                )
            try:
                approved = bool(await self.confirm(
                    f"MCP 工具 `{tool_name}` 需要确认（权限规则 action=ask）\n  参数：{arguments}"))
            except Exception as e:  # noqa: BLE001 —— 确认门炸了 → 拒绝（安全优先）
                approved = False
                logger.warning(f"Confirm gate failed for {tool_name}: {e}")
            if not approved:
                return ToolExecutionResult(
                    success=False,
                    tool_name=tool_name,
                    error=f"用户取消了 MCP 工具 {tool_name} 的调用",
                    execution_time=time.time() - start_time
                )
        elif not permission_result.allowed:
            return ToolExecutionResult(
                success=False,
                tool_name=tool_name,
                error=f"Permission denied: {permission_result.reason}",
                execution_time=time.time() - start_time
            )

        # 获取工具
        tool = self.tool_registry.get(tool_name)
        if not tool:
            return ToolExecutionResult(
                success=False,
                tool_name=tool_name,
                error=f"Tool {tool_name} not found",
                execution_time=time.time() - start_time
            )
        
        # 执行工具
        try:
            if tool.server_name and tool.server_name in self.mcp_clients:
                # 通过MCP服务器执行
                result = await self._execute_via_mcp(tool, arguments, timeout)
            else:
                # 本地执行
                result = await self._execute_locally(tool, arguments, timeout)
            
            result.execution_time = time.time() - start_time
            
            # 记录执行历史
            self.execution_history.append(result)
            
            return result
            
        except asyncio.TimeoutError:
            return ToolExecutionResult(
                success=False,
                tool_name=tool_name,
                error=f"Tool execution timed out after {timeout} seconds",
                execution_time=time.time() - start_time
            )
        except Exception as e:
            return ToolExecutionResult(
                success=False,
                tool_name=tool_name,
                error=str(e),
                execution_time=time.time() - start_time
            )
    
    async def _execute_via_mcp(
        self,
        tool: Tool,
        arguments: Dict[str, Any],
        timeout: float
    ) -> ToolExecutionResult:
        """通过MCP服务器执行工具"""
        client = self.mcp_clients.get(tool.server_name)
        if not client:
            return ToolExecutionResult(
                success=False,
                tool_name=tool.name,
                error=f"MCP server {tool.server_name} not connected"
            )
        
        # 创建工具调用
        tool_call = MCPToolCall(
            tool_name=tool.name,
            arguments=arguments
        )
        
        # 执行调用
        result = await asyncio.wait_for(
            client.call_tool(tool_call),
            timeout=timeout
        )
        
        return ToolExecutionResult(
            success=result.success,
            tool_name=tool.name,
            output=result.output,
            error=result.error,
            execution_time=result.execution_time,
            metadata={
                "server_name": tool.server_name,
                "transport": client.transport.value
            }
        )
    
    async def _execute_locally(
        self,
        tool: Tool,
        arguments: Dict[str, Any],
        timeout: float
    ) -> ToolExecutionResult:
        """本地执行工具"""
        # 这里可以实现本地工具执行逻辑
        # 目前返回一个模拟结果
        
        logger.info(f"Executing tool locally: {tool.name}")
        
        # 模拟执行
        await asyncio.sleep(0.1)
        
        return ToolExecutionResult(
            success=True,
            tool_name=tool.name,
            output={"message": f"Tool {tool.name} executed locally"},
            metadata={"local": True}
        )
    
    async def execute_batch(
        self,
        tool_calls: List[Dict[str, Any]],
        agent_role: str = "",
        max_concurrent: int = 5
    ) -> List[ToolExecutionResult]:
        """批量执行工具"""
        results = []
        
        # 创建信号量限制并发数
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def execute_with_semaphore(tool_name: str, arguments: Dict[str, Any]):
            async with semaphore:
                return await self.execute(tool_name, arguments, agent_role)
        
        # 创建任务列表
        tasks = []
        for call in tool_calls:
            tool_name = call.get("tool_name") or call.get("name")
            arguments = call.get("arguments", {})
            task = execute_with_semaphore(tool_name, arguments)
            tasks.append(task)
        
        # 并发执行
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理异常结果
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                tool_name = tool_calls[i].get("tool_name") or tool_calls[i].get("name")
                final_results.append(ToolExecutionResult(
                    success=False,
                    tool_name=tool_name,
                    error=str(result)
                ))
            else:
                final_results.append(result)
        
        return final_results
    
    def get_execution_history(
        self,
        tool_name: Optional[str] = None,
        limit: int = 100
    ) -> List[ToolExecutionResult]:
        """获取执行历史"""
        history = self.execution_history
        
        if tool_name:
            history = [r for r in history if r.tool_name == tool_name]
        
        return history[-limit:]
    
    def clear_history(self) -> None:
        """清空执行历史"""
        self.execution_history.clear()
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取执行统计"""
        if not self.execution_history:
            return {
                "total_executions": 0,
                "success_rate": 0.0,
                "average_execution_time": 0.0
            }
        
        total = len(self.execution_history)
        successful = sum(1 for r in self.execution_history if r.success)
        total_time = sum(r.execution_time for r in self.execution_history)
        
        return {
            "total_executions": total,
            "success_rate": successful / total if total > 0 else 0.0,
            "average_execution_time": total_time / total if total > 0 else 0.0,
            "tool_usage": self._get_tool_usage_stats()
        }
    
    def _get_tool_usage_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取工具使用统计"""
        stats = {}
        
        for result in self.execution_history:
            tool_name = result.tool_name
            if tool_name not in stats:
                stats[tool_name] = {
                    "total_calls": 0,
                    "successful_calls": 0,
                    "failed_calls": 0,
                    "total_time": 0.0
                }
            
            stats[tool_name]["total_calls"] += 1
            if result.success:
                stats[tool_name]["successful_calls"] += 1
            else:
                stats[tool_name]["failed_calls"] += 1
            stats[tool_name]["total_time"] += result.execution_time
        
        return stats


class AsyncToolExecutor(ToolExecutor):
    """异步工具执行器，支持更高级的并发控制"""
    
    def __init__(
        self,
        tool_registry: ToolRegistry,
        permission_manager: Optional[ToolPermissionManager] = None,
        max_workers: int = 10,
        confirm: Optional[Any] = None
    ):
        super().__init__(tool_registry, permission_manager, confirm=confirm)
        self.max_workers = max_workers
        self.worker_semaphore = asyncio.Semaphore(max_workers)
        self.running_tasks: Dict[str, asyncio.Task] = {}
    
    async def execute_with_progress(
        self,
        tool_calls: List[Dict[str, Any]],
        agent_role: str = "",
        progress_callback: Optional[callable] = None
    ) -> List[ToolExecutionResult]:
        """带进度回调的批量执行"""
        results = []
        completed = 0
        total = len(tool_calls)
        
        async def execute_and_report(tool_name: str, arguments: Dict[str, Any], index: int):
            nonlocal completed
            
            async with self.worker_semaphore:
                result = await self.execute(tool_name, arguments, agent_role)
                completed += 1
                
                if progress_callback:
                    progress_callback(completed, total, tool_name, result)
                
                return result
        
        # 创建任务
        tasks = []
        for i, call in enumerate(tool_calls):
            tool_name = call.get("tool_name") or call.get("name")
            arguments = call.get("arguments", {})
            task = execute_and_report(tool_name, arguments, i)
            tasks.append(task)
        
        # 执行任务
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                tool_name = tool_calls[i].get("tool_name") or tool_calls[i].get("name")
                final_results.append(ToolExecutionResult(
                    success=False,
                    tool_name=tool_name,
                    error=str(result)
                ))
            else:
                final_results.append(result)
        
        return final_results
    
    async def cancel_execution(self, execution_id: str) -> bool:
        """取消执行"""
        if execution_id in self.running_tasks:
            task = self.running_tasks[execution_id]
            task.cancel()
            del self.running_tasks[execution_id]
            return True
        return False
    
    async def cancel_all(self) -> None:
        """取消所有执行"""
        for execution_id in list(self.running_tasks.keys()):
            await self.cancel_execution(execution_id)
