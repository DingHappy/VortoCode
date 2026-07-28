"""助手名册 —— 多助手（每人一个专属小蜜）的配置面与体检。

## 为什么需要它

一个人一个助手时，配置就是"几个环境变量 + 一个服务"，脑子记得住。三个人起就不一样了：
每人要有**自己的工作区**（人设、会话历史、记忆按人隔离）、**自己的凭证**、**自己的服务实例**。
配错的表现全都是同一个样子——**机器人装死**。而"装死"是这套系统里最贵的故障：
主人第一反应是"坏了/连不上"，排查能耗一整晚（真机 2026-07-26 的白名单漏配就是这样）。

所以这里不存凭证、不起服务，只做一件事：**在你把三个服务 enable 起来之前，把配错的地方指出来**。

## 最要紧的那条检查：凭证复用

`scripts/deploy-agent-vm.sh` 收尾就写着一句提醒——「钉钉 bot 只有一个，别在别处再起带 --im 的
serve，两个桥会抢同一个 bot」。多助手正是最容易踩它的场景：复制粘贴一份 env 改个名字，
CLIENT_ID 忘了换。表现不是"报错"，而是两个桥抢同一条长连接，消息**随机**落到其中一个——
时灵时不灵，最难查的那种。名册体检把它变成一句人话。

## 纪律

- **名册里绝不放凭证**：只放 env 文件的路径。名册本身是可以进 git 的配置，凭证不是。
- **体检从不打印凭证值**：只报"这个键在不在"、"两个助手是不是用了同一份"。比对在内存里做。
- 权限太松（group/other 可读的 env 文件）要报出来——那等于把 bot 的全权凭据摊开给同机用户。
"""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# systemd 模板实例名（`vortocode-assistant@<name>.service`）能安全承载的字符。
# 收窄到这一档是因为实例名会进服务名、路径和 journal 标签——放宽只会换来奇怪的故障。
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")

# 各通道**必须**有的凭证键。缺任何一个，桥都会 fail-closed 拒启（见 gateway/im_service）。
REQUIRED_KEYS: Dict[str, Tuple[str, ...]] = {
    "dingtalk": ("VORTOCODE_DD_CLIENT_ID", "VORTOCODE_DD_CLIENT_SECRET", "VORTOCODE_DD_OWNER_ID"),
    "telegram": ("VORTOCODE_TG_TOKEN", "VORTOCODE_TG_OWNER_ID"),
}

# 判"两个助手是不是共用了同一个机器人"看哪个键。**只比对，不打印**。
_IDENTITY_KEY: Dict[str, str] = {
    "dingtalk": "VORTOCODE_DD_CLIENT_ID",
    "telegram": "VORTOCODE_TG_TOKEN",
}

DEFAULT_ROSTER = ".vortocode/assistants.yaml"


@dataclass
class Assistant:
    name: str
    channel: str
    workspace: str
    env_file: str
    mode: str = "plan"
    role: str = "researcher"        # researcher = 不给改代码/落分支/开 PR 的工具面；owner = 全量

    @property
    def with_dev(self) -> bool:
        return self.role == "owner"

    @property
    def unit(self) -> str:
        return f"vortocode-assistant@{self.name}.service"


@dataclass
class Finding:
    """一条体检结论。level: ok | warn | fail（fail = 起不来或会互相打架）。"""
    who: str
    level: str
    detail: str

    @property
    def glyph(self) -> str:
        return {"ok": "✅", "warn": "⚠️", "fail": "⛔"}.get(self.level, "?")


@dataclass
class Roster:
    assistants: List[Assistant] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)      # 名册本身读不了/格式不对

    def get(self, name: str) -> Optional[Assistant]:
        return next((a for a in self.assistants if a.name == name), None)


def roster_path(repo_root: str, override: str = "") -> Path:
    return Path(override) if override else Path(repo_root) / DEFAULT_ROSTER


def load_roster(repo_root: str, override: str = "") -> Roster:
    """读名册。文件不存在 → 空名册（不是错误：没配多助手是完全正常的状态）。"""
    path = roster_path(repo_root, override)
    if not path.is_file():
        return Roster()
    try:
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return Roster(errors=[f"名册读不了（{path}）：{type(e).__name__}: {e}"])
    if not isinstance(raw, dict) or not isinstance(raw.get("assistants"), list):
        return Roster(errors=[f"名册格式不对（{path}）：顶层要有 assistants: 列表"])

    out, errs = [], []
    for i, item in enumerate(raw["assistants"], 1):
        if not isinstance(item, dict):
            errs.append(f"第 {i} 条不是映射（应为 name/channel/... 的键值对）")
            continue
        name = str(item.get("name") or "").strip()
        channel = str(item.get("channel") or "").strip().lower()
        if not name:
            errs.append(f"第 {i} 条缺 name")
            continue
        out.append(Assistant(
            name=name, channel=channel,
            workspace=str(item.get("workspace") or "").strip(),
            env_file=str(item.get("env_file") or "").strip(),
            mode=str(item.get("mode") or "plan").strip().lower(),
            role=str(item.get("role") or "researcher").strip().lower()))
    return Roster(assistants=out, errors=errs)


