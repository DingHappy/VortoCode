"""技能系统 - 类似 Claude Code 的 Skills"""

import os
import yaml
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SkillArgument(BaseModel):
    """技能参数"""
    name: str
    description: str = ""
    required: bool = True
    default: Any = None


class SkillHook(BaseModel):
    """技能钩子"""
    event: str  # pre_execute, post_execute
    command: str


class SkillDefinition(BaseModel):
    """技能定义"""
    name: str
    description: str
    version: str = "1.0.0"
    author: str = ""
    category: str = "general"
    arguments: List[SkillArgument] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    hooks: List[SkillHook] = Field(default_factory=list)
    content: str = ""
    examples: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)


class Skill:
    """技能实例"""
    
    def __init__(self, definition: SkillDefinition, path: Path):
        self.definition = definition
        self.path = path
        self.loaded = False
    
    @property
    def name(self) -> str:
        return self.definition.name
    
    @property
    def description(self) -> str:
        return self.definition.description
    
    def execute(self, arguments: Dict[str, Any] = None) -> str:
        """执行技能"""
        content = self.definition.content
        
        # 替换参数
        if arguments:
            for key, value in arguments.items():
                placeholder = f"${{{key}}}"
                content = content.replace(placeholder, str(value))
        
        return content
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.definition.name,
            "description": self.definition.description,
            "version": self.definition.version,
            "category": self.definition.category,
            "arguments": [
                {
                    "name": a.name,
                    "description": a.description,
                    "required": a.required
                }
                for a in self.definition.arguments
            ],
            "capabilities": self.definition.capabilities,
            "tools": self.definition.tools,
            "examples": self.definition.examples
        }


class SkillRegistry:
    """技能注册表"""
    
    def __init__(self):
        self.skills: Dict[str, Skill] = {}
        self.skill_dirs: List[Path] = []
    
    def add_skill_directory(self, directory: Path):
        """添加技能目录"""
        if directory not in self.skill_dirs:
            self.skill_dirs.append(directory)
            self._scan_directory(directory)
    
    def _scan_directory(self, directory: Path):
        """扫描技能目录"""
        if not directory.exists():
            return
        
        for skill_dir in directory.iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    try:
                        skill = self._load_skill(skill_md)
                        self.skills[skill.name] = skill
                        logger.info(f"Loaded skill: {skill.name}")
                    except Exception as e:
                        logger.error(f"Failed to load skill from {skill_md}: {e}")
    
    def _load_skill(self, skill_path: Path) -> Skill:
        """加载技能"""
        content = skill_path.read_text(encoding='utf-8')
        
        # 解析 frontmatter 和内容
        parts = content.split('---', 2)
        
        if len(parts) >= 3:
            # 有 frontmatter
            frontmatter = yaml.safe_load(parts[1])
            skill_content = parts[2].strip()
        else:
            # 没有 frontmatter
            frontmatter = {}
            skill_content = content
        
        # 创建技能定义
        definition = SkillDefinition(
            name=frontmatter.get('name', skill_path.parent.name),
            description=frontmatter.get('description', ''),
            version=frontmatter.get('version', '1.0.0'),
            author=frontmatter.get('author', ''),
            category=frontmatter.get('category', 'general'),
            arguments=[
                SkillArgument(**arg) 
                for arg in frontmatter.get('arguments', [])
            ],
            capabilities=frontmatter.get('capabilities', []),
            tools=frontmatter.get('tools', []),
            hooks=[
                SkillHook(**hook) 
                for hook in frontmatter.get('hooks', [])
            ],
            content=skill_content,
            examples=frontmatter.get('examples', []),
            dependencies=frontmatter.get('dependencies', [])
        )
        
        return Skill(definition=definition, path=skill_path)
    
    def get_skill(self, name: str) -> Optional[Skill]:
        """获取技能"""
        return self.skills.get(name)
    
    def list_skills(self, category: str = None) -> List[Skill]:
        """列出技能"""
        if category:
            return [s for s in self.skills.values() if s.definition.category == category]
        return list(self.skills.values())
    
    def search_skills(self, query: str) -> List[Skill]:
        """搜索技能"""
        query_lower = query.lower()
        results = []
        
        for skill in self.skills.values():
            if (query_lower in skill.name.lower() or 
                query_lower in skill.description.lower() or
                any(query_lower in cap.lower() for cap in skill.definition.capabilities)):
                results.append(skill)
        
        return results
    
    def reload(self):
        """重新加载所有技能"""
        self.skills.clear()
        for directory in self.skill_dirs:
            self._scan_directory(directory)


