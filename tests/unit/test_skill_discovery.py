"""技能发现系统测试"""

import pytest
import asyncio
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from src.skills import (
    SkillVersion,
    SkillManifest,
    SkillDiscoveryConfig,
    SkillDiscovery,
    SkillVersionManager,
    create_skill_discovery
)


class TestSkillVersion:
    """技能版本测试"""
    
    def test_create_version(self):
        """测试创建版本"""
        version = SkillVersion(
            version="1.0.0",
            changelog="Initial release",
            deprecated=False
        )
        
        assert version.version == "1.0.0"
        assert version.changelog == "Initial release"
        assert version.deprecated is False
        assert isinstance(version.release_date, datetime)
    
    def test_version_defaults(self):
        """测试版本默认值"""
        version = SkillVersion(version="1.0.0")
        
        assert version.deprecated is False
        assert version.dependencies == []
        assert version.min_platform_version is None


class TestSkillManifest:
    """技能清单测试"""
    
    def test_create_manifest(self):
        """测试创建清单"""
        manifest = SkillManifest(
            name="test-skill",
            description="A test skill",
            author="Test Author",
            current_version="1.0.0"
        )
        
        assert manifest.name == "test-skill"
        assert manifest.description == "A test skill"
        assert manifest.author == "Test Author"
        assert manifest.current_version == "1.0.0"
        assert manifest.enabled is True
    
    def test_manifest_defaults(self):
        """测试清单默认值"""
        manifest = SkillManifest(name="test")
        
        assert manifest.license == "MIT"
        assert manifest.tags == []
        assert manifest.categories == []
        assert manifest.versions == []
        assert manifest.dependencies == []
        assert manifest.capabilities == []
        assert manifest.user_invocable is True


class TestSkillDiscoveryConfig:
    """技能发现配置测试"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = SkillDiscoveryConfig()
        
        assert config.auto_discover is True
        assert config.discover_interval == 300
        assert config.version_management is True
        assert config.allow_multiple_versions is False
        assert config.cache_enabled is True
        assert config.cache_ttl == 3600
    
    def test_custom_config(self):
        """测试自定义配置"""
        config = SkillDiscoveryConfig(
            skill_dirs=["/custom/path"],
            auto_discover=False,
            cache_enabled=False
        )
        
        assert config.skill_dirs == ["/custom/path"]
        assert config.auto_discover is False
        assert config.cache_enabled is False


class TestSkillDiscovery:
    """技能发现器测试"""
    
    @pytest.fixture
    def temp_skill_dir(self):
        """创建临时技能目录"""
        temp_dir = tempfile.mkdtemp()
        skill_dir = Path(temp_dir) / "skills"
        skill_dir.mkdir()
        
        # 创建测试技能
        skill_content = """---
name: test-skill
description: A test skill
capabilities:
  - testing
  - example
user_invocable: true
---

# Test Skill

This is a test skill.

## Instructions

1. Do something
2. Do something else
"""
        
        skill_file = skill_dir / "test-skill" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text(skill_content)
        
        yield str(skill_dir)
        
        # 清理
        shutil.rmtree(temp_dir)
    
    @pytest.mark.asyncio
    async def test_discover_skills(self, temp_skill_dir):
        """测试发现技能"""
        config = SkillDiscoveryConfig(
            skill_dirs=[temp_skill_dir],
            auto_discover=False
        )
        
        discovery = SkillDiscovery(config)
        skills = await discovery.discover_skills()
        
        assert len(skills) == 1
        assert skills[0].metadata.name == "test-skill"
        assert "testing" in skills[0].metadata.capabilities
    
    @pytest.mark.asyncio
    async def test_discover_for_task(self, temp_skill_dir):
        """测试为任务发现技能"""
        config = SkillDiscoveryConfig(
            skill_dirs=[temp_skill_dir],
            auto_discover=False
        )
        
        discovery = SkillDiscovery(config)
        await discovery.discover_skills()
        
        # 基于任务发现技能
        skills = await discovery.discover_for_task(
            "Write a test",
            requirements=["testing"]
        )
        
        assert len(skills) > 0
        assert skills[0].metadata.name == "test-skill"
    
    @pytest.mark.asyncio
    async def test_skill_statistics(self, temp_skill_dir):
        """测试技能统计"""
        config = SkillDiscoveryConfig(
            skill_dirs=[temp_skill_dir],
            auto_discover=False
        )
        
        discovery = SkillDiscovery(config)
        await discovery.discover_skills()
        
        stats = discovery.get_statistics()
        
        assert stats["total_skills"] == 1
        assert stats["skill_dirs"] == 1


class TestSkillVersionManager:
    """技能版本管理器测试"""
    
    @pytest.fixture
    def discovery_with_manifest(self):
        """创建带清单的发现器"""
        temp_dir = tempfile.mkdtemp()
        skill_dir = Path(temp_dir) / "skills"
        skill_dir.mkdir()
        
        # 创建技能目录
        skill_path = skill_dir / "test-skill"
        skill_path.mkdir()
        
        # 创建SKILL.md
        skill_content = """---
