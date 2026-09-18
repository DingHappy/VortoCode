"""外向操作：跑宿主机命令 / 开 PR。

两个都申报 `outward=True` 并过确认门——它们是"能对外部世界造成后果"的那一档。
命令执行还要过 `require_shell()` 闸与沙箱（缺沙箱后端时 fail-closed 拒跑，不裸执行）。
"""

from __future__ import annotations


from src.agents.tool import Tool
from src.agents.tools._common import _truthy


def build_command_tool(repo_root: str, confirm) -> list[Tool]:
    """UI 无关的 run_command（给 Web 用，注入 async confirm 门）。

    高危但带三层关口：危险操作硬拒 + 逐条 `await confirm(msg)` 确认 + build 门控。
    confirm(message) 是 async、返回 bool（Web 端走 WS 确认；超时/拒绝都安全不跑）。
    """
    from src.agents.gate import request
    from src.agents.trust import EXECUTE, READ

    background_sources: dict[str, dict] = {}

    def _record_command_effects(before_state, tool: str = "run_command") -> None:
        from src.gateway.change_sources import record_workspace_side_effects

        record_workspace_side_effects(
            repo_root, before_state, source="agent", tool=tool,
        )

    async def _run(args: dict) -> str:
        import asyncio
        from src.agents.sandbox import resolve_sandbox
        from src.agents.shell import (is_dangerous, is_read_only, run_command,
                                      run_command_background)
        cmd = str(args.get("command") or args.get("cmd") or "").strip()
        if not cmd:
            return "run_command 需要 command。"
        why = is_dangerous(cmd)
        if why:
            return f"拒绝执行（疑似危险操作：{why}）。请换更具体、安全的命令。"
        bg = _truthy(args.get("background"))
        label = "后台启动命令" if bg else "执行命令"
        decision = resolve_sandbox()
        if not decision.allowed:
            return f"拒绝执行：{decision.reason}"
        sandbox_notice = f"\n{decision.reason}" if not decision.isolated else ""
        # 污点警示由内核的 confirm gate 统一加（make_confirm_gate）——这里不再各自拼前缀，
        # 否则新加的确认点又会漏（此前 9 个确认点里只有 2 个记得加）。
        # 申报操作类别，好让授权档位分得开读和执行：`cat src/x.py` 和 `rm -rf build/` 不该
        # 一个待遇。判不出来按执行面算（fail-closed），见 src/agents/shell.py:is_read_only。
        kind = READ if is_read_only(cmd) else EXECUTE
        if not await request(confirm, f"在仓库根目录{label}？\n  $ {cmd}{sandbox_notice}", kind):
            return f"用户拒绝了命令：{cmd}"
        from src.gateway.change_sources import capture_workspace_state
        before_state = capture_workspace_state(repo_root)
        # 若预判时不是已确认的 auto fallback，执行阶段必须继续要求隔离，避免 backend/policy
        # 在确认后变化时静默降级。显式 off 仍由 policy 自身放行。
        require_isolation = not decision.fallback
        if bg:
            res = await asyncio.to_thread(
                run_command_background, repo_root, cmd, require_isolation=require_isolation
            )
            if not res.get("ok"):
                return f"后台启动失败：{res.get('error')}"
            _record_command_effects(before_state)
            background_sources[str(res["id"])] = capture_workspace_state(repo_root) or before_state
            warning = (f"\n{res.get('warning')}\n" if res.get("warning") else "")
            return (f"已后台启动命令 `{cmd}`，句柄 {res['id']}（pid {res['pid']}）。{warning}"
                    f"用 read_output(id={res['id']}) 看输出、stop_command(id={res['id']}) 停止。")
        res = await asyncio.to_thread(
            run_command, repo_root, cmd, require_isolation=require_isolation
        )
        _record_command_effects(before_state)
        warning = (f"{res.get('warning')}\n" if res.get("warning") else "")
        return f"命令 `{cmd}` 退出码 {res['code']}。\n{warning}输出尾部：\n{res['output'][-3000:]}"

    async def _read_output(args: dict) -> str:
        import asyncio
        from src.agents.shell import read_background
        bid = str(args.get("id") or args.get("bid") or "").strip()
        if not bid:
            return "read_output 需要 id（后台命令句柄，如 bg1）。"
        tail = args.get("tail")
        tail = int(tail) if str(tail).strip().isdigit() else None
        res = await asyncio.to_thread(read_background, bid, tail)
        if not res.get("ok"):
            return res.get("error", "读取失败")
        before_state = background_sources.get(bid)
        if before_state is not None:
            _record_command_effects(before_state, tool="run_command:background")
            if res.get("status") in {"exited", "done", "failed", "cancelled", "stopped"}:
                background_sources.pop(bid, None)
            else:
                from src.gateway.change_sources import capture_workspace_state
                background_sources[bid] = capture_workspace_state(repo_root) or before_state
        head = f"[{res['id']}] {res['status']}" + (f"（退出码 {res['code']}）" if res['code'] is not None else "")
        drop = f"\n（⚠ 有 {res['dropped']} 行因缓冲上限被挤掉、未读到）" if res.get("dropped") else ""
        return f"{head}{drop}\n{(res['output'] or '(暂无新输出)')[-3000:]}"

    async def _stop(args: dict) -> str:
        import asyncio
        from src.agents.shell import stop_background
        bid = str(args.get("id") or args.get("bid") or "").strip()
        if not bid:
            return "stop_command 需要 id。"
        res = await asyncio.to_thread(stop_background, bid)
        if not res.get("ok"):
            return res.get("error", "停止失败")
        before_state = background_sources.pop(bid, None)
        if before_state is not None:
            _record_command_effects(before_state, tool="run_command:background")
        return f"已停止后台命令 {bid}（退出码 {res.get('code')}）。"

    return [Tool("run_command",
                 "在仓库根目录跑任意 shell 命令（pytest/ruff/git/pip/make…）；高危，每条都需确认、"
                 "明显危险操作直接拒（仅 build）。长驻命令（dev server / watch / tail -f）传 "
                 "background=true 后台起、立即返回句柄，再用 read_output 看输出",
                 {"command": "要执行的 shell 命令",
                  "background": "可选，true=后台起长驻进程（不阻塞），用 read_output/stop_command 管理"},
                 _run, read_only=False, outward=True,
                 required_capabilities=("host_process",)),
            Tool("read_output",
                 "读某后台命令（run_command background=true 起的）的新增输出 + 运行状态；tail=N 看最近 N 行。只读",
                 {"id": "后台命令句柄，如 bg1", "tail": "可选，看最近 N 行"},
                 _read_output, read_only=True, required_capabilities=("host_process",)),
            Tool("stop_command",
                 "停掉某后台命令（terminate→kill）。用完 dev server / watcher 记得收摊",
                 {"id": "后台命令句柄，如 bg1"}, _stop, read_only=False,
                 required_capabilities=("host_process",))]   # 终止进程是运行态副作用→仅 build


