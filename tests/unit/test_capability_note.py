"""能力更新通告——旧会话历史里的「我做不到」不能在升级后继续被当真话复读。

真机 2026-07-27：#243 把 screenshot_page 接上并部署后，钉钉里再要截图**仍被拒**——
会话历史跨重启持久化，里面躺着升级前那句（当时如实的）「我没有浏览器截图功能」，
模型对自己说过话的一致性压过了系统提示与工具清单，把过期否认逐字复读（连推荐的
第三方工具名都一样）。提示词治不了：#233 的「不许凭印象否认」就在系统提示里，
照样输给上下文先例。

修法：存盘带上工具清单（session_store `tool_names`），复原时与新装配清单 diff
（agent_session.capability_update_note / inject_capability_note），有出入就把
「清单变了」这个**事实**注进历史，让新旧两句话在上下文里正面相遇。

镜像方向同样要防：研究员档（with_dev=False）复原一份满配旧会话时，历史里它
「跑过 dev_auto」，不注「移除」通告它就会许诺自己已经没有的能力。
"""

from src.gateway.agent_session import capability_update_note, inject_capability_note
from src.gateway.sessions import SessionTable
from src.im.bridge import IMBridge
from src.im.channel import ChannelAdapter
from src.web.session_store import load_session, save_session

OWNER = "owner-1"
SID = f"sid-test-{OWNER}"

# 真机那句过期否认（原文缩写）：升级前如实、升级后成谎——测试里就用它当旧历史
STALE_DENIAL = [
    {"role": "user", "content": "帮我截一张谷歌网站的首页"},
    {"role": "assistant", "content": "我无法为你截图。我没有浏览器截图功能，只能读取网页的文本内容。"},
]


class _Adapter(ChannelAdapter):
    """构造 bridge 用的最小壳——本文件只测装配/复原，不跑回合，不会碰收发。"""
    owner_id = OWNER


class _LLM:
    async def chat(self, messages, **kwargs):
        return {"content": "x"}


def _bridge(tmp_path, **kw):
    return IMBridge(str(tmp_path), _Adapter(), OWNER, channel="test", llm=_LLM(), **kw)


def _notes(history):
    return [m for m in history if "[能力更新]" in str(m.get("content", ""))]


# ---------------------------------------------------------------- 纯判定（内核）
def test_no_change_means_no_note():
    assert capability_update_note(["a", "b"], {"a": 1, "b": 2}.keys()) is None


def test_added_and_removed_are_listed():
    note = capability_update_note(["old_tool", "keep"], ["keep", "screenshot_page"])
    assert "新增：screenshot_page" in note and "移除：old_tool" in note
    assert "做不到" in note                      # 通告必须点名「你自己说过的做不到可能过时」


def test_legacy_session_without_recorded_list_gets_generic_note():
    """None=「从没记录过」≠「记录过且为空」：给不出 diff 也要提醒，不能装作没变。"""
    note = capability_update_note(None, ["screenshot_page"])
    assert note and "[能力更新]" in note and "工具清单为准" in note


def test_empty_current_list_says_nothing():
    assert capability_update_note(["a"], []) is None   # 连当前清单都拿不到就别装懂


def test_long_lists_are_capped_not_dumped():
    note = capability_update_note([], [f"t{i:02d}" for i in range(12)])
    assert "等12个" in note and "t09" not in note       # 只展示前 8 个，其余计数


def test_injection_skips_empty_history():
    class _A:
        history: list = []
        tools = {"a": 1}
    assert inject_capability_note(_A(), {"tool_names": []}) is False   # 没有旧话可过期


# ---------------------------------------------------------------- IM 端（真机复现路径）
def test_im_restore_announces_new_tool(tmp_path):
    """复现 2026-07-27：旧会话存档没有 screenshot_page → 新装配注入点名它的通告。"""
    names = sorted(_bridge(tmp_path).agent.tools)      # 当前装配的真实清单
    assert "screenshot_page" in names                  # 工具真的在 IM 端（否则否认就是真话）
    save_session(str(tmp_path), SID, [], list(STALE_DENIAL), None,
                 tool_names=[n for n in names if n != "screenshot_page"])

    b = _bridge(tmp_path)
    notes = _notes(b.agent.history)
    assert len(notes) == 1 and "新增：screenshot_page" in notes[0]["content"]
    assert b.agent.history[-1] is notes[0]             # 注在末尾，紧邻下一个真实回合