name: test-skill
description: A test skill
capabilities:
  - testing
---

# Test Skill

This is a test skill.
"""
        (skill_path / "SKILL.md").write_text(skill_content)
        
        # 创建manifest.yaml
        manifest_content = """name: test-skill
description: A test skill
author: Test Author
current_version: "1.0.0"
versions:
  - version: "1.0.0"
    changelog: Initial release
  - version: "1.1.0"
    changelog: Added new feature
"""
        (skill_path / "manifest.yaml").write_text(manifest_content)
        
        config = SkillDiscoveryConfig(
            skill_dirs=[str(skill_dir)],
            auto_discover=False
        )
        
        discovery = SkillDiscovery(config)
        
        yield discovery
        
        # 清理
        shutil.rmtree(temp_dir)
    
    @pytest.mark.asyncio
    async def test_get_skill_versions(self, discovery_with_manifest):
        """测试获取技能版本"""
        await discovery_with_manifest.discover_skills()
        
        version_manager = SkillVersionManager(discovery_with_manifest)
        versions = await version_manager.get_skill_versions("test-skill")
        
        assert len(versions) == 2
        assert versions[0].version == "1.0.0"
        assert versions[1].version == "1.1.0"
    
    @pytest.mark.asyncio
    async def test_get_latest_version(self, discovery_with_manifest):
        """测试获取最新版本"""
        await discovery_with_manifest.discover_skills()
        
        version_manager = SkillVersionManager(discovery_with_manifest)
        latest = await version_manager.get_latest_version("test-skill")
        
        assert latest is not None
        assert latest.version == "1.1.0"
    
    @pytest.mark.asyncio
    async def test_register_version(self, discovery_with_manifest):
        """测试注册版本"""
        await discovery_with_manifest.discover_skills()
        
        version_manager = SkillVersionManager(discovery_with_manifest)
        
        new_version = SkillVersion(
            version="2.0.0",
            changelog="Major update"
        )
        
        success = await version_manager.register_version("test-skill", new_version)
        
        assert success is True
        
        # 验证版本已添加
        versions = await version_manager.get_skill_versions("test-skill")
        assert len(versions) == 3
        
        # 验证最新版本
        latest = await version_manager.get_latest_version("test-skill")
        assert latest.version == "2.0.0"
    
    @pytest.mark.asyncio
    async def test_deprecate_version(self, discovery_with_manifest):
        """测试废弃版本"""
        await discovery_with_manifest.discover_skills()
        
        version_manager = SkillVersionManager(discovery_with_manifest)
        
        success = await version_manager.deprecate_version("test-skill", "1.0.0")
        
        assert success is True
        
        # 验证版本已废弃
        versions = await version_manager.get_skill_versions("test-skill")
        v1 = next(v for v in versions if v.version == "1.0.0")
        assert v1.deprecated is True


class TestCreateSkillDiscovery:
    """创建技能发现器工厂测试"""
    
    def test_create_default(self):
        """测试创建默认发现器"""
        discovery = create_skill_discovery()
        
        assert isinstance(discovery, SkillDiscovery)
        assert discovery.config.auto_discover is True
    
    def test_create_custom_config(self):
        """测试创建自定义配置发现器"""
        config = SkillDiscoveryConfig(auto_discover=False)
        discovery = create_skill_discovery(config)
        
        assert isinstance(discovery, SkillDiscovery)
        assert discovery.config.auto_discover is False
    
    def test_create_auto_reload(self):
        """测试创建自动重载发现器"""
        discovery = create_skill_discovery(auto_reload=True)
        
        # 注意：AutoReloadingSkillDiscovery是SkillDiscovery的子类
        assert isinstance(discovery, SkillDiscovery)


if __name__ == "__main__":
    pytest.main([__file__])
