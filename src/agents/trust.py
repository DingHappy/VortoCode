"""授权级别——把"这个会话愿意少问几次"收成**一个有序档位**，喂进确认门的 `pre_authorized` 维。

为什么不是每端各加一个开关：`decide()`（gate.py）只认三个输入，其中 `pre_authorized` 是
"这次免不免确认"。此前只有 CLI 的 ``--yes`` 这一种全有全无的写法，于是 Desktop 想要"读操作
别老问、写还是要拍板"就无处可表达，用户只能要么一直点确认、要么没有中间档。

档位（有序，越往后越松）：

- ``ASK``（默认）：每次写盘/执行都问。
- ``READS``：只读操作（read_file/grep/只读命令）免确认；**写与执行照问**。
- ``FULL``：写与执行也免确认——"最高授权，不再确认"。

**三条硬约束不受档位影响**（都在本模块或 gate 里收口，端改不了）：

1. **污点回合一切授权失效**——`decide()` 里那条规矩不动：本回合摄入过 web/搜索/MCP 后，
   哪怕 FULL 也强制问人（问不到人则拒）。这是 D0 防提示注入的落点。
2. **无人值守（cron/heartbeat）永远 ASK**——它问不到人，任何档位对它只意味着"静默放行"。
   `resolve()` 直接把它夹回 ASK，于是 `decide()` 判 DENY，保持 fail-closed。
3. **外部会话（Web/IM/研究员）最高 READS**——这些会话天生要读外部内容，给写/执行的空白
   支票等于把提示注入的落点搬到确认门之外。

端申报"用户选了哪档"，能给到哪档由 `resolve()` 按能力档案说了算——与 `can_ask_human`
默认最严同一条纪律：端忘了申报只会更严，不会更松。
"""

from __future__ import annotations

from typing import Optional

ASK = "ask"
READS = "reads"
FULL = "full"

LEVELS = (ASK, READS, FULL)
_ORDER = {level: index for index, level in enumerate(LEVELS)}

# 操作分类。工具调确认门时申报自己是哪类；**缺省按最重的算**（见 `pre_authorized`），
# 新工具忘了申报只会多问一次，不会少问一次。
READ = "read"          # read_file / grep / git 只读 / 只读命令
WRITE = "write"        # 写盘、改文件、落分支
EXECUTE = "execute"    # run_command 等执行面
DELIVER = "deliver"    # 外发：开 PR、发图/发文件、发布制品

# 每档免确认覆盖哪些类别。
_AUTHORIZED = {
    ASK: frozenset(),
    READS: frozenset({READ}),
    FULL: frozenset({READ, WRITE, EXECUTE, DELIVER}),
}

# 能力档案 → 该档案允许的最高授权级别。表里没有的档案按最严处理。
_PROFILE_CEILING = {
    "local": FULL,
    "external": READS,
    "researcher": READS,
    "unattended": ASK,
}


def normalize(level: Optional[str]) -> str:
    """把端传来的字符串收敛成合法档位；不认识的一律按最严的 ASK。"""
    value = (level or "").strip().lower()
    return value if value in _ORDER else ASK


def ceiling(profile: Optional[str]) -> str:
    """该能力档案允许的最高授权级别。

    **申报了就按表夹，认不出的档案按最严**（新档案忘了登记 → 只会更严）。``None`` 是"端没申报
    档案"，此时不夹——那是 ``make_confirm_gate`` 的直接调用方（TUI 的项目 allow 规则、CLI 的
    ``--yes``）的既有语义：授权与否由它们自己判，本模块不在这里二次否决。走 `build_session`
    的路径永远带着档案（默认 local/external），所以三端跑的都是夹过的。
    """
    if profile is None:
        return FULL
    name = getattr(profile, "name", profile)
    return _PROFILE_CEILING.get(str(name or "").strip().lower(), ASK)


def resolve(level: Optional[str], profile: Optional[str] = None) -> str:
    """用户选的档位 ∩ 能力档案的上限——取更严的那个。"""
    requested = normalize(level)
    limit = ceiling(profile)
    return requested if _ORDER[requested] <= _ORDER[limit] else limit


def pre_authorized(level: Optional[str], kind: Optional[str] = None) -> bool:
    """这一档下，这类操作是否免确认。

    `kind` 缺省（或不认识）时按 WRITE 算——最重的那一类：工具忘了申报类别时，宁可多问一次。
    """
    category = (kind or "").strip().lower()
    if category not in (READ, WRITE, EXECUTE, DELIVER):
        category = WRITE
    return category in _AUTHORIZED[normalize(level)]