def test_im_restore_unchanged_list_stays_byte_identical(tmp_path):
    """什么都没变就一个字都别加——通告冒多了，人就学会忽略它了。

    存盘必须连指纹一起写（真实的 `_persist` 就是这么做的）：只写 tool_names 会落进
    "存档早于指纹机制"那条规则，那是**另一个**场景。
    """
    from src.gateway.agent_session import session_behavior_fp

    agent = _bridge(tmp_path).agent
    save_session(str(tmp_path), SID, [], list(STALE_DENIAL), None,
                 tool_names=sorted(agent.tools), behavior_fp=session_behavior_fp(agent))
    assert _notes(_bridge(tmp_path).agent.history) == []


def test_persist_records_the_fingerprint_so_the_next_restore_is_quiet(tmp_path):
    """端到端：真实存盘路径必须带上指纹，否则每次重启都会被当成"升级过"而通告。"""
    b = _bridge(tmp_path)
    b.agent.history = list(STALE_DENIAL)
    b._persist()
    assert load_session(str(tmp_path), SID)["behavior_fp"], "_persist 没写指纹"
    assert _notes(_bridge(tmp_path).agent.history) == []


def test_im_legacy_session_gets_generic_note_once(tmp_path):
    """升级瞬间磁盘上就是这种档（无 tool_names 字段）——必须被通用通告救到，且只注一次。"""
    save_session(str(tmp_path), SID, [], list(STALE_DENIAL), None)   # 老格式：无清单
    b1 = _bridge(tmp_path)
    assert len(_notes(b1.agent.history)) == 1
    b1._persist()                                      # 存盘 → 从此带清单
    b2 = _bridge(tmp_path)
    assert len(_notes(b2.agent.history)) == 1          # 不重复注

    saved = load_session(str(tmp_path), SID)
    assert "screenshot_page" in (saved["tool_names"] or [])   # 存盘真的带上了清单


def test_researcher_restoring_full_session_hears_about_removals(tmp_path):
    """镜像方向：满配旧会话交给研究员档复原，得知道 dev_auto 已经没了。"""
    full = sorted(_bridge(tmp_path).agent.tools)
    assert "dev_auto" in full
    save_session(str(tmp_path), SID, [], list(STALE_DENIAL), None, tool_names=full)

    b = _bridge(tmp_path, with_dev=False)
    notes = _notes(b.agent.history)
    assert len(notes) == 1 and "移除：" in notes[0]["content"]
    assert "dev_auto" in notes[0]["content"]


# ---------------------------------------------------------------- Web 端（同一内核，另一咽喉）
def test_web_session_table_restore_and_persist(tmp_path):
    from types import SimpleNamespace

    key = "sid-web-1"
    save_session(str(tmp_path), key, [], list(STALE_DENIAL), None, tool_names=["a"])
    tbl = SessionTable()
    sess = tbl.get(key, repo_root=str(tmp_path),
                   factory=lambda: SimpleNamespace(history=[], plan=None, tools={"a": 1, "b": 2}))
    notes = _notes(sess["agent"].history)
    assert len(notes) == 1 and "新增：b" in notes[0]["content"]

    tbl.persist(key, str(tmp_path))                    # Web 存盘同样要盖上清单
    assert load_session(str(tmp_path), key)["tool_names"] == ["a", "b"]


def test_stub_agent_without_tools_never_breaks_restore(tmp_path):
    """通告是尽力而为：拿不到工具清单（协议桩/降级装配）时静默跳过，不许影响会话可用。"""
    from types import SimpleNamespace

    key = "sid-web-2"
    save_session(str(tmp_path), key, [], list(STALE_DENIAL), None, tool_names=["a"])
    sess = SessionTable().get(key, repo_root=str(tmp_path),
                              factory=lambda: SimpleNamespace(history=[], plan=None))
    assert _notes(sess["agent"].history) == []
    assert len(sess["agent"].history) == len(STALE_DENIAL)   # 历史原样复原


# ---------------------------------------------------------------- 存储层字段语义
def test_save_without_tool_names_preserves_recorded_list(tmp_path):
    """不带清单的调用方（升级期的旧代码路径）不许把已记录的清单抹掉。"""
    save_session(str(tmp_path), SID, [], [], None, tool_names=["a", "b"])
    save_session(str(tmp_path), SID, [], [{"role": "user", "content": "hi"}], None)
    assert load_session(str(tmp_path), SID)["tool_names"] == ["a", "b"]


def test_legacy_file_loads_tool_names_as_none_not_empty(tmp_path):
    save_session(str(tmp_path), SID, [], [], None)
    assert load_session(str(tmp_path), SID)["tool_names"] is None


