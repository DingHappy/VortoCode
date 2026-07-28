"""长期记忆：写入 / 召回 / 提案复核。

写入要过 `src/memory/write_policy.py` 的四级策略（durable/proposal/quarantine/reject）——
污点回合的指令性文本与疑似凭据不进长期记忆。召回申报 `untrusted_source=True`：
记忆里可能存着当初从外部摄入的内容，读回来同样算摄入。
"""

from __future__ import annotations

from pathlib import Path

from src.agents.tool import Tool


def _longterm_store(repo_root: str):
    """跨会话长期记忆用的 SessionStore（与 TUI 同一 db、同一固定 __longterm__ session_id）。"""
    from src.memory.session_store import SessionStore
    return SessionStore(str(Path(repo_root) / ".vortocode" / "sessions.db"))


def build_memory_tools(repo_root: str, confirm=None, *, source: str = "agent",
                       session_id=None) -> list[Tool]:
    """跨端同源的长期记忆工具：真实写门 + 污点/凭据隔离 + 来源审计。"""
    from src.memory.write_policy import (MemoryWritePolicy, MemoryWriteRequest, MemoryWriter,
                                         confirmation_message, new_origin_session)
    store_holder = {}
    policy = MemoryWritePolicy()
    fallback_session = new_origin_session(source)

    def _store():
        # 仅装配 agent / plan 模式不应创建 sessions.db；真正读写记忆时才打开。
        if "store" not in store_holder:
            store_holder["store"] = _longterm_store(repo_root)
        return store_holder["store"]

    def _writer():
        return MemoryWriter(_store())

    def _origin_session() -> str:
        value = session_id() if callable(session_id) else session_id
        return str(value or fallback_session)

    async def _save_memory(args: dict) -> str:
        content = str(args.get("content", "")).strip()
        if not content:
            return "save_memory 需要 content（要长期记住的事实/偏好/约定）。"
        from src.agents.taint import is_tainted
        tainted = is_tainted()
        request = MemoryWriteRequest(
            content=content,
            source=source,
            session_id=_origin_session(),
            tainted=tainted,
            write_method="tool",
        )
        decision = policy.evaluate(request)
        if decision.outcome == "reject":
            reason = "内容为空" if "empty" in decision.reasons else "内容过长"
            return f"保存记忆失败: {reason}"
        prompt = confirmation_message(decision, decision.content)
        if tainted and decision.outcome == "durable":
            prompt = "⚠ 本回合摄入过外部内容；若保存，会带污点来源审计。\n" + prompt
        if confirm is None:
            return "保存记忆失败: 当前入口没有可用的用户确认门。"
        try:
            approved = bool(await confirm(prompt))
        except Exception as e:  # noqa: BLE001
            return f"保存记忆确认失败: {e}"
        if not approved:
            return "用户取消了记忆写入；未保存长期记忆或提案。"
        try:
            result = _writer().write(request, confirmed=True, confirmed_by="user")
        except Exception as e:  # noqa: BLE001
            return f"保存记忆失败: {e}"
        if result.status == "stored":
            return f"已记住（跨会话，id={result.record_id}）：{decision.content[:80]}"
        return f"{result.message}（id={result.record_id}）"

    async def _remember_repo(args: dict) -> str:
        """把一条**关于本仓库**的事实写进 .vortocode/memory/repo.md（下个会话自动进系统提示）。

        写入门槛**高于** save_memory：repo.md 每轮都被注入系统提示 = "系统事实"，故只收
        policy 判定为 durable 的内容；污点回合的指令性文本（proposal）与疑似凭据（quarantine）
        一律拒绝——绝不让外部内容经这里变成每轮喂给模型的事实（提示注入的最佳跳板）。
        """
        from src.agents.repo_memory import append_repo_memory
        content = str(args.get("content", "")).strip()
        if not content:
            return "remember_repo 需要 content（关于本仓库的事实：构建/测试命令、目录约定、踩过的坑）。"
        from src.agents.taint import is_tainted
        tainted = is_tainted()
        request = MemoryWriteRequest(content=content, source=source,
                                     session_id=_origin_session(), tainted=tainted,
                                     write_method="tool", memory_type="repo_fact")
        decision = policy.evaluate(request)
        if decision.outcome == "reject":
            reason = "内容为空" if "empty" in decision.reasons else "内容过长"
            return f"写入仓库记忆失败: {reason}"
        if decision.outcome != "durable":
            # 仓库记忆会自动进系统提示，比会话记忆更敏感 → 非 durable 一律不落盘
            why = ("疑似含凭据" if decision.outcome == "quarantine"
                   else "疑似来自外部内容的指令性文本")
            return (f"拒绝写入仓库记忆（{why}：{'/'.join(decision.reasons) or '策略拦截'}）。"
                    "仓库记忆每轮都会进系统提示，只接受可信的仓库事实；"
                    "如确需留存，请改用 save_memory（走提案/隔离审阅流程）。")
        if confirm is None:
            return "写入仓库记忆失败: 当前入口没有可用的用户确认门。"
        try:
            approved = bool(await confirm(
                "把这条事实写进**仓库记忆** .vortocode/memory/repo.md？\n"
                "（今后本仓库的每个会话都会自动带上它，子 agent 也会看到）\n"
                f"  {decision.content[:240]}"))
        except Exception as e:  # noqa: BLE001
            return f"写入仓库记忆确认失败: {e}"
        if not approved:
            return "用户取消了仓库记忆写入。"
        try:
            p = append_repo_memory(repo_root, decision.content)
        except Exception as e:  # noqa: BLE001
            return f"写入仓库记忆失败: {e}"
        msg = (f"已写入仓库记忆（{p}）：{decision.content[:80]}\n"
               "下个会话装配时自动进系统提示（本会话的系统提示保持不变）。")
        from src.agents.repo_memory import repo_memory_body
        _body, dropped = repo_memory_body(repo_root)
        if dropped > 0:              # 如实说：文件超上限，注入时会挤掉更早的条目（新写的这条一定在）
            msg += (f"\n⚠ 仓库记忆已超注入上限：只有最新的若干条会进系统提示，"
                    f"更早的 {dropped} 条不再注入。建议精简这个文件。")
        return msg

    async def _recall_memory(args: dict) -> str:
        q = str(args.get("query", "")).strip()
        try:
            store = _store()
            rows = (store.search_memories("__longterm__", q, 10) if q
                    else store.get_memories("__longterm__"))
        except Exception as e:  # noqa: BLE001
            return f"检索记忆失败: {e}"
        if not rows:
            return "（没有相关的长期记忆）"
        return "相关长期记忆:\n" + "\n".join(f"- {r['content']}" for r in rows[:10])

    async def _list_proposals(args: dict) -> str:
        status = str(args.get("status", "open")).strip().lower() or "open"
        if status not in {"open", "pending", "quarantined", "approved", "rejected", "all"}:
            return "status 可选 open/pending/quarantined/approved/rejected/all。"
        rows = _store().list_memory_proposals(None if status == "all" else status, limit=30)
        if not rows:
            return "（没有匹配的记忆提案/隔离记录）"
        lines = ["记忆提案/隔离记录:"]
        for row in rows:
            preview = " ".join(str(row.get("content") or "").split())[:120]
            lines.append(
                f"- {row['id']} · {row['status']} · {row['decision']} · "
                f"source={row['source']} · {preview}"
            )
        return "\n".join(lines)

    async def _review_proposal(args: dict) -> str:
        proposal_id = str(args.get("id", "")).strip()
        action = str(args.get("action", "")).strip().lower()
        if not proposal_id or action not in {"approve", "reject"}:
            return "review_memory_proposal 需要 id 和 action（approve/reject）。"
        row = _store().get_memory_proposal(proposal_id)
        if row is None:
            return f"未找到记忆提案 {proposal_id}。"
        if action == "approve" and row.get("status") == "quarantined":
            return "隔离记录含疑似凭据，不能批准；请提交脱敏后的安全记忆。"
        preview = " ".join(str(row.get("content") or "").split())[:240]
        if confirm is None:
            return "审阅记忆提案失败: 当前入口没有可用的用户确认门。"
        try:
            approved = bool(await confirm(
                f"{action} 记忆提案 {proposal_id}？\n"
                f"  状态={row.get('status')} 来源={row.get('source')}\n  {preview}"
            ))
        except Exception as e:  # noqa: BLE001
            return f"审阅记忆提案确认失败: {e}"
        if not approved:
            return f"用户取消了 {action} 记忆提案 {proposal_id}。"
        result = _writer().review(
            proposal_id, action, confirmed=True, reviewer="user", session_id=_origin_session()
        )
        if not result.get("ok"):
            return f"审阅记忆提案失败: {result.get('error', '未知错误')}"
        if result["status"] == "approved":
            return f"已批准提案 {proposal_id}，长期记忆 id={result['memory_id']}。"
        return f"已拒绝记忆提案 {proposal_id}。"

    return [Tool("save_memory", "确认后保存跨会话长期记忆；外部指令/疑似凭据进入隔离提案（仅 build）",
                 {"content": "要记住的内容"}, _save_memory, read_only=False),
            Tool("remember_repo",
                 "确认后把**关于本仓库**的事实写进仓库记忆（构建/测试命令、目录约定、踩过的坑）；"
                 "今后每个会话与子 agent 自动带上（仅 build）",
                 {"content": "关于本仓库的事实"}, _remember_repo, read_only=False),
            Tool("recall_memory", "检索跨会话长期记忆（不传 query 则列出全部）",
                 {"query": "可选，关键词"}, _recall_memory,
                 read_only=True, untrusted_source=True),
            Tool("list_memory_proposals", "列出未进入正常召回的记忆提案/凭据隔离记录",
                 {"status": "可选 open/pending/quarantined/approved/rejected/all"},
                 _list_proposals, read_only=True, untrusted_source=True),
            Tool("review_memory_proposal", "经用户确认批准或拒绝记忆提案；凭据隔离记录不能批准（仅 build）",
                 {"id": "提案 id", "action": "approve 或 reject"},
                 _review_proposal, read_only=False)]
