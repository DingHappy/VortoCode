"""研究员助手：给同事用的资料助理（每人一个，独立工作区与人设）。

需求原话：员工的助手**不改主项目代码**，职责是收集与整理资料，**但可以写代码来更好地
帮助收集资料**。这句话划出的边界很干净：

- 写代码 ✅ —— 在自己的沙盒工作区里写抓取/清洗脚本
- 跑脚本 ✅ —— run_command（沙箱内）
- 改主项目 / 落分支 / 开 PR ❌ —— 那是老板那台助手的事

关键设计：**run_command 与 dev 流水线分开给**。绑一起会逼人二选一——给全套（权限过大）
或连脚本都跑不了（等于废了"写代码辅助收集"）。
"""
from src.agents.capabilities import (EXTERNAL_CONTENT, RESEARCHER_PROFILE, SessionCapabilities,
                                     normalize_profile)
from src.agents.main_agent import build_agent_tools, make_confirm_gate
from src.im.channel import ChannelAdapter


def _names(**kw):
    return {t.name for t in build_agent_tools(".", confirm=make_confirm_gate(), **kw)}


# ---------------------------------------------------------------- 工具面

def test_researcher_cannot_touch_the_main_codebase():
    """不给改主项目代码/落分支/开 PR 的工具——这是它与老板那台助手的**根本区别**。"""
    res = _names(with_dev=False)
    for forbidden in ("dev_auto", "dev_isolated", "dev_parallel", "dev_resume",
                      "open_pr", "pr_fix"):
        assert forbidden not in res, f"研究员档不该有 {forbidden}"


def test_researcher_keeps_what_it_needs_to_work():
    """查资料、看页面、写脚本、跑脚本、把结果发给本人——一样都不能少。"""
    res = _names(with_dev=False)
    for needed in ("web_search", "web_fetch", "screenshot_page",
                   "read_file", "write_file", "edit_file",
                   "run_command", "send_image", "send_file"):
        assert needed in res, f"研究员档缺了 {needed}，干不了活"


def test_run_command_is_not_bundled_with_the_dev_pipeline():
    """**分开给**：去掉 dev 流水线不该顺手把 run_command 也拿走。

    绑在一起会逼人二选一：给全套（权限过大）或连脚本都跑不了（废了"写代码辅助收集"）。
    """
    assert "run_command" in _names(with_dev=False)
    assert "dev_auto" not in _names(with_dev=False)


def test_full_profile_unchanged():
    """老板那台不受影响——研究员档是**新增**，不是把大家都降级。"""
    full = _names()
    assert "dev_auto" in full and "open_pr" in full and "run_command" in full


# ---------------------------------------------------------------- 能力档（凭据维度）

def test_researcher_profile_cannot_reach_credentials():
    """凭据维度与 external 同样严：碰不到主机进程/认证远端/敏感文件。"""
    caps = SessionCapabilities.for_profile(RESEARCHER_PROFILE, ".")
    assert caps.allowed == frozenset({EXTERNAL_CONTENT})


def test_researcher_profile_is_recognized():
    assert normalize_profile("researcher") == RESEARCHER_PROFILE


def test_unknown_profile_still_fails_closed():
    """乱填的档位仍旧落到最严的 external，不因为新增了一档就放宽。"""
    assert normalize_profile("boss-mode") != RESEARCHER_PROFILE


# ---------------------------------------------------------------- 人设（本人自述）

def test_persona_round_trips_and_reaches_the_system_prompt(tmp_path):
    """人设由**本人自述**并持久化，重启后仍在，且真的进了系统提示。"""
    from src.im.bridge import IMBridge

    b = IMBridge(str(tmp_path), ChannelAdapter(), "u1", channel="dingtalk", with_dev=False)
    assert b._load_persona() == ""                       # 初次为空 → 会走引导
    b._save_persona("我是小林，做竞品调研，帮我盯同行的产品更新")

    b2 = IMBridge(str(tmp_path), ChannelAdapter(), "u1", channel="dingtalk", with_dev=False)
    assert "小林" in b2._load_persona()                   # 跨实例持久化
    assert "小林" in b2.agent._system("plan")             # 真的注入了系统提示


def test_persona_prompt_asks_the_person_not_the_admin(tmp_path):
    """引导语要问**本人**——本人两句话胜过旁人揣摩三段。"""
    from src.im.bridge import IMBridge

    b = IMBridge(str(tmp_path), ChannelAdapter(), "u1", channel="dingtalk", with_dev=False)
    p = b.persona_prompt()
    assert "怎么称呼你" in p and "希望我帮你做什么" in p


def test_personas_are_isolated_per_workspace(tmp_path):
    """每个助手一个工作区 → 人设天然按人隔离，A 的人设不会漏进 B。"""
    from src.im.bridge import IMBridge

    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    IMBridge(str(a_dir), ChannelAdapter(), "u1", channel="dingtalk",
             with_dev=False)._save_persona("我是甲，做市场")
    bb = IMBridge(str(b_dir), ChannelAdapter(), "u2", channel="dingtalk", with_dev=False)
    assert bb._load_persona() == "", "乙读到了甲的人设——工作区没隔离干净"