def _read_env_keys(path: Path) -> Dict[str, str]:
    """把 env 文件解析成键值表。**调用方只准用键名与相等性比较，绝不打印值。**"""
    out: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def _check_one(a: Assistant) -> List[Finding]:
    findings: List[Finding] = []

    if not _NAME_RE.match(a.name):
        findings.append(Finding(a.name, "fail",
                                "名字不能用作 systemd 实例名（只允许小写字母/数字/下划线/连字符，"
                                "开头是字母或数字，≤31 字符）"))
    if a.channel not in REQUIRED_KEYS:
        findings.append(Finding(a.name, "fail",
                                f"未知通道 {a.channel!r}（可选 {'/'.join(REQUIRED_KEYS)}）"))
    if a.mode not in ("plan", "build"):
        findings.append(Finding(a.name, "fail", f"mode 只能是 plan / build，配的是 {a.mode!r}"))
    if a.role not in ("researcher", "owner"):
        findings.append(Finding(a.name, "fail",
                                f"role 只能是 researcher / owner，配的是 {a.role!r}"))

    # 工作区：每人一个独立目录，人设/会话/记忆天然按人隔离。共用一个目录会串人设与会话历史。
    if not a.workspace:
        findings.append(Finding(a.name, "fail", "缺 workspace（每个助手必须有独立工作区，否则人设与会话会串）"))
    elif not Path(a.workspace).is_dir():
        findings.append(Finding(a.name, "warn",
                                f"工作区还不存在：{a.workspace}（起服务前 mkdir -p 一下）"))

    if not a.env_file:
        findings.append(Finding(a.name, "fail", "缺 env_file（凭证放那里，名册里不放凭证）"))
        return findings

    env_path = Path(a.env_file)
    if not env_path.is_file():
        findings.append(Finding(a.name, "fail", f"env 文件不存在：{a.env_file}"))
        return findings

    # 权限：env 文件里是 bot 的全权凭据，group/other 可读等于摊开给同机所有用户
    try:
        mode = stat.S_IMODE(env_path.stat().st_mode)
        if mode & 0o077:
            findings.append(Finding(a.name, "warn",
                                    f"env 文件权限过松（{oct(mode)}）——里面是 bot 全权凭据，"
                                    f"chmod 600 {a.env_file}"))
    except OSError:
        pass

    keys = _read_env_keys(env_path)
    missing = [k for k in REQUIRED_KEYS.get(a.channel, ()) if not keys.get(k)]
    if missing:
        # 只报键名，不报值
        findings.append(Finding(a.name, "fail",
                                f"env 里缺这些键（或值为空）：{'、'.join(missing)}——"
                                f"桥会 fail-closed 拒启"))

    # 白名单漏了自己：最难猜的一种配错。表现是"机器人不理我 + 每个确认都等到超时被拒"。
    owner_key = {"dingtalk": "VORTOCODE_DD_OWNER_ID", "telegram": "VORTOCODE_TG_OWNER_ID"}.get(a.channel)
    allow_raw = keys.get({"dingtalk": "VORTOCODE_DD_ALLOW_FROM",
                          "telegram": "VORTOCODE_TG_ALLOW_FROM"}.get(a.channel, ""),
                         keys.get("VORTOCODE_IM_ALLOW_FROM"))
    if owner_key and allow_raw is not None:
        owner = keys.get(owner_key, "")
        allowed = {p for p in re.split(r"[,\s;]+", allow_raw) if p}
        if not allowed:
            findings.append(Finding(a.name, "warn",
                                    "白名单显式配成空 = 拒绝一切入站（含本人）。"
                                    "这是合法的 fail-closed 配法，但多半不是你想要的"))
        elif owner and owner not in allowed:
            findings.append(Finding(a.name, "fail",
                                    "白名单里没有 owner 本人——他自己发的消息和审批（y/n、按钮）"
                                    "都会被丢掉，表现为「机器人不理人、确认永远超时」"))

    if not findings:
        findings.append(Finding(a.name, "ok",
                                f"{a.channel} · {a.mode} · {a.role} · 工作区 {a.workspace}"))
    return findings


