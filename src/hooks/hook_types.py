"""具体的 Hook 实现"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from .hook import Hook, HookEvent, HookEventType, HookResult

logger = logging.getLogger(__name__)


class CommandHook(Hook):
    """命令 Hook - 执行 shell 命令"""
    
    def __init__(
        self,
        name: str,
        event_types: List[HookEventType],
        command: str,
        args: Optional[List[str]] = None,
        timeout: int = 60,
        shell: bool = False,           # true：command 当完整 shell 字符串跑（如 "ruff format ."）
        cwd: Optional[str] = None,     # 执行目录（默认进程 cwd = 仓库根）
        **kwargs
    ):
        super().__init__(name, event_types, **kwargs)
        self.command = command
        self.args = args or []
        self.timeout = timeout
        self.shell = shell
        self.cwd = cwd

    async def execute(self, event: HookEvent) -> HookResult:
        """执行命令"""
        # 准备输入数据
        input_data = json.dumps({
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "source": event.source,
            "data": event.data,
            "context": event.context
        }, ensure_ascii=False)

        try:
            # 执行命令：shell=True 把 command 当整条 shell 跑（方便 "ruff format ."），否则 exec command+args
            if self.shell:
                process = await asyncio.create_subprocess_shell(
                    self.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.cwd,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    self.command,
                    *self.args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.cwd,
                )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_data.encode()),
                timeout=self.timeout
            )
            
            if process.returncode == 0:
                # 解析输出
                try:
                    output = json.loads(stdout.decode())
                    return HookResult(
                        success=True,
                        stop_execution=output.get("stop_execution", False),
                        modify_data=output.get("modify_data", {}),
                        message=output.get("message")
                    )
                except json.JSONDecodeError:
                    return HookResult(
                        success=True,
                        message=stdout.decode().strip()
                    )
            else:
                return HookResult(
                    success=False,
                    error=stderr.decode().strip()
                )
        
        except asyncio.TimeoutError:
            return HookResult(
                success=False,
                error=f"Command execution timeout ({self.timeout}s)"
            )
        except Exception as e:
            return HookResult(
                success=False,
                error=str(e)
            )


class HTTPHook(Hook):
    """HTTP Hook - 发送 HTTP 请求"""
    
    def __init__(
        self, 
        name: str,
        event_types: List[HookEventType],
        url: str,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        **kwargs
    ):
        super().__init__(name, event_types, **kwargs)
        self.url = url
        self.method = method
        self.headers = headers or {}
        self.timeout = timeout
    
    async def execute(self, event: HookEvent) -> HookResult:
        """执行 HTTP 请求"""
        import aiohttp
        
        payload = {
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "source": event.source,
            "data": event.data,
            "context": event.context
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    self.method,
                    self.url,
                    json=payload,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return HookResult(
                            success=True,
                            stop_execution=result.get("stop_execution", False),
                            modify_data=result.get("modify_data", {}),
                            message=result.get("message")
                        )
                    else:
                        return HookResult(
                            success=False,
                            error=f"HTTP request failed: {response.status}"
                        )
        
        except Exception as e:
            return HookResult(
                success=False,
                error=str(e)
            )


class PromptHook(Hook):
    """Prompt Hook - 使用 LLM 评估"""
    
    def __init__(
        self, 
        name: str,
        event_types: List[HookEventType],
        prompt: str,
        model: str = "gpt-4o-mini",
        **kwargs
    ):
        super().__init__(name, event_types, **kwargs)
        self.prompt = prompt
        self.model = model
    
    async def execute(self, event: HookEvent) -> HookResult:
        """执行 prompt 评估"""
        # 构建完整 prompt
        full_prompt = self.prompt.replace(
            "$EVENT_DATA", 
            json.dumps(event.data, indent=2, ensure_ascii=False)
        )
        
        try:
            # 调用 LLM（需要配置 LLM 客户端）
            response = await self._call_llm(full_prompt)
            
            # 解析响应
            try:
                result = json.loads(response)
                return HookResult(
                    success=True,
                    stop_execution=result.get("stop_execution", False),
                    modify_data=result.get("modify_data", {}),
                    message=result.get("message")
                )
            except json.JSONDecodeError:
                return HookResult(
                    success=True,
                    message=response
                )
        except Exception as e:
            return HookResult(
                success=False,
                error=str(e)
            )
    
    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM"""
        # 这里需要根据配置调用相应的 LLM API
        # 目前返回默认响应
        logger.warning("LLM client not configured, returning default response")
        return '{"message": "LLM evaluation skipped - client not configured"}'
