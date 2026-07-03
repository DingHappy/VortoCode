"""技能系统测试"""

import pytest

from src.skills.skill import (
    SkillMetadata,
    SkillArgument,
    SkillHook,
    SkillResult
)
from src.skills.registry import SkillRegistry, SimpleSkill
from src.skills.parser import SkillParser


class TestSkillMetadata:
    """技能元数据测试"""
    
    def test_create_metadata(self):
        """测试创建元数据"""
        metadata = SkillMetadata(
            name="test_skill",
            description="Test skill",
            version="1.0.0",
            author="Test Author"
        )
        
        assert metadata.name == "test_skill"
        assert metadata.description == "Test skill"
        assert metadata.version == "1.0.0"
        assert metadata.author == "Test Author"
    
    def test_default_values(self):
        """测试默认值"""
        metadata = SkillMetadata(name="test")
        
        assert metadata.version == "1.0.0"
        assert metadata.author == ""
        assert metadata.capabilities == []
        assert metadata.tools == []
        assert metadata.skills == []
        assert metadata.user_invocable is True


class TestSkillArgument:
    """技能参数测试"""
    
    def test_create_argument(self):
        """测试创建参数"""
        arg = SkillArgument(
            name="input",
            description="Input parameter",
            required=True,
            default=None
        )
        
        assert arg.name == "input"
        assert arg.description == "Input parameter"
        assert arg.required is True
        assert arg.default is None
    
    def test_optional_argument(self):
        """测试可选参数"""
        arg = SkillArgument(
            name="optional",
            description="Optional parameter",
            required=False,
            default="default_value"
        )
        
        assert arg.required is False
        assert arg.default == "default_value"


class TestSkillHook:
    """技能Hook测试"""
    
    def test_create_hook(self):
        """测试创建Hook"""
        hook = SkillHook(
            type="command",
            command="echo hello"
        )
        
        assert hook.type == "command"
        assert hook.command == "echo hello"
        assert hook.url is None
        assert hook.prompt is None
    
    def test_http_hook(self):
        """测试HTTP Hook"""
        hook = SkillHook(
            type="http",
            url="http://example.com/webhook"
        )
        
        assert hook.type == "http"
        assert hook.url == "http://example.com/webhook"


class TestSkillResult:
    """技能结果测试"""
    
    def test_create_result(self):
        """测试创建结果"""
        result = SkillResult(
            success=True,
            output="Test output",
            error=None,
            artifacts=["file1.py", "file2.py"],
            metrics={"duration": 1.5}
        )
        
        assert result.success is True
        assert result.output == "Test output"
        assert result.error is None
        assert len(result.artifacts) == 2
        assert result.metrics["duration"] == 1.5
    
    def test_failure_result(self):
        """测试失败结果"""
        result = SkillResult(
            success=False,
            error="Test error"
        )
        
        assert result.success is False
        assert result.error == "Test error"


class TestSimpleSkill:
    """简单技能测试"""
    
    @pytest.fixture
    def skill(self):
        metadata = SkillMetadata(
            name="test_skill",
            description="Test skill",
            capabilities=["testing"]
        )
        return SimpleSkill(
            metadata=metadata,
            instructions="Do something",
            arguments=[
                SkillArgument(name="input", required=True)
            ]
        )
    
    def test_skill_initialization(self, skill):
        """测试技能初始化"""
        assert skill.metadata.name == "test_skill"
        assert skill.instructions == "Do something"
        assert len(skill.arguments) == 1
    
    @pytest.mark.asyncio
    async def test_skill_execute(self, skill):
        """测试技能执行"""
        result = await skill.execute(
            args={"input": "test"},
            context={"workspace": "/tmp"}
        )
        
        assert result.success is True
        assert result.output is not None
    
    @pytest.mark.asyncio
    async def test_skill_execute_missing_required_arg(self, skill):
        """测试缺少必需参数"""
        result = await skill.execute(args={})
        
        assert result.success is False
        assert result.error is not None


class TestSkillRegistry:
    """技能注册表测试"""
    
    @pytest.fixture
    def registry(self):
        return SkillRegistry()
    
    def test_register_skill(self, registry):
        """测试注册技能"""
        metadata = SkillMetadata(name="test_skill")
        skill = SimpleSkill(metadata=metadata)
        
        registry.register(skill)
        
        assert registry.get("test_skill") is not None
        assert registry.get("test_skill").metadata.name == "test_skill"
    
    def test_unregister_skill(self, registry):
        """测试注销技能"""
        metadata = SkillMetadata(name="test_skill")
        skill = SimpleSkill(metadata=metadata)
        
        registry.register(skill)
        assert registry.get("test_skill") is not None
        
        registry.unregister("test_skill")
        assert registry.get("test_skill") is None
    
    def test_list_skills(self, registry):
        """测试列出技能"""
        metadata1 = SkillMetadata(name="skill1", capabilities=["cap1"])
        metadata2 = SkillMetadata(name="skill2", capabilities=["cap2"])
        
        skill1 = SimpleSkill(metadata=metadata1)
        skill2 = SimpleSkill(metadata=metadata2)
        
        registry.register(skill1)
        registry.register(skill2)
        
        skills = registry.list_skills()
        assert len(skills) == 2
    
    def test_list_skills_by_capability(self, registry):
        """测试按能力列出技能"""
        metadata1 = SkillMetadata(name="skill1", capabilities=["testing"])
        metadata2 = SkillMetadata(name="skill2", capabilities=["deployment"])
        metadata3 = SkillMetadata(name="skill3", capabilities=["testing", "deployment"])
        
        skill1 = SimpleSkill(metadata=metadata1)
        skill2 = SimpleSkill(metadata=metadata2)
        skill3 = SimpleSkill(metadata=metadata3)
        
        registry.register(skill1)
        registry.register(skill2)
        registry.register(skill3)
        
        testing_skills = registry.list_skills(capability="testing")
        deployment_skills = registry.list_skills(capability="deployment")
        
        assert len(testing_skills) == 2
        assert len(deployment_skills) == 2
    
    def test_discover_for_task(self, registry):
        """测试为任务发现技能"""
        metadata = SkillMetadata(
            name="test_skill",
            description="A skill for testing",
            capabilities=["testing"]
        )
        skill = SimpleSkill(metadata=metadata)
        registry.register(skill)
        
        # 测试匹配
        skills = registry.discover_for_task(["testing"])
        assert len(skills) > 0
        
        # 测试不匹配
        skills = registry.discover_for_task(["deployment"])
        assert len(skills) == 0


class TestSkillParser:
    """技能解析器测试"""
    
    def test_parse_metadata(self):
        """测试解析元数据"""
        content = """---
name: test-skill
description: A test skill
version: 1.0.0
capabilities:
  - testing
  - example
---

# Test Skill

This is a test skill.
"""
        metadata, instructions, arguments, hooks = SkillParser.parse_content(content)
        
        assert metadata.name == "test-skill"
        assert metadata.description == "A test skill"
        assert metadata.version == "1.0.0"
        assert "testing" in metadata.capabilities
        assert "example" in metadata.capabilities


if __name__ == "__main__":
    pytest.main([__file__])
