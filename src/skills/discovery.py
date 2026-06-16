"""技能发现和版本管理"""

import json
import logging
import os
import re
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from .skill import Skill, SkillMetadata, SkillResult
from .registry import SkillRegistry, SimpleSkill
from .parser import SkillParser

logger = logging.getLogger(__name__)


class SkillVersion(BaseModel):
    """技能版本"""
    version: str
    release_date: datetime = Field(default_factory=datetime.now)
    changelog: str = ""
    deprecated: bool = False
    min_platform_version: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list)


class SkillManifest(BaseModel):
    """技能清单"""
    name: str
    description: str = ""
    author: str = ""
    license: str = "MIT"
    homepage: str = ""
    repository: str = ""
    tags: List[str] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)
    versions: List[SkillVersion] = Field(default_factory=list)
    current_version: str = "1.0.0"
    min_platform_version: str = "0.1.0"
    dependencies: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    user_invocable: bool = True
    enabled: bool = True


class SkillDiscoveryConfig(BaseModel):
    """技能发现配置"""
    # 技能目录
    skill_dirs: List[str] = Field(default_factory=lambda: [
        "skills",
        ".auto-dev-crew/skills",
        "~/.auto-dev-crew/skills"
    ])
    
    # 自动发现
    auto_discover: bool = True
    discover_interval: int = 300  # 秒
    
    # 版本管理
    version_management: bool = True
    allow_multiple_versions: bool = False
    auto_update: bool = False
    
    # 缓存
    cache_enabled: bool = True
    cache_ttl: int = 3600  # 秒
    
    # 过滤
    include_patterns: List[str] = Field(default_factory=lambda: ["**/SKILL.md"])
    exclude_patterns: List[str] = Field(default_factory=lambda: ["**/test/**", "**/.*"])


