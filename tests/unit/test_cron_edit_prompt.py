"""改已有 prompt 作业的内容（cron_set_prompt）——真机 2026-07-30 逼出来的能力。

主人在钉钉说"给新闻作业加上来源网页地址"，agent 回"我无法直接编辑 .vortocode/cron.yaml"，
然后给出两个难看的方案：建一个 `daily-tech-news-v2` 再停掉旧的、或者你自己去手改 yaml。

那是**我自己的设计**：`cron_set_web` 只改开关，注释里写着"要换内容请另建一个作业"。
理由当时是"不许改内容，所以劫持不了 relay_duty 这类已被信任的作业"——这条：

- **对 command 作业成立**：它只可能由人手写 yaml 产生，agent 从来造不出。改它继续锁死。
- **对 prompt 作业不成立**：`cron_add` 本来就能建任意 prompt 的作业（同样过确认门），
  "改 prompt" 与"建新的 + 停旧的"终态完全相同。锁它买不到安全，只把人逼去手改文件——
  这正是我在 #250 亲手诊断过的"安全剧场"，却在 prompt 这件事上又犯了一遍。
"""

import pytest

from src.gateway.cron import CronEditError, load_jobs, set_job_prompt

YAML = """\
# 主人手写的注释——改完必须还在
jobs:
  - name: relay_duty          # 受信任的 command 作业
    schedule: at 02:00
    command: python -m src.gateway.relay_duty
    enabled: true
  - name: daily-tech-news
    schedule: at 09:00
    prompt: |
      搜索今天的科技新闻并总结：
      1. 用 web_search 搜多个关键词
    allow_web: true
    enabled: true
    announce: im
  - name: tail-job
    schedule: every 30m
    prompt: 别动我
    enabled: false
"""


def _repo(tmp_path, text=YAML):
    (tmp_path / ".vortocode").mkdir(exist_ok=True)
    (tmp_path / ".vortocode" / "cron.yaml").write_text(text, encoding="utf-8")
    return str(tmp_path)


def _job(root, name):
    return next(j for j in load_jobs(root) if j.name == name)


# --------------------------------------------------------------- 该守的边界
def test_command_job_stays_locked(tmp_path):
    """**relay_duty 的保护一分不减**——改 command 作业是另一个风险类别。

    这条是整个改动能成立的前提：放开 prompt 作业的同时，人手写的确定性作业必须纹丝不动。
    """
    root = _repo(tmp_path)
    with pytest.raises(CronEditError, match="command"):
        set_job_prompt(root, "relay_duty", "干点别的")
    assert _job(root, "relay_duty").command == "python -m src.gateway.relay_duty"


def test_unknown_job_refused(tmp_path):
    root = _repo(tmp_path)
    with pytest.raises(CronEditError, match="无此作业"):
        set_job_prompt(root, "不存在", "x")


def test_empty_prompt_refused(tmp_path):
    root = _repo(tmp_path)
    with pytest.raises(CronEditError):
        set_job_prompt(root, "daily-tech-news", "   ")


# --------------------------------------------------------------- 改得对
def test_multiline_prompt_replaced_wholesale(tmp_path):
    root = _repo(tmp_path)
    new = ("搜索今天的科技新闻并总结：\n"
           "1. 用 web_search 搜多个关键词\n"
           "2. **每条必须附上来源网页的完整 URL**")
    set_job_prompt(root, "daily-tech-news", new)
    assert _job(root, "daily-tech-news").prompt == new


def test_neighbours_and_comments_survive(tmp_path):
    """整块重写多行字段最容易伤到相邻作业与注释——这正是我在 `_set_bool_field` 里
    只肯做单行改的原因。放开多行改就必须把这条钉死。"""
    root = _repo(tmp_path)
    set_job_prompt(root, "daily-tech-news", "全新内容\n第二行")

    text = (tmp_path / ".vortocode" / "cron.yaml").read_text(encoding="utf-8")
    assert "# 主人手写的注释——改完必须还在" in text
    assert "# 受信任的 command 作业" in text          # 行内注释也要在

    assert _job(root, "tail-job").prompt == "别动我"
    assert _job(root, "tail-job").enabled is False
    assert _job(root, "relay_duty").schedule.raw == "at 02:00"
    assert [j.name for j in load_jobs(root)] == ["relay_duty", "daily-tech-news", "tail-job"]


def test_sibling_fields_of_target_untouched(tmp_path):
    """只改 prompt——出网许可/启用态/排期/announce 一个都不许顺带变。

    尤其 allow_web：改内容时顺手把出网许可弄丢（或弄出来）都是安全事故。
    """
    root = _repo(tmp_path)
    set_job_prompt(root, "daily-tech-news", "新内容")
    j = _job(root, "daily-tech-news")
    assert (j.allow_web, j.enabled, j.schedule.raw, j.announce) == (True, True, "at 09:00", "im")


def test_single_line_prompt_job(tmp_path):
    root = _repo(tmp_path)
    set_job_prompt(root, "tail-job", "改成新的一行")
    assert _job(root, "tail-job").prompt == "改成新的一行"
    assert _job(root, "daily-tech-news").allow_web is True


