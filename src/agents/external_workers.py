"""外部执行者：把隔离 dev 流水线的「实现 / 审查」角色交给本机 Codex CLI 或 Claude Code CLI。

VortoCode 仍是中介：拆解、worktree 隔离、自测重试、逐件与集成验证、确认门、开 PR 全走原流水线，
这里只把「谁来写 / 谁来审」换成外部 CLI。`ExternalWorkerAgent` 与 `MainAgent` 同样暴露
`run_turn(message, mode, emit)`，所以 `dev_tools` 只在两个工厂点换实现即可。

配置（只认主进程环境变量，不读仓库文件——能改仓库的不该能换执行者）::

    VORTOCODE_EXTERNAL_WORKERS=implement=codex,review=claude
    VORTOCODE_EXTERNAL_WORKER_TIMEOUT=1800        # 单次运行秒数，超时杀整个进程组
    VORTOCODE_CODEX_BIN / VORTOCODE_CLAUDE_BIN    # 可选：CLI 路径（缺省走 PATH）

安全边界（改之前先读 docs/MODEL_ROUTING.md「外部执行者」）：
- 可写只在流水线给的一次性 worktree 里；审查一律只读。
- 永不拼 `--dangerously-*` / `danger-full-access` / `bypassPermissions`；显式参数覆盖用户级放宽。
- 子进程环境走 `sandbox.policy.child_env()`，再剥 GitHub 令牌：交付由 VortoCode 做，执行者不 push。
- unattended 档位 fail-closed；事件里出现联网动作就把本回合标污点。
- 不继承个人配置里的 hooks / 插件 / 记忆 / 通知 / MCP / 权限放宽，只透传「用哪个模型、哪家服务」：
  Codex 用 `--ignore-user-config` 再按白名单回填 model / model_provider；Claude 只加载 local 级
  settings，再从用户 settings.json 回填 `model` 与 `env`（第三方模型通常配在这里）。
- 不存会话：Codex `--ephemeral`、Claude `--no-session-persistence`（每次都是新会话，不续用）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

WORKERS = ("codex", "claude")
ROLES = ("implement", "review")
ROLE_ENV = "VORTOCODE_EXTERNAL_WORKERS"
TIMEOUT_ENV = "VORTOCODE_EXTERNAL_WORKER_TIMEOUT"
DEFAULT_TIMEOUT = 1800
# 交付（push / 开 PR）只由 VortoCode 做；外部执行者拿不到仓库写令牌。
_EXTRA_STRIPPED = frozenset({"GITHUB_TOKEN", "GH_TOKEN", "GH_ENTERPRISE_TOKEN"})
_CLAUDE_READ_TOOLS = ("Read", "Glob", "Grep", "Bash(python -m pytest *)", "Bash(pytest *)")
_CLAUDE_WRITE_TOOLS = ("Edit", "Write", "MultiEdit")
_CLAUDE_NETWORK_TOOLS = frozenset({"WebFetch", "WebSearch"})
_TAIL = 2000


class ExternalWorkerError(RuntimeError):
    """外部 CLI 没有给出可用结果（找不到命令、超时、非零退出、事件报错）。"""


@dataclass
class WorkerEvent:
    kind: str                 # session | message | command | file | tool | usage | error | result
    text: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class WorkerRun:
    worker: str
    session_id: str = ""
    final_text: str = ""
    events: list = field(default_factory=list)
    error: str = ""
    exit_code: Optional[int] = None
    used_network: bool = False
    cost_usd: Optional[float] = None
    usage: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error and self.exit_code == 0


# ── 配置 ──────────────────────────────────────────────────────────────────

def parse_role_workers(raw: str) -> dict[str, str]:
    """`implement=codex,review=claude` → {role: worker}。未知角色/执行者忽略并告警（不猜）。"""
    table: dict[str, str] = {}
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        role, _, worker = part.partition("=")
        role, worker = role.strip().lower(), worker.strip().lower()
        if role in ROLES and worker in WORKERS:
            table[role] = worker
        else:
            logger.warning("忽略无效的外部执行者配置项 %r（角色 %s，执行者 %s）",
                           part.strip(), "/".join(ROLES), "/".join(WORKERS))
    return table


def worker_for_role(role: str) -> Optional[str]:
    return parse_role_workers(os.getenv(ROLE_ENV, "")).get(role)


def worker_timeout() -> int:
    try:
        value = int(os.getenv(TIMEOUT_ENV, "") or DEFAULT_TIMEOUT)
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def worker_binary(worker: str) -> Optional[str]:
    override = os.getenv(f"VORTOCODE_{worker.upper()}_BIN", "").strip()
    return override or shutil.which(worker)


def worker_env() -> dict[str, str]:
    from src.sandbox.policy import child_env
    return {k: v for k, v in child_env().items() if k not in _EXTRA_STRIPPED}


# ── 个人配置透传（只透传模型选择） ─────────────────────────────────────────

_CODEX_PASSTHROUGH = ("model", "model_reasoning_effort", "model_provider", "service_tier")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _toml_value(value: Any) -> Optional[str]:
    """把配置值写回 `-c key=value` 能解析的 TOML 字面量；不认识的类型返回 None（跳过，不猜）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        items = [_toml_value(v) for v in value]
        return None if any(i is None for i in items) else "[" + ", ".join(items) + "]"  # type: ignore[arg-type]
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            rendered = _toml_value(item)
            if rendered is None:
                return None
            parts.append(f"{json.dumps(str(key), ensure_ascii=False)} = {rendered}")
        return "{ " + ", ".join(parts) + " }"
    return None


def codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")


def codex_user_overrides(config_path: Optional[Path] = None) -> list[str]:
    """从用户 config.toml 只取模型选择（含所选第三方 provider 的定义），拼成 `-c` 参数。"""
    path = config_path or codex_home() / "config.toml"
    try:
        try:
            import tomllib                      # type: ignore[import-not-found]
        except ModuleNotFoundError:             # Python 3.10
            import tomli as tomllib             # type: ignore[no-redef]
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ModuleNotFoundError):
        return []
    args: list[str] = []
    for key in _CODEX_PASSTHROUGH:
        rendered = _toml_value(config.get(key)) if key in config else None
        if rendered is not None:
            args += ["-c", f"{key}={rendered}"]
    provider = config.get("model_provider")
    table = (config.get("model_providers") or {}).get(provider) if isinstance(provider, str) else None
    if isinstance(table, dict) and _SAFE_KEY.match(provider or ""):
        for key, value in table.items():
            rendered = _toml_value(value)
            if rendered is not None and _SAFE_KEY.match(str(key)):
                args += ["-c", f"model_providers.{provider}.{key}={rendered}"]
    return args


def claude_settings_path() -> Path:
    return Path(os.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"


def claude_user_settings(settings_path: Optional[Path] = None) -> tuple[list[str], dict[str, str]]:
    """从用户 settings.json 只取 `model`（→ --model）与 `env`（→ 子进程环境，不进 argv）。

    第三方模型（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL…）通常就配在 `env` 里；
    hooks、插件、权限放宽仍然不加载。`apiKeyHelper` 是取凭据的命令，按原样交给 --settings。
    """
    try:
        settings = json.loads((settings_path or claude_settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {}
    if not isinstance(settings, dict):
        return [], {}
    args: list[str] = []
    model = settings.get("model")
    if isinstance(model, str) and model.strip():
        args += ["--model", model.strip()]
    helper = settings.get("apiKeyHelper")
    if isinstance(helper, str) and helper.strip():
        args += ["--settings", json.dumps({"apiKeyHelper": helper.strip()}, ensure_ascii=False)]
    raw_env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
    env = {str(k): str(v) for k, v in raw_env.items()
           if _ENV_NAME.match(str(k)) and isinstance(v, (str, int, float, bool))}
    return args, env


def user_config(worker: str) -> tuple[list[str], dict[str, str]]:
    """(额外 argv, 额外环境)。环境里同样剥掉交付令牌：个人配置不能把 push 能力带回来。"""
    if worker == "codex":
        return codex_user_overrides(), {}
    args, env = claude_user_settings()
    from src.sandbox.policy import is_stripped_env
    return args, {k: v for k, v in env.items() if k not in _EXTRA_STRIPPED and not is_stripped_env(k)}


# ── 命令行 ────────────────────────────────────────────────────────────────

def build_argv(worker: str, *, cwd: str, write: bool, system: str = "",
               binary: str = "", extra: tuple[str, ...] | list[str] = ()) -> list[str]:
    """拼一次无头运行的 argv。权限只由这里的显式参数决定，不继承用户级放宽。

    提示词一律走 stdin（见 `stdin_text`）：审查提示词带整份 diff，放进 argv 会撞 ARG_MAX。
    """
    if worker == "codex":
        return [binary or "codex", "exec", "--json", "--ignore-user-config", "--ephemeral",
                "-s", "workspace-write" if write else "read-only",
                "-C", cwd, "-c", "mcp_servers={}", *extra, "-"]
    if worker == "claude":
        tools = _CLAUDE_READ_TOOLS + (_CLAUDE_WRITE_TOOLS if write else ())
        argv = [binary or "claude", "-p", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "dontAsk", "--permission-prompts", "none",
                "--setting-sources", "local", "--strict-mcp-config", "--no-session-persistence",
                *extra]
        if system.strip():
            argv += ["--append-system-prompt", system.strip()]
        return argv + ["--allowedTools", *tools]
    raise ValueError(f"未知外部执行者 {worker!r}")


def stdin_text(worker: str, prompt: str, system: str = "") -> str:
    """Codex 没有追加系统提示的参数，规则拼在提示词前；Claude 的规则走 --append-system-prompt。"""
    if worker == "codex" and system.strip():
        return f"{system.strip()}\n\n---\n\n{prompt}"
    return prompt


# ── 事件解析 ──────────────────────────────────────────────────────────────

def parse_codex_event(obj: dict) -> list[WorkerEvent]:
    kind = obj.get("type")
    if kind == "thread.started":
        return [WorkerEvent("session", data={"session_id": str(obj.get("thread_id") or "")})]
    if kind == "turn.completed":
        return [WorkerEvent("usage", data=dict(obj.get("usage") or {}))]
    if kind in ("turn.failed", "error"):
        err = obj.get("error") if isinstance(obj.get("error"), dict) else {}
        return [WorkerEvent("error", str(err.get("message") or obj.get("message") or kind))]
    if kind != "item.completed":
        return []
    item = obj.get("item") or {}
    itype = item.get("type")
    if itype == "agent_message":
        return [WorkerEvent("message", str(item.get("text") or ""))]
    if itype == "command_execution":
        return [WorkerEvent("command", str(item.get("command") or ""),
                            {"exit_code": item.get("exit_code"),
                             "output": str(item.get("aggregated_output") or "")[-_TAIL:]})]
    if itype == "file_change":
        paths = [str(c.get("path") or "") for c in item.get("changes") or [] if isinstance(c, dict)]
        return [WorkerEvent("file", ", ".join(paths), {"paths": paths})]
    if itype == "web_search":
        return [WorkerEvent("tool", str(item.get("query") or "web_search"), {"network": True})]
    if itype == "mcp_tool_call":
        return [WorkerEvent("tool", f"{item.get('server')}.{item.get('tool')}", {"network": True})]
    if itype == "error":
        return [WorkerEvent("error", str(item.get("message") or "error"))]
    return []


def parse_claude_event(obj: dict) -> list[WorkerEvent]:
    kind = obj.get("type")
    if kind == "system" and obj.get("subtype") == "init":
        return [WorkerEvent("session", data={"session_id": str(obj.get("session_id") or "")})]
    if kind == "assistant":
        events = []
        for block in (obj.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                events.append(WorkerEvent("message", str(block["text"])))
            elif block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                args = block.get("input") or {}
                detail = args.get("command") or args.get("file_path") or args.get("pattern") or ""
                network = name in _CLAUDE_NETWORK_TOOLS or name.startswith("mcp__")
                if name in _CLAUDE_WRITE_TOOLS:
                    events.append(WorkerEvent("file", str(detail), {"paths": [str(detail)]}))
                elif name == "Bash":
                    events.append(WorkerEvent("command", str(detail)))
                else:
                    events.append(WorkerEvent("tool", f"{name} {detail}".strip(), {"network": network}))
        return events
    if kind == "result":
        data = {"session_id": str(obj.get("session_id") or ""), "subtype": obj.get("subtype"),
                "cost_usd": obj.get("total_cost_usd"), "usage": obj.get("usage") or {},
                "denials": obj.get("permission_denials") or []}
        if obj.get("is_error") or obj.get("subtype") != "success":
            return [WorkerEvent("error", str(obj.get("result") or obj.get("subtype") or "error"), data)]
        return [WorkerEvent("result", str(obj.get("result") or ""), data)]
    return []


_PARSERS = {"codex": parse_codex_event, "claude": parse_claude_event}


def fold_event(run: WorkerRun, event: WorkerEvent) -> None:
    """把一条事件并进运行结果（纯函数式累积，便于离线测试）。"""
    run.events.append(event)
    if event.kind == "session" and event.data.get("session_id"):
        run.session_id = event.data["session_id"]
    elif event.kind == "message" and event.text.strip():
        run.final_text = event.text           # Codex：最后一条 agent_message 即结论
    elif event.kind == "result":
        run.final_text = event.text or run.final_text
        run.session_id = event.data.get("session_id") or run.session_id
        run.cost_usd = event.data.get("cost_usd")
        run.usage = dict(event.data.get("usage") or {})
    elif event.kind == "usage":
        run.usage = dict(event.data)
    elif event.kind == "error":
        run.error = event.text or "外部执行者报错"
        if event.data.get("session_id"):
            run.session_id = event.data["session_id"]
    if event.data.get("network"):
        run.used_network = True


# ── 运行 ──────────────────────────────────────────────────────────────────

def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run_worker(worker: str, prompt: str, cwd: str, *, write: bool, system: str = "",
                     on_event: Optional[Callable[[WorkerEvent], Any]] = None,
                     timeout: Optional[int] = None) -> WorkerRun:
    """跑一次外部 CLI，逐行解析 JSONL 事件。失败如实写进 `WorkerRun.error`，不抛。"""
    run = WorkerRun(worker)
    binary = worker_binary(worker)
    if not binary:
        run.error = f"找不到 {worker} 命令：请先安装并登录，或设置 VORTOCODE_{worker.upper()}_BIN"
        return run
    extra, extra_env = user_config(worker)
    argv = build_argv(worker, cwd=cwd, write=write, system=system, binary=binary, extra=extra)
    parse = _PARSERS[worker]
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env={**worker_env(), **extra_env}, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True, limit=16 * 1024 * 1024)
    stderr_chunks: list[bytes] = []

    async def _drain_stderr():
        assert proc.stderr is not None
        while chunk := await proc.stderr.read(65536):
            stderr_chunks.append(chunk)
            if sum(map(len, stderr_chunks)) > 64 * 1024:
                stderr_chunks.pop(0)

    async def _read_stdout():
        assert proc.stdout is not None
        while line := await proc.stdout.readline():
            try:
                obj = json.loads(line)
            except ValueError:
                continue                       # 非 JSON 行（横幅/日志）不算事件
            if not isinstance(obj, dict):
                continue
            for event in parse(obj):
                fold_event(run, event)
                if on_event is not None:
                    try:
                        on_event(event)
                    except Exception:  # noqa: BLE001 —— 进度回调失败不影响运行
                        logger.debug("外部执行者进度回调出错", exc_info=True)

    async def _feed_stdin():
        assert proc.stdin is not None
        try:
            proc.stdin.write(stdin_text(worker, prompt, system).encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass                               # 进程提前退出：以退出码与 stderr 为准
        finally:
            proc.stdin.close()

    stderr_task = asyncio.ensure_future(_drain_stderr())
    stdin_task = asyncio.ensure_future(_feed_stdin())
    try:
        await asyncio.wait_for(_read_stdout(), timeout or worker_timeout())
        run.exit_code = await proc.wait()
    except asyncio.TimeoutError:
        _kill_group(proc)
        await proc.wait()
        run.exit_code = proc.returncode
        run.error = f"{worker} 运行超时（{timeout or worker_timeout()} 秒），已终止"
    except asyncio.CancelledError:
        _kill_group(proc)
        raise
    finally:
        if proc.returncode is None:
            _kill_group(proc)
        for task in (stdin_task, stderr_task):
            try:
                await asyncio.wait_for(task, 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
    if not run.error and run.exit_code != 0:
        tail = b"".join(stderr_chunks).decode("utf-8", "replace").strip()[-_TAIL:]
        run.error = f"{worker} 退出码 {run.exit_code}" + (f"：{tail}" if tail else "")
    logger.info("外部执行者 %s 结束：exit=%s session=%s cost=%s error=%s",
                worker, run.exit_code, run.session_id, run.cost_usd, run.error[:200])
    return run


def describe_event(worker: str, event: WorkerEvent) -> str:
    """给进度播报用的一行文字；不值得播的返回空串。"""
    if event.kind == "command":
        return f"[{worker}] $ {event.text[:100]}"
    if event.kind == "file":
        return f"[{worker}] 改动 {event.text[:100]}"
    if event.kind == "error":
        return f"[{worker}] 出错：{event.text[:100]}"
    return ""


QUESTION_MARKER = "VORTO_QUESTION"
QUESTION_RULES = (
    "\n\n【缺少必要事实时】不要猜测，也不要改与任务无关的东西。先停止修改，"
    f"然后让最后一条回复只包含一行：{QUESTION_MARKER} " + '{"question": "具体问题", '
    '"options": ["最多 3 个建议答案"], "context": "已确认的事实、文件证据和未完成内容"}'
    "\n这一行会转给人回答；回答后会在干净的工作区重新实现本块，未提交的改动不会保留。"
)


def parse_question(text: str) -> Optional[dict]:
    """从结论里找 `VORTO_QUESTION {json}` 行；JSON 坏了就把整行余下部分当问题，不丢。"""
    for line in reversed((text or "").splitlines()):
        stripped = line.strip().strip("`").strip()
        if not stripped.startswith(QUESTION_MARKER):
            continue
        rest = stripped[len(QUESTION_MARKER):].strip().lstrip(":：").strip()
        try:
            data = json.loads(rest)
        except ValueError:
            data = {"question": rest}
        if not isinstance(data, dict):
            data = {"question": str(data)}
        question = str(data.get("question") or "").strip()
        if not question:
            return None
        options = data.get("options") if isinstance(data.get("options"), list) else []
        return {"question": question[:2000], "options": [str(o)[:200] for o in options[:3]],
                "context": str(data.get("context") or "")[:4000]}
    return None


class ExternalWorkerAgent:
    """与 MainAgent 同形的最小适配：流水线只调 `run_turn`。"""

    def __init__(self, worker: str, cwd: str, *, write: bool, system: str = "",
                 capabilities: Any = None,
                 on_progress: Optional[Callable[[str], None]] = None,
                 question: Any = None) -> None:
        if worker not in WORKERS:
            raise ValueError(f"未知外部执行者 {worker!r}")
        self.worker, self.cwd, self.write, self.system = worker, str(cwd), write, system
        # 开发任务的提问绑定（task_questions.DevelopmentQuestionBinding）：执行者没有我们的工具，
        # 只能按约定输出一行 VORTO_QUESTION；由这里替它调用同一个 ask_task_question。
        self.question = question
        self.capabilities = capabilities
        self.on_progress = on_progress
        self.tools: dict = {}
        self.last_run: Optional[WorkerRun] = None

    async def run_turn(self, message: str, mode: str = "build", emit: Any = None, **_: Any) -> str:
        from src.agents.capabilities import UNATTENDED_PROFILE
        if getattr(self.capabilities, "profile", None) == UNATTENDED_PROFILE:
            raise ExternalWorkerError("无人值守档位不允许启动外部执行者（fail-closed）")

        def _event(event: WorkerEvent) -> None:
            line = describe_event(self.worker, event)
            if line and self.on_progress is not None:
                self.on_progress(line)

        tool = getattr(self.question, "tool", None)
        system = self.system + (QUESTION_RULES if tool is not None else "")
        from src.agents.taint import mark_tainted
        if getattr(self.question, "tainted", False):
            mark_tainted()                     # 之前的问答来自不可信回合：本回合同样按污点处理
        run = await run_worker(self.worker, message, self.cwd, write=self.write and mode != "plan",
                               system=system, on_event=_event)
        self.last_run = run
        if run.used_network:
            mark_tainted()
        if not run.ok:
            raise ExternalWorkerError(run.error or f"{self.worker} 未给出结果")
        asked = parse_question(run.final_text)
        if asked is not None:
            if tool is None:                   # 没有提问通道（非开发任务 / 提问次数用尽）：如实失败，不装成结论
                raise ExternalWorkerError(f"{self.worker} 需要人工确认但当前任务不能提问：{asked['question'][:300]}")
            if self.on_progress is not None:
                self.on_progress(f"[{self.worker}] 提问：{asked['question'][:100]}")
            await tool.handler(asked)          # 落台账并抛 ToolTurnYield，与内部子 agent 提问同一路径
        return run.final_text


def external_agent_for_role(role: str, cwd: str, *, system: str = "", capabilities: Any = None,
                            on_progress: Optional[Callable[[str], None]] = None,
                            question: Any = None) -> Optional[ExternalWorkerAgent]:
    """角色配了外部执行者就造一个；没配返回 None（调用方照旧用内部子 agent）。"""
    worker = worker_for_role(role)
    if worker is None:
        return None
    return ExternalWorkerAgent(worker, cwd, write=role == "implement", system=system,
                               capabilities=capabilities, on_progress=on_progress,
                               question=question if role == "implement" else None)


def describe_worker(worker: str) -> dict:
    """给 doctor / 设置页用：装没装、登没登录、实际用哪个模型。只读，不发模型请求。"""
    import subprocess
    info: dict = {"worker": worker, "binary": worker_binary(worker), "logged_in": None,
                  "auth": "", "model": ""}
    extra, extra_env = user_config(worker)
    if "--model" in extra:
        info["model"] = extra[extra.index("--model") + 1]
    for i, arg in enumerate(extra[:-1]):
        if arg == "-c" and extra[i + 1].startswith(("model=", "model_provider=")):
            key, _, value = extra[i + 1].partition("=")
            info["model" if key == "model" else "provider"] = value.strip('"')
    if not info["binary"]:
        return info
    cmd = [info["binary"], "login", "status"] if worker == "codex" else [info["binary"], "auth", "status"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                           env={**worker_env(), **extra_env}, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        info["auth"] = f"无法检查登录状态：{type(error).__name__}"
        return info
    text = (r.stdout or "") + (r.stderr or "")
    if worker == "codex":
        info["logged_in"] = r.returncode == 0 and "logged in" in text.lower()
        info["auth"] = text.strip().splitlines()[0][:80] if text.strip() else ""
    else:
        try:
            status = json.loads(r.stdout)
        except ValueError:
            status = {}
        third_party = any(k in extra_env or os.getenv(k) for k in
                          ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"))
        info["logged_in"] = bool(status.get("loggedIn")) or third_party
        info["auth"] = str(status.get("authMethod") or ("settings.json env" if third_party else ""))
        if extra_env.get("ANTHROPIC_BASE_URL"):
            info["provider"] = "ANTHROPIC_BASE_URL（第三方）"
    return info