class SkillManager:
    """技能管理器"""
    
    def __init__(self, workdir: str):
        self.workdir = Path(workdir)
        self.registry = SkillRegistry()
        
        # 添加默认技能目录
        self._add_default_skills()
        
        # 添加项目技能目录
        project_skills = self.workdir / ".vortocode" / "skills"
        if project_skills.exists():
            self.registry.add_skill_directory(project_skills)
    
    def _add_default_skills(self):
        """添加默认技能"""
        # 创建默认技能目录
        default_skills_dir = Path(__file__).parent.parent.parent / "skills"
        if default_skills_dir.exists():
            self.registry.add_skill_directory(default_skills_dir)
        
        # 创建内置技能
        self._create_builtin_skills()
    
    def _create_builtin_skills(self):
        """创建内置技能"""
        builtin_skills = [
            SkillDefinition(
                name="code-review",
                description="审查代码质量、安全性和最佳实践",
                category="review",
                capabilities=["code_review", "security"],
                tools=["read_file"],
                content="""请审查以下代码：

${code}

检查以下方面：
1. 代码质量和可读性
2. 安全漏洞
3. 性能问题
4. 最佳实践
5. 错误处理

提供详细的审查报告。"""
            ),
            SkillDefinition(
                name="write-tests",
                description="为代码编写测试用例",
                category="testing",
                capabilities=["unit_testing", "integration_testing"],
                tools=["read_file", "write_file"],
                content="""为以下代码编写测试：

${code}

要求：
1. 覆盖所有公共方法
2. 测试边界条件
3. 测试错误情况
4. 使用 ${framework} 测试框架"""
            ),
            SkillDefinition(
                name="refactor",
                description="重构代码以提高质量",
                category="development",
                capabilities=["code_refactoring"],
                tools=["read_file", "write_file"],
                content="""重构以下代码：

${code}

目标：
1. 提高可读性
2. 减少重复
3. 改善命名
4. 优化结构
5. 保持功能不变"""
            ),
            SkillDefinition(
                name="debug",
                description="调试和修复错误",
                category="development",
                capabilities=["bug_analysis"],
                tools=["read_file", "execute_command"],
                content="""调试以下问题：

${issue}

步骤：
1. 分析错误信息
2. 定位问题根源
3. 提供修复方案
4. 验证修复"""
            ),
            SkillDefinition(
                name="document",
                description="生成代码文档",
                category="documentation",
                capabilities=["documentation"],
                tools=["read_file", "write_file"],
                content="""为以下代码生成文档：

${code}

包括：
1. 模块说明
2. 函数/方法文档
3. 使用示例
4. API 文档"""
            ),
            SkillDefinition(
                name="optimize",
                description="优化代码性能",
                category="development",
                capabilities=["performance_analysis"],
                tools=["read_file", "execute_command"],
                content="""优化以下代码的性能：

${code}

分析：
1. 时间复杂度
2. 空间复杂度
3. 瓶颈位置
4. 优化建议"""
            ),
            SkillDefinition(
                name="api-design",
                description="设计 RESTful API",
                category="architecture",
                capabilities=["api_design"],
                tools=["write_file"],
                content="""设计 API：${description}

要求：
1. RESTful 风格
2. 一致的命名规范
3. 合理的资源划分
4. 完整的错误处理
5. OpenAPI 文档"""
            ),
            SkillDefinition(
                name="database-design",
                description="设计数据库模型",
                category="architecture",
                capabilities=["database_design"],
                tools=["write_file"],
                content="""设计数据库模型：${description}

考虑：
1. 表结构设计
2. 关系定义
3. 索引优化
4. 数据完整性
5. 迁移脚本"""
            )
        ]
        
        for skill_def in builtin_skills:
            skill = Skill(definition=skill_def, path=Path("builtin"))
            self.registry.skills[skill.name] = skill
    
    def get_skill(self, name: str) -> Optional[Skill]:
        """获取技能"""
        return self.registry.get_skill(name)
    
    def list_skills(self, category: str = None) -> List[Skill]:
        """列出技能"""
        return self.registry.list_skills(category)
    
    def search_skills(self, query: str) -> List[Skill]:
        """搜索技能"""
        return self.registry.search_skills(query)
    
    def execute_skill(self, name: str, arguments: Dict[str, Any] = None) -> str:
        """执行技能"""
        skill = self.get_skill(name)
        if not skill:
            raise ValueError(f"Skill not found: {name}")
        
        return skill.execute(arguments)
    
    def create_skill(self, name: str, description: str, content: str, **kwargs) -> Skill:
        """创建新技能"""
        # 创建技能目录
        skill_dir = self.workdir / ".vortocode" / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建 SKILL.md
        skill_md = skill_dir / "SKILL.md"
        
        # 生成 frontmatter
        frontmatter = {
            'name': name,
            'description': description,
            'version': '1.0.0',
            **kwargs
        }
        
        # 写入文件
        with open(skill_md, 'w', encoding='utf-8') as f:
            f.write('---\n')
            yaml.dump(frontmatter, f, default_flow_style=False)
            f.write('---\n\n')
            f.write(content)
        
        # 重新加载
        self.registry.reload()
        
        return self.get_skill(name)
