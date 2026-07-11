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


def load_repo_memory(repo_root: str) -> str:
    """拼成可直接追加到 extra_system 的一段；没有内容则空串。

    装配时调用一次（会话内静态 → 不破坏 system 前缀稳定）。超长截断并标注。
    """
    text = read_repo_memory(repo_root)
    if not text:
        return ""
    truncated = ""
    if len(text) > MAX_REPO_MEMORY_CHARS:
        text = text[:MAX_REPO_MEMORY_CHARS]
        truncated = "\n…(仓库记忆过长已截断；建议精简 .vortocode/memory/repo.md)"
    return ("【仓库记忆】(本仓库的既有事实：构建/测试命令、目录约定、已知坑。"
            "与之矛盾时以当前代码为准，并用 remember_repo 更正)\n"
            f"{text}{truncated}")


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