class SkillDiscovery:
    """技能发现器"""
    
    def __init__(self, config: Optional[SkillDiscoveryConfig] = None):
        if config is None:
            config = SkillDiscoveryConfig()
        
        self.config = config
        self.skill_registry = SkillRegistry()
        self.manifests: Dict[str, SkillManifest] = {}
        self.skill_cache: Dict[str, Tuple[Skill, datetime]] = {}
        
        # 扩展技能目录
        self._expand_skill_dirs()
    
    def _expand_skill_dirs(self) -> None:
        """扩展技能目录路径"""
        expanded_dirs = []
        for skill_dir in self.config.skill_dirs:
            expanded = Path(skill_dir).expanduser()
            if expanded.exists():
                expanded_dirs.append(str(expanded))
        
        self.config.skill_dirs = expanded_dirs
    
    async def discover_skills(self) -> List[Skill]:
        """发现所有技能"""
        discovered_skills = []
        
        for skill_dir in self.config.skill_dirs:
            skills = await self._discover_in_directory(skill_dir)
            discovered_skills.extend(skills)
        
        # 注册发现的技能
        for skill in discovered_skills:
            self.skill_registry.register(skill)
        
        logger.info(f"Discovered {len(discovered_skills)} skills")
        return discovered_skills
    
    async def _discover_in_directory(self, directory: str) -> List[Skill]:
        """在目录中发现技能"""
        skills = []
        skill_dir = Path(directory)
        
        if not skill_dir.exists():
            return skills
        
        # 查找所有SKILL.md文件
        for skill_file in skill_dir.rglob("SKILL.md"):
            try:
                # 检查是否匹配包含模式
                if not self._matches_patterns(skill_file, self.config.include_patterns):
                    continue
                
                # 检查是否匹配排除模式
                if self._matches_patterns(skill_file, self.config.exclude_patterns):
                    continue
                
                # 加载技能
                skill = await self._load_skill(skill_file)
                if skill:
                    skills.append(skill)
                    
            except Exception as e:
                logger.error(f"Failed to load skill {skill_file}: {e}")
        
        return skills
    
    def _matches_patterns(self, file_path: Path, patterns: List[str]) -> bool:
        """检查文件是否匹配模式"""
        file_str = str(file_path)
        
        for pattern in patterns:
            # 简单的模式匹配
            if pattern.startswith("**/"):
                # 匹配任意目录深度
                suffix = pattern[3:]
                if file_str.endswith(suffix) or suffix in file_str:
                    return True
            elif pattern.startswith("*"):
                # 匹配文件扩展名
                if file_str.endswith(pattern[1:]):
                    return True
            else:
                # 精确匹配
                if pattern in file_str:
                    return True
        
        return False
    
    async def _load_skill(self, skill_file: Path) -> Optional[Skill]:
        """加载技能"""
        # 检查缓存
        cache_key = str(skill_file)
        if self.config.cache_enabled and cache_key in self.skill_cache:
            skill, cache_time = self.skill_cache[cache_key]
            cache_age = (datetime.now() - cache_time).total_seconds()
            if cache_age < self.config.cache_ttl:
                return skill
        
        try:
            # 解析技能文件
            metadata, instructions, arguments, hooks = SkillParser.parse_file(skill_file)
            
            # 加载清单文件（如果存在）
            manifest = await self._load_manifest(skill_file.parent)
            
            # 创建技能对象
            skill = SimpleSkill(
                metadata=metadata,
                instructions=instructions,
                arguments=arguments,
                hooks=hooks
            )
            
            # 添加清单信息
            if manifest:
                skill.metadata.version = manifest.current_version
            
            # 更新缓存
            if self.config.cache_enabled:
                self.skill_cache[cache_key] = (skill, datetime.now())
            
            return skill
            
        except Exception as e:
            logger.error(f"Failed to load skill {skill_file}: {e}")
            return None
    
    async def _load_manifest(self, skill_dir: Path) -> Optional[SkillManifest]:
        """加载技能清单"""
        manifest_file = skill_dir / "manifest.yaml"
        if not manifest_file.exists():
            manifest_file = skill_dir / "manifest.json"
        
        if not manifest_file.exists():
            return None
        
        try:
            with open(manifest_file, 'r', encoding='utf-8') as f:
                if manifest_file.suffix == '.yaml':
                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)
            
            manifest = SkillManifest(**data)
            self.manifests[manifest.name] = manifest
            
            return manifest
            
        except Exception as e:
            logger.error(f"Failed to load manifest {manifest_file}: {e}")
            return None
    
    async def discover_for_task(
        self,
        task_description: str,
        requirements: List[str] = None,
        top_k: int = 5
    ) -> List[Skill]:
        """为任务发现相关技能"""
        # 使用现有的发现方法
        matching_skills = self.skill_registry.discover_for_task(requirements or [])
        
        # 如果没有匹配的技能，尝试基于任务描述发现
        if not matching_skills:
            matching_skills = await self._discover_by_description(task_description)
        
        return matching_skills[:top_k]
    
    async def _discover_by_description(self, description: str) -> List[Skill]:
        """基于描述发现技能"""
        description_words = set(description.lower().split())
        matching_skills = []
        
        for skill in self.skill_registry.skills.values():
            # 计算描述匹配分数
            skill_text = f"{skill.metadata.name} {skill.metadata.description}"
            skill_words = set(skill_text.lower().split())
            
            overlap = len(description_words & skill_words)
            if overlap > 0:
                score = overlap / len(description_words) if description_words else 0
                matching_skills.append((score, skill))
        
        # 按分数排序
        matching_skills.sort(key=lambda x: x[0], reverse=True)
        
        return [skill for _, skill in matching_skills]
    
    async def check_dependencies(self, skill: Skill) -> Dict[str, Any]:
        """检查技能依赖"""
        manifest = self.manifests.get(skill.metadata.name)
        if not manifest:
            return {"satisfied": True, "missing": []}
        
        missing_deps = []
        for dep in manifest.dependencies:
            if dep not in self.skill_registry.skills:
                missing_deps.append(dep)
        
        return {
            "satisfied": len(missing_deps) == 0,
            "missing": missing_deps
        }
    
    async def get_skill_versions(self, skill_name: str) -> List[SkillVersion]:
        """获取技能版本"""
        manifest = self.manifests.get(skill_name)
        if not manifest:
            return []
        
        return manifest.versions
    
    async def update_skill(self, skill_name: str, new_version: str) -> bool:
        """更新技能版本"""
        manifest = self.manifests.get(skill_name)
        if not manifest:
            return False
        
        # 查找新版本
        for version in manifest.versions:
            if version.version == new_version:
                manifest.current_version = new_version
                
                # 重新加载技能
                await self._reload_skill(skill_name)
                
                logger.info(f"Updated skill {skill_name} to version {new_version}")
                return True
        
        logger.warning(f"Version {new_version} not found for skill {skill_name}")
        return False
    
    async def _reload_skill(self, skill_name: str) -> None:
        """重新加载技能"""
        # 查找技能文件
        for skill_dir in self.config.skill_dirs:
            skill_path = Path(skill_dir)
            for skill_file in skill_path.rglob("SKILL.md"):
                try:
                    metadata, _, _, _ = SkillParser.parse_file(skill_file)
                    if metadata.name == skill_name:
                        # 重新加载
                        skill = await self._load_skill(skill_file)
                        if skill:
                            self.skill_registry.register(skill)
                        return
                except Exception:
                    continue
    
    async def enable_skill(self, skill_name: str) -> bool:
        """启用技能"""
        skill = self.skill_registry.get(skill_name)
        if skill:
            # 更新清单
            manifest = self.manifests.get(skill_name)
            if manifest:
                manifest.enabled = True
            
            return True
        return False
    
    async def disable_skill(self, skill_name: str) -> bool:
        """禁用技能"""
        skill = self.skill_registry.get(skill_name)
        if skill:
            # 更新清单
            manifest = self.manifests.get(skill_name)
            if manifest:
                manifest.enabled = False
            
            return True
        return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_skills": len(self.skill_registry.skills),
            "skill_dirs": len(self.config.skill_dirs),
            "manifests_loaded": len(self.manifests),
            "cache_size": len(self.skill_cache)
        }
    
    async def refresh_cache(self) -> None:
        """刷新缓存"""
        self.skill_cache.clear()
        await self.discover_skills()
    
    def clear_cache(self) -> None:
        """清空缓存"""
        self.skill_cache.clear()


