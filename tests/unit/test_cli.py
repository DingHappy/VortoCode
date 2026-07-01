"""CLI 参数解析测试（离线，不触发任何业务执行）。

只验证 argparse 层：无命令给总览、必填参数被强制、子命令能解析。
不调用 run_*（那些需 LLM/网络），所以这里只测“解析与分发前”的行为。
"""

import sys

import pytest

from src import cli


def test_no_command_prints_overview_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vortocode"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "可用命令" in out
    assert "self-analyze" in out and "self-fix" in out      # 命令带说明
    assert "常用示例" in out                                  # 有示例


def test_self_fix_requires_paths(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vortocode", "self-fix"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code != 0                                 # argparse 缺必填参数 -> 退出码 2
    err = capsys.readouterr().err
    assert "--paths" in err


def test_run_command_retired(monkeypatch, capsys):
    # 5 角色批处理入口 vc run 已退役（被 agent 隔离 dev 流水线取代）→ 应作为未知命令报错
    monkeypatch.setattr(sys, "argv", ["vortocode", "run", "-t", "x"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code != 0                       # 无效命令：非零退出


def test_help_is_per_command(monkeypatch, capsys):
    # 子命令 -h 只显示该命令自己的参数（不再是全局糊一脸）
    monkeypatch.setattr(sys, "argv", ["vortocode", "self-improve", "-h"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--apply" in out and "--max-fixes" in out
    assert "--stage" not in out          # quant 的参数不该出现在 self-improve 帮助里


# ---- 长跑命令的运行计时（_with_progress）----

@pytest.mark.asyncio
async def test_with_progress_returns_value_and_silent_on_non_tty(monkeypatch, capsys):
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)

    async def work():
        return 42

    assert await cli._with_progress(work(), "x") == 42
    assert capsys.readouterr().err == ""              # 非 TTY（管道/日志）完全静默


@pytest.mark.asyncio
async def test_with_progress_propagates_exception(monkeypatch):
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)

    async def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await cli._with_progress(boom(), "x")


@pytest.mark.asyncio
async def test_with_progress_ticks_and_clears_on_tty(monkeypatch, capsys):
    import asyncio
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

    async def work():
        await asyncio.sleep(0.05)
        return "ok"

    assert await cli._with_progress(work(), "测试中") == "ok"
    err = capsys.readouterr().err
    assert "测试中" in err and "⏳" in err             # TTY 下出现计时行
    assert "\x1b[K" in err                             # 结束清掉计时行


# ---- headless agent（仿 claude -p）----

class _ScriptedLLM:
    """按调用顺序依次返回预设 content；用完停在最后一条（与 test_main_agent 同构）。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages, **kwargs):
        i = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return {"content": self.responses[i]}


def test_agent_subcommand_parses(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vortocode", "agent", "-h"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--build" in out and "--yes" in out and "--json" in out
    assert "claude -p" in out                          # 帮助点明对标 headless 模式


def test_agent_missing_prompt_exits_2(monkeypatch):
    # 非 TTY stdin 但给空 → 解析为无 prompt → 退出码 2
    monkeypatch.setattr(sys, "argv", ["vortocode", "agent"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)   # 交互式 → 不读 stdin
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2


def test_read_prompt_arg_variants(monkeypatch):
    assert cli._read_prompt_arg("  hi  ") == "hi"            # 显式位置参数
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin\n"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert cli._read_prompt_arg("-") == "from stdin"         # 显式 - 读 stdin
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped\n"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert cli._read_prompt_arg(None) == "piped"             # 管道（非 TTY）省略也读 stdin


def test_maybe_expand_command(tmp_path):
    cmds = tmp_path / ".vortocode" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "review.md").write_text("审查代码找 bug：$ARGUMENTS", encoding="utf-8")
    (cmds / "ping.md").write_text("说一句你好。", encoding="utf-8")
    d = str(tmp_path)
    assert cli._maybe_expand_command("/review src/foo.py", d) == "审查代码找 bug：src/foo.py"
    assert cli._maybe_expand_command("/ping", d) == "说一句你好。"
    assert cli._maybe_expand_command("/nope x", d) == "/nope x"        # 未知命令原样
    assert cli._maybe_expand_command("普通问题", d) == "普通问题"        # 非命令原样
    assert cli._maybe_expand_command("", d) == ""


def test_cli_history_save_load_roundtrip(tmp_path):
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    cli._save_cli_history(str(tmp_path), hist)
    assert (tmp_path / ".vortocode" / "cli_session.json").is_file()
    assert cli._load_cli_history(str(tmp_path)) == hist
    assert cli._load_cli_history(str(tmp_path / "nope")) == []      # 缺文件 → []


def test_cli_history_strips_multimodal_blocks(tmp_path):
    # 多模态 content（图/音 base64）落盘时折成纯文本，不写 base64
    hist = [{"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 9999}}]}]
    cli._save_cli_history(str(tmp_path), hist)
    raw = (tmp_path / ".vortocode" / "cli_session.json").read_text()
    assert "AAAA" not in raw and "data:image" not in raw            # 没有 base64
    loaded = cli._load_cli_history(str(tmp_path))
    assert loaded[0]["content"] == "看图[图片]"


@pytest.mark.asyncio
async def test_headless_continue_carries_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class _SeenLLM:
        def __init__(self, reply):
            self.reply = reply
            self.seen = None

        async def chat(self, messages, **k):
            self.seen = [m for m in messages if m["role"] != "system"]
            return {"content": self.reply}

    await cli.run_agent_headless("我叫小明", llm=_SeenLLM("你好小明"), quiet=True)
    assert (tmp_path / ".vortocode" / "cli_session.json").is_file()  # 第一轮落盘
    llm2 = _SeenLLM("你叫小明")
    await cli.run_agent_headless("我叫什么", llm=llm2, quiet=True, continue_session=True)
    # 续聊：第二轮发给 LLM 的消息里带上了第一轮的历史
    assert any("小明" in (m.get("content") or "") for m in llm2.seen if isinstance(m.get("content"), str))
    assert len(llm2.seen) >= 3                                       # 上轮 user+assistant + 本轮 user


@pytest.mark.asyncio
async def test_headless_no_continue_is_fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class _SeenLLM:
        def __init__(self): self.seen = None
        async def chat(self, messages, **k):
            self.seen = [m for m in messages if m["role"] != "system"]
            return {"content": "ok"}

    cli._save_cli_history(str(tmp_path), [{"role": "user", "content": "旧对话"}])
    llm = _SeenLLM()
    await cli.run_agent_headless("新问题", llm=llm, quiet=True)       # 不 continue
    assert not any("旧对话" in (m.get("content") or "") for m in llm.seen if isinstance(m.get("content"), str))


def test_agent_dispatch_expands_custom_command(monkeypatch, tmp_path):
    cmds = tmp_path / ".vortocode" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "review.md").write_text("审查：$ARGUMENTS", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    captured = {}

    async def _fake_headless(prompt, **kw):
        captured["prompt"] = prompt
        return ""

    monkeypatch.setattr(cli, "run_agent_headless", _fake_headless)
    monkeypatch.setattr(sys, "argv", ["vortocode", "agent", "/review cli.py"])
    cli.main()
    assert captured["prompt"] == "审查：cli.py"                       # dispatch 层已展开


def test_strip_markup():
    assert cli._strip_markup("🔧 [b]read_file[/b][dim] path=a[/dim]") == "🔧 read_file path=a"
    assert cli._strip_markup("[#7fce9a]✓[/] done") == "✓ done"


@pytest.mark.asyncio
async def test_headless_pure_reply_to_stdout(monkeypatch, capsys):
    # 非 TTY（capsys 下 stdout 非 tty）→ 不流式，一次性 print 回复
    reply = await cli.run_agent_headless("你好", llm=_ScriptedLLM("我是 VortoCode。"))
    assert reply == "我是 VortoCode。"
    cap = capsys.readouterr()
    assert cap.out.strip() == "我是 VortoCode。"             # 回复进 stdout
    assert cap.err == ""                                     # 闲聊不调工具，stderr 干净


@pytest.mark.asyncio
async def test_headless_json_output(monkeypatch, capsys):
    import json
    reply = await cli.run_agent_headless(
        "搜一下 main",
        llm=_ScriptedLLM('{"tool":"grep","args":{"pattern":"def main"}}', "找到了。"),
        as_json=True)
    assert reply == "找到了。"
    payload = json.loads(capsys.readouterr().out)
    assert payload["reply"] == "找到了。"
    assert payload["mode"] == "plan"
    assert any(t["tool"] == "grep" for t in payload["tools"])   # 工具调用被记进 JSON


@pytest.mark.asyncio
async def test_headless_dangerous_op_auto_denied_in_build(monkeypatch, capsys):
    # build 模式下 run_command 需确认；headless 默认拒绝（不执行任何 shell）
    reply = await cli.run_agent_headless(
        "跑一下命令", build=True,
        llm=_ScriptedLLM('{"tool":"run_command","args":{"command":"echo hi"}}', "已说明。"))
    assert reply == "已说明。"
    err = capsys.readouterr().err
    assert "自动拒绝" in err                                  # 外向/高危操作被默认拦下


# ---- headless agent：图片输入 ----

class _CapturingLLM:
    """记下最后一次 chat 的 messages，便于断言多模态 content 块；恒定回一句。"""

    def __init__(self, reply):
        self.reply = reply
        self.last_messages = None

    async def chat(self, messages, **kwargs):
        self.last_messages = messages
        return {"content": self.reply}


def _png(tmp_path):
    import base64
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    p = tmp_path / "shot.png"
    p.write_bytes(raw)
    return p


def test_valid_image_ref(tmp_path):
    p = _png(tmp_path)
    assert cli._valid_image_ref(str(p))                      # 存在的本地图
    assert cli._valid_image_ref("https://x/y.png")           # URL 放行
    assert cli._valid_image_ref("data:image/png;base64,AA")  # data URL 放行
    assert not cli._valid_image_ref(str(tmp_path / "nope.png"))   # 不存在
    assert not cli._valid_image_ref(str(p) + ".txt")         # 非图


def test_agent_bad_image_exits_2(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["vortocode", "agent", "-i", "/no/such.png", "hi"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2


@pytest.mark.asyncio
async def test_headless_attaches_image_to_message(tmp_path, capsys):
    p = _png(tmp_path)
    llm = _CapturingLLM("图里是一个 1x1 像素。")
    reply = await cli.run_agent_headless("这是什么", llm=llm, images=[str(p)])
    assert reply == "图里是一个 1x1 像素。"
    # 首条 user 消息应是内容块数组：含 text + image_url(data URL)
    user_msgs = [m for m in llm.last_messages if m["role"] == "user"]
    content = user_msgs[0]["content"]
    assert isinstance(content, list)
    assert any(b.get("type") == "text" for b in content)
    img = [b for b in content if b.get("type") == "image_url"]
    assert img and img[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "🖼 1 张图" in capsys.readouterr().err            # stderr 提示带图


def _wav(tmp_path):
    import base64
    raw = base64.b64decode("UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=")
    p = tmp_path / "clip.wav"
    p.write_bytes(raw)
    return p


def test_valid_audio_ref(tmp_path):
    p = _wav(tmp_path)
    assert cli._valid_audio_ref(str(p))                      # 存在的本地音频
    assert cli._valid_audio_ref("data:audio/mp3;base64,QQ")  # data URL 放行
    assert not cli._valid_audio_ref("https://x/y.mp3")       # input_audio 不收 http URL
    assert not cli._valid_audio_ref(str(tmp_path / "nope.mp3"))   # 不存在
    assert not cli._valid_audio_ref(str(p) + ".txt")         # 非音频


def test_agent_bad_audio_exits_2(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["vortocode", "agent", "-a", "/no/such.mp3", "hi"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2


@pytest.mark.asyncio
async def test_headless_attaches_audio_to_message(tmp_path, capsys):
    p = _wav(tmp_path)
    llm = _CapturingLLM("音频说：你好。")
    reply = await cli.run_agent_headless("转写", llm=llm, audio=[str(p)])
    assert reply == "音频说：你好。"
    content = [m for m in llm.last_messages if m["role"] == "user"][0]["content"]
    aud = [b for b in content if b.get("type") == "input_audio"]
    assert aud and aud[0]["input_audio"]["format"] == "wav" and aud[0]["input_audio"]["data"]
    assert "🎧 1 段音频" in capsys.readouterr().err


# ---- 语音回复（TTS：--speak）----

class _TTSLLM:
    """既能 chat（驱动回合）又能 tts（合成语音）的假 LLM。记下 tts 收到的文本/voice。"""
    def __init__(self, reply, wav=b"RIFFfake"):
        self.reply = reply
        self.wav = wav
        self.tts_calls = []

    async def chat(self, messages, **k):
        return {"content": self.reply}

    async def tts(self, text, voice=None, model=None):
        self.tts_calls.append({"text": text, "voice": voice})
        return self.wav


def test_speak_flag_parses(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vortocode", "agent", "-h"])
    with pytest.raises(SystemExit):
        cli.main()
    out = capsys.readouterr().out
    assert "--speak" in out and "--voice" in out


def test_play_audio_no_player(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _p: None)     # 系统没装播放器
    assert cli._play_audio("/tmp/whatever.wav") is False


@pytest.mark.asyncio
async def test_speak_reply_writes_wav(tmp_path, capsys):
    out = tmp_path / "r.wav"
    llm = _TTSLLM("没用到这")
    path = await cli._speak_reply("你好世界", "alloy", str(out), llm, quiet=False)
    assert path == str(out)
    assert out.read_bytes() == b"RIFFfake"                    # 写出了合成音频
    assert llm.tts_calls == [{"text": "你好世界", "voice": "alloy"}]
    assert "语音已写入" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_speak_reply_handles_failure(tmp_path, capsys):
    class _BoomTTS:
        async def tts(self, text, voice=None, model=None):
            raise RuntimeError("upstream 500")
    path = await cli._speak_reply("文字", None, str(tmp_path / "x.wav"), _BoomTTS(), quiet=False)
    assert path is None                                       # 失败不抛、返回 None
    assert "语音合成失败" in capsys.readouterr().err


def test_headless_agent_loads_project_instructions(tmp_path):
    # .vortocode 下放 AGENTS.md → headless 主 agent 的系统提示带上项目约定
    (tmp_path / "AGENTS.md").write_text("# 约定\n本仓库一律用中文注释。", encoding="utf-8")

    async def _confirm(_m):
        return False

    agent = cli._build_headless_agent(str(tmp_path), max_steps=None, on_tool=None,
                                      on_plan=None, confirm=_confirm)
    sys_prompt = agent._system("plan")
    assert "项目指令" in sys_prompt and "一律用中文注释" in sys_prompt


@pytest.mark.asyncio
async def test_headless_mcp_connects_adds_tools_and_shuts_down(monkeypatch, tmp_path):
    # --mcp：连 MCP → 把工具接入本回合 → agent 能调 mcp__* 工具 → 回合结束关掉 manager
    import src.agents.mcp_tools as mt
    from src.agents.main_agent import Tool
    calls = {"ping": 0, "shutdown": 0}

    async def ping_handler(a):
        calls["ping"] += 1
        return "pong"

    class FakeMgr:
        async def shutdown(self):
            calls["shutdown"] += 1

    async def fake_connect(repo_root):
        return FakeMgr(), [Tool("mcp__srv__ping", "[MCP:srv] ping", {}, ping_handler, read_only=False)]
    monkeypatch.setattr(mt, "connect_mcp", fake_connect)
    monkeypatch.chdir(tmp_path)

    llm = _ScriptedLLM('{"tool":"mcp__srv__ping","args":{}}', "调用完成。")
    reply = await cli.run_agent_headless("用 ping", build=True, use_mcp=True, quiet=True, llm=llm)
    assert reply == "调用完成。"
    assert calls["ping"] == 1                          # MCP 工具确被接入并调用
    assert calls["shutdown"] == 1                      # 回合结束关掉 MCP（不残留子进程）


@pytest.mark.asyncio
async def test_headless_mcp_no_config_runs_clean(monkeypatch, tmp_path):
    # --mcp 但仓库无 config/mcp.yaml → 不报错、照常跑
    monkeypatch.chdir(tmp_path)
    reply = await cli.run_agent_headless("你好", use_mcp=True, quiet=True,
                                         llm=_ScriptedLLM("你好呀。"))
    assert reply == "你好呀。"


def test_headless_agent_has_research_delegation(tmp_path):
    # headless CLI agent 现也带只读子 agent 委派（task/research_parallel）——补齐与 TUI 的差距
    async def _confirm(_m):
        return False

    agent = cli._build_headless_agent(str(tmp_path), max_steps=None, on_tool=None,
                                      on_plan=None, confirm=_confirm)
    for name in ("task", "research_parallel"):
        assert name in agent.tools and agent.tools[name].read_only is True


@pytest.mark.asyncio
async def test_headless_speak_end_to_end(tmp_path, capsys):
    out = tmp_path / "reply.wav"
    llm = _TTSLLM("这是回复。")
    reply = await cli.run_agent_headless("讲一句", llm=llm, speak=True, speak_out=str(out))
    assert reply == "这是回复。"
    assert out.read_bytes() == b"RIFFfake"                    # 回复被合成并落盘
    assert llm.tts_calls and llm.tts_calls[0]["text"] == "这是回复。"