def build_pr_tool(repo_root: str, confirm) -> list[Tool]:
    """UI 无关的 open_pr（给 Web 用，注入 async confirm 门）。外向操作：push + gh pr create，需确认。"""
    async def _open_pr(args: dict) -> str:
        import asyncio
        from src.agents.vcs import push_and_open_pr
        branch = str(args.get("branch", "")).strip()
        title = str(args.get("title", "")).strip()
        body = str(args.get("body", "")).strip()
        if not branch or not title:
            return "open_pr 需要 branch 和 title。"
        if not await confirm(
                f"把分支 {branch} push 到 origin 并开 PR「{title}」？这是外向操作（推到远端、建 PR）。"):
            return f"用户拒绝了为 {branch} 开 PR。"
        res = await asyncio.to_thread(push_and_open_pr, repo_root, branch, title, body)
        if res["ok"]:
            return f"已 push {branch} 并开 PR：{res['url']}"
        if res.get("pushed"):
            return f"已 push {branch}，但开 PR 失败：{res['error']}（可手动 gh pr create）"
        return f"开 PR 失败：{res['error']}"

    return [Tool("open_pr",
                 "把一个本地分支（如 dev_isolated 产出的 vorto/...）push 到 origin 并开 PR；"
                 "外向操作、需确认，gh 不可用则只 push（仅 build）",
                 {"branch": "要开 PR 的分支名", "title": "PR 标题", "body": "可选，PR 正文"},
                 _open_pr, read_only=False, outward=True,
                 required_capabilities=("authenticated_outbound",))]