# ---------------------------------------------------------------- 行为契约变化（工具清单未变）
#
# 真机 2026-07-27 二次翻车：#247 只改了系统规则与工具描述、**工具清单一个没动**，于是上面那套
# 通告静默通过；而历史里三条旧回复还写着"请按 Tab 切换到 build 模式"，模型照抄自己说过的话。
# 线上系统提示当时已无 Tab、工具结果里也没有——**修好的规则被旧上下文压过去了**。
# 结论：行为变了也会让旧结论过期，不只是工具增删。

def test_behavior_change_alone_still_announces(tmp_path):
    note = capability_update_note(["a"], ["a"], "fp-old", "fp-new")
    assert note and "行为规则" in note and "工具清单未变" in note


def test_nothing_changed_stays_silent(tmp_path):
    assert capability_update_note(["a"], ["a"], "same", "same") is None


def test_tool_and_behavior_change_are_reported_together(tmp_path):
    note = capability_update_note(["a"], ["a", "b"], "fp-old", "fp-new")
    assert "新增：b" in note and "行为规则亦有更新" in note


def test_archive_predating_fingerprints_is_treated_as_upgraded(tmp_path):
    """存档缺少我们现在会记录的字段 = **确实升级过**（那个字段是被某次升级加进来的），不是猜测。

    真机 2026-07-27：#248 给会话加了指纹，可当时那个中毒会话的存档只有 tool_names、清单又
    恰好没变 → 通告静默通过，历史里三条"请按 Tab"继续毒着。只触发一次（下次存盘就带指纹了）。
    """
    assert capability_update_note(["a"], ["a"], None, "fp-new") is not None


def test_agent_without_fingerprint_support_never_triggers_noise(tmp_path):
    """反向：当前算不出指纹（协议桩/降级装配）时别乱报——那不是"变了"的证据。"""
    assert capability_update_note(["a"], ["a"], "fp-old", None) is None
    assert capability_update_note(["a"], ["a"], None, None) is None


def test_fingerprint_tracks_tool_descriptions_not_just_names(tmp_path):
    """#247 改的正是工具描述与规则文案——名字没动，指纹必须动。"""
    from src.agents.main_agent import MainAgent, build_read_tools

    tools = build_read_tools(str(tmp_path))
    base = MainAgent(tools).behavior_fingerprint()
    assert base == MainAgent(build_read_tools(str(tmp_path))).behavior_fingerprint(), "同样装配指纹应稳定"

    tools[0].description += "（改了描述）"
    assert MainAgent(tools).behavior_fingerprint() != base


def test_fingerprint_tracks_mode_switch_hint(tmp_path):
    """端申报的切换方式属能力契约的一部分（#247 就是靠它从空变成 /mode build）。"""
    from src.agents.main_agent import MainAgent, build_read_tools

    a = MainAgent(build_read_tools(str(tmp_path))).behavior_fingerprint()
    b = MainAgent(build_read_tools(str(tmp_path)),
                  mode_switch_hint="回复 `/mode build`").behavior_fingerprint()
    assert a != b


def test_fingerprint_ignores_repo_content(tmp_path):
    """仓库记忆/项目指令变了不代表能力边界变了——混进去会让通告天天冒。"""
    from src.agents.main_agent import MainAgent, build_read_tools

    tools = build_read_tools(str(tmp_path))
    assert (MainAgent(tools, extra_system="项目约定 A").behavior_fingerprint()
            == MainAgent(tools, extra_system="完全不同的项目约定 B").behavior_fingerprint())


def test_fingerprint_survives_round_trip_to_disk(tmp_path):
    from src.agents.main_agent import MainAgent, build_read_tools

    fp = MainAgent(build_read_tools(str(tmp_path))).behavior_fingerprint()
    save_session(str(tmp_path), SID, [], [], None, tool_names=["a"], behavior_fp=fp)
    assert load_session(str(tmp_path), SID)["behavior_fp"] == fp

    save_session(str(tmp_path), SID, [], [{"role": "user", "content": "x"}], None)
    assert load_session(str(tmp_path), SID)["behavior_fp"] == fp, "不带指纹的调用方把已记录的抹了"


def test_im_restore_announces_behavior_change(tmp_path):
    """端到端：存档工具清单一致、仅指纹不同 → 复原时仍要注入通告。"""
    names = sorted(_bridge(tmp_path).agent.tools)
    save_session(str(tmp_path), SID, [], list(STALE_DENIAL), None,
                 tool_names=names, behavior_fp="stale-fingerprint")
    notes = _notes(_bridge(tmp_path).agent.history)
    assert len(notes) == 1 and "行为规则" in notes[0]["content"]