def _check_shared_credentials(assistants: List[Assistant]) -> List[Finding]:
    """**两个助手用了同一个机器人**——多助手最容易踩、也最难查的一条。

    表现不是报错，是两个桥抢同一条长连接、消息随机落到其中一个：时灵时不灵。
    `deploy-agent-vm.sh` 收尾那句"钉钉 bot 只有一个"提醒的就是它；复制 env 改个名字忘了换
    CLIENT_ID 就会中招。比对只在内存里做，**只报名字不报凭证**。
    """
    seen: Dict[Tuple[str, str], List[str]] = {}
    for a in assistants:
        key_name = _IDENTITY_KEY.get(a.channel)
        if not key_name or not a.env_file:
            continue
        value = _read_env_keys(Path(a.env_file)).get(key_name, "")
        if not value:
            continue
        seen.setdefault((a.channel, value), []).append(a.name)

    out: List[Finding] = []
    for (channel, _value), names in seen.items():
        if len(names) > 1:
            out.append(Finding("、".join(names), "fail",
                               f"这几个助手共用同一个 {channel} 机器人凭证——两条桥会抢同一条长连接，"
                               f"消息随机落到其中一个（时灵时不灵，最难查的那种）。"
                               f"每个助手要在开放平台**各建一个应用**，拿各自的凭证"))
    return out


def _check_shared_workspace(assistants: List[Assistant]) -> List[Finding]:
    """两个助手共用一个工作区 → 人设、会话历史、记忆全串在一起。"""
    seen: Dict[str, List[str]] = {}
    for a in assistants:
        if a.workspace:
            seen.setdefault(str(Path(a.workspace)), []).append(a.name)
    return [Finding("、".join(names), "fail",
                    f"这几个助手共用工作区 {ws}——人设、会话历史、记忆会串在一起，"
                    f"每人一个目录")
            for ws, names in seen.items() if len(names) > 1]


def check_roster(roster: Roster) -> List[Finding]:
    """把名册体检一遍。返回逐条结论（fail 表示起不来或会互相打架）。"""
    findings: List[Finding] = [Finding("名册", "fail", e) for e in roster.errors]
    dup_names = {a.name for a in roster.assistants
                 if [x.name for x in roster.assistants].count(a.name) > 1}
    for name in sorted(dup_names):
        findings.append(Finding(name, "fail", "名字重复——systemd 实例名会撞车"))
    for a in roster.assistants:
        findings.extend(_check_one(a))
    findings.extend(_check_shared_credentials(roster.assistants))
    findings.extend(_check_shared_workspace(roster.assistants))
    return findings


def summarize(findings: List[Finding]) -> Tuple[bool, str]:
    """(能不能起, 一句话结论)。"""
    bad = [f for f in findings if f.level == "fail"]
    warn = [f for f in findings if f.level == "warn"]
    if bad:
        return False, f"{len(bad)} 处会让助手起不来或互相打架，先修这些"
    if warn:
        return True, f"可以起，但有 {len(warn)} 处值得改（见上）"
    return True, "名册体检通过"


def systemd_hint(a: Assistant) -> str:
    """把一个助手的启用命令拼出来（模板见 examples/systemd/）。"""
    return (f"sudo systemctl enable --now {a.unit}\n"
            f"  · 工作区 WorkingDirectory={a.workspace}\n"
            f"  · 凭证   EnvironmentFile={a.env_file}\n"
            f"  · 看日志 journalctl -u {a.unit} -f")


def env_template(channel: str) -> str:
    """给一个通道的 env 文件模板（**只有键名，不含任何真凭证**）。"""
    keys = REQUIRED_KEYS.get((channel or "").strip().lower())
    if not keys:
        return f"# 未知通道 {channel!r}（可选 {'/'.join(REQUIRED_KEYS)}）\n"
    lines = [f"# VortoCode 助手凭证（{channel}）—— chmod 600，别进 git",
             "# 每个助手必须在开放平台**各建一个应用**：共用凭证会让两条桥抢同一个机器人。"]
    lines += [f"{k}=" for k in keys]
    lines += ["OPENAI_API_KEY=",
              "# 可选：入站白名单（不设 = 只放 owner；设成空 = 全拒，连 owner 也拒）",
              f"# VORTOCODE_{'DD' if channel == 'dingtalk' else 'TG'}_ALLOW_FROM="]
    return "\n".join(lines) + "\n"
