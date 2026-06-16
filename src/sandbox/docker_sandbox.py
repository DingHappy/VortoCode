"""Docker 沙箱执行环境"""

import asyncio
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SandboxStatus(str, Enum):
    """沙箱状态"""
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class SandboxConfig:
    """沙箱配置"""
    image: str = "python:3.11-slim"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0  # CPU 核心数
    timeout: int = 300  # 秒
    network_enabled: bool = False
    readonly_rootfs: bool = False
    volumes: Dict[str, str] = field(default_factory=dict)  # host -> container
    environment: Dict[str, str] = field(default_factory=dict)
    working_dir: str = "/workspace"


@dataclass
class SandboxResult:
    """沙箱执行结果"""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    container_id: Optional[str] = None


class DockerSandbox:
    """Docker 沙箱"""
    
    def __init__(self, config: SandboxConfig = None):
        self.config = config or SandboxConfig()
        self.container_id: Optional[str] = None
        self.status = SandboxStatus.CREATED
        self.temp_dir: Optional[Path] = None
    
    async def create(self) -> str:
        """创建容器"""
        try:
            # 检查 Docker 是否可用
            if not await self._check_docker():
                raise RuntimeError("Docker is not available")
            
            # 创建临时目录
            self.temp_dir = Path(tempfile.mkdtemp(prefix="auto-dev-crew-"))
            
            # 构建 docker run 命令
            cmd = ["docker", "run", "-d"]
            
            # 资源限制
            cmd.extend(["--memory", self.config.memory_limit])
            cmd.extend(["--cpus", str(self.config.cpu_limit)])
            
            # 网络
            if not self.config.network_enabled:
                cmd.append("--network=none")
            
            # 只读文件系统
            if self.config.readonly_rootfs:
                cmd.append("--read-only")
            
            # 卷挂载
            for host_path, container_path in self.config.volumes.items():
                cmd.extend(["-v", f"{host_path}:{container_path}"])
            
            # 挂载临时工作目录
            cmd.extend(["-v", f"{self.temp_dir}:{self.config.working_dir}"])
            
            # 环境变量
            for key, value in self.config.environment.items():
                cmd.extend(["-e", f"{key}={value}"])
            
            # 工作目录
            cmd.extend(["-w", self.config.working_dir])
            
            # 镜像和命令
            cmd.extend([self.config.image, "sleep", "infinity"])
            
            # 执行命令
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                self.container_id = stdout.decode().strip()
                self.status = SandboxStatus.RUNNING
                logger.info(f"Created container: {self.container_id}")
                return self.container_id
            else:
                raise RuntimeError(f"Failed to create container: {stderr.decode()}")
        
        except Exception as e:
            self.status = SandboxStatus.ERROR
            logger.error(f"Failed to create sandbox: {e}")
            raise
    
    async def execute(
        self,
        command: str,
        timeout: int = None,
        workdir: str = None
    ) -> SandboxResult:
        """执行命令"""
        if not self.container_id:
            raise RuntimeError("Sandbox not created")
        
        timeout = timeout or self.config.timeout
        workdir = workdir or self.config.working_dir
        
        try:
            # 构建 docker exec 命令
            cmd = [
                "docker", "exec",
                "-w", workdir,
                self.container_id,
                "sh", "-c", command
            ]
            
            # 执行命令
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                return SandboxResult(
                    success=False,
                    stdout="",
                    stderr=f"Command timed out after {timeout}s",
                    exit_code=-1,
                    duration=timeout
                )
            
            return SandboxResult(
                success=proc.returncode == 0,
                stdout=stdout.decode(),
                stderr=stderr.decode(),
                exit_code=proc.returncode,
                duration=0  # TODO: 计算实际耗时
            )
        
        except Exception as e:
            return SandboxResult(
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration=0
            )
    
    async def copy_to_sandbox(self, local_path: str, container_path: str):
        """复制文件到沙箱"""
        if not self.container_id:
            raise RuntimeError("Sandbox not created")
        
        # 确保容器路径是绝对路径
        if not container_path.startswith('/'):
            container_path = f"{self.config.working_dir}/{container_path}"
        
        cmd = ["docker", "cp", local_path, f"{self.container_id}:{container_path}"]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to copy file: {stderr.decode()}")
    
    async def copy_from_sandbox(self, container_path: str, local_path: str):
        """从沙箱复制文件"""
        if not self.container_id:
            raise RuntimeError("Sandbox not created")
        
        cmd = ["docker", "cp", f"{self.container_id}:{container_path}", local_path]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to copy file: {stderr.decode()}")
    
    async def stop(self):
        """停止容器"""
        if self.container_id:
            try:
                cmd = ["docker", "stop", self.container_id]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()
                self.status = SandboxStatus.STOPPED
            except Exception as e:
                logger.error(f"Failed to stop container: {e}")
    
    async def destroy(self):
        """销毁容器"""
        if self.container_id:
            try:
                # 停止容器
                await self.stop()
                
                # 删除容器
                cmd = ["docker", "rm", "-f", self.container_id]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()
                
                # 清理临时目录
                if self.temp_dir and self.temp_dir.exists():
                    shutil.rmtree(self.temp_dir)
                
                self.container_id = None
                logger.info("Container destroyed")
            
            except Exception as e:
                logger.error(f"Failed to destroy container: {e}")
    
    async def _check_docker(self) -> bool:
        """检查 Docker 是否可用"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False


class SandboxManager:
    """沙箱管理器"""
    
    def __init__(self):
        self.sandboxes: Dict[str, DockerSandbox] = {}
    
    async def create_sandbox(
        self,
        name: str,
        config: SandboxConfig = None
    ) -> DockerSandbox:
        """创建沙箱"""
        sandbox = DockerSandbox(config)
        await sandbox.create()
        self.sandboxes[name] = sandbox
        return sandbox
    
    def get_sandbox(self, name: str) -> Optional[DockerSandbox]:
        """获取沙箱"""
        return self.sandboxes.get(name)
    
    async def destroy_sandbox(self, name: str):
        """销毁沙箱"""
        sandbox = self.sandboxes.get(name)
        if sandbox:
            await sandbox.destroy()
            del self.sandboxes[name]
    
    async def destroy_all(self):
        """销毁所有沙箱"""
        for name in list(self.sandboxes.keys()):
            await self.destroy_sandbox(name)
    
    def list_sandboxes(self) -> List[Dict[str, Any]]:
        """列出所有沙箱"""
        return [
            {
                "name": name,
                "container_id": sandbox.container_id,
                "status": sandbox.status.value
            }
            for name, sandbox in self.sandboxes.items()
        ]


class SandboxExecutor:
    """沙箱执行器"""
    
    def __init__(self, manager: SandboxManager = None):
        self.manager = manager or SandboxManager()
    
    async def execute_in_sandbox(
        self,
        code: str,
        language: str = "python",
        timeout: int = 60
    ) -> SandboxResult:
        """在沙箱中执行代码"""
        # 选择镜像
        images = {
            "python": "python:3.11-slim",
            "javascript": "node:18-slim",
            "typescript": "node:18-slim"
        }
        
        image = images.get(language, "python:3.11-slim")
        
        # 创建沙箱
        config = SandboxConfig(
            image=image,
            timeout=timeout,
            network_enabled=False
        )
        
        sandbox_name = f"exec-{id(code)}"
        sandbox = await self.manager.create_sandbox(sandbox_name, config)
        
        try:
            # 写入代码文件
            extensions = {
                "python": ".py",
                "javascript": ".js",
                "typescript": ".ts"
            }
            ext = extensions.get(language, ".py")
            code_file = f"/workspace/code{ext}"
            
            # 写入临时文件
            temp_file = sandbox.temp_dir / f"code{ext}"
            temp_file.write_text(code)
            
            # 执行代码
            commands = {
                "python": f"python {code_file}",
                "javascript": f"node {code_file}",
                "typescript": f"npx ts-node {code_file}"
            }
            command = commands.get(language, f"python {code_file}")
            
            return await sandbox.execute(command, timeout=timeout)
        
        finally:
            # 清理
            await self.manager.destroy_sandbox(sandbox_name)
    
    async def run_tests(
        self,
        test_command: str,
        workdir: str,
        timeout: int = 300
    ) -> SandboxResult:
        """在沙箱中运行测试"""
        config = SandboxConfig(
            image="python:3.11-slim",
            volumes={workdir: "/workspace"},
            timeout=timeout,
            network_enabled=True  # 测试可能需要网络
        )
        
        sandbox_name = f"test-{id(test_command)}"
        sandbox = await self.manager.create_sandbox(sandbox_name, config)
        
        try:
            return await sandbox.execute(test_command, timeout=timeout)
        finally:
            await self.manager.destroy_sandbox(sandbox_name)
