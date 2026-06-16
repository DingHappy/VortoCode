"""多项目管理器 - 支持并行开发多个项目"""

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ProjectConfig(BaseModel):
    """项目配置"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    path: str
    description: str = ""
    git_repo: Optional[str] = None
    tech_stack: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    last_active: datetime = Field(default_factory=datetime.now)
    is_active: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProjectState:
    """项目状态"""
    
    def __init__(self, config: ProjectConfig):
        self.config = config
        self.workdir = Path(config.path)
        
        # 项目特定的组件
        self.sessions: Dict[str, Any] = {}
        self.current_session_id: Optional[str] = None
        
        # 索引缓存
        self.index_cache: Dict[str, Any] = {}
        
        # Agent 实例
        self.agents: Dict[str, Any] = {}
        
        # 统计
        self.stats = {
            "tasks_completed": 0,
            "tokens_used": 0,
            "files_modified": 0,
            "errors_fixed": 0
        }
    
    def update_activity(self):
        """更新最后活跃时间"""
        self.config.last_active = datetime.now()


class ProjectManager:
    """项目管理器"""
    
    def __init__(self, storage_path: str = ".auto-dev-crew/projects"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # 项目状态
        self.projects: Dict[str, ProjectState] = {}
        self.active_project_id: Optional[str] = None
        
        # 加载已有项目
        self._load_projects()
    
    def _load_projects(self):
        """加载已有项目"""
        import json
        
        config_file = self.storage_path / "projects.json"
        if config_file.exists():
            try:
                data = json.loads(config_file.read_text())
                for project_data in data.get("projects", []):
                    config = ProjectConfig(**project_data)
                    if Path(config.path).exists():
                        self.projects[config.id] = ProjectState(config)
                
                self.active_project_id = data.get("active_project_id")
                logger.info(f"Loaded {len(self.projects)} projects")
            except Exception as e:
                logger.error(f"Failed to load projects: {e}")
    
    def _save_projects(self):
        """保存项目配置"""
        import json
        
        config_file = self.storage_path / "projects.json"
        data = {
            "active_project_id": self.active_project_id,
            "projects": [p.config.dict() for p in self.projects.values()]
        }
        
        config_file.write_text(json.dumps(data, indent=2, default=str))
    
    def add_project(
        self,
        name: str,
        path: str,
        description: str = "",
        tech_stack: List[str] = None
    ) -> ProjectConfig:
        """添加项目"""
        # 验证路径
        project_path = Path(path).resolve()
        if not project_path.exists():
            project_path.mkdir(parents=True, exist_ok=True)
        
        # 检查是否已存在
        for project in self.projects.values():
            if project.workdir == project_path:
                raise ValueError(f"Project already exists: {project.config.name}")
        
        # 创建项目配置
        config = ProjectConfig(
            name=name,
            path=str(project_path),
            description=description,
            tech_stack=tech_stack or []
        )
        
        # 创建项目状态
        self.projects[config.id] = ProjectState(config)
        
        # 如果是第一个项目，设为活跃
        if not self.active_project_id:
            self.active_project_id = config.id
        
        # 保存
        self._save_projects()
        
        logger.info(f"Added project: {name} ({config.id})")
        return config
    
    def remove_project(self, project_id: str) -> bool:
        """移除项目"""
        if project_id not in self.projects:
            return False
        
        # 如果是活跃项目，切换到其他项目
        if self.active_project_id == project_id:
            other_projects = [pid for pid in self.projects if pid != project_id]
            self.active_project_id = other_projects[0] if other_projects else None
        
        # 删除项目
        del self.projects[project_id]
        
        # 保存
        self._save_projects()
        
        logger.info(f"Removed project: {project_id}")
        return True
    
    def switch_project(self, project_id: str) -> bool:
        """切换活跃项目"""
        if project_id not in self.projects:
            return False
        
        self.active_project_id = project_id
        self.projects[project_id].update_activity()
        
        # 保存
        self._save_projects()
        
        logger.info(f"Switched to project: {project_id}")
        return True
    
    def get_active_project(self) -> Optional[ProjectState]:
        """获取活跃项目"""
        if self.active_project_id:
            return self.projects.get(self.active_project_id)
        return None
    
    def get_project(self, project_id: str) -> Optional[ProjectState]:
        """获取项目"""
        return self.projects.get(project_id)
    
    def list_projects(self) -> List[Dict[str, Any]]:
        """列出所有项目"""
        projects = []
        for project in self.projects.values():
            projects.append({
                "id": project.config.id,
                "name": project.config.name,
                "path": project.config.path,
                "description": project.config.description,
                "tech_stack": project.config.tech_stack,
                "is_active": project.config.id == self.active_project_id,
                "last_active": project.config.last_active.isoformat(),
                "stats": project.stats
            })
        
        # 按最后活跃时间排序
        projects.sort(key=lambda x: x["last_active"], reverse=True)
        return projects
    
    def get_project_files(self, project_id: str) -> List[Dict[str, Any]]:
        """获取项目文件列表"""
        project = self.projects.get(project_id)
        if not project:
            return []
        
        files = []
        ignore_dirs = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build'}
        
        for item in project.workdir.rglob("*"):
            # 跳过忽略的目录
            if any(d in item.parts for d in ignore_dirs):
                continue
            
            if item.is_file():
                files.append({
                    "name": item.name,
                    "path": str(item.relative_to(project.workdir)),
                    "type": item.suffix,
                    "size": item.stat().st_size,
                    "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat()
                })
        
        return files
    
    def search_in_project(self, project_id: str, query: str) -> List[Dict[str, Any]]:
        """在项目中搜索"""
        project = self.projects.get(project_id)
        if not project:
            return []
        
        results = []
        query_lower = query.lower()
        
        # 搜索文件名
        for item in project.workdir.rglob("*"):
            if item.is_file() and query_lower in item.name.lower():
                results.append({
                    "type": "file",
                    "name": item.name,
                    "path": str(item.relative_to(project.workdir)),
                    "match": "filename"
                })
        
        # 搜索文件内容（限制前 100 个文件）
        file_count = 0
        for item in project.workdir.rglob("*"):
            if item.is_file() and item.suffix in ('.py', '.js', '.ts', '.md', '.txt'):
                try:
                    content = item.read_text(encoding='utf-8')
                    if query_lower in content.lower():
                        # 找到匹配的行
                        for i, line in enumerate(content.split('\n'), 1):
                            if query_lower in line.lower():
                                results.append({
                                    "type": "content",
                                    "file": str(item.relative_to(project.workdir)),
                                    "line": i,
                                    "text": line.strip()[:100]
                                })
                                break
                
                except (UnicodeDecodeError, PermissionError):
                    # 跳过无法读取的文件
                    pass
                except Exception as e:
                    logger.debug(f"Error searching file {item}: {e}")
                
                file_count += 1
                if file_count >= 100:
                    break
        
        return results[:50]  # 限制返回数量
    
    def update_stats(self, project_id: str, **kwargs):
        """更新项目统计"""
        project = self.projects.get(project_id)
        if project:
            for key, value in kwargs.items():
                if key in project.stats:
                    project.stats[key] += value


class MultiProjectOrchestrator:
    """多项目编排器"""
    
    def __init__(self, project_manager: ProjectManager):
        self.project_manager = project_manager
        self.project_orchestrators: Dict[str, Any] = {}
    
    async def execute_in_project(
        self,
        project_id: str,
        task: str,
        **kwargs
    ) -> Dict[str, Any]:
        """在指定项目中执行任务"""
        project = self.project_manager.get_project(project_id)
        if not project:
            return {"success": False, "error": "Project not found"}
        
        # 切换到该项目
        self.project_manager.switch_project(project_id)
        
        # 获取或创建项目的编排器
        orchestrator = self._get_orchestrator(project_id)
        
        # 执行任务
        result = await orchestrator.orchestrate(task, **kwargs)
        
        # 更新统计
        self.project_manager.update_stats(
            project_id,
            tasks_completed=1 if result.get("success") else 0
        )
        
        return result
    
    async def execute_parallel(
        self,
        tasks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """并行执行多个项目的任务"""
        import asyncio
        
        coroutines = []
        for task_info in tasks:
            project_id = task_info.get("project_id")
            task = task_info.get("task")
            
            coroutines.append(
                self.execute_in_project(project_id, task)
            )
        
        return await asyncio.gather(*coroutines, return_exceptions=True)
    
    def _get_orchestrator(self, project_id: str) -> Any:
        """获取项目的编排器"""
        if project_id not in self.project_orchestrators:
            # 延迟导入避免循环依赖
            from .engine import SelfOrchestratingEngine
            
            project = self.project_manager.get_project(project_id)
            orchestrator = SelfOrchestratingEngine()
            
            # 设置工作目录
            orchestrator.workdir = project.workdir
            
            self.project_orchestrators[project_id] = orchestrator
        
        return self.project_orchestrators[project_id]
    
    def get_all_stats(self) -> Dict[str, Any]:
        """获取所有项目统计"""
        stats = {}
        for project_id, project in self.project_manager.projects.items():
            stats[project_id] = {
                "name": project.config.name,
                "stats": project.stats
            }
        return stats