def test_prompt_with_yaml_metacharacters_is_escaped(tmp_path):
    """prompt 里带冒号/引号/缩进是常态——手拼字符串必出转义洞，这里走真 yaml 序列化。

    若转义没做对，轻则文件坏，重则被 prompt 内容注出额外的 yaml 键（比如塞一个
    `allow_web: true` 进去）——那就是提示注入直接改配置。
    """
    root = _repo(tmp_path)
    nasty = ("标题: 值\n"
             '  allow_web: true\n'
             "- name: 注入进来的作业\n"
             "引号 ' 和 \" 都有")
    set_job_prompt(root, "tail-job", nasty)
    assert _job(root, "tail-job").prompt == nasty
    assert _job(root, "tail-job").allow_web is False, "prompt 内容注出了额外的 yaml 键"
    assert [j.name for j in load_jobs(root)] == ["relay_duty", "daily-tech-news", "tail-job"]


def test_multiline_uses_literal_block_for_humans(tmp_path):
    """多行落成字面块（`prompt: |`）而不是引号折行——这个文件主人是要手改的。"""
    root = _repo(tmp_path)
    set_job_prompt(root, "daily-tech-news", "第一行\n第二行\n第三行")
    text = (tmp_path / ".vortocode" / "cron.yaml").read_text(encoding="utf-8")
    assert "prompt: |" in text
    assert "第一行\n" in text and "第二行\n" in text


def test_flow_style_job_refused_instead_of_corrupted(tmp_path):
    """行内/流式 yaml 定位不到块 → 明确拒绝并让人工编辑，**绝不冒险乱改**。"""
    root = _repo(tmp_path, "jobs: [{name: inline, schedule: at 01:00, prompt: x}]\n")
    with pytest.raises(CronEditError, match="人工编辑"):
        set_job_prompt(root, "inline", "新内容")


# --------------------------------------------------------------- 工具层：确认必须给出差异
@pytest.mark.asyncio
async def test_tool_shows_before_and_after_in_confirm(tmp_path):
    """改一个**已被信任**的作业，社工面比新建高得多——人对"改一下 daily-tech-news"的警惕
    远低于"新建一个陌生作业"。唯一的补偿是让他看见**具体差异**，而不是只看见熟悉的名字。
    """
    from src.agents.tools.cron import build_cron_tools

    root = _repo(tmp_path)
    asked: list = []

    async def _confirm(msg):
        asked.append(msg)
        return True

    tool = {t.name: t for t in build_cron_tools(root, confirm=_confirm)}["cron_set_prompt"]
    out = await tool.handler({"name": "daily-tech-news", "prompt": "加上来源 URL"})

    assert asked, "改内容没有过确认门"
    prompt_text = asked[0]
    assert "改前" in prompt_text and "改后" in prompt_text
    assert "web_search" in prompt_text                       # 旧内容摆出来了
    assert "加上来源 URL" in prompt_text                      # 新内容也摆出来了
    assert "启用" in prompt_text                              # 启用态要说明白
    assert "✅" in out and _job(root, "daily-tech-news").prompt == "加上来源 URL"


@pytest.mark.asyncio
async def test_tool_refusal_leaves_file_untouched(tmp_path):
    from src.agents.tools.cron import build_cron_tools

    root = _repo(tmp_path)

    async def _no(_msg):
        return False

    tool = {t.name: t for t in build_cron_tools(root, confirm=_no)}["cron_set_prompt"]
    out = await tool.handler({"name": "daily-tech-news", "prompt": "别写进去"})
    assert "未改动" in out
    assert "web_search" in _job(root, "daily-tech-news").prompt


@pytest.mark.asyncio
async def test_tool_receipt_reflects_disk_not_intent(tmp_path):
    """回执按**盘上实际内容**重新渲染——2026-07-27 的教训：cron_add 的回执自相矛盾
    （文案说"已启用"、贴出的 yaml 却是 enabled: false），模型信了更具体的那半边。"""
    from src.agents.tools.cron import build_cron_tools

    root = _repo(tmp_path)

    async def _yes(_msg):
        return True

    tool = {t.name: t for t in build_cron_tools(root, confirm=_yes)}["cron_set_prompt"]
    out = await tool.handler({"name": "daily-tech-news", "prompt": "盘上校验用的内容"})
    assert "盘上校验用的内容" in out


@pytest.mark.asyncio
async def test_tool_is_a_write_tool(tmp_path):
    """`Tool.read_only` 默认 True——漏标就等于让 plan 模式改得动 cron.yaml（自测逮到过的真洞）。"""
    from src.agents.tools.cron import build_cron_tools

    tool = {t.name: t for t in build_cron_tools(_repo(tmp_path))}["cron_set_prompt"]
    assert tool.read_only is False


@pytest.mark.asyncio
async def test_tool_noop_when_identical(tmp_path):
    """内容没变就别惊动人——每一次多余的确认都在消耗"确认"这个信号的价值。"""
    from src.agents.tools.cron import build_cron_tools

    root = _repo(tmp_path)
    asked: list = []

    async def _confirm(msg):
        asked.append(msg)
        return True

    tools = {t.name: t for t in build_cron_tools(root, confirm=_confirm)}
    same = _job(root, "daily-tech-news").prompt
    out = await tools["cron_set_prompt"].handler({"name": "daily-tech-news", "prompt": same})
    assert "无需改动" in out and asked == []