class SkillVersionManager:
    """技能版本管理器"""
    
    def __init__(self, discovery: SkillDiscovery):
        self.discovery = discovery
        self.version_history: Dict[str, List[SkillVersion]] = {}
    
    async def register_version(
        self,
        skill_name: str,
        version: SkillVersion
    ) -> bool:
        """注册技能版本"""
        manifest = self.discovery.manifests.get(skill_name)
        if not manifest:
            return False
        
        # 检查版本是否已存在
        for existing_version in manifest.versions:
            if existing_version.version == version.version:
                logger.warning(f"Version {version.version} already exists for skill {skill_name}")
                return False
        
        # 添加版本
        manifest.versions.append(version)
        
        # 记录历史
        if skill_name not in self.version_history:
            self.version_history[skill_name] = []
        self.version_history[skill_name].append(version)
        
        logger.info(f"Registered version {version.version} for skill {skill_name}")
        return True
    
    async def get_skill_versions(self, skill_name: str) -> List[SkillVersion]:
        """获取技能版本"""
        manifest = self.discovery.manifests.get(skill_name)
        if not manifest:
            return []
        
        return manifest.versions
    
    async def deprecate_version(
        self,
        skill_name: str,
        version: str
    ) -> bool:
        """废弃技能版本"""
        manifest = self.discovery.manifests.get(skill_name)
        if not manifest:
            return False
        
        for v in manifest.versions:
            if v.version == version:
                v.deprecated = True
                logger.info(f"Deprecated version {version} for skill {skill_name}")
                return True
        
        return False
    
    async def get_latest_version(self, skill_name: str) -> Optional[SkillVersion]:
        """获取最新版本"""
        manifest = self.discovery.manifests.get(skill_name)
        if not manifest or not manifest.versions:
            return None
        
        # 按版本号排序
        sorted_versions = sorted(
            manifest.versions,
            key=lambda v: self._parse_version(v.version),
            reverse=True
        )
        
        return sorted_versions[0]
    
    def _parse_version(self, version: str) -> Tuple[int, ...]:
        """解析版本号"""
        try:
            # 移除前缀v
            version = version.lstrip('v')
            return tuple(map(int, version.split('.')))
        except ValueError:
            return (0, 0, 0)
    
    async def check_compatibility(
        self,
        skill_name: str,
        version: str,
        platform_version: str
    ) -> bool:
        """检查版本兼容性"""
        manifest = self.discovery.manifests.get(skill_name)
        if not manifest:
            return False
        
        for v in manifest.versions:
            if v.version == version:
                if v.min_platform_version:
                    return self._compare_versions(
                        platform_version,
                        v.min_platform_version
                    ) >= 0
                return True
        
        return False
    
    def _compare_versions(self, version1: str, version2: str) -> int:
        """比较版本号"""
        v1 = self._parse_version(version1)
        v2 = self._parse_version(version2)
        
        if v1 < v2:
            return -1
        elif v1 > v2:
            return 1
        else:
            return 0


