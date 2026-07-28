"""自愈层 —— 已知故障的**白名单**处置：能自己修的就地修好，修不了的把话说到位。

## 要解决的问题

到 2026-07-27 为止，这个系统在失败时的能力止步于「如实报错」（#591fdfa 那批把"通道故障
伪装成空结论"治好了）。但报完仍然要人：主人在钉钉收到一句 `Author identity unknown`，
还得自己想起来这是新机器没配 git 身份、还得 ssh 上去敲两行。**失败了没有自己解决问题的能力**
——这是"离不开作者"的核心症状。

## 为什么是白名单，不是"让模型看着办"

让模型读报错自己想办法，等于把**任意命令执行**接到一个可被污染的输入面上：报错文本里可能
带着外部内容（网页片段、别人贴的日志、被改过的本地文件）。所以这里的纪律是：

1. **只认白名单**：认不出的故障一律原样上报，不做任何动作。
2. **动作全部硬编码**：处置命令写死在代码里，**绝不从错误文本里取任何参数**。
   报错内容只用来"认出是哪一类"，不参与"要执行什么"。
3. **能自动修的必须够窄**：可逆、幂等、作用域限本仓库。碰全局配置 / 要装东西 / 要联网的
   一律划为 `human` —— 说清楚要人做什么，但不自己动手。
4. **一次为限**：同一处故障修过一次就进冷却，再犯直接升级给人。自愈不许变成重试风暴，
   否则一个修不好的毛病会把决策台账和主人的手机刷爆。
5. **留痕**：每次处置（成功/失败/跳过）都落通知台账。静默自愈比不自愈更可怕——
   人会以为系统一直很健康，而它一直在带病自我修补。

## 白名单从哪来

每一条都对应**真机上实际发生过**的故障，不是想出来的：

- `git-identity`   2026-07-26 新机器没配提交身份，流水线跑到最后一步 commit 挂掉，
                   而 doctor 当时显示绿（只查了 git 在不在、仓库对不对）。
- `relay-offline`  2026-07-27 15:35 VM 上所有外部请求 SSL 握手超时——出海代理节点挂了。
                   表现是"模型不可用"，人第一反应是去查代码/查 key，方向全错。
- `cron-no-web`    2026-07-27 09:47 定时作业没 allow_web，模型回了一大段"我没有联网工具"，
                   看起来像能力缺失，其实是这个作业没申报出网许可。
- `stale-worktree` 隔离流水线的一次性 worktree 没清干净时，后续建同名 worktree 会被拒。
- `sandbox-missing` / `gh-missing`  装机缺件，只能人来装（后者还带 systemd PATH 那个坑）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# 同一条处置的冷却（秒）：修过一次还犯，就不是"抖动"，是真有问题 → 升级给人，别再自己较劲。
_COOLDOWN = 6 * 3600

# 自动处置的总开关。默认**开**：这些动作都够窄（可逆、幂等、限本仓），而它们要救的场景恰恰是
# "没人盯着"。置 0/false/no/off 关掉——关掉后仍然会诊断并给出人工处置指引，只是不动手。
_ENABLED_ENV = "VORTOCODE_SELF_HEAL"

# 自动处置时用的提交身份。**刻意不冒充主人**：自动落的提交要一眼看得出是机器落的。
_BOT_NAME = "VortoCode Agent"
_BOT_EMAIL = "vortocode-agent@localhost"


def self_heal_enabled() -> bool:
    return os.getenv(_ENABLED_ENV, "1").strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class Remedy:
    """一条已知故障的处置方案。

    `fix` 为 None 即 `kind="human"`：我们认得出这是什么，但没有安全的自动解法，
    只能把话说到位。**认得出但修不了，也远比一句原始报错有用**——真机上最贵的不是修，
    是"这报错到底在说什么"那半小时。
    """
    name: str
    what: str                        # 这是什么故障（一句人话）
    human_fix: str                   # 人要做什么（auto 的也填：自动处置失败时的兜底指引）
    pattern: re.Pattern
    fix: Optional[Callable[[str], Tuple[bool, str]]] = None   # (repo_root) -> (成功?, 说明)

    @property
    def kind(self) -> str:
        return "auto" if self.fix is not None else "human"


@dataclass
class Outcome:
    """一次自愈尝试的结果。`healed=True` 才代表"故障已排除，值得再试一次"。"""
    remedy: Optional[Remedy]
    healed: bool
    detail: str

    @property
    def diagnosed(self) -> bool:
        return self.remedy is not None

    def note(self) -> str:
        """拼给通报/台账看的一段话；认不出就返回空串（不给上层添噪音）。"""
        if self.remedy is None:
            return ""
        head = f"🩺 诊断：{self.remedy.what}"
        if self.healed:
            return f"{head}\n🔧 已自动处置：{self.detail}"
        return f"{head}\n👉 需要你：{self.remedy.human_fix}" + (f"\n（{self.detail}）" if self.detail else "")


# --------------------------------------------------------------------- 处置动作（全部硬编码）
def _git(repo_root: str, *args: str, timeout: int = 10) -> Tuple[int, str]:
    """跑一条 git 子命令。**参数全部由调用处字面量给出**，不接受任何来自错误文本的内容。"""
    try:
        r = subprocess.run(["git", "-C", str(repo_root), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def _fix_git_identity(repo_root: str) -> Tuple[bool, str]:
    """给**本仓库**配一个机器人提交身份（不碰全局配置）。

    只在两项都缺时才写，**绝不覆盖已有身份**——主人配过的东西不许被自愈改掉。
    作用域是 `.git/config`（worktree 与主仓共用），所以隔离流水线里的提交也随之解锁。
    """
    have_name = _git(repo_root, "config", "--get", "user.name")[1]
    have_email = _git(repo_root, "config", "--get", "user.email")[1]
    if have_name and have_email:
        return False, "身份已存在，未改动"
    if have_name or have_email:
        # 只缺一半：多半是人手工配了一半，别替他补另一半（补出来的身份可能不是他想要的）
        return False, "身份只配了一半，交给人处理（不替你猜另一半）"
    rc1, out1 = _git(repo_root, "config", "user.name", _BOT_NAME)
    rc2, out2 = _git(repo_root, "config", "user.email", _BOT_EMAIL)
    if rc1 != 0 or rc2 != 0:
        return False, f"写 git 配置失败：{out1} {out2}".strip()
    return True, (f"已给本仓库配上 {_BOT_NAME} <{_BOT_EMAIL}>（仅本仓库，未动全局；"
                  f"要换成你自己的：git -C <repo> config user.name '…'）")


def _fix_stale_worktree(repo_root: str) -> Tuple[bool, str]:
    """清掉已失效的 worktree 登记（`git worktree prune`）。幂等、只删已经不存在的那些。"""
    rc, out = _git(repo_root, "worktree", "prune", "-v", timeout=30)
    if rc != 0:
        return False, f"prune 失败：{out[:200]}"
    return True, (f"已清理失效的 worktree 登记（{out[:160]}）" if out
                  else "已跑 worktree prune（没有需要清理的登记）")


# --------------------------------------------------------------------- 白名单
# 匹配面刻意收窄：只认**我们自己产生的**错误文本特征。宁可漏诊（原样上报，不做动作），
# 也不要误诊后去动一个其实没坏的东西。
_REMEDIES: Tuple[Remedy, ...] = (
    Remedy(
        name="git-identity",
        what="git 提交身份没配——落分支时 commit 必然失败，这不是代码问题",
        human_fix=('git config --global user.name "你的名字" && '
                   'git config --global user.email "你的邮箱"'),
        pattern=re.compile(r"Author identity unknown|empty ident name|"
                           r"Please tell me who you are|提交身份未配置", re.I),
        fix=_fix_git_identity,
    ),
    Remedy(
        name="stale-worktree",
        what="有失效的 worktree 登记挡路——隔离流水线建不出一次性工作区",
        human_fix="git -C <repo> worktree prune；仍不行就看 git worktree list 里有没有手工残留",
        pattern=re.compile(r"is already registered|already exists.*worktree|"
                           r"worktree.*already (?:exists|registered)|missing but (?:already )?locked", re.I),
        fix=_fix_stale_worktree,
    ),
    Remedy(
        name="relay-offline",
        what="出不去网/中转站连不上（SSL 握手超时、连接被重置）——**多半是出海代理的节点挂了，"
             "不是代码问题、也不是 key 过期**",
        human_fix="去 mihomo 换一个活着的节点（别把节点手动钉死在某一个上），"
                  "再 curl 一下中转站确认通了；排查见 docs/OPS.md 第六节",
        pattern=re.compile(r"SSL.{0,20}(?:handshake|握手).{0,20}(?:timeout|超时)|"
                           r"SSLError|handshake operation timed out|"
                           r"Connection reset by peer|Max retries exceeded|"
                           r"Failed to establish a new connection|Cannot connect to host", re.I),
        fix=None,          # 从这台机器里没有安全的自动解法：节点在宿主机的代理上
    ),
    Remedy(
        name="cron-no-web",
        what="这个定时作业没有出网许可，所以模型说自己「没有联网工具」——是权限没给，不是能力缺失",
        human_fix="让 agent 跑 cron_set_web 给这个作业开出网许可（会单独向你要一次授权），"
                  "或手工在 .vortocode/cron.yaml 里给它加 allow_web: true",
        pattern=re.compile(r"没有\s*`?web_search`?\s*/?\s*`?web_fetch`?|"
                           r"不包含联网搜索能力|没有联网(?:搜索)?(?:工具|能力)|"
                           r"无法.{0,10}实时(?:抓取|获取)", re.I),
        fix=None,          # 开权限必须有真人点头（作业级出网是 2026-07-27 主人拍板的边界）
    ),
    Remedy(
        name="sandbox-missing",
        what="OS 沙箱不可用——无人值守下的命令执行会 fail-closed 拒跑",
        human_fix="Ubuntu/Debian：sudo apt install bubblewrap，并确认允许 user namespace；"
                  "macOS 自带 sandbox-exec，不该走到这里",
        pattern=re.compile(r"OS 沙箱不可用|沙箱不可用|bubblewrap|bwrap.*not found", re.I),
        fix=None,          # 要 sudo + 装包，不该由无人值守的自己来做
    ),
    Remedy(
        name="gh-missing",
        what="gh CLI 不在 PATH 或没登录——分支落得下、PR 开不了",
        human_fix="装 gh 并 gh auth login；**若确认已装还报这个，去看服务进程的 PATH**"
                  "（systemd/launchd 给的 PATH 比登录 shell 窄，真机 2026-07-26 栽过）",
        pattern=re.compile(r"gh(?:\s+CLI)?\s*(?:不在|not found|未安装)|"
                           r"gh: command not found|gh auth login", re.I),
        fix=None,          # 登录是交互式的，自动不了
    ),
    Remedy(
        name="token-budget",
        what="本次作业撞到 token 预算上限，已就地停止（这是封顶生效，不是故障）",
        human_fix="要让它跑完就调高这个作业的 budget（.vortocode/cron.yaml），"
                  "或把任务拆小；不想封顶就删掉 budget 字段",
        pattern=re.compile(r"token 预算超限|TokenBudget", re.I),
        fix=None,          # 自动抬预算等于把封顶废掉——这条**刻意**不给自动解
    ),
)


def diagnose(error_text: str) -> Optional[Remedy]:
    """认出这是白名单里的哪一类故障；认不出返回 None（上层原样上报，不做任何动作）。"""
    text = str(error_text or "")
    if not text.strip():
        return None
    for remedy in _REMEDIES:
        if remedy.pattern.search(text):
            return remedy
    return None


# --------------------------------------------------------------------- 冷却台账（防重试风暴）
def _ledger_path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / "logs" / "remediation.json"


def _load_ledger(repo_root: str) -> dict:
    try:
        return json.loads(_ledger_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _record_attempt(repo_root: str, name: str, ts: float) -> None:
    p = _ledger_path(repo_root)
    data = _load_ledger(repo_root)
    data[name] = ts
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _in_cooldown(repo_root: str, name: str, now: float) -> bool:
    last = _load_ledger(repo_root).get(name)
    return isinstance(last, (int, float)) and (now - float(last)) < _COOLDOWN


# --------------------------------------------------------------------- 对外入口
def remediate(repo_root: str, error_text: str, *, source: str = "unknown",
              now: Optional[float] = None) -> Outcome:
    """诊断一段错误文本，能自动处置就处置。**永不抛异常**——自愈是旁路，绝不能反过来炸调用方。

    返回 `Outcome`：`healed=True` 表示故障已排除、上层值得再试一次；其余情况把
    `outcome.note()` 拼进给人的通报里就行（认不出时它是空串）。
    """
    import time
    stamp = float(now if now is not None else time.time())
    try:
        remedy = diagnose(error_text)
        if remedy is None:
            return Outcome(None, False, "")
        if remedy.fix is None:                       # 认得出，但只能人来修
            return Outcome(remedy, False, "")
        if not self_heal_enabled():
            return Outcome(remedy, False, f"自动处置已关闭（{_ENABLED_ENV}=0）")
        if _in_cooldown(repo_root, remedy.name, stamp):
            # 修过一次还犯 → 不是抖动。再修一次也多半没用，而且会变成重试风暴。
            return Outcome(remedy, False, "最近已自动处置过一次，仍然复发——这次不再自动重试，请人工介入")
        _record_attempt(repo_root, remedy.name, stamp)
        ok, detail = remedy.fix(repo_root)
        _audit(repo_root, remedy, ok, detail, source)
        return Outcome(remedy, ok, detail)
    except Exception as e:  # noqa: BLE001 —— 自愈自己炸了，也只当"没修成"，不影响原错误上报
        return Outcome(None, False, f"自愈层自身出错 {type(e).__name__}: {e}")


def _audit(repo_root: str, remedy: Remedy, ok: bool, detail: str, source: str) -> None:
    """把每一次自动处置落进通知台账。

    静默自愈比不自愈更可怕——人会以为系统一直很健康，而它一直在带病自我修补。
    """
    try:
        from src.gateway.notices import record_notice
        record_notice(repo_root,
                      f"{'🔧 自愈成功' if ok else '⚠ 自愈未成功'} [{remedy.name}]：{detail}",
                      source=f"self-heal:{source}")
    except Exception:  # noqa: BLE001 —— 留痕失败不该影响处置本身
        pass


def known_remedies() -> List[Remedy]:
    """白名单快照（doctor / 文档 / 测试用）。"""
    return list(_REMEDIES)
