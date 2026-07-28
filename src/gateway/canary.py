"""端到端交付验收（canary）——证明「一句话进来、结果送到人手机上」这条链**整条**是通的。

## 为什么单测覆盖不了这件事

2026-07-27 一天里修的五个 bug 全是同一个病：**每一段各自都对，接缝是断的**。

- `cron_run` 工具忘了传 `notify=` → 作业跑成功了、台账里结果齐全，主人手机上什么都没有。
  `run_job` 的单测绿、`make_notifier` 的单测绿、工具本身的单测也绿——没有任何一条测的是
  「这两段接上了没有」。
- `make_confirm_gate` 里直接拼 `TAINT_WARNING` 常量，而分档函数 `taint_prefix()` 只有 TUI 在调
  → 改了措辞的那个 PR 单测全绿，**用户看到的字一个没变**。
- 三处硬编码各自教用户"请按 Tab 键"，而钉钉聊天窗口里没有 Tab 键。改了两处，第三处继续毒着。

这类缺陷的共同形状是：**产物落到了台账/返回值里，但没落到人眼前**。断言函数返回值的测试对它
天然免疫——返回值确实是对的。所以这里换一个断言面：**只看假适配器的收件箱**（`FakeChannel.sent`），
那是进程内最接近"手机屏幕上真的出现了什么"的东西。链路里任何一环把东西丢了，收件箱就是空的。

## 与单测的分工

单测证明**每一段**的行为正确；canary 证明**接缝**没断。两者不可互相替代，也不该互相重复——
所以这里刻意不测业务分支（那些有各自的单测），只测五条交付通路是否贯通。

## 在哪里跑

- `vc canary`：任何时候手动跑，也是 `deploy-agent-vm.sh` 部署后的强制关卡（红了部署即失败）。
  **不依赖 pytest**（不必装 dev extra）、不触网、不用真模型、不碰真 `.vortocode/`（跑在临时目录里）。
- `tests/e2e/test_delivery_chain.py`：同一份 harness 进 CI，另加更细的分项断言。

harness（`FakeChannel` / `ScriptedLLM`）**只写这一份**、两处共用。今天的教训就是同一件事有三个
调用方时第三个总会漏——验收工具自己更不该重蹈覆辙。
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

OWNER = "canary-owner"

# 别端专属的**键盘**操作提示：出现在 IM 出站文本里就是场地泄漏（聊天窗口里没有 Tab / 方向键）。
# 注意刻意**不**拦"回复 y"——钉钉的确认就是靠文本回 y/n（dingtalk.py send_confirm），那是本端的
# 正当交互。拦的是"按/press 某个键"这种只有终端才成立的说法。
_VENUE_LEAKS = [
    re.compile(r"按\s*[`*]*\s*Tab", re.I),
    re.compile(r"按\s*[`*]*\s*Enter"),
    re.compile(r"按\s*[`*]*\s*(?:回车|方向键|上下键|空格键)"),
    re.compile(r"按\s*[`*]*\s*[yYnN]\b"),          # "按 y" 是键盘；"回复 y" 才是聊天
    re.compile(r"\bpress\s+(?:the\s+)?(?:tab|enter|esc)\b", re.I),
    re.compile(r"快捷键"),
    re.compile(r"Ctrl\s*\+", re.I),
]


@dataclass
class Lane:
    """一条交付通路的验收结论。"""
    name: str
    ok: bool
    detail: str

    @property
    def glyph(self) -> str:
        return "✅" if self.ok else "⛔"


# --------------------------------------------------------------------- harness
class FakeChannel:
    """假 IM 通道：**收件箱就是断言面**。不触网，行为对齐真适配器的契约。

    `edits_supported=False` 对齐钉钉（进度只能发新消息）——这是两种通道里更严的一侧，
    真机上出问题的也一直是它。
    """

    edits_supported = False

    def __init__(self, owner_id: str = OWNER, auto_approve: Optional[bool] = None):
        self.owner_id = str(owner_id)
        self.sent: list = []                  # [("text"|"confirm"|"edit"|"ack", payload)]
        self.auto_approve = auto_approve
        self._q: asyncio.Queue = asyncio.Queue()
        self.closed = False

    # —— 断言面 ——
    def texts(self) -> List[str]:
        return [p for k, p in self.sent if k == "text"]

    def confirms(self) -> List[str]:
        return [p[0] for k, p in self.sent if k == "confirm"]

    def outbound(self) -> List[str]:
        """所有会出现在人眼前的文本（正文 + 确认提问 + 编辑内容），场地泄漏扫这个面。"""
        out: List[str] = []
        for kind, payload in self.sent:
            if kind == "text":
                out.append(str(payload))
            elif kind == "confirm":
                out.append(str(payload[0]))
            elif kind == "edit":
                out.append(str(payload[1]))
        return out

    # —— ChannelAdapter 契约 ——
    def push(self, ev) -> None:
        self._q.put_nowait(ev)

    def stop(self) -> None:
        self._q.put_nowait(None)

    async def poll(self):
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    async def send_text(self, text: str) -> str:
        self.sent.append(("text", str(text)))
        return f"mid-{len(self.sent)}"

    async def edit_text(self, message_id: str, text: str) -> None:
        self.sent.append(("edit", (message_id, str(text))))

    async def send_confirm(self, text: str, callback_id: str) -> None:
        self.sent.append(("confirm", (str(text), callback_id)))
        if self.auto_approve is not None:            # 模拟主人点按钮/回 y
            from src.im.channel import ChannelEvent
            self.push(ChannelEvent(kind="callback", sender_id=self.owner_id,
                                   callback_id=callback_id, approved=self.auto_approve, ack="q"))

    async def send_image(self, path: str, caption: str = "") -> bool:
        self.sent.append(("image", (str(path), str(caption))))
        return True

    async def send_file(self, path: str, caption: str = "") -> bool:
        self.sent.append(("file", (str(path), str(caption))))
        return True

    async def ack_callback(self, ev) -> None:
        self.sent.append(("ack", ev.callback_id))

    def commit_reply_target(self, ev) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class ScriptedLLM:
    """按序吐预设 content 驱动主 loop（提示式 JSON 工具协议）。用尽后复读最后一条。"""

    def __init__(self, *responses: str):
        self._r = list(responses) or ["（脚本为空）"]
        self.n = 0

    async def chat(self, messages, **kwargs):
        i = min(self.n, len(self._r) - 1)
        self.n += 1
        return {"content": self._r[i]}


@dataclass
class _Sandbox:
    """canary 的一次性工作区 + 被改动的进程级全局（跑完必须原样放回）。"""
    root: str
    _tmp: object = None
    _env: dict = field(default_factory=dict)
    _notifier: object = None

    def __enter__(self):
        from src.gateway import im_runtime
        self._notifier = im_runtime._OWNER_NOTIFIER
        # 提示式协议：ScriptedLLM 吐的是裸 JSON 工具调用，native 模式下不会被解析成工具调用。
        # 沙箱关掉：canary 不跑 shell，别让宿主机缺 bubblewrap 变成假红。
        for k, v in (("VORTOCODE_NATIVE_TOOLS", "0"), ("VORTOCODE_SANDBOX", "off")):
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        from src.gateway import im_runtime
        im_runtime.set_owner_notifier(self._notifier)      # 别把真桥的投递口留在假适配器上
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self._tmp is not None:
            self._tmp.cleanup()
        return False


def _sandbox() -> _Sandbox:
    tmp = tempfile.TemporaryDirectory(prefix="vc-canary-")
    box = _Sandbox(root=tmp.name)
    box._tmp = tmp
    return box


async def _drive(bridge, chan: FakeChannel, *events, timeout: float = 20.0) -> None:
    """驱动一个完整回合：起 run() → 灌事件 → 等回合跑完 → 收尾。"""
    run_task = asyncio.create_task(bridge.run())
    for ev in events:
        chan.push(ev)

    async def _wait():
        while bridge._turn_task is None:
            await asyncio.sleep(0)
        await bridge._turn_task

    try:
        await asyncio.wait_for(_wait(), timeout=timeout)
    finally:
        chan.stop()
        try:
            await asyncio.wait_for(run_task, timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            run_task.cancel()


def _bridge(root: str, chan: FakeChannel, llm, *, mode: str = "build"):
    """建一条**主人配置**的桥（with_dev=True）——那才是真机上跑的那条。

    别图省事用 with_dev=False：那是给同事的研究员助手，第一条消息会被"请先自述人设"截胡，
    回合根本起不来，五条链路里三条会假超时（写这个 canary 时先踩了一遍）。
    """
    from src.im.bridge import IMBridge
    return IMBridge(root, chan, OWNER, channel="canary", mode=mode, llm=llm, with_dev=True)


def _msg(text: str, sender: str = OWNER):
    from src.im.channel import ChannelEvent
    return ChannelEvent(kind="message", sender_id=sender, text=text)


# --------------------------------------------------------------------- 五条通路
async def lane_inbound(root: str) -> Lane:
    """① 入站 → 回合 → 回复送达。链路最外层：桥装配、会话、回复投递，任一断则收件箱空。"""
    chan = FakeChannel()
    bridge = _bridge(root, chan, ScriptedLLM("我收到了，这是回复正文。"))
    await _drive(bridge, chan, _msg("你好"))
    hit = [t for t in chan.texts() if "这是回复正文" in t]
    if not hit:
        return Lane("入站→回复", False,
                    f"用户发了消息，但最终回复没送到出站面。收件箱：{chan.texts()[:3]}")
    return Lane("入站→回复", True, f"回复已送达（出站 {len(chan.texts())} 条）")


async def lane_confirm(root: str) -> Lane:
    """② 确认真的发出去了 + 批准后写盘真的发生了。

    这条盯的是 #252 那类：确认门的判定改了、但端上看到的字/走的路没跟着变。断言分两截——
    **确认提问出现在收件箱**（不是"gate 返回了 ASK"），**文件真的落盘**（不是"工具返回了成功"）。

    靶子用 `save_skill` 而不是 `write_file`：IM 端**根本没有 write_file**（改代码一律走隔离
    流水线，不给直接写主工作区的工具）。写这个 canary 时按想当然选了 write_file，红了才发现——
    这条链路的工具面和我脑子里的不是一回事。
    """
    chan = FakeChannel(auto_approve=True)
    target = Path(root) / ".vortocode" / "skills" / "canary-skill" / "SKILL.md"
    bridge = _bridge(root, chan, ScriptedLLM(
        '{"tool":"save_skill","args":{"name":"canary-skill","description":"探针",'
        '"instructions":"canary-ok"}}',
        "已经写好了。"))
    await _drive(bridge, chan, _msg("把这套流程存成技能"))

    if not chan.confirms():
        return Lane("确认往返", False,
                    "写操作没有触发任何确认提问——人不在关口上了（确认门没接到这个端）")
    if not target.is_file():
        return Lane("确认往返", False,
                    f"主人批准了，但东西没落盘。收件箱：{chan.texts()[:3]}")
    if "canary-ok" not in target.read_text(encoding="utf-8"):
        return Lane("确认往返", False, "落盘了但内容不对")
    return Lane("确认往返", True, "确认已送达 → 批准 → 内容真的落盘")


async def lane_venue(root: str) -> Lane:
    """③ 场地泄漏：出站文本里不能教用户按终端的键。

    只扫这一轮实际发出的文本不够（覆盖面全看脚本走到哪），所以**连静态面一起扫**：
    系统提示 + 全部工具描述。三处硬编码"请按 Tab"里有两处就藏在这两个面上——
    改了两处放过第三处，正是因为当时只按字符串搜了自己记得的地方。
    """
    chan = FakeChannel(auto_approve=True)
    bridge = _bridge(root, chan, ScriptedLLM(
        '{"tool":"write_file","args":{"path":"venue.txt","content":"x"}}',
        "好了。"), mode="plan")
    await _drive(bridge, chan, _msg("帮我改一下代码"))

    surface = list(chan.outbound())
    agent = bridge.agent
    # 静态面：**两个模式的**系统提示 + 全部工具描述。plan 的 mode_rule 正是"请按 Tab"的老窝，
    # 只扫 build 会漏掉它。
    for mode in ("plan", "build"):
        try:
            surface.append(str(agent._system(mode)))
        except Exception:  # noqa: BLE001 —— 取不到就只扫动态面，别让 canary 自己变成故障源
            pass
    for tool in (getattr(agent, "tools", None) or {}).values():
        surface.append(str(getattr(tool, "description", "")))

    for text in surface:
        for pat in _VENUE_LEAKS:
            m = pat.search(text)
            if m:
                lo = max(0, m.start() - 40)
                return Lane("场地泄漏", False,
                            f"IM 出站面出现别端的键盘操作提示 {m.group()!r}："
                            f"…{text[lo:m.end() + 40]}…")
    return Lane("场地泄漏", True, f"出站面 {len(surface)} 段文本无键盘操作提示")


async def lane_notify(root: str) -> Lane:
    """④ 三路投递器的 IM 那一路真能走到出站面。

    `make_notifier` 的 ③ 号通道是 `im_runtime.notify_owner`——桥起来时注册、停时注销。
    注册断了的表现是「台账里什么都有、手机上什么都没有」，而台账那一路照常绿。
    """
    from src.gateway import im_runtime
    from src.gateway.notices import load_notices, make_notifier

    chan = FakeChannel()
    got: list = []

    async def _sink(text: str) -> None:
        got.append(str(text))
        await chan.send_text(str(text))

    im_runtime.set_owner_notifier(_sink)
    try:
        await make_notifier(root)("canary 投递探针")
    finally:
        im_runtime.set_owner_notifier(None)

    if not got:
        return Lane("通知投递", False, "投递器没走到 IM 这一路——结果只会落台账，人收不到")
    if not any("canary 投递探针" in t for t in chan.texts()):
        return Lane("通知投递", False, f"IM 这一路被走到了，但出站面没有正文：{got[:2]}")
    if not any("canary 投递探针" in (n.get("text") or "") for n in load_notices(root, 10)):
        return Lane("通知投递", False, "IM 送到了，但台账没落账——台账才是唯一有持久保证的一路")
    return Lane("通知投递", True, "台账 + IM 两路都到了")


async def lane_cron_delivery(root: str) -> Lane:
    """⑤ cron 作业跑完，结果真的送到出站面——#253 的**确切**复现。

    刻意走**工具**（`build_cron_tools` 里的 `cron_run`）而不是直接调 `run_job_by_name`：
    当时断的正是工具到投递器这一截，库函数自己一直是好的。测库函数会永远绿。
    """
    from src.agents.main_agent import build_cron_tools
    from src.gateway import cron as cron_mod
    from src.gateway import im_runtime

    chan = FakeChannel()
    got: list = []

    async def _sink(text: str) -> None:
        got.append(str(text))
        await chan.send_text(str(text))

    # 一个 prompt 作业；run_session 注入假实现，不烧 token、不触网。
    cron_yaml = Path(root) / ".vortocode" / "cron.yaml"
    cron_yaml.parent.mkdir(parents=True, exist_ok=True)
    cron_yaml.write_text(
        "jobs:\n"
        "  - name: canary-job\n"
        "    schedule: at 03:00\n"
        "    prompt: canary 探针作业\n"
        "    announce: im\n"
        "    enabled: true\n", encoding="utf-8")

    async def _fake_session(*a, **k):
        return "canary 作业产出"

    real_run_job = cron_mod.run_job

    async def _patched(repo_root, job, **kw):
        # 必须判 None，不能用 setdefault：`run_job_by_name` 是**显式**传 `run_session=None` 的，
        # 键存在、setdefault 不生效 → 掉进真的 run_isolated_session 去连模型，卡到超时。
        if kw.get("run_session") is None:
            kw["run_session"] = _fake_session
        return await real_run_job(repo_root, job, **kw)

    async def _approve(_msg: str) -> bool:
        return True

    im_runtime.set_owner_notifier(_sink)
    cron_mod.run_job = _patched                    # type: ignore[assignment]
    try:
        tool = {t.name: t for t in build_cron_tools(root, confirm=_approve)}["cron_run"]
        out = await tool.handler({"name": "canary-job"})
        inflight = cron_mod._TRIGGER_INFLIGHT.get("canary-job")
        if inflight is not None:
            await asyncio.wait_for(asyncio.shield(inflight), timeout=20)
    finally:
        cron_mod.run_job = real_run_job            # type: ignore[assignment]
        im_runtime.set_owner_notifier(None)

    if "未触发" in str(out):
        return Lane("cron 结果投递", False, f"作业没触发起来：{out}")
    if not got:
        return Lane("cron 结果投递", False,
                    "作业跑完了，但**没推给任何人**——结果只在台账里，"
                    "主人手机上一片安静（#253 原病）")
    if not any("canary 作业产出" in t for t in chan.texts()):
        return Lane("cron 结果投递", False, f"推了一条，但正文不是作业产出：{got[:2]}")
    return Lane("cron 结果投递", True, "手动触发 → 作业产出 → 推到出站面")


async def lane_cold_push(root: str) -> Lane:
    """⑥ 冷启动投递：服务刚重启、主人一夜没说话——通知照样要能到出站面。

    2026-07-28 真机事故的确切复现：01:32 重启清掉内存里的 sessionWebhook 后，钉钉适配器的
    `send_text` 什么都不发还返回成功——早 9 点新闻、凌晨值班通报、"已就绪"横幅全部无声蒸发，
    而各段单测照常全绿。本 canary 当时的 lane_notify 也拦不住它：FakeChannel 的 send_text
    永远成功，静默丢发生在**真适配器**的通道选择里。

    所以这条用真 `DingTalkAdapter`（假 transport，不触网）：从 make_notifier 一路走到
    主动推送通道（batchSend 面），任何一环把"没 webhook"静默吞掉都会在这里现形。
    lane 内联的投递口与 im_service 注册的 `bridge.notify_send` 同构（一行透传，不吞异常）；
    那一行的等价性由 tests/unit/test_im_bridge.py 钉着。
    """
    from src.gateway import im_runtime
    from src.gateway.notices import make_notifier
    from src.im.dingtalk import DingTalkAdapter

    sent: list = []

    async def _never_reply(_wh, _payload):
        raise AssertionError("冷启动没有 webhook，不该走会话回复通道")

    adapter = DingTalkAdapter("canary-cid", "canary-secret", OWNER, reply_fn=_never_reply)

    async def _record(msg_key, msg_param):
        sent.append((msg_key, msg_param))

    adapter._send_msg = _record          # type: ignore[method-assign] —— 拦在 batchSend 面，不触网

    async def _notify_send(text: str) -> None:
        await adapter.send_text(text)

    im_runtime.set_owner_notifier(_notify_send)
    try:
        await make_notifier(root)("冷启动投递探针")
    finally:
        im_runtime.set_owner_notifier(None)

    if not sent:
        return Lane("冷启动投递", False,
                    "重启后（无 sessionWebhook）通知没走到主动推送通道——夜里/清晨的定时产出"
                    "会无声蒸发（2026-07-28 原病），而台账照常显示'已投递'")
    if "冷启动投递探针" not in str(sent[0]):
        return Lane("冷启动投递", False, f"主动通道被走到了，但正文不对：{sent[:1]}")
    return Lane("冷启动投递", True, "无 webhook → 主动通道（batchSend 面）送达")


_LANES = (lane_inbound, lane_confirm, lane_venue, lane_notify, lane_cron_delivery,
          lane_cold_push)


async def run_canary() -> List[Lane]:
    """跑全部交付通路，返回逐条结论。任何一条炸了都收敛成 fail，不往外抛。"""
    out: List[Lane] = []
    with _sandbox() as box:
        for fn in _LANES:
            try:
                out.append(await fn(box.root))
            except Exception as e:  # noqa: BLE001 —— 一条炸不拖全体，但必须算红
                name = fn.__name__.replace("lane_", "")
                out.append(Lane(name, False, f"验收本身抛异常 {type(e).__name__}: {e}"))
    return out


def summarize(lanes: List[Lane]) -> tuple:
    """(是否全绿, 一句话结论)。"""
    bad = [ln for ln in lanes if not ln.ok]
    if not bad:
        return True, f"交付链路 {len(lanes)}/{len(lanes)} 条贯通"
    return False, f"{len(bad)}/{len(lanes)} 条交付链路是断的：" + "、".join(ln.name for ln in bad)
