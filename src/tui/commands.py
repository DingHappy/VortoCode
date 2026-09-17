"""TUI 的斜杠命令实现（`/model` `/diff` `/commit` `/memory` …）。

## 为什么单独成一个 mixin

`tui/app.py` 曾是 5942 行，其中 `VortoCodeTUI` 一个类占 5507 行、206 个方法。
唯一大而内聚的一簇就是这 38 个 `_cmd_*`（约 1580 行 / 类的 29%）——
它们回答的是同一个问题：**"用户敲了一条斜杠命令，做什么"**，与"怎么把界面画出来"
是两件事。

## 为什么用 mixin 而不是抽成独立对象

实测这些方法压倒性使用的是 app 自己的辅助（`self._emit` 164 次、`self._chrome` 55 次），
真正碰 Textual API 的只有 14 个方法、主要是 `run_worker`（10 处）。抽成独立对象要么
把 app 整个传进去（换汤不换药），要么为 20 多个属性造一层转发（纯负担）。
mixin 保持方法解析原样，**行为逐字节不变**，同时让"命令做什么"成为可单独打开的一册。

诚实说明：这是**可读性**改进，不是解耦——耦合面（`self.*`）一处没减，只是挪了位置。
真正的解耦要先给 app 定出一层稳定的内部接口，那是另一件事。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from textual import work
from textual.widgets import RichLog

# ListPicker / _AGENTS_DB 留在 app.py（前者是本 TUI 的小组件，后者是路径常量）。
# **函数体内导入**：app 在模块级导入本模块，这里再模块级导回去就成真环。
# 这正是 self-analyze 环检测该放行的那类边（2026-08-03 刚把那条判据修准）。


class TUICommandsMixin:
    """斜杠命令实现。**只被 `VortoCodeTUI` 继承**，依赖它提供的 `_emit`/`_chrome`/`agent` 等。"""

    def _cmd_model(self, arg: str) -> None:
        """/model：无参弹 opencode 式模型选择器（回车切换，本会话生效）；带参直接切。

        选择器候选优先取服务端 `GET /models` 的实际可用列表（中转站是 OpenAI 兼容协议，
        报出来的才是真开通的），失败/离线回落到静态 _COMMON_MODELS——标题标注来源。
        """
        arg = (arg or "").strip()
        cur = self._sb.get("model", "?")
        if not arg:
            self.run_worker(self._open_model_picker(cur), exclusive=True, group="model-picker")
            return
        self._model_override = arg          # agent 未建时，_build_main_agent 会读它应用
        if self.agent is not None:
            try:
                self.agent.set_model(arg)
            except Exception as e:  # noqa: BLE001
                self._chrome(f"[red]切换模型失败：{e}[/red]")
                return
        self._sb["model"] = arg
        self._render_statusbar()
        self._chrome(f"[green]已切换模型 → {arg}[/green]"
                     "[dim]（本会话后续对话生效；未授权的模型会在下次调用时报 403）[/dim]")

    # ---------------------------------------------------------------- 技能（SKILL.md）
    def _cmd_skills(self, arg: str) -> None:
        """/skills 列出技能；/skills reload 重新扫描并让主 agent 下次重建。"""
        if arg.strip() == "reload":
            reg = self._skill_registry(reload=True)
            self.agent = None               # 让主 agent 下次重建、拿到新技能目录
            self._chrome(f"[green]已重载技能（{len(reg.skills)} 个）；下次对话生效[/green]")
            return
        reg = self._skill_registry()
        if not reg.skills:
            self._emit("(没有技能。把 SKILL.md 放到 skills/<名>/ 或 .vortocode/skills/<名>/ 后 /skills reload)")
            return
        lines = ["可用技能（对话里说\"用 X 技能\"，或主 agent 自动选用；/skills reload 重载）:"]
        for s in reg.skills.values():
            lines.append(f"  {s.name} — {s.description}")
        self._emit("\n".join(lines))

    def _cmd_tools(self) -> None:
        """列出主 agent 可用工具（plan 只读可用；写/重型仅 build）。"""
        agent = self.agent or self._build_main_agent()
        lines = ["主 agent 工具（plan 只读可用；写/重型仅 build）:"]
        for t in agent._tool_list:
            gate = "只读" if t.read_only else "写/重型"
            lines.append(f"  {t.name} [{gate}] — {t.description}")
        self._emit("\n".join(lines))

    def _cmd_permissions(self, arg: str = "") -> None:
        """/permissions：查看本会话权限状态与项目 allow/deny/profile 规则。"""
        raw = (arg or "").strip()
        low = raw.lower()
        if low in ("plan", "build"):
            self._set_mode(low)
        elif low in ("show --effective", "effective", "show effective"):
            self._emit(self._permission_effective_text())
            return
        elif low.startswith("explain "):
            parts = raw.split(maxsplit=2)
            tool = parts[1].strip() if len(parts) > 1 else ""
            value = parts[2].strip() if len(parts) > 2 else ""
            if not tool:
                self._emit("用法: /permissions explain <tool> [value]")
                return
            self._emit(self._permission_explain_text(tool, value))
            return
        elif low in ("reset", "reset-session", "session-reset"):
            self._clear_always_allow()       # 会话标志 + 项目设置里的常驻授权一并撤销
            self._sync_subtitle()
            self._chrome("[green]已清除本会话及项目设置中「始终允许」的写/命令授权[/green]")
        elif low.startswith("deny"):
            parts = raw.split(maxsplit=2)
            if len(parts) < 2:
                self._emit("用法: /permissions deny <tool> [glob]")
                return
            tool = parts[1].strip()
            pattern = parts[2].strip().strip('"').strip("'") if len(parts) > 2 else ""
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tool):
                self._emit("权限规则工具名非法；用法: /permissions deny <tool> [glob]")
                return
            cfg = self._append_permission_deny(tool, pattern)
            shown = f"{tool}: {pattern}" if pattern else tool
            self._chrome(f"[green]已追加 deny 规则：{shown}（{cfg}）[/green]")
        elif low.startswith("allow"):
            parts = raw.split(maxsplit=2)
            if len(parts) < 2:
                self._emit("用法: /permissions allow <tool> [glob]")
                return
            tool = parts[1].strip()
            pattern = parts[2].strip().strip('"').strip("'") if len(parts) > 2 else ""
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tool):
                self._emit("权限规则工具名非法；用法: /permissions allow <tool> [glob]")
                return
            cfg = self._append_permission_allow(tool, pattern)
            shown = f"{tool}: {pattern}" if pattern else tool
            self._chrome(f"[green]已追加 allow 规则：{shown}（{cfg}）[/green]")
        elif low.startswith("profile"):
            parts = raw.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                self._emit("用法: /permissions profile <name|none>")
                return
            profile = parts[1].strip()
            if profile.lower() in ("none", "off", "clear", "default", "-"):
                profile = ""
            elif not re.match(r"^[A-Za-z0-9_.-]+$", profile):
                self._emit("权限 profile 名非法；仅支持字母、数字、点、下划线和短横线。")
                return
            cfg = self._set_permission_profile(profile)
            if profile:
                self._chrome(f"[green]已切换权限 profile：{profile}（{cfg}）[/green]")
            else:
                self._chrome(f"[green]已清除权限 profile（{cfg}）[/green]")
        elif raw:
            self._emit("用法: /permissions [show --effective|explain <tool> [value]|plan|build|reset|allow <tool> [glob]|deny <tool> [glob]|profile <name|none>]")
            return
        from src.agents.permissions import load_permissions
        agent = self.agent or self._build_main_agent()
        read_tools = [t.name for t in agent._tool_list if t.read_only]
        write_tools = [t.name for t in agent._tool_list if not t.read_only]
        cfg = Path(self.repo_root) / ".vortocode" / "permissions.yaml"
        perm = load_permissions(self.repo_root)
        if perm.profile:
            profile = perm.profile if perm.profile_found else f"{perm.profile}（未找到，不生效）"
        else:
            profile = "未设置"
        lines = [
            "[b]工具权限[/b]",
            f"模式: {self.mode}（plan 只允许只读工具；build 可请求写/重型工具）",
            f"会话能力 profile: {self._capability_profile}",
            f"项目 profile: {profile}",
            f"始终允许（记住）: 写={'yes' if self._allow_writes_session else 'no'} · "
            f"命令={'yes' if self._allow_commands_session else 'no'}"
            + ("  （/permissions reset 撤销）"
               if (self._allow_writes_session or self._allow_commands_session) else ""),
            f"工具: 只读 {len(read_tools)} 个 · 写/重型 {len(write_tools)} 个",
            "",
            f"项目 allow 规则: {cfg}",
        ]
        if perm.allow_rules:
            for tool, glob in perm.allow_rules:
                pat = glob if glob is not None else "*"
                lines.append(f"  allow {tool}: {pat}")
        else:
            lines.append("  （无 allow 规则；写/命令默认仍需人工确认）")
        lines += [
            "",
            f"项目 deny 规则: {cfg}",
        ]
        if perm.rules:
            for tool, glob in perm.rules:
                pat = glob if glob is not None else "*"
                lines.append(f"  deny {tool}: {pat}")
        else:
            lines.append("  （无 deny 规则；危险命令仍会走内置拦截和人工确认）")
        lines += [
            "",
            "配置示例:",
            "```yaml",
            "allow:",
            '  - "run_command: pytest *"',
            '  - write_file: "docs/*.md"',
            "deny:",
            "  - web_fetch",
            '  - "run_command: rm *"',
            '  - edit_file: "*/secrets/*"',
            "profile: dev",
            "profiles:",
            "  dev:",
            "    allow:",
            '      - "run_command: ruff *"',
            "```",
            "",
            "提示: allow 只免人工确认；deny、plan/build、危险命令和外发确认仍优先。",
            "排查: /permissions show --effective · /permissions explain <tool> [value]",
        ]
        self._emit("\n".join(lines))

    def _cmd_memory_repo(self, arg: str = "") -> None:
        """/memory repo [add <事实>]：看/写**仓库记忆**（.vortocode/memory/repo.md）。

        与长期记忆的区别：仓库记忆跟着代码库走（不跟人），装配时静态注入系统提示、
        dev 流水线的子 agent 也会带上——存"构建/测试命令、目录约定、踩过的坑"这类事实。
        """
        from src.agents.repo_memory import (MAX_REPO_MEMORY_CHARS, append_repo_memory,
                                            read_repo_memory, repo_memory_path)
        raw = (arg or "").strip()
        path = repo_memory_path(self.repo_root)
        if raw.lower().startswith("add "):
            content = raw[4:].strip()
            if not content:
                self._emit("用法: /memory repo add <关于本仓库的事实>")
                return
            # 与 remember_repo 工具同一条策略线：仓库记忆每轮进系统提示，只收 durable。
            from src.memory.write_policy import MemoryWritePolicy, MemoryWriteRequest
            decision = MemoryWritePolicy().evaluate(MemoryWriteRequest(
                content=content, source="tui_user", session_id=self.session_id,
                write_method="slash_command", memory_type="repo_fact"))
            if decision.outcome != "durable":
                self._emit(f"拒绝写入仓库记忆（{'/'.join(decision.reasons) or decision.outcome}）。"
                           "仓库记忆每轮都会进系统提示，只接受可信的仓库事实。")
                return
            try:
                append_repo_memory(self.repo_root, decision.content)
            except Exception as e:  # noqa: BLE001
                self._emit(f"写入仓库记忆失败: {e}")
                return
            self._chrome(f"[green]已写入仓库记忆：{decision.content[:80]}[/green]")
            self._emit(f"下个会话装配时生效（{path}）。")
            return
        if raw:
            self._emit("用法: /memory repo [add <关于本仓库的事实>]")
            return
        text = read_repo_memory(self.repo_root)
        if not text:
            self._emit(f"（本仓库还没有仓库记忆）\n文件: {path}\n"
                       "用 /memory repo add <事实> 或让 agent 调 remember_repo 写入。"
                       "存构建/测试命令、目录约定、踩过的坑——今后每个会话与 dev 子 agent 自动带上。")
            return
        note = f"（超过 {MAX_REPO_MEMORY_CHARS} 字，注入时会截断）" if len(text) > MAX_REPO_MEMORY_CHARS else ""
        self._emit(f"[b]仓库记忆[/b] {path} {note}\n{text}")

    def _cmd_memory(self, arg: str = "") -> None:
        """/memory：查看/管理项目指令与长期记忆。"""
        raw = (arg or "").strip()
        low = raw.lower()
        if low.startswith("add "):
            content = raw[4:].strip()
            if not content:
                self._emit("用法: /memory add <要跨会话记住的事实/偏好/约定>")
                return
            from src.memory.write_policy import MemoryWriteRequest, MemoryWriter
            result = MemoryWriter(self.sessions.store).write(
                MemoryWriteRequest(
                    content=content,
                    source="tui_user",
                    session_id=self.session_id,
                    write_method="slash_command",
                ),
                confirmed=True,
                confirmed_by="explicit_user_command",
            )
            if result.status == "stored":
                self._chrome(f"[green]已加入长期记忆 {result.record_id}：{content[:80]}[/green]")
            elif result.record_id:
                self._chrome(f"[yellow]{result.message}（id={result.record_id}）[/yellow]")
            else:
                self._emit(f"保存记忆失败: {result.message}")
            self._emit(self._memory_status_text())
            return
        if low in ("list", "ls"):
            self._emit(self._memory_list_text())
            return
        if low in ("proposals", "proposal", "pending", "quarantine"):
            self._emit(self._memory_proposals_text())
            return
        if low == "repo" or low.startswith("repo "):
            self._cmd_memory_repo(raw[4:].strip())
            return
        if low.startswith(("approve ", "reject ")):
            action, proposal_id = raw.split(maxsplit=1)
            from src.memory.write_policy import MemoryWriter
            result = MemoryWriter(self.sessions.store).review(
                proposal_id.strip(),
                action.lower(),
                confirmed=True,
                reviewer="tui_user",
                session_id=self.session_id,
            )
            if result.get("ok"):
                if result["status"] == "approved":
                    self._chrome(
                        f"[green]已批准提案 {proposal_id} → 长期记忆 {result['memory_id']}[/green]"
                    )
                else:
                    self._chrome(f"[green]已拒绝记忆提案 {proposal_id}[/green]")
            else:
                self._emit(f"审阅记忆提案失败: {result.get('error', '未知错误')}")
            self._emit(self._memory_proposals_text())
            return
        if low.startswith(("delete ", "del ", "rm ")):
            parts = raw.split(maxsplit=1)
            memory_id = parts[1].strip() if len(parts) > 1 else ""
            if not memory_id:
                self._emit("用法: /memory delete <id>")
                return
            ok = self.sessions.store.delete_memory("__longterm__", memory_id)
            if ok:
                self._chrome(f"[green]已删除长期记忆 {memory_id}[/green]")
            else:
                self._emit(f"未找到长期记忆 id={memory_id}")
            self._emit(self._memory_list_text())
            return
        if low in ("auto on", "auto true", "auto 1"):
            self._auto_memory = True
            self._save_setting("auto_memory", True)
            self._chrome("[green]自动记忆候选提示已开启[/green]")
            self._emit(self._memory_status_text())
            return
        if low in ("auto off", "auto false", "auto 0"):
            self._auto_memory = False
            self._save_setting("auto_memory", False)
            self._chrome("[dim]自动记忆候选提示已关闭[/dim]")
            self._emit(self._memory_status_text())
            return
        if low in ("init", "init project"):
            self._init_memory_file(local=False)
            self._emit(self._memory_status_text())
            return
        if low in ("init local", "local"):
            self._init_memory_file(local=True)
            self._emit(self._memory_status_text())
            return
        if raw:
            self._emit(
                "用法: /memory [list|add <文本>|delete <id>|proposals|approve <id>|reject <id>|"
                "repo|repo add <仓库事实>|auto on|auto off|init|init local]"
            )
            return
        self._emit(self._memory_status_text())

    def _cmd_tasks(self, arg: str = "") -> None:
        """/tasks：列出 dev_auto 持久化计划；show 看详情；resume 续跑。"""
        raw = (arg or "").strip()
        from src.agents.dev_plan import format_plan_detail, format_plan_list, list_plans, load_plan
        if not raw or raw.lower() in {"list", "ls"}:
            self._emit(format_plan_list(list_plans(self.repo_root)))
            return
        parts = raw.split(maxsplit=1)
        action = parts[0].lower()
        if action in {"show", "detail", "details", "resume", "continue"}:
            if len(parts) < 2 or not parts[1].strip():
                self._emit("用法: /tasks show <plan_id> 或 /tasks resume <plan_id>")
                return
            pid = parts[1].strip()
        else:
            pid = raw
        plan = load_plan(self.repo_root, pid)
        if plan is None:
            self._emit(f"找不到 dev 计划 {pid}。用 /tasks 查看最近计划。")
            return
        if action in {"resume", "continue"}:
            prompt = (
                f"续跑 dev 计划 {plan.plan_id}？\n"
                f"任务: {plan.task[:120]}\n"
                f"分支: {plan.branch} · 状态: {plan.status}\n"
                "这会切到 build，并让主 agent 调用 dev_resume。"
            )

            def _done(ok: bool | None) -> None:
                if not ok:
                    self._emit("已取消续跑计划。")
                    return
                if self.mode != "build":
                    self.mode = "build"
                    self._sync_subtitle()
                    self._chrome("[green]→ 已切到 build 模式[/green]")
                    self._record_mode_change()
                self._continue_text_route(
                    f"请续跑 dev 计划 plan_id={plan.plan_id}，调用 dev_resume 工具继续未完成任务。")

            self._begin_inline_confirm(prompt, scope="escalate", callback=_done)
            return
        self._emit(format_plan_detail(plan))

    def _cmd_audit(self, arg: str) -> None:
        """/audit 看最近的工具调用审计（.vortocode/audit.log）。"""
        p = Path(self.repo_root) / ".vortocode" / "audit.log"
        active = self._active_worker_lines()
        if not p.is_file():
            if active:
                self._emit("\n".join(active) + "\n\n(暂无审计记录；主 agent 调用工具后才有)")
            else:
                self._emit("(暂无审计记录；主 agent 调用工具后才有)")
            return
        lines = p.read_text(encoding="utf-8").splitlines()
        text = f"工具调用审计（共 {len(lines)} 条，显示最近 15）:\n" + "\n".join(lines[-15:])
        if active:
            text = "\n".join(active) + "\n\n" + text
        self._emit(text)

    def _cmd_artifacts(self) -> None:
        """/artifacts 列出已发布的制品（标题/版本/类型/链接）。需起 Web 服务器才能打开。"""
        from src.web.artifacts import ArtifactStore, artifact_url
        items = ArtifactStore(self.repo_root).list()
        if not items:
            self._emit("(还没有制品。build 模式下让主 agent publish_artifact，"
                       "或说\"把这个做成可分享的页面\")")
            return
        lines = [f"已发布制品（共 {len(items)}；浏览器开 /artifacts 是画廊，需先起 Web 服务器）:"]
        for m in items:
            lines.append(f"  {m['title']} [v{m['version']} · {m.get('kind', 'html')}] "
                         f"— {artifact_url(None, m['id'])}")
        lines.append("提示：对话里 @artifact:<id> 可把某制品当前内容带给主 agent 迭代。")
        self._emit("\n".join(lines))

    def _cmd_diff(self, arg: str = "") -> None:
        """/diff：把工作区改动着色渲染出来；支持 stat/hunks/cached/路径过滤。"""
        import shlex
        import subprocess
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            self._emit(f"用法: /diff [stat|hunks|cached|staged] [路径...]（参数解析失败: {e}）")
            return
        cached = False
        stat = False
        hunks = False
        paths: list[str] = []
        for tok in tokens:
            low = tok.lower()
            if low in {"cached", "staged", "--cached", "--staged"}:
                cached = True
            elif low in {"stat", "--stat"}:
                stat = True
            elif low in {"hunk", "hunks", "--hunks"}:
                hunks = True
            elif tok.startswith("-"):
                self._emit("用法: /diff [stat|hunks|cached|staged] [路径...]（不透传其它 git 参数）")
                return
            else:
                paths.append(tok)
        if stat and hunks:
            self._emit("用法: /diff stat 或 /diff hunks，二者不要混用")
            return
        if hunks:
            from src.agents.git_workflow import diff_hunks_for_review, format_diff_hunks
            self._emit(format_diff_hunks(diff_hunks_for_review(self.repo_root, cached=cached, paths=paths)))
            return
        cmd = ["git", "diff"]
        if cached:
            cmd.append("--cached")
        if stat:
            cmd.append("--stat")
        if paths:
            cmd.append("--")
            cmd.extend(paths)
        try:
            r = subprocess.run(cmd, cwd=self.repo_root,
                               capture_output=True, text=True, timeout=15)
        except Exception as e:  # noqa: BLE001
            self._emit(f"git diff 失败: {e}（不是 git 仓库？）")
            return
        if r.returncode != 0:
            self._emit((r.stderr or r.stdout or "git diff 失败").strip())
            return
        diff = r.stdout or ""
        if not diff.strip():
            label = " ".join(cmd)
            self._emit(f"({label} 为空)")
            return
        label = " ".join(cmd)
        self._chrome(f"[dim]工作区改动（{label}）:[/dim]")
        if stat:
            self._emit(diff.rstrip())
            return
        self._render_diff_text(diff, max_lines=400)

    def _cmd_changes(self, arg: str = "") -> None:
        """/changes：提交前变更审查摘要；支持 cached/staged 和路径过滤。"""
        import shlex
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            self._emit(f"用法: /changes [cached|staged] [路径...]（参数解析失败: {e}）")
            return
        cached = False
        paths: list[str] = []
        for tok in tokens:
            low = tok.lower()
            if low in {"cached", "staged", "--cached", "--staged"}:
                cached = True
            elif tok.startswith("-"):
                self._emit("用法: /changes [cached|staged] [路径...]（不透传其它 git 参数）")
                return
            else:
                paths.append(tok)
        from src.agents.git_workflow import change_review, format_change_review
        self._emit(format_change_review(change_review(self.repo_root, cached=cached, paths=paths)))

    def _cmd_rewind(self, arg: str = "") -> None:
        """/rewind：预览/撤销主 agent 的工具写入（按回合分组，LIFO）。

        裸命令只**预览**（安全默认），显式给数字才动文件——人敲下的数字即意图，不再弹确认。
        只覆盖 edit_file/write_file/rename_symbol 的写入；被手改过的文件自动跳过（不覆盖手改）。
        """
        from src.memory.rewind import group_turns, rewind_turns
        if not (self._persist_on and self.session_id):
            self._chrome("[yellow]会话持久化未开启，没有可撤销的编辑记录[/yellow]")
            return
        a = (arg or "").strip()
        if not a or a == "list":
            groups = group_turns(self.sessions.store, self.session_id)
            if not groups:
                self._emit("本会话没有可撤销的 agent 编辑"
                           "（只记录 edit_file/write_file/rename_symbol 的写入）。")
                return
            lines = ["**可撤销的编辑回合**（新 → 旧）："]
            for i, g in enumerate(groups[:10], 1):
                at = g["at"][11:19] if len(g["at"]) >= 19 else g["at"]
                files = ", ".join(g["files"][:5]) + ("…" if len(g["files"]) > 5 else "")
                lines.append(f"{i}. {at} · {len(g['edits'])} 处改动：{files}")
            if len(groups) > 10:
                lines.append(f"…还有 {len(groups) - 10} 个更早的回合")
            lines.append("")
            lines.append("`/rewind 1` 撤销最近 1 个回合（`/rewind 2` 撤两个，以此类推）；"
                         "被你手改过的文件会自动跳过。")
            self._emit("\n".join(lines))
            return
        try:
            num = int(a)
        except ValueError:
            self._chrome("[red]用法: /rewind（预览）或 /rewind <n>（撤销最近 n 个回合）[/red]")
            return
        res = rewind_turns(self.sessions.store, self.session_id, self.repo_root, n=num)
        if not res["turns"]:
            self._emit("本会话没有可撤销的 agent 编辑。")
            return
        if res["reverted"]:
            self._chrome(f"[green]↩ 已撤销 {len(res['turns'])} 个回合的写入，"
                         f"还原 {len(res['reverted'])} 处：{', '.join(res['reverted'])}"
                         "（/diff 或 git diff 复核）[/green]")
        for rel, why in res["skipped"]:
            self._chrome(f"[yellow]  ⤷ 跳过 {rel}：{why}[/yellow]")
        if not res["reverted"] and res["skipped"]:
            self._chrome("[yellow]没有文件被还原（全部跳过，见上）[/yellow]")

    @work(exclusive=False, group="checkpoint")
    async def _cmd_checkpoint(self, arg: str = "") -> None:
        """/checkpoint [list] | restore <n>：回合级工作区快照（影子 git，含 shell 副作用）。

        restore 是**整树覆盖**（快照之后的一切改动都会没，包括你的手改）——所以先列出会被覆盖的
        文件、再走确认门，绝不像 /rewind 那样静默跳过手改（那是精细路径，这里是强力路径）。
        跑在 worker 里：确认门必须在 worker 上下文（见 _standing_grant_holds 的任务边界说明）。
        """
        from src.memory.checkpoint import changed_since, checkpoints_enabled, list_snapshots, restore
        if not checkpoints_enabled():
            self._chrome("[yellow]工作区快照已关闭（VORTOCODE_CHECKPOINT=0）[/yellow]")
            return
        parts = (arg or "").split()
        snaps = list_snapshots(self.repo_root)
        if not snaps:
            self._emit("还没有工作区快照（每个回合开始前自动打一个；本会话尚未跑过回合）。")
            return
        if not parts or parts[0] == "list":
            lines = ["**工作区快照**（新 → 旧；每回合开始前自动打）："]
            for i, s in enumerate(snaps[:10], 1):
                at = s["at"][11:19] if len(s["at"]) >= 19 else s["at"]
                lines.append(f"{i}. {at} · {s['short']} · {s['label'][:50]}")
            lines.append("")
            lines.append("`/checkpoint restore 1` 把工作区**整树还原**到第 1 个快照之时"
                         "（能撤 run_command 等 shell 改动；会覆盖之后的一切改动，含你的手改——"
                         "会先列清单让你确认）。精细撤销 agent 的逐次编辑用 `/rewind`。")
            self._emit("\n".join(lines))
            return
        if parts[0] != "restore" or len(parts) < 2:
            self._chrome("[red]用法: /checkpoint [list] 或 /checkpoint restore <n>[/red]")
            return
        try:
            idx = int(parts[1])
        except ValueError:
            self._chrome("[red]/checkpoint restore 需要快照编号（先 /checkpoint 看列表）[/red]")
            return
        if not 1 <= idx <= len(snaps):
            self._chrome(f"[red]没有第 {idx} 个快照（共 {len(snaps)} 个）[/red]")
            return
        snap = snaps[idx - 1]
        files = changed_since(self.repo_root, snap["sha"])
        if not files:
            self._emit(f"工作区与该快照（{snap['short']}）一致，无需还原。")
            return
        shown = "\n".join(f"  · {f}" for f in files[:20])
        more = f"\n  …还有 {len(files) - 20} 个" if len(files) > 20 else ""
        self._emit(f"还原到快照 {snap['short']}（{snap['label'][:40]}）将**覆盖**以下 "
                   f"{len(files)} 个文件的当前内容：\n{shown}{more}")
        ok = await self._confirm_always(
            f"整树还原工作区到快照 {snap['short']}？\n"
            f"  {len(files)} 个文件会被覆盖成快照时的内容；此后的一切改动（含你的手改）都会丢失。\n"
            f"  精细撤销 agent 编辑请改用 /rewind。",
            scope="restore")     # 破坏性、影响面远超"改一个文件" → 永不可「始终允许」
        if not ok:
            self._chrome("[yellow]已取消还原[/yellow]")
            return
        res = restore(self.repo_root, snap["sha"])
        if not res["ok"]:
            self._chrome(f"[red]还原失败：{res['error']}[/red]")
            return
        self._chrome(f"[green]↩ 已整树还原到快照 {snap['short']}，"
                     f"{len(res['restored'])} 个文件复位（/diff 或 git diff 复核）[/green]")

    def _cmd_review(self, arg: str = "") -> None:
        """/review [--fix] [hunk H1] [cached] [路径...]：LLM diff review，只报 P0/P1。"""
        import shlex
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            self._emit(f"用法: /review [--fix] [hunk H1] [cached|staged] [路径...]（参数解析失败: {e}）")
            return
        cached = False
        fix = False
        hunk_id = ""
        paths: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            low = tok.lower()
            if low in {"cached", "staged", "--cached", "--staged"}:
                cached = True
            elif low in {"--fix", "fix"}:
                fix = True
            elif low in {"hunk", "--hunk"}:
                i += 1
                if i >= len(tokens) or tokens[i].startswith("-"):
                    self._emit("用法: /review [--fix] [hunk H1] [cached|staged] [路径...]")
                    return
                hunk_id = tokens[i].strip().upper()
            elif tok.startswith("-"):
                self._emit("用法: /review [--fix] [hunk H1] [cached|staged] [路径...]（不透传其它参数）")
                return
            else:
                paths.append(tok)
            i += 1

        async def _run():
            try:
                result = await self._run_diff_review(cached=cached, paths=paths, hunk_id=hunk_id)
            except Exception as e:  # noqa: BLE001
                self._emit(f"review 出错: {e}")
                return
            self._emit(result)
            if fix:
                self._offer_review_fix(result, cached=cached, paths=paths, hunk_id=hunk_id)

        self.run_worker(_run(), exclusive=True, group="review")

    def _cmd_verify(self, arg: str = "") -> None:
        """/verify [selector|--changed] 或 /verify run <命令>。"""
        import shlex
        raw_arg = arg or ""
        try:
            tokens = shlex.split(raw_arg)
        except ValueError as e:
            self._emit(f"用法: /verify [selector|--changed] [cached] 或 /verify run <命令>（参数解析失败: {e}）")
            return
        if tokens and tokens[0].lower() in {"profiles", "profile", "list"}:
            if len(tokens) == 1 or tokens[0].lower() in {"profiles", "list"}:
                from src.agents.verify_profiles import format_verify_profiles, load_verify_profiles
                self._emit(format_verify_profiles(load_verify_profiles(self.repo_root)))
                return
            self._cmd_verify_profile(tokens[1])
            return
        if len(tokens) == 1 and not tokens[0].startswith("-"):
            from src.agents.verify_profiles import resolve_verify_profile
            resolved = resolve_verify_profile(self.repo_root, tokens[0])
            if resolved.get("ok"):
                self._cmd_verify_profile(str(resolved.get("name") or tokens[0]))
                return
        if tokens and tokens[0].lower() in {"run", "runtime", "cmd", "command"}:
            parts = raw_arg.strip().split(maxsplit=1)
            cmd = parts[1].strip() if len(parts) > 1 else ""
            self._cmd_verify_run(cmd)
            return
        changed = False
        cached = False
        selector_parts: list[str] = []
        for tok in tokens:
            low = tok.lower()
            if low in {"--changed", "changed"}:
                changed = True
            elif low in {"cached", "staged", "--cached", "--staged"}:
                cached = True
            elif tok.startswith("-"):
                self._emit("用法: /verify [selector|--changed] [cached] 或 /verify run <命令>（不透传其它测试参数）")
                return
            else:
                selector_parts.append(tok)
        selector = " ".join(selector_parts).strip()
        if changed and selector:
            self._emit("用法: /verify [selector] 或 /verify --changed [cached]，二者不要混用")
            return
        from src.agents.test_detect import detect_test_cmd, is_pytest_cmd
        note = ""
        if changed:
            from src.agents.git_workflow import changed_test_selection
            sel = changed_test_selection(self.repo_root, cached=cached)
            if not sel.get("ok"):
                self._emit(f"改动测试选择失败: {sel.get('error', '')}")
                return
            if not sel.get("changed_paths"):
                self._emit(sel.get("reason") or "没有改动可验证。")
                return
            base_cmd = detect_test_cmd(self.repo_root)
            selectors = list(sel.get("selectors") or [])
            if selectors and is_pytest_cmd(base_cmd):
                import sys
                cmd = [sys.executable, "-m", "pytest", "-q", *selectors]
            else:
                cmd = base_cmd
            note = str(sel.get("reason") or "")
        else:
            cmd = detect_test_cmd(self.repo_root, selector or None)
        cmd_text = " ".join(cmd)

        async def _run():
            detail = f"\n  {note}" if note else ""
            if not await self._confirm_command(
                    "运行仓库测试验证？\n"
                    f"  $ {cmd_text}\n"
                    f"测试可能写入缓存或耗时较久。{detail}",
                    tool_name="run_command",
                    args={"command": cmd_text}):
                self._emit("已取消验证。")
                return
            self._chrome(f"[dim]$ {cmd_text}[/dim]")
            from src.agents.worktree import run_tests
            res = await asyncio.to_thread(run_tests, self.repo_root, cmd)
            ok = bool(res.get("ok"))
            self._audit_event("verify", {
                "ok": ok,
                "cmd": res.get("cmd") or cmd_text,
                "changed": changed,
                "cached": cached,
                "note": note,
            })
            status = "验证通过 ✓" if ok else "验证失败 ✗"
            color = self._tc("text-success", "#7fce9a") if ok else self._tc("text-error", "#f08a8a")
            self._chrome(f"[{color}]{status}[/][dim]（{res.get('cmd') or cmd_text}）[/dim]")
            out = str(res.get("output") or "").strip()
            if out:
                self._emit(f"{status}（{res.get('cmd') or cmd_text}）\n输出尾部:\n{out[-3000:]}")
            else:
                self._emit(f"{status}（{res.get('cmd') or cmd_text}）")

        self.run_worker(_run(), exclusive=True, group="verify")

    def _cmd_verify_profile(self, name: str) -> None:
        from src.agents.verify_profiles import format_verify_profiles, resolve_verify_profile
        resolved = resolve_verify_profile(self.repo_root, name)
        if not resolved.get("ok"):
            self._emit(str(resolved.get("error") or "verify profile 加载失败"))
            profiles = resolved.get("profiles") or {}
            if profiles:
                self._emit(format_verify_profiles({"ok": True, "profiles": profiles}))
            return
        profile = resolved.get("profile") or {}
        desc = str(profile.get("description") or "").strip()
        label = f"profile {resolved.get('name')}" + (f" · {desc}" if desc else "")
        if str(profile.get("serve") or "").strip():
            # serve profile 必须起服务后再探活/浏览器验证，不能只跑派生的展示 cmd。
            self._cmd_verify_runtime_profile(str(resolved.get("name") or ""), profile, label)
        else:
            self._cmd_verify_run(str(profile.get("cmd") or ""), label=label)

    def _cmd_verify_runtime_profile(self, name: str, profile: dict, label: str) -> None:
        """跑一个 serve+check/browser profile：起服务 → 探活/浏览器 → 无论成败都停 serve。

        复用 worktree.run_runtime_check（与自主流水线同源）。危险命令同步 fail-fast（serve/check 任一），
        再起 worker 跑（内部阻塞轮询丢线程），带确认门。"""
        serve = str(profile.get("serve") or "").strip()
        check = str(profile.get("check") or "").strip()
        cmd = str(profile.get("cmd") or "").strip()
        browser = profile.get("browser") if isinstance(profile.get("browser"), dict) else None
        browser_url = str((browser or {}).get("url") or "").strip()
        # Mirror run_runtime_check: with a browser probe, check is the only
        # pre-flight; otherwise cmd falls back to being the readiness probe, so
        # legacy serve+cmd profiles surface (and guard) the command they run.
        probe = check if browser else (check or cmd)
        probe_label = "check" if check else "cmd"
        from src.agents.shell import is_dangerous
        for c in (serve, probe):
            if not c:
                continue
            danger = is_dangerous(c)
            if danger:
                self._emit(f"拒绝执行高危验证命令: {danger}")
                return

        async def _run():
            action = "起服务 + 浏览器验证" if browser else "起服务 + 探活"
            details = f"  serve: {serve}\n"
            if probe:
                details += f"  {probe_label}: {probe}\n"
            if browser:
                details += f"  browser: {browser_url}\n"
            if not await self._confirm_command(
                    f"运行 {label}（{action}）？\n"
                    + details
                    + "会后台起服务、验证后自动停掉；请确认命令不会做外向或破坏性操作。"):
                self._emit("已取消 runtime 验证。")
                return
            target = f"browser: {browser_url}" if browser else f"{probe_label}: {probe}"
            self._chrome(f"[dim]$ serve: {serve} | {target}[/dim]")
            from src.agents.worktree import run_runtime_check
            import time
            res = await asyncio.to_thread(
                run_runtime_check,
                self.repo_root,
                {**profile, "name": name},
                repo_root=self.repo_root,
                run_id=f"manual-{name}-{time.time_ns()}",
            )
            ok = bool(res.get("ok"))
            self._audit_event("verify_runtime", {
                "ok": ok,
                "name": name,
                "serve": serve,
                "cmd": res.get("cmd") or probe,
                "screenshot_path": res.get("screenshot_path") or "",
                "sandbox": res.get("sandbox") or {},
            })
            status = "runtime 验证通过 ✓" if ok else "runtime 验证失败 ✗"
            color = self._tc("text-success", "#7fce9a") if ok else self._tc("text-error", "#f08a8a")
            self._chrome(f"[{color}]{status}[/][dim]（{name}）[/dim]")
            out = str(res.get("output") or "").strip()
            evidence = (f"\n截图: {res.get('screenshot_path')}" if res.get("screenshot_path") else "")
            self._emit(f"{status}（{name}）" + evidence
                       + (f"\n输出尾部:\n{out[-3000:]}" if out else ""))

        self.run_worker(_run(), exclusive=True, group="verify")

    def _cmd_verify_run(self, cmd: str, *, label: str = "runtime 验证命令") -> None:
        """Run a user-supplied runtime/smoke command with existing command gates."""
        # 空命令 / 高危命令同步 fail-fast：既立即给用户反馈，也避免为一条注定被拒的命令
        # 起 worker（无运行中的事件循环时 run_worker 会抛 RuntimeError）。真正执行时协程内还会再查一遍。
        cmd = (cmd or "").strip()
        if not cmd:
            self._emit("用法: /verify run <命令>")
            return
        from src.agents.shell import is_dangerous
        danger = is_dangerous(cmd)
        if danger:
            self._emit(f"拒绝执行高危验证命令: {danger}")
            return
        self.run_worker(self._run_verify_command_now(cmd, label=label), exclusive=True, group="verify")

    def _cmd_preflight(self, arg: str = "") -> None:
        """/preflight [cached]：提交/开 PR 前只读检查。"""
        import shlex
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            self._emit(f"用法: /preflight [cached|staged]（参数解析失败: {e}）")
            return
        cached = False
        for tok in tokens:
            low = tok.lower()
            if low in {"cached", "staged", "--cached", "--staged"}:
                cached = True
            else:
                self._emit("用法: /preflight [cached|staged]")
                return
        from src.agents.git_workflow import format_preflight_report, preflight_report
        self._emit(format_preflight_report(preflight_report(self.repo_root, cached=cached)))

    def _cmd_git(self, arg: str = "") -> None:
        """/git：查看当前分支、改动文件、staged/unstaged diffstat。"""
        if (arg or "").strip():
            self._emit("用法: /git")
            return
        from src.agents.git_workflow import format_status_summary, status_summary
        self._emit(format_status_summary(status_summary(self.repo_root)))

    def _cmd_commit(self, arg: str = "") -> None:
        """/commit <msg>：提交 staged 改动；/commit all <msg> 先 git add -A。"""
        import shlex
        try:
            tokens = shlex.split(arg or "")
        except ValueError as e:
            self._emit(f"用法: /commit <message>|suggest 或 /commit all <message|--suggest>（参数解析失败: {e}）")
            return
        if not tokens:
            self._emit("用法: /commit <message>|suggest 或 /commit all <message|--suggest>")
            return
        stage_all = tokens[0].lower() == "all"
        raw_msg_tokens = tokens[1:] if stage_all else tokens
        suggest = any(t.lower() in {"suggest", "--suggest"} for t in raw_msg_tokens)
        msg_tokens = [t for t in raw_msg_tokens if t.lower() not in {"suggest", "--suggest"}]
        message = " ".join(msg_tokens).strip()
        if suggest:
            from src.agents.git_workflow import suggest_commit_message
            suggested = suggest_commit_message(self.repo_root, stage_all=stage_all)
            if not suggested.get("ok"):
                self._emit(f"生成提交信息失败: {suggested.get('error', '')}")
                return
            message = str(suggested.get("message") or "").strip()
        if not message:
            self._emit("用法: /commit <message>|suggest 或 /commit all <message|--suggest>")
            return

        async def _run():
            from src.agents.git_workflow import commit_changes, has_any_changes, has_staged_changes
            if stage_all:
                if not has_any_changes(self.repo_root):
                    self._emit("没有工作区改动可提交。")
                    return
            elif not has_staged_changes(self.repo_root):
                self._emit("没有 staged 改动可提交。用 /commit all <message> 可先 git add -A。")
                return
            if self.mode != "build":
                prompt = "当前是 plan 模式。切到 build 并执行本地 git commit？"
            else:
                prompt = "执行本地 git commit？"
            detail = f"\n  message: {message}"
            if suggest:
                detail += "\n  message 由当前改动自动生成"
            if stage_all:
                detail += "\n  会先执行: git add -A"
            if not await self._confirm_command(prompt + detail):
                self._emit("已取消 commit。")
                return
            if self.mode != "build":
                self.mode = "build"
                self._sync_subtitle()
                self._chrome("[green]→ 已切到 build 模式[/green]")
                self._record_mode_change()
            result = await asyncio.to_thread(commit_changes, self.repo_root, message, stage_all=stage_all)
            if not result.get("ok"):
                self._emit(f"commit 失败: {result.get('error', '')}")
                return
            self._audit_event("commit", {"sha": result.get("sha"), "stage_all": stage_all, "message": message})
            self._emit(f"已提交 {result.get('sha')}: {message}")
            self._render_statusbar()

        self.run_worker(_run(), exclusive=True, group="git")

    def _cmd_pr(self, arg: str = "") -> None:
        """/pr：预览或创建当前分支 PR。外向操作，创建前必须确认。"""
        if self._try_pr_subcommand(arg):
            return
        opts = self._parse_pr_args(arg)
        if not opts.get("ok"):
            self._emit(opts.get("error", "用法: /pr [preview|draft] [base <ref>] [title]"))
            return
        from src.agents.git_workflow import format_pr_preview, pr_preview
        preview = pr_preview(self.repo_root, base=opts["base"], title=opts["title"])
        if opts["preview"] or not preview.get("ok"):
            self._emit(format_pr_preview(preview))
            return

        async def _run():
            text = format_pr_preview(preview)
            if opts.get("draft"):
                text += "\n\n将创建 draft PR。"
            if not await self._confirm_outward(text + "\n\n确认 push 当前分支并创建 PR？"):
                self._emit("已取消开 PR。")
                return
            from src.agents.vcs import push_and_open_pr
            res = await asyncio.to_thread(
                push_and_open_pr,
                self.repo_root,
                preview["branch"],
                preview["title"],
                preview["body"],
                preview["base"],
                "origin",
                bool(opts.get("draft")),
            )
            self._audit_event("open_pr", {
                "branch": preview.get("branch"),
                "base": preview.get("base"),
                "title": preview.get("title"),
                "draft": bool(opts.get("draft")),
                "ok": bool(res.get("ok")),
                "url": res.get("url", ""),
            })
            if res.get("ok"):
                self._emit(f"已创建 PR: {res.get('url')}")
            elif res.get("pushed"):
                self._emit(f"已 push {preview['branch']}，但开 PR 失败: {res.get('error', '')}")
            else:
                self._emit(f"开 PR 失败: {res.get('error', '')}")

        self.run_worker(_run(), exclusive=True, group="git")

    def _cmd_pr_check(self, arg: str = "") -> None:
        """/pr-check <ref>：读取 PR review 评论 + 失败 CI。只读命令。"""
        ref = (arg or "").strip()
        if not ref:
            self._emit("用法: /pr-check <PR号或分支名>")
            return

        async def _run():
            from src.agents.vcs import pr_feedback
            fb = await asyncio.to_thread(pr_feedback, self.repo_root, ref)
            self._emit(self._format_pr_feedback(fb, ref))

        self.run_worker(_run(), exclusive=True, group="git")

    def _cmd_pr_doctor(self, arg: str = "", *, alias: str = "/pr doctor") -> None:
        """/pr doctor <ref> 或 /fix-ci <ref>：诊断 PR review/CI，确认后切 build 修复。"""
        opts = self._parse_pr_doctor_args(arg, alias)
        if not opts.get("ok"):
            self._emit(str(opts.get("error") or f"用法: {alias} [verify|fix] <ref>"))
            return
        ref = str(opts["ref"])
        action = str(opts["action"])
        if not ref:
            self._emit(f"用法: {alias} <PR号或vorto/*分支名>")
            return

        async def _run():
            from src.agents.pr_doctor import format_pr_doctor_report, pr_doctor_report
            report = await asyncio.to_thread(pr_doctor_report, self.repo_root, ref)
            self._emit(format_pr_doctor_report(report))
            if not report.get("ok"):
                return
            if action == "verify":
                cmd, label = await self._choose_verify_template(report)
                if not cmd:
                    self._emit("未选择 PR Doctor verify 动作，或没有可安全执行的 verify 模板。")
                    return
                result = await self._run_verify_command_now(cmd, label=label or "PR Doctor 最小复现")
                self._emit(self._format_pr_verify_followup(report, ref, result))
                return
            if not report.get("can_fix"):
                return
            pr = report.get("pr") or ref
            branch = report.get("branch") or "未知分支"
            if not await self._confirm_always(     # 模式切换 + 调 pr_fix ≠ 写一个文件，不得铸权
                    f"按 PR #{pr} 的诊断切到 build 并修复 {branch}？\n"
                    "将调用 pr_fix 处理 review/CI 反馈；push 更新 PR 前仍会二次确认。",
                    scope="escalate"):
                self._emit(f"已保留在 {self.mode} 模式；可稍后运行 /pr-fix {ref}。")
                return
            self._set_mode("build")
            self._continue_text_route(
                f"请根据 PR Doctor 诊断修复 PR {ref} 的 review/CI 反馈；调用 pr_fix 工具，参数 pr={ref}。"
            )

        self.run_worker(_run(), exclusive=True, group="git")

    def _cmd_pr_fix(self, arg: str = "") -> None:
        """/pr-fix <ref>：确认后切 build，并让主 agent 调 pr_fix 工具。"""
        ref = (arg or "").strip()
        if not ref:
            self._emit("用法: /pr-fix <PR号或vorto/*分支名>")
            return

        def _after_confirm(ok: bool | None) -> None:
            if not ok:
                self._emit("已取消 PR 反馈修复。")
                return
            self._set_mode("build")
            self._continue_text_route(
                f"请读取并修复 PR {ref} 的 review/CI 反馈；调用 pr_fix 工具，参数 pr={ref}。"
            )

        self._begin_inline_confirm(
            f"按 PR {ref} 的 review/CI 反馈自动修复？会切到 build 模式，并由 pr_fix 在 vorto/* 分支上改动、自测、再确认 push。",
            scope="escalate",
            callback=_after_confirm,
        )

    def _cmd_commands(self, arg: str = "") -> None:
        """/commands：列出/初始化/预览 .vortocode/commands 下的自定义命令。"""
        raw = (arg or "").strip()
        low = raw.lower()
        if low == "reload":
            self._user_cmds = None
        elif low.startswith("init "):
            name = raw.split(maxsplit=1)[1].strip()
            from src.agents.user_commands import default_command_template, is_valid_command_name
            if not is_valid_command_name(name):
                self._emit("用法: /commands init <name>（name 只能包含字母、数字、下划线和连字符）")
                return
            path = Path(self.repo_root) / ".vortocode" / "commands" / f"{name}.md"
            if path.exists():
                self._emit(f"命令 /{name} 已存在：{path}")
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(default_command_template(name), encoding="utf-8")
            self._user_cmds = None
            self._chrome(f"[green]已创建自定义命令模板：{path}[/green]")
            self._emit(f"可用 `/commands preview {name} 示例目标` 预览展开结果，或直接 `/{name} ...` 执行。")
            return
        elif low.startswith("preview "):
            parts = raw.split(maxsplit=2)
            if len(parts) < 2:
                self._emit("用法: /commands preview <name> [args...]")
                return
            name = parts[1].lstrip("/")
            args = parts[2] if len(parts) > 2 else ""
            from src.agents.user_commands import expand_command
            uc = self._user_commands().get(name)
            if uc is None:
                self._emit(f"没有自定义命令 /{name}。先用 /commands init {name} 创建，或 /commands reload 重扫。")
                return
            expanded = expand_command(uc.template, args)
            meta = self._user_command_label(uc)
            self._emit(f"预览 /{name}" + (f" · {meta}" if meta else "") + f"\n\n```text\n{expanded}\n```")
            return
        elif raw and low not in {"list", "ls"}:
            self._emit("用法: /commands [list|reload|init <name>|preview <name> [args...]]")
            return
        cmds = self._user_commands()
        if not cmds:
            self._emit("没有自定义命令。在 `.vortocode/commands/<名>.md` 写提示模板即可用 `/<名>` 调起"
                       "（支持 $ARGUMENTS / $1 占位符；frontmatter 可写 description/mode/model/args）。\n"
                       "也可用 `/commands init <name>` 生成模板。")
            return
        lines = ["[b]自定义命令[/b]（.vortocode/commands）:"]
        for n, uc in sorted(cmds.items()):
            lines.append(f"  [b]/{n}[/b] — {self._user_command_label(uc)}")
        lines.append("[dim]/commands init <name> 生成模板；/commands preview <name> [args] 只预览展开；/commands reload 重扫。[/dim]")
        self._chrome("\n".join(lines))

    def _cmd_hooks(self, arg: str = "") -> None:
        """/hooks：列出、初始化、dry-run 测试 .vortocode/hooks.yaml 生命周期钩子。"""
        cfg = Path(self.repo_root) / ".vortocode" / "hooks.yaml"
        raw = (arg or "").strip()
        low = raw.lower()
        if low in {"trust", "untrust"}:
            from src.hooks.trust import set_project_trusted
            trusted = low == "trust"
            if not cfg.is_file():
                self._emit("没有 hooks.yaml。先用 /hooks init 生成模板，再审查并信任。")
                return
            if not set_project_trusted(self.repo_root, trusted):
                self._emit("Hook 信任状态写入失败；项目必须是现有 Git 工作区。")
                return
            self._agent = None                 # 下一回合按新信任状态重建，当前主循环不热换控制面
            self._emit("已信任并启用项目 Hook。" if trusted else "已撤销项目 Hook 信任；下一回合不再执行。")
            return
        if low == "init":
            if cfg.exists():
                self._emit(f"{cfg} 已存在；不会覆盖。用 /hooks 查看，或手动编辑。")
                return
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(
                "# VortoCode tool lifecycle hooks. /hooks test post_tool_use write_file 可 dry-run matcher。\n"
                "hooks:\n"
                "  - name: sample-tool-hook\n"
                "    type: command\n"
                "    event_types: [post_tool_use]\n"
                "    matcher: write_file|edit_file\n"
                "    shell: true\n"
                "    timeout: 10\n"
                "    command: python -c \"import json,sys; e=json.load(sys.stdin); print('hook saw '+e['data'].get('tool',''))\"\n",
                encoding="utf-8",
            )
            self._chrome(f"[green]已创建 hooks 模板：{cfg}[/green]")
            self._emit("用 `/hooks test post_tool_use write_file` 试跑 matcher；确认后可把 command 改成格式化/通知脚本。")
            return
        if low == "test" or low.startswith("test "):
            rest = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
            self._cmd_hooks_test(rest.strip())
            return
        if raw and low not in {"list", "ls"}:
            self._emit("用法: /hooks [list|init|trust|untrust|test [event] [tool]]")
            return
        hs = self._load_hook_system()
        if hs is None:
            self._emit(
                "没有 hooks。在 `.vortocode/hooks.yaml` 配置工具生命周期钩子，例如 edit_file 后自动格式化：\n"
                "```yaml\nhooks:\n  - name: fmt\n    type: command\n    event_types: [post_tool_use]\n"
                "    matcher: edit_file|write_file   # 只在这些工具后触发\n    shell: true\n    command: ruff format .\n```")
            return
        hooks = [h for h in hs.list_hooks() if h.__class__.__name__ != "AuditLogHook"]
        if not hooks:
            self._emit(f"{cfg} 里没有可用钩子（或都解析失败）。")
            return
        from src.hooks.trust import is_project_trusted
        trusted = is_project_trusted(self.repo_root)
        lines = [f"[b]工具生命周期钩子[/b]（{cfg}） · "
                 + ("[green]已信任[/green]" if trusted else "[yellow]未信任，仅预览[/yellow]")]
        for h in hooks:
            evs = "/".join(e.value for e in h.event_types)
            m = f" · 仅工具 [b]{h.matcher}[/b]" if getattr(h, "matcher", None) else ""
            state = "" if h.enabled else " [dim](禁用)[/dim]"
            lines.append(f"  [b]{h.name}[/b] [{self._tc('text-primary', '#8ab4f8')}]{evs}[/]{m}{state}")
        lines.append("[dim]/hooks trust 审查后启用；/hooks untrust 撤销；/hooks test [event] [tool] 只 dry-run matcher。[/dim]")
        self._chrome("\n".join(lines))

    def _cmd_hooks_test(self, arg: str = "") -> None:
        """只测试 hook event/matcher 是否命中，不执行 hook 命令。"""
        from src.hooks import HookEventType
        hs = self._load_hook_system()
        if hs is None:
            self._emit("没有 hooks.yaml。先用 /hooks init 生成模板，或手动创建 .vortocode/hooks.yaml。")
            return
        parts = (arg or "").split()
        event_name = "post_tool_use"
        tool = "write_file"
        valid_events = {e.value for e in HookEventType}
        if len(parts) == 1:
            if parts[0] in valid_events:
                event_name = parts[0]
            else:
                tool = parts[0]
        elif len(parts) >= 2:
            event_name, tool = parts[0], parts[1]
        if event_name not in valid_events:
            self._emit(f"未知 hook event: {event_name}。可用示例: post_tool_use/pre_tool_use/tool_error。")
            return
        event_type = HookEventType(event_name)
        hooks = [h for h in hs.list_hooks() if h.__class__.__name__ != "AuditLogHook"]
        matched, skipped = [], []
        for h in hooks:
            event_ok = event_type in h.event_types
            tool_ok = h.matches_tool(tool)
            if h.enabled and event_ok and tool_ok:
                matched.append(h.name)
            else:
                why = []
                if not h.enabled:
                    why.append("disabled")
                if not event_ok:
                    why.append("event")
                if not tool_ok:
                    why.append("matcher")
                skipped.append(f"{h.name}({','.join(why)})")
        lines = [f"hooks dry-run: event={event_name}, tool={tool}"]
        lines.append("命中: " + (", ".join(matched) if matched else "（无）"))
        if skipped:
            lines.append("跳过: " + ", ".join(skipped))
        self._emit("\n".join(lines))

    def _cmd_speak(self, arg: str = "") -> None:
        """/speak：朗读 agent 回复的开关（on/off 可显式指定，否则切换）。开了之后每条回复合成语音播放。"""
        a = (arg or "").strip().lower()
        if a in ("on", "开"):
            self._speak_replies = True
        elif a in ("off", "关"):
            self._speak_replies = False
        else:
            self._speak_replies = not self._speak_replies
        if self._speak_replies:
            self._chrome("[green]🔊 朗读已开[/green][dim]（每条回复用 mimo-v2.5-tts 合成播放；再 /speak 关闭）[/dim]")
        else:
            self._chrome("[dim]🔊 朗读已关[/dim]")

    @work(exclusive=True, group="action")
    async def _cmd_mcp(self, arg: str) -> None:
        """/mcp 连接 config/mcp.yaml 的 MCP 服务器；/mcp list 列出；/mcp off 断开。"""
        arg = arg.strip()
        if arg in ("off", "disconnect"):
            if self._mcp is not None:
                try:
                    await self._mcp.shutdown()
                except Exception:  # noqa: BLE001
                    pass
            self._mcp, self._mcp_tools, self.agent = None, [], None
            self._chrome("[green]已断开 MCP[/green]")
            return
        if arg == "list":
            if not self._mcp_tools:
                self._emit("(未连接 MCP；/mcp 连接 config/mcp.yaml 里 enabled 的服务器)")
            else:
                self._emit("已接入的 MCP 工具:\n"
                           + "\n".join(f"  {t.name} — {t.description}" for t in self._mcp_tools))
            return
        # 连接
        self._chrome("[cyan]连接 MCP 服务器（config/mcp.yaml）…[/cyan]")
        try:
            from src.agents.mcp_tools import connect_mcp

            async def _mcp_confirm(message: str) -> bool:
                # MCP 权限规则里 action=ask 的工具 → 真的问人。scope="mcp"：外部不可信来源的
                # 工具调用，**永不可「始终允许」**（不在铸权白名单里，见 _confirm_always）。
                return await self._confirm_always(message, scope="mcp")

            mgr, mcp_tools = await connect_mcp(
                self.repo_root, capability_profile=self._capability_profile,
                confirm=_mcp_confirm
            )
            self._mcp = mgr
            self._mcp_tools = mcp_tools
            self.agent = None            # 让主 agent 下次重建、拿到 MCP 工具
            servers = list(getattr(mgr, "mcp_clients", {}).keys()) if mgr is not None else []
            self._emit(f"已接入 {len(self._mcp_tools)} 个 MCP 工具，来自服务器: {', '.join(servers) or '（无）'}")
            if not self._mcp_tools:
                self._emit("（external 仅连接 credentialed: false 且无 headers 的 HTTP MCP；stdio 默认拒绝）")
        except Exception as e:  # noqa: BLE001
            self._emit(f"MCP 连接失败: {e}")

    # ---------------------------------------------------------------- 已创建的 agent
    def _cmd_agents(self) -> None:
        from src.tui.app import _AGENTS_DB   # 延迟导入：app 模块级导入本模块，模块级导回去会成真环
        from src.agents.manager import AgentManager
        mgr = AgentManager(persist_path=_AGENTS_DB(self.repo_root))
        agents = mgr.list_agents()
        if not agents:
            self._emit("(无已创建的 agent；可在网页或 API 创建)")
            return
        lines = ["已创建的 agent（/runagent <id> <任务> 运行）:"]
        for a in agents:
            off = "" if a.config.is_active else " (停用)"
            lines.append(f"  {a.config.id}  {a.config.name} [{a.config.role}]{off}")
        self._emit("\n".join(lines))

    # ---------------------------------------------------------------- 会话
    def _cmd_sessions(self, arg: str = "") -> None:
        """/sessions：会话选择器；search/rename/delete 管理历史会话。"""
        from src.tui.app import ListPicker   # 延迟导入：app 模块级导入本模块，模块级导回去会成真环
        raw = (arg or "").strip()
        low = raw.lower()
        if low.startswith(("search ", "find ")):
            parts = raw.split(maxsplit=1)
            query = parts[1].strip() if len(parts) > 1 else ""
            if not query:
                self._emit("用法: /sessions search <关键词>")
                return
            rows = [r for r in self.sessions.store.search_sessions(query, limit=30)
                    if r.get("id") != self.session_id]
            if not rows:
                self._emit(f"没有匹配的其他历史会话: {query}")
                return
            items = []
            for r in rows:
                label = self._session_picker_label(r)
                snippet = self._summary_text(r.get("match_snippet") or "", 120)
                hit = f" · 命中消息 {r.get('match_count', 0)}" if r.get("match_count") else ""
                snip = f" · {snippet}" if snippet else ""
                mark = "  ← 当前" if r["id"] == self.session_id else ""
                items.append((r["id"], f"{r['id']}  {label}{hit}{snip}{mark}"))

            def _done(sid) -> None:
                if sid and sid != self.session_id:
                    self._cmd_resume(sid)

            self.push_screen(ListPicker(f"搜索会话: {query} · 回车恢复", items, initial=self.session_id), _done)
            return
        if low.startswith("rename "):
            parts = raw.split(maxsplit=2)
            if len(parts) < 3 or not parts[1].strip() or not parts[2].strip():
                self._emit("用法: /sessions rename <id> <name>")
                return
            sid, name = parts[1].strip(), parts[2].strip()
            if not self.sessions.store.get_session(sid):
                self._emit(f"没有会话 {sid}")
                return
            self.sessions.store.update_session(sid, name=name)
            self._chrome(f"[green]已重命名会话 {sid} → {name}[/green]")
            self._emit(self._session_manage_list_text())
            return
        if low.startswith(("delete ", "del ", "rm ")):
            parts = raw.split(maxsplit=1)
            sid = parts[1].strip() if len(parts) > 1 else ""
            if not sid:
                self._emit("用法: /sessions delete <id>")
                return
            row = self.sessions.store.get_session(sid)
            if not row:
                self._emit(f"没有会话 {sid}")
                return

            def _done(ok: bool | None) -> None:
                if not ok:
                    self._emit("已取消删除会话。")
                    return
                self.sessions.store.delete_session(sid)
                if sid == self.session_id:
                    self.session_id = self.sessions.start_session()
                    self.agent = None
                    self._capability_profile = "local"
                    self._capabilities = None
                    self.transcript.clear()
                    try:
                        self.query_one("#log", RichLog).clear()
                    except Exception:  # noqa: BLE001
                        pass
                    self._chrome(f"[yellow]已删除当前会话，并新建会话 {self.session_id}[/yellow]")
                else:
                    self._chrome(f"[green]已删除会话 {sid}[/green]")
                self._emit(self._session_manage_list_text())

            self._begin_inline_confirm(
                f"删除会话 {sid}（{row.get('name') or sid}）及其消息、任务、编辑和记忆？\n"
                "此操作不可撤销。",
                scope="sessions",
                callback=_done,
            )
            return
        if raw and low not in {"list", "ls"}:
            self._emit("用法: /sessions [list|search <关键词>|rename <id> <name>|delete <id>]")
            return
        rows = self.sessions.list_recent_sessions(20)
        if not rows:
            self._emit("(暂无历史会话)")
            return
        items = []
        for r in rows:
            summ = self.sessions.store.get_session_summary(r["id"])
            mark = "  ← 当前" if r["id"] == self.session_id else ""
            label = self._session_picker_label(r)
            items.append((r["id"],
                          f"{r['id']}  {label} · 消息 {summ.get('messages', 0)} · "
                          f"{(r.get('updated_at') or '')[:19]}{mark}"))

        def _done(sid) -> None:
            if sid and sid != self.session_id:
                self._cmd_resume(sid)

        self.push_screen(ListPicker("历史会话 · 回车恢复", items, initial=self.session_id), _done)

    def _cmd_resume(self, sid: str) -> None:
        if not self.sessions.resume_session(sid):
            self._chrome(f"[red]没有会话 {sid}[/red]")
            return
        self.session_id = sid
        msgs = self.sessions.get_messages(500)
        self.query_one("#log", RichLog).clear()
        self.transcript.clear()
        self._persist_on = False            # 回放期间不重复落盘
        self.agent = None                   # 丢掉上个会话的 agent 上下文
        self._capabilities = None
        self._session_last_user = ""
        self._chrome(f"[green]已恢复会话 {sid}（{len(msgs)} 条）[/green]")
        last_snapshot = None
        for m in msgs:
            try:
                md = json.loads(m.get("metadata") or "{}")
            except Exception:  # noqa: BLE001
                md = {}
            if md.get("agent_history"):     # agent 上下文快照：不显示，只留最后一份用于重建
                last_snapshot = m["content"]
                continue
            if md.get("markup"):
                self._chrome(m["content"])
            else:
                self._emit(m["content"])
        self._persist_on = True
        session_summary = ""
        try:
            row = self.sessions.store.get_session(sid) or {}
            session_md = self._session_metadata(row)
            session_summary = str(session_md.get("summary") or "")
            profile = str(session_md.get("capability_profile") or "external").strip().lower()
            self._capability_profile = profile if profile in {"local", "external"} else "external"
        except Exception:  # noqa: BLE001
            row = {}
            session_summary = ""
        self._emit(self._resume_context_text(row))
        self._restore_agent_history(last_snapshot, fallback_summary=session_summary)

    def _cmd_new(self, profile: str = "") -> None:
        from src.llm.client import reset_usage
        requested = (profile or "local").strip().lower()
        if requested not in {"local", "external"}:
            self._emit("用法: /new [local|external]（local=开发/凭据；external=网页/MCP、无凭据）")
            return
        self.session_id = self.sessions.start_session()
        self.agent = None                   # 新会话 = 全新 agent 上下文
        self._capability_profile = requested
        self._capabilities = None
        self._load_always_allow()           # "始终允许"是**项目级**常驻授权：/new 不清，按设置重载
        #                                     （要撤销走 /permissions reset）
        reset_usage()                       # 用量也清零
        # 上下文压力告警状态跟着新会话归零：否则旧会话提示过之后 _ctx_alerted 一直是 True，
        # 而复位只发生在"pct 回落到警戒线以下"——新会话若**第一条输入就冲到 95%**，中间没有
        # 回落过，于是永远等不到复位、该提示的时候反而不提示（codex 审出的边界问题）。
        self._ctx_alerted = False
        self._ctx_pct = 0
        self._render_plan([])               # 收起上个会话的计划面板
        from src.agents.shell import stop_all_background
        n_bg = stop_all_background()        # 收掉上个会话遗留的后台命令，别泄漏 dev server 进程
        if n_bg:
            self._chrome(f"[dim]■ 已停止 {n_bg} 个后台命令[/dim]")
        self.query_one("#log", RichLog).clear()
        self.transcript.clear()
        self._chrome(f"[green]已新建 {requested} 会话 {self.session_id}[/green]")
        self._sync_subtitle()

    def _cmd_usage(self, arg: str) -> None:
        """/usage 看本会话用量（估算）；/usage reset 清零。"""
        from src.llm.client import get_usage, reset_usage
        if arg.strip() == "reset":
            reset_usage()
            self._sync_subtitle()
            self._chrome("[green]已清零用量计数[/green]")
            return
        u = get_usage()
        msg = (f"本会话用量（估算）: 调用 {u['calls']} 次 · 输入 ~{u['prompt_tokens']} · "
               f"输出 ~{u['completion_tokens']} · 合计 ~{u['total_tokens']} tokens")
        cached = u.get("cached_tokens", 0)
        if cached:                              # 上游缓存确有命中 → 报命中率，证明缓存在自有中转生效
            pt = max(1, u.get("prompt_tokens", 0))
            msg += f"\n其中输入命中缓存 ~{cached} tokens（约 {int(cached * 100 / pt)}%，上游 prompt 缓存已生效）"
        else:
            msg += "\n[输入缓存未观测到命中：上游/中转未回报 cached_tokens，或本会话前缀尚未复用]"
        by_model = u.get("by_model") or {}
        if by_model:                            # 按模型分项 + 估算成本（内置单价表，未登记的模型不计价）
            from src.models.cost import cost_for
            total_cost = 0.0
            unpriced = False
            rows = []
            for name in sorted(by_model):
                m = by_model[name]
                c = cost_for(name, int(m.get("prompt_tokens", 0)), int(m.get("completion_tokens", 0)))
                if c is None:
                    unpriced = True
                else:
                    total_cost += c
                row = (f"  {name}: 调用 {m.get('calls', 0)} · 输入 ~{m.get('prompt_tokens', 0)} · "
                       f"输出 ~{m.get('completion_tokens', 0)}")
                if m.get("cached_tokens"):
                    row += f" · 缓存命中 ~{m.get('cached_tokens', 0)}"
                if c is None:
                    row += " · 费用未知（未配置单价）"
                else:
                    row += f" · ≈${c:.4f}"
                rows.append(row)
            msg += "\n按模型:\n" + "\n".join(rows)
            if unpriced:
                msg += f"\n费用合计未知；已知部分估算: ≈${total_cost:.4f}（仍有未计价用量）"
            else:
                msg += f"\n估算成本合计: ≈${total_cost:.4f}"
        ctx = self._context_usage_label()
        if ctx:
            msg += f"\n当前上下文占用（估算）: {ctx}"
        self._emit(msg)

    def _cmd_context(self, arg: str) -> None:
        """/context：查看上下文占用；/context auto|compact|balanced|preserve 切策略并持久化。"""
        arg = (arg or "").strip().lower()
        if arg:
            policy = self._set_context_policy(arg)
            if policy is None:
                self._emit("用法: /context [auto|compact|balanced|preserve]")
                return
            self._chrome(f"[green]上下文策略已切换为 {policy}（已写入 .vortocode/settings.json）[/green]")
        if self.agent is None:
            self.agent = self._build_main_agent()
        try:
            u = self.agent.context_usage(self.mode)
        except Exception as e:  # noqa: BLE001
            self._emit(f"上下文统计失败: {e}")
            return

        def tok(name: str) -> str:
            return self._fmt_tokens_short(int(u.get(name, 0)))

        raw_policy = str(u.get("raw_policy") or "auto")
        effective = str(u.get("policy") or raw_policy)
        lines = [
            "[b]上下文窗口[/b]（估算，按当前下一轮请求计算）",
            f"策略: {raw_policy} → 当前生效 {effective}",
            f"占用: {tok('used_tokens')}/{tok('max_context_tokens')} tokens · {int(u.get('pct', 0))}%",
            "",
            "分解:",
            f"  system: {tok('system_tokens')}",
            f"  history(保留): {tok('history_tokens')} / 原始 {tok('raw_history_tokens')}",
            f"  summary: {tok('summary_tokens')}",
            f"  plan: {tok('plan_tokens')}",
            f"  messages: {int(u.get('trimmed_history_messages', 0))}/{int(u.get('history_messages', 0))}",
            "",
            "压缩:",
            f"  compact: {'on' if u.get('compact_enabled') else 'off'}",
            f"  当前策略触发线: {tok('max_context_tokens')}",
            f"  压缩后最近原文预算: {tok('recent_budget')}",
            f"  下一轮会压缩: {'yes' if u.get('will_compact') else 'no'}",
            "",
            "可选策略:",
            "  /context auto      plan=balanced, build=preserve",
            "  /context compact   日常轻量对话，更早压缩",
            "  /context balanced  普通协作",
            "  /context preserve  高强度开发，尽量保留原文",
        ]
        if effective == "preserve":
            lines.append("\n建议: 当前适合开发/调试长任务；如果只是问答，可切 /context compact 节省上下文。")
        elif effective == "compact":
            lines.append("\n建议: 当前适合日常对话；进入长任务或 PR 审核前可切 /context preserve。")
        else:
            lines.append("\n建议: auto 会随 plan/build 自动调整；大多数项目默认用它。")
        self._emit("\n".join(lines))

    def _cmd_compact(self, arg: str) -> None:
        """/compact [preview | <重点保留的说明>]：手动压缩旧对话。

        带说明时把"重点保留什么"透传给摘要器（如 `/compact 保留登录改造的决策和踩过的坑`），
        长任务里能保住自己在意的那条线，而不是听天由命被通用摘要压掉。
        """
        raw = (arg or "").strip()
        if self.agent is None:
            self.agent = self._build_main_agent()
        preview = self.agent.compact_preview(self.mode)
        if raw.lower() == "preview":
            status = "可压缩" if preview.get("can_compact") else "暂不可压缩"
            self._emit(f"上下文压缩预览：{status}\n{self._compact_preview_text(preview)}")
            return
        focus = raw                          # 其余一律当"重点保留"说明（空=按默认策略压缩）

        async def _run():
            result = await self.agent.compact_now(self.mode, focus=focus)
            if not result.get("ok"):
                self._emit(f"上下文压缩未执行：{result.get('reason', '未知原因')}\n"
                           f"{self._compact_preview_text(result)}")
                return
            self._audit_event("compact", {
                "before_messages": result.get("before_messages"),
                "after_messages": result.get("after_messages"),
                "before_tokens": result.get("before_tokens"),
                "after_tokens": result.get("after_tokens"),
                "summary_len": len(result.get("summary") or ""),
                "focus": focus[:200],
            })
            self._render_statusbar()
            head = "上下文已压缩。" + (f"（重点保留：{focus[:60]}）" if focus else "")
            self._emit(f"{head}\n"
                       f"{self._compact_preview_text(result)}\n"
                       f"消息数: {result['before_messages']} → {result['after_messages']}；"
                       f"history tokens: ~{self._fmt_tokens_short(int(result['before_tokens']))}"
                       f" → ~{self._fmt_tokens_short(int(result['after_tokens']))}")
        self.run_worker(_run(), exclusive=True, group="compact")

    def _cmd_theme(self, arg: str) -> None:
        """/theme：无参弹主题选择器（↑↓ **实时预览**，回车定、Esc 恢复）；带参直接切。"""
        from src.tui.app import ListPicker   # 延迟导入：app 模块级导入本模块，模块级导回去会成真环
        names = sorted(self.available_themes)
        name = arg.strip()
        if not name:
            cur = self.theme
            items = [(n, f"{n}{'  ← 当前' if n == cur else ''}") for n in names]

            def _preview(n) -> None:        # 高亮即套用（opencode 式实时预览）
                if n in self.available_themes:
                    self.theme = n

            def _done(sel) -> None:
                if sel and sel in self.available_themes:
                    self.theme = sel
                    self._persist_theme(sel)
                    self._chrome(f"[green]→ 主题切到 {sel}（已记住）[/green]")
                else:
                    self.theme = cur        # Esc：恢复进弹窗前的主题
            self.push_screen(ListPicker("选择主题 · ↑↓ 实时预览", items, on_highlight=_preview,
                                        initial=cur), _done)
            return
        if name not in self.available_themes:
            self._emit(f"没有主题「{name}」。/theme 看全部。")
            return
        self.theme = name                   # Textual 响应式：立刻重绘 chrome（边框/面板/底色）
        self._persist_theme(name)
        self._chrome(f"[green]→ 主题切到 {name}（已记住）[/green]")
