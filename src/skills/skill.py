import logging
logger = logging.getLogger(__name__)
"""技能基类和数据模型"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SkillMetadata(BaseModel):
    """技能元数据"""
    name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    capabilities: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)  # 依赖的技能
    context: Optional[str] = None  # fork 或 None
    agent: Optional[str] = None
    model: str = "inherit"
    effort: str = "medium"
    disable_model_invocation: bool = False
    user_invocable: bool = True


class SkillArgument(BaseModel):
    """技能参数"""
    name: str
    description: str = ""
    required: bool = True
    default: Any = None


class SkillHook(BaseModel):
    """技能 Hook"""
    type: str = "command"  # command, http, prompt
    command: Optional[str] = None
    url: Optional[str] = None
    prompt: Optional[str] = None


class SkillResult(BaseModel):
    """技能执行结果"""
    success: bool
    output: Any = None
    error: Optional[str] = None
    artifacts: List[str] = Field(default_factory=list)  # 生成的文件路径
    metrics: Dict[str, Any] = Field(default_factory=dict)  # 执行指标


class Skill(ABC):
    """技能基类"""
    
    def __init__(
        self,
        metadata: SkillMetadata,
        instructions: str = "",
        arguments: Optional[List[SkillArgument]] = None,
        hooks: Optional[Dict[str, List[SkillHook]]] = None
    ):
        self.metadata = metadata
        self.instructions = instructions
        self.arguments = arguments or []
        self.hooks = hooks or {}
        self._loaded = False
        self._context: Dict[str, Any] = {}
    
    @abstractmethod
    async def execute(
        self, 
        args: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> SkillResult:
        """执行技能"""
        pass
    
    def _validate_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """验证参数"""
        validated = {}
        
        for arg_def in self.arguments:
            if arg_def.name in args:
                validated[arg_def.name] = args[arg_def.name]
            elif arg_def.required:
                raise ValueError(f"Missing required argument: {arg_def.name}")
            else:
                validated[arg_def.name] = arg_def.default
        
        return validated
    
    def _render_instructions(
        self, 
        args: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """渲染指令"""
        instructions = self.instructions
        
        # 替换参数占位符 $arg_name
        for key, value in args.items():
            placeholder = f"${key}"
            instructions = instructions.replace(placeholder, str(value))
        
        # 替换 $ARGUMENTS
        if "$ARGUMENTS" in instructions:
            args_str = " ".join(f"{k}={v}" for k, v in args.items())
            instructions = instructions.replace("$ARGUMENTS", args_str)
        
        # 替换上下文变量
        if context:
            for key, value in context.items():
                placeholder = f"${{{key}}}"
                instructions = instructions.replace(placeholder, str(value))
        
        return instructions
    
    async def _execute_hooks(
        self, 
        event: str, 
        data: Any
    ) -> None:
        """执行 hooks"""
        hooks = self.hooks.get(event, [])
        for hook_config in hooks:
            if hook_config.type == "command" and hook_config.command:
                await self._execute_command_hook(hook_config.command, data)
            elif hook_config.type == "http" and hook_config.url:
                await self._execute_http_hook(hook_config.url, data)
    
    async def _execute_command_hook(self, command: str, data: Any) -> None:
        """执行命令 hook"""
        import asyncio
        try:
            from src.agents.sandbox import child_env
            # skill 的 pre_execute 命令源自 .vortocode/skills（gitignored，无 trust 门）：
            # 同样剥操作密钥，不让 skill 生命周期钩子成为绕过 child_env 的外带口。
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env(),
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                logger.warning(f"Hook execution failed: {stderr.decode()}")
        except Exception as e:
            logger.error(f"Hook execution error: {e}")
    
    async def _execute_http_hook(self, url: str, data: Any) -> None:
        """执行 HTTP hook"""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data) as response:
                    if response.status != 200:
                        logger.warning(f"Hook request failed: {response.status}")
        except Exception as e:
            logger.error(f"Hook request error: {e}")
    
    def __repr__(self) -> str:
        return f"<Skill(name={self.metadata.name}, version={self.metadata.version})>"
