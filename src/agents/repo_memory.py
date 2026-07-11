"""每仓库记忆（`.vortocode/memory/repo.md`）。

**为什么要它**：隔离 dev 流水线每进一个新仓库都失忆——构建怪癖、真正能跑的测试命令、
已知的坑，每次重新试错。会话级长期记忆（SessionStore 的 `__longterm__`）是"跟着人"的，
且要模型主动 `recall_memory` 才检索；仓库记忆是"跟着代码库"的，装配时静态注入系统提示，
子 agent 也拿得到——直接抬升 dev_auto 的成功率。

**与项目指令（AGENTS.md）的分工**：AGENTS.md 是人写给 agent 的规矩（提交进仓库、要评审）；
repo.md 是 **agent 自己攒下来的事实**（gitignored，属本地经验）。两者都进系统提示，但来源不同。

**安全**：repo.md 每个会话都会被自动注入系统提示 ⇒ 它是"系统事实"。因此写入门槛**高于**
会话长期记忆：只有 `MemoryWritePolicy` 判为 `durable` 的内容才允许落盘；污点回合里的指令性
文本（proposal）与疑似凭据（quarantine）**一律拒绝**——绝不能让外部内容经由这里变成
每轮都喂给模型的"事实"（提示注入的最佳跳板）。

**前缀缓存**：装配时读一次、会话内不变（静态），故不破坏 #172 立下的
"system 会话内字节级稳定"不变量。
"""

from __future__ import annotations

from pathlib import Path

# 相对仓库根的位置。放 .vortocode/ 下 = 默认 gitignored（本地经验，不污染别人的仓库）。
REPO_MEMORY_REL = Path(".vortocode") / "memory" / "repo.md"

# 注入上限：仓库记忆是**每轮**都在系统提示里的常驻成本，必须精炼。
# 超出则截断并明示（宁可截断，也不悄悄丢或撑爆预算）。
MAX_REPO_MEMORY_CHARS = 2_000

_HEADER = "# 仓库记忆（VortoCode 自动维护）\n\n" \
          "> agent 在本仓库攒下的事实：构建/测试命令、目录约定、踩过的坑。\n" \
          "> 用 `remember_repo` 工具追加（需确认）；直接编辑本文件亦可。\n"


def repo_memory_path(repo_root: str) -> Path:
    return Path(repo_root) / REPO_MEMORY_REL


def read_repo_memory(repo_root: str) -> str:
    """读原始正文（不含注入格式）；文件不存在/读失败返回空串。"""
    p = repo_memory_path(repo_root)
    try:
        if not p.is_file():
            return ""
        return p.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _entries(text: str) -> list[str]:
    """文件里的条目行（`- ` 开头）。append_repo_memory 只写这种行；手改的自由文本没有。"""
    return [ln for ln in text.splitlines() if ln.startswith("- ")]


def repo_memory_body(repo_root: str) -> tuple[str, int]:
    """要注入的正文 + 被丢弃的条目数。

    **保留最新的条目**（从尾往前收），而不是切文件开头——append 是往尾部追加的，
    按开头截断意味着**文件一旦超过上限，此后写进去的每一条事实都永远读不到**，
    而工具还在报"写入成功"（codex 审出的真问题：成功写入一个永远不会被加载的事实）。
    """
    text = read_repo_memory(repo_root)
    if not text:
        return "", 0
    rows = _entries(text)
    if not rows:                                    # 手改的自由文本：没有条目结构，只能整体截断
        if len(text) <= MAX_REPO_MEMORY_CHARS:
            return text, 0
        return text[:MAX_REPO_MEMORY_CHARS], -1     # -1 = 自由文本被截断（条目数未知）
    kept: list[str] = []
    used = 0
    for ln in reversed(rows):                       # 最新的优先——新写的事实**必定**被注入
        if kept and used + len(ln) + 1 > MAX_REPO_MEMORY_CHARS:
            break
        kept.append(ln[:MAX_REPO_MEMORY_CHARS])     # 单条就超上限也要留（截断它，别整条丢）
        used += len(ln) + 1
    kept.reverse()
    return "\n".join(kept), len(rows) - len(kept)


def load_repo_memory(repo_root: str) -> str:
    """拼成可直接追加到 extra_system 的一段；没有内容则空串。

    装配时调用一次（会话内静态 → 不破坏 system 前缀稳定）。
    """
    body, dropped = repo_memory_body(repo_root)
    if not body:
        return ""
    note = ""
    if dropped > 0:
        note = (f"\n…(还有 {dropped} 条更早的仓库记忆未注入——已超上限，只带最新的；"
                "建议精简 .vortocode/memory/repo.md)")
    elif dropped < 0:
        note = "\n…(仓库记忆过长已截断；建议精简 .vortocode/memory/repo.md)"
    return ("【仓库记忆】(本仓库的既有事实：构建/测试命令、目录约定、已知坑。"
            "与之矛盾时以当前代码为准，并用 remember_repo 更正)\n"
            f"{body}{note}")


def dev_subagent_system(repo_root: str, base: str) -> str:
    """隔离实现子 agent 的 extra_system = 角色指令 + 仓库记忆。

    **所有**造实现子 agent 的地方都该走这里（build_dev_tools 的 _make_writer、TUI 自己的
    dev_isolated/dev_parallel……）。此前各处各拼一份，加仓库记忆时漏掉了 TUI 那两个
    ——"dev 子 agent 也带上"只在一半路径成立（codex 审出的真问题）。

    注意 repo_root 必须是**主仓库**：`.vortocode/` 是 gitignored，worktree 里没有这份文件。
    """
    mem = load_repo_memory(repo_root)
    return f"{base}\n\n{mem}" if mem else base


def append_repo_memory(repo_root: str, content: str) -> Path:
    """把一条事实追加进 repo.md（调用方**必须**先过 MemoryWritePolicy 且拿到用户确认）。

    只做落盘：策略判定在 build_memory_tools 的 remember_repo 里，避免两处各判一套。
    """
    entry = " ".join(str(content or "").split()).strip()
    if not entry:
        raise ValueError("仓库记忆内容为空")
    p = repo_memory_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""
    if not existing.strip():
        existing = _HEADER
    if not existing.endswith("\n"):
        existing += "\n"
    p.write_text(f"{existing}- {entry}\n", encoding="utf-8")
    return p
