"""`.vortocode/` 托管忽略清单的覆盖契约——生成态不该冒进目标仓库的 git status。

真机诊断（2026-09-17）：桌面端的改动列表里混着 `.vortocode/journal`。VortoCode 往目标仓库
写运行时状态，`ensure_state_gitignore` 负责让这些状态对用户的 git status 隐形；但清单是手写
的，新加一处状态就少一条，**而且没有任何东西会红**——直到某天在一个自己没 gitignore
`.vortocode/` 的仓库里，用户看见一堆不知道哪来的文件。

这条契约扫出 src 里所有 `.vortocode/<名字>` 路径字面量，逐个丢进真 git 仓库问
`git check-ignore`：**工具写的必须被忽略，用户手改的必须不被忽略**。两边都断，所以
"把整个 .vortocode/ 一忽略了之"也会红——那会挡住用户版本化自己的 permissions.yaml。
"""

import re
import subprocess
from pathlib import Path

import pytest

from src.utils.state_dir import ensure_state_gitignore

# 用户手改、可能想 git add 的配置——**不该**被忽略。
USER_CONFIG = {
    "AGENTS.md", "BACKLOG.md", "HEARTBEAT.md", "agents", "assistants.yaml", "commands",
    "cron.yaml", "hooks.yaml", "instructions.md", "permissions.yaml", "persona.md",
    "pipelines", "review-policy.yaml", "skills", "verify.yaml",
}

# 不是目标仓库里的路径，扫描分辨不出来，在此登记豁免。
NOT_A_REPO_PATH = {
    "im_inbox",      # 在 ~/.vortocode 下（IM 收件箱跨仓库共用），不落目标仓库
    "sessions",      # 只在 cron.py 的 docstring 里出现，说的是"本模块不写这个"
    ".gitignore",    # 清单文件自己
}

_PATTERNS = (
    re.compile(r'\.vortocode"\s*/\s*"([A-Za-z0-9_.-]+)"'),   # Path(root) / ".vortocode" / "x"
    re.compile(r'\.vortocode/([A-Za-z0-9_.-]+)'),            # 字符串或注释里的 ".vortocode/x"
)


def _referenced_names() -> dict[str, str]:
    """src 里出现过的 `.vortocode/<名字>` → 第一个引用它的文件（报错时好定位）。"""
    found: dict[str, str] = {}
    for path in sorted(Path("src").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in _PATTERNS:
            for match in pattern.finditer(text):
                found.setdefault(match.group(1), path.as_posix())
    return found


@pytest.fixture()
def repo(tmp_path):
    """一个**没有** gitignore 掉 .vortocode/ 的目标仓库——正是出问题的那种。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    ensure_state_gitignore(str(tmp_path))
    return tmp_path


def _ignored(repo: Path, name: str) -> bool:
    """问 git：这个名字被忽略了吗。

    两种写法都问：托管清单里目录带尾斜杠（``journal/``），而 ``check-ignore`` 对一个不存在
    的路径默认按文件匹配——只问文件形式的话，所有目录条目都会被误报成"漏登记"。
    不落盘：``check-ignore`` 纯粹按模式匹配，路径存不存在都行。
    """
    return any(
        subprocess.run(["git", "check-ignore", "-q", candidate],
                       cwd=repo, capture_output=True).returncode == 0
        for candidate in (f".vortocode/{name}", f".vortocode/{name}/")
    )


def test_the_scan_actually_finds_things():
    names = _referenced_names()
    assert len(names) > 30, f"扫描没跑通，这条契约会空过：{names}"


def test_generated_state_is_invisible_to_the_target_repo(repo):
    leaking = sorted(
        f"{name}（{source}）"
        for name, source in _referenced_names().items()
        if name not in USER_CONFIG and name not in NOT_A_REPO_PATH and not _ignored(repo, name)
    )
    assert not leaking, (
        "这些生成态会冒进用户的 git status——加进 src/utils/state_dir.py 的 _STATE_ENTRIES；"
        f"若其实是用户配置，加进本文件的 USER_CONFIG 并说明理由：\n  " + "\n  ".join(leaking))


@pytest.mark.parametrize("name", sorted(USER_CONFIG))
def test_user_config_stays_versionable(repo, name):
    """反方向也要断：不能图省事把整个 .vortocode/ 一忽略了之。"""
    assert not _ignored(repo, name), f"{name} 是用户配置，不该被我们忽略"


def test_upgrade_reaches_an_existing_repo(tmp_path):
    """老仓库里已有托管区 → 新条目要能补进去，且区外的用户规则逐字保留。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    ensure_state_gitignore(str(tmp_path))
    gitignore = tmp_path / ".vortocode" / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8").replace("journal/\n", "") + "\n# 我自己的规则\n*.bak\n",
        encoding="utf-8")

    ensure_state_gitignore(str(tmp_path))
    text = gitignore.read_text(encoding="utf-8")
    assert "journal/" in text
    assert "# 我自己的规则" in text and "*.bak" in text
    assert text.count("# >>> vortocode managed") == 1      # 不能追加出第二个托管区
