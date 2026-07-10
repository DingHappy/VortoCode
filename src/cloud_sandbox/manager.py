"""云端沙箱系统 - 类似 E2B 的安全隔离执行环境"""

import logging
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SandboxStatus(str, Enum):
    """沙箱状态"""
    CREATING = "creating"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    DESTROYED = "destroyed"


class SandboxConfig(BaseModel):
    """沙箱配置"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    image: str = "python:3.11-slim"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout: int = 300  # 秒
    network_enabled: bool = False
    env_vars: Dict[str, str] = Field(default_factory=dict)
    volumes: Dict[str, str] = Field(default_factory=dict)
    working_dir: str = "/workspace"
    auto_destroy: bool = True
    max_lifetime: int = 3600  # 秒


class ExecutionResult(BaseModel):
    """执行结果"""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration: float = 0.0
    memory_used: int = 0
    cpu_used: float = 0.0


class FileInfo(BaseModel):
    """文件信息"""
    path: str
    name: str
    size: int
    is_directory: bool
    modified: datetime
    permissions: str = "rw-r--r--"


class SandboxInstance:
    """沙箱实例"""
    
    def __init__(self, config: SandboxConfig):
        self.config = config
        self.id = config.id
        self.status = SandboxStatus.CREATING
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.container_id: Optional[str] = None
        self.execution_count = 0
        self.total_duration = 0.0
        self.filesystem: Dict[str, str] = {}  # path -> content
    
    async def start(self) -> bool:
        """启动沙箱"""
        try:
            # 创建临时目录
            self.work_dir = Path(f"/tmp/sandbox-{self.id}")
            self.work_dir.mkdir(parents=True, exist_ok=True)
            
            self.status = SandboxStatus.RUNNING
            self.last_activity = datetime.now()
            
            logger.info(f"Sandbox {self.id} started")
            return True
        
        except Exception as e:
            logger.error(f"Failed to start sandbox {self.id}: {e}")
            self.status = SandboxStatus.ERROR
            return False
    
    async def execute(
        self, 
        command: str, 
        language: str = "bash",
        timeout: int = None
    ) -> ExecutionResult:
        """执行命令"""
        import time
        
        if self.status != SandboxStatus.RUNNING:
            return ExecutionResult(
                success=False,
                stderr=f"Sandbox is not running (status: {self.status})"
            )
        
        start_time = time.time()
        timeout = timeout or self.config.timeout

        # 统一走 runner：Docker → OS sandbox；仅显式 off + shell gate 可走宿主机。
        from ..sandbox.runner import run_code
        r = await run_code(
            command, language=language, timeout=timeout, workspace=str(self.work_dir)
        )

        duration = time.time() - start_time
        self.execution_count += 1
        self.total_duration += duration
        self.last_activity = datetime.now()

        return ExecutionResult(
            success=r.success,
            stdout=r.stdout,
            stderr=r.stderr or r.error,
            exit_code=r.exit_code,
            duration=duration,
        )
    
    async def write_file(self, path: str, content: str) -> bool:
        """写入文件"""
        try:
            file_path = self.work_dir / path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding='utf-8')
            
            self.filesystem[path] = content
            self.last_activity = datetime.now()
            
            return True
        except Exception as e:
            logger.error(f"Failed to write file {path}: {e}")
            return False
    
    async def read_file(self, path: str) -> Optional[str]:
        """读取文件"""
        try:
            file_path = self.work_dir / path
            if file_path.exists():
                return file_path.read_text(encoding='utf-8')
            return None
        except Exception as e:
            logger.error(f"Failed to read file {path}: {e}")
            return None
    
    async def list_files(self, path: str = "") -> List[FileInfo]:
        """列出文件"""
        try:
            dir_path = self.work_dir / path if path else self.work_dir
            
            files = []
            for item in dir_path.iterdir():
                files.append(FileInfo(
                    path=str(item.relative_to(self.work_dir)),
                    name=item.name,
                    size=item.stat().st_size if item.is_file() else 0,
                    is_directory=item.is_dir(),
                    modified=datetime.fromtimestamp(item.stat().st_mtime)
                ))
            
            return sorted(files, key=lambda f: (not f.is_directory, f.name))
        except Exception as e:
            logger.error(f"Failed to list files: {e}")
            return []
    
    async def delete_file(self, path: str) -> bool:
        """删除文件"""
        try:
            file_path = self.work_dir / path
            if file_path.exists():
                if file_path.is_file():
                    file_path.unlink()
                else:
                    import shutil
                    shutil.rmtree(file_path)
                
                if path in self.filesystem:
                    del self.filesystem[path]
                
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete file {path}: {e}")
            return False
    
    async def stop(self):
        """停止沙箱"""
        self.status = SandboxStatus.STOPPED
        logger.info(f"Sandbox {self.id} stopped")
    
    async def destroy(self):
        """销毁沙箱"""
        try:
            # 清理工作目录
            import shutil
            if self.work_dir.exists():
                shutil.rmtree(self.work_dir)
            
            self.status = SandboxStatus.DESTROYED
            logger.info(f"Sandbox {self.id} destroyed")
        except Exception as e:
            logger.error(f"Failed to destroy sandbox {self.id}: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "id": self.id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "execution_count": self.execution_count,
            "total_duration": self.total_duration,
            "files_count": len(self.filesystem)
        }


class CloudSandboxManager:
    """云端沙箱管理器"""
    
    def __init__(self, max_sandboxes: int = 10):
        self.max_sandboxes = max_sandboxes
        self.sandboxes: Dict[str, SandboxInstance] = {}
        self.templates: Dict[str, SandboxConfig] = {}
        
        # 注册默认模板
        self._register_default_templates()
    
    def _register_default_templates(self):
        """注册默认模板"""
        self.templates["python"] = SandboxConfig(
            name="Python",
            image="python:3.11-slim",
            memory_limit="512m",
            cpu_limit=1.0
        )
        
        self.templates["javascript"] = SandboxConfig(
            name="JavaScript",
            image="node:18-slim",
            memory_limit="512m",
            cpu_limit=1.0
        )
        
        self.templates["fullstack"] = SandboxConfig(
            name="Full Stack",
            image="node:18-slim",
            memory_limit="1g",
            cpu_limit=2.0,
            network_enabled=True
        )
    
    async def create_sandbox(
        self,
        template: str = "python",
        name: str = None,
        config: SandboxConfig = None
    ) -> SandboxInstance:
        """创建沙箱"""
        if len(self.sandboxes) >= self.max_sandboxes:
            raise ValueError(f"Maximum number of sandboxes ({self.max_sandboxes}) reached")
        
        # 使用模板或自定义配置
        if config:
            sandbox_config = config
        elif template in self.templates:
            sandbox_config = self.templates[template].model_copy()
            if name:
                sandbox_config.name = name
        else:
            sandbox_config = SandboxConfig(name=name or "default")
        
        # 创建沙箱实例
        sandbox = SandboxInstance(sandbox_config)
        
        # 启动沙箱
        success = await sandbox.start()
        if not success:
            raise RuntimeError(f"Failed to start sandbox")
        
        self.sandboxes[sandbox.id] = sandbox
        
        logger.info(f"Created sandbox: {sandbox.id} ({sandbox_config.name})")
        return sandbox
    
    async def get_sandbox(self, sandbox_id: str) -> Optional[SandboxInstance]:
        """获取沙箱"""
        return self.sandboxes.get(sandbox_id)
    
    async def list_sandboxes(self) -> List[Dict[str, Any]]:
        """列出所有沙箱"""
        return [s.get_stats() for s in self.sandboxes.values()]
    
    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """销毁沙箱"""
        sandbox = self.sandboxes.get(sandbox_id)
        if sandbox:
            await sandbox.destroy()
            del self.sandboxes[sandbox_id]
            return True
        return False
    
    async def destroy_all(self):
        """销毁所有沙箱"""
        for sandbox_id in list(self.sandboxes.keys()):
            await self.destroy_sandbox(sandbox_id)
    
    async def execute_in_sandbox(
        self,
        sandbox_id: str,
        command: str,
        language: str = "bash",
        timeout: int = None
    ) -> ExecutionResult:
        """在沙箱中执行命令"""
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            return ExecutionResult(
                success=False,
                stderr=f"Sandbox not found: {sandbox_id}"
            )
        
        return await sandbox.execute(command, language, timeout)
    
    async def execute_code(
        self,
        code: str,
        language: str = "python",
        template: str = None,
        timeout: int = 60
    ) -> ExecutionResult:
        """执行代码（自动创建沙箱）"""
        # 选择模板
        if not template:
            template = "python" if language == "python" else "javascript"
        
        # 创建沙箱
        sandbox = await self.create_sandbox(template)
        
        try:
            # 执行代码
            result = await sandbox.execute(code, language, timeout)
            return result
        finally:
            # 销毁沙箱
            await self.destroy_sandbox(sandbox.id)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_sandboxes": len(self.sandboxes),
            "running_sandboxes": len([s for s in self.sandboxes.values() 
                                     if s.status == SandboxStatus.RUNNING]),
            "templates": list(self.templates.keys()),
            "max_sandboxes": self.max_sandboxes
        }
