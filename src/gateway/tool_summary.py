"""工具调用 → 时间线里一句用户看得懂的动作（Desktop 时间线 / 活动记录共用）。"""
import re
from typing import Any, Dict


def tool_summary(name: str, args: Dict[str, Any]) -> str:
    """把工具调用压成一句"用户看得懂的动作"；敏感值不进入标题。

    表要**覆盖全部内建工具**：漏掉的会退回 ``name.replace("_", " ")``，于是时间线里冒出
    ``dev parallel``、``review memory proposal`` 这类内部标识符——看着像日志，不像进展
    （2026-09-17 真机诊断）。``tests/unit/test_timeline_noise.py`` 会在新增工具漏登记时变红。

    退回原名只留给**非内建工具**（MCP / 扩展）：那些名字不归本仓管，原样显示比硬翻成
    "工具调用"更有信息量。
    """
    path = str(args.get("path") or args.get("file") or "").strip()
    query = str(args.get("query") or args.get("pattern") or "").strip()
    command = str(args.get("command") or "").strip()
    title = str(args.get("name") or args.get("title") or args.get("skill") or "").strip()
    task = str(args.get("task") or args.get("description") or args.get("brief") or "").strip()
    symbol = str(args.get("symbol") or "").strip()
    job = str(args.get("id") or args.get("job") or "").strip()

    def _with(prefix: str, detail: str, limit: int = 60) -> str:
        if not detail:
            return prefix.rstrip("：")
        joiner = "" if prefix.endswith("：") else " "
        return f"{prefix}{joiner}{detail[:limit]}"

    labels = {
        # —— 读代码 ——
        "read_file": _with("读取", path) if path else "读取文件",
        "read_local_file": _with("读取本机文件", path),
        "list_files": _with("浏览", path) if path else "浏览文件",
        "glob": _with("按文件名查找", query) if query else "按文件名查找",
        "grep": _with("搜索", query) if query else "搜索内容",
        "search_code": _with("搜索", query) if query else "搜索代码",
        "document_symbols": _with("列出符号：", path) if path else "列出文件里的符号",
        "find_definition": _with("查找定义", symbol or query),
        "find_references": _with("查找引用", symbol or query),
        # —— 改代码 ——
        "edit_file": _with("修改", path) if path else "修改文件",
        "write_file": _with("写入", path) if path else "写入文件",
        "rename_symbol": _with("重命名符号", symbol),
        # —— 跑命令 ——
        "run_command": _with("运行", command, 120) if command else "运行命令",
        "run_tests": _with("跑测试", path or command, 80),
        "read_output": _with("读取运行输出", job),
        "stop_command": _with("停止运行", job),
        # —— 隔离开发流水线 ——
        "dev_auto": _with("启动隔离开发流水线：", task, 50),
        "dev_isolated": _with("在隔离分支里实现：", task, 50),
        "dev_parallel": _with("并行隔离实现：", task, 50),
        "dev_resume": _with("续跑上次的开发计划", job),
        "git_status": "查看工作区改动",
        "show_diff": _with("查看改动", path),
        "list_branches": "查看分支",
        "open_pr": _with("开 PR：", title or task, 50),
        "pr_fix": _with("按评审意见修复 PR", job),
        # —— 记忆与技能 ——
        "save_memory": "记住这件事",
        "recall_memory": _with("回忆", query) if query else "回忆相关记忆",
        "search_conversations": _with("翻找以前的对话", query) if query else "翻找以前的对话",
        "read_conversation_excerpt": "查看以前对话的片段",
        "personal_goals": "核对个人目标与下一步",
        "remember_repo": "记住仓库经验",
        "list_memory_proposals": "查看待确认的记忆",
        "review_memory_proposal": "处理待确认的记忆",
        "save_skill": _with("保存技能", title),
        "use_skill": _with("使用技能", title),
        "list_personal_skills": "查看个人技能",
        "use_personal_skill": "加载个人技能指令",
        # —— 上网与研究 ——
        "web_search": f"搜索网页：{query[:100]}" if query else "搜索网页",
        "web_fetch": "读取网页",
        "screenshot_page": "给网页截图",
        # —— 浏览器操控（输入内容可能敏感，只显示元素编号，不进标题）——
        "browser_open": _with("浏览器打开", str(args.get("url") or "").strip(), 80),
        "browser_snapshot": "读取浏览器页面",
        "browser_screenshot": "给浏览器页面截图",
        "browser_click": _with("浏览器点击元素", str(args.get("ref") or "").strip()),
        "browser_type": _with("浏览器输入到元素", str(args.get("ref") or "").strip()),
        "research_parallel": _with("并行调研：", task, 50),
        "task_status": _with("查看任务：", str(args.get("task_id") or ""), 50),
        "task_model_profiles": "查看模型任务表现",
        "task_model_assess": _with("评估是否继续原模型：", str(args.get("task_id") or ""), 50),
        "task_inbox": "读取后台任务交接",
        "task_acknowledge": _with("记录交接处理：", str(args.get("task_id") or ""), 50),
        "task_review": _with("验收子任务：", str(args.get("task_id") or ""), 50),
        "task_followup": _with("继续子任务：", str(args.get("task_id") or ""), 50),
        "task_answer": _with("回答子任务：", str(args.get("task_id") or ""), 50),
        "task_release": _with("推进依赖任务：", str(args.get("task_id") or ""), 50),
        "task_reconcile": _with("核对依赖失效：", str(args.get("task_id") or ""), 50),
        "task": _with("交给子 agent：", task, 50),
        # —— 定时任务 ——
        "cron_add": _with("新建定时任务", title),
        "cron_list": "查看定时任务",
        "cron_run": _with("立即运行定时任务", title or job),
        "cron_toggle": _with("启用/停用定时任务", title or job),
        "cron_set_prompt": _with("改写定时任务的提示词", title or job),
        "cron_set_web": _with("调整定时任务的联网设置", title or job),
        # —— 制品与外发 ——
        "publish_artifact": _with("发布制品", title or path),
        "list_artifacts": "查看制品",
        "delete_artifact": _with("删除制品", title or path),
        "send_file": _with("发送文件", path),
        "send_image": _with("发送图片", path),
        "send_document": _with("发送文档", path),
        # —— 会话控制 ——
        "update_plan": "更新执行计划",
        "request_build": "请求切换到 Build",
        "request_workspace": "请求项目工作区" if str(args.get("scope") or "") == "project"
        else "请求临时工作区",
    }
    return labels.get(name) or re.sub(r"_+", " ", name).strip() or "工具调用"