class AutoReloadingSkillDiscovery(SkillDiscovery):
    """自动重载技能发现器"""
    
    def __init__(self, config: Optional[SkillDiscoveryConfig] = None):
        super().__init__(config)
        self.file_watchers: Dict[str, Any] = {}
        self.reload_callbacks: List[callable] = []
    
    async def start_watching(self) -> None:
        """开始监视文件变化"""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
            
            class SkillFileHandler(FileSystemEventHandler):
                def __init__(self, discovery):
                    self.discovery = discovery
                
                def on_modified(self, event):
                    if event.src_path.endswith("SKILL.md"):
                        logger.info(f"Skill file modified: {event.src_path}")
                        asyncio.create_task(self.discovery._reload_skill_file(event.src_path))
                
                def on_created(self, event):
                    if event.src_path.endswith("SKILL.md"):
                        logger.info(f"New skill file: {event.src_path}")
                        asyncio.create_task(self.discovery._load_skill(Path(event.src_path)))
            
            handler = SkillFileHandler(self)
            observer = Observer()
            
            for skill_dir in self.config.skill_dirs:
                if Path(skill_dir).exists():
                    observer.schedule(handler, skill_dir, recursive=True)
                    logger.info(f"Watching skill directory: {skill_dir}")
            
            observer.start()
            self.file_watchers["main"] = observer
            
        except ImportError:
            logger.warning("watchdog not installed. File watching disabled.")
    
    async def stop_watching(self) -> None:
        """停止监视文件变化"""
        for name, observer in self.file_watchers.items():
            observer.stop()
            observer.join()
        
        self.file_watchers.clear()
    
    async def _reload_skill_file(self, file_path: str) -> None:
        """重新加载技能文件"""
        try:
            skill = await self._load_skill(Path(file_path))
            if skill:
                self.skill_registry.register(skill)
                
                # 调用回调
                for callback in self.reload_callbacks:
                    try:
                        await callback(skill)
                    except Exception as e:
                        logger.error(f"Reload callback error: {e}")
        except Exception as e:
            logger.error(f"Failed to reload skill file {file_path}: {e}")
    
    def add_reload_callback(self, callback: callable) -> None:
        """添加重载回调"""
        self.reload_callbacks.append(callback)


def create_skill_discovery(
    config: Optional[SkillDiscoveryConfig] = None,
    auto_reload: bool = False
) -> SkillDiscovery:
    """创建技能发现器工厂函数"""
    if auto_reload:
        return AutoReloadingSkillDiscovery(config)
    else:
        return SkillDiscovery(config)
