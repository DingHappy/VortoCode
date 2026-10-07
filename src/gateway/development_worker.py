"""Background development execution with explicit workspace and interaction factories."""
from src.agents.worktree_bindings import bind_worktree_owner


async def execute_development(root, task, on_progress, *, build_tools, load_plan,
                              questions_factory=None):
    async def _deny(_m):                       # 后台无人值守：外向操作默认拒绝（push 交给人点 open_pr）
        return False

    questions, question_factory = None, None
    answered_round = bool(task.development.get("execution_revision"))
    if task.kind in {"dev", "dev-resume"} and task.owner_session:
        from src.agents.task_questions import bind_development_question
        if questions_factory is None:
            raise RuntimeError("Owned development tasks require a question service")
        questions = questions_factory()
        questions.begin(task)

        def question_factory(plan, block):
            task.plan_id, task.branch = plan.plan_id, plan.branch
            questions.validate_execution(task)
            return bind_development_question(questions, task, plan, block)

    tools = {t.name: t for t in build_tools(root, on_progress=on_progress,
                                                confirm=_deny, draft_pr=True, question_factory=question_factory)}
    # 用 **task-scoped plan_id** 钉住本次计划——绝不靠 list_plans()[0]（全局最新）猜：并发跑多任务时
    # 那会拿到别的任务刚生成的 plan/branch，导致 open_pr 给错任务推错分支（#128 评审）。
    from src.agents.tool import ToolTurnYield
    try:
        if (task.kind == "dev-resume" or answered_round) and task.plan_id:
            from src.gateway.task_recovery import validate_recovery_plan
            if not answered_round:
                validate_recovery_plan(task, load_plan(root, task.plan_id))
            pid = task.plan_id
            with bind_worktree_owner(task_id=task.id, owner_session=task.owner_session, plan_id=pid):
                result = await tools["dev_resume"].handler({"plan_id": pid})
        else:
            pid = task.plan_id or f"bg-{task.id}"
            task.plan_id = pid
            on_progress(f"已绑定持久计划 {pid}")
            with bind_worktree_owner(task_id=task.id, owner_session=task.owner_session, plan_id=pid):
                result = await tools["dev_auto"].handler({"task": task.prompt, "plan_id": pid})
    except ToolTurnYield:
        from src.gateway.tasks import TaskBlocked
        if questions is None or not questions.awaiting(task):
            raise RuntimeError("开发提问未能持久保存") from None
        raise TaskBlocked() from None
    plan = load_plan(root, pid)         # 按确定 id 精确取回本次 C1 计划（可 dev_resume 续跑）
    if plan is not None:
        task.plan_id = plan.plan_id
        task.branch = plan.branch
    if (plan is None or plan.status not in {"integrated", "done"}
            or any(not block.landed for block in plan.blocks)
            or isinstance(plan.review, dict) and plan.review.get("blocked")):
        task.result = str(result or "")[-4000:]
        reason = "开发任务未提交完整持久计划" if plan is None else "开发计划仍未完成：" + plan.summary()
        raise RuntimeError(reason + "\n" + task.result[-800:])
    return result
