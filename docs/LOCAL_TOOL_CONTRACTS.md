# 本机工具类型声明

沿用 Tool 现有的 required_args 与 argument_schema，并通过共享 ToolRuntime 在执行前校验；不再增加同名的仅声明字段，也不替换 Tool 定义。

本切片为文件读取、命令、网页、记忆、技能、委派、定时、小组和计划补常用字段的显式类型与必填项。文件写入继续保留 #756 的 path/content/old/new 校验；空文本仍有效。计划 steps 与成员对象的必填字段由同一校验器增加的嵌套 required 支持，不声明当前执行器不支持的约束。

保留 main 的 developer/project、外部 engine 枚举、动态 owner 和并发设置；只声明 parallel，不恢复已删除的任务数、轮数或唤醒额度。对外操作与业务规则仍使用原确认门和 handler。

旧 handler 的兼容别名仍保留，但模型调用必须提供当前声明的规范必填字段。未迁移的可选字段保留原处理；完整外部 input_schema 的处理边界仍见 TOOL_RUNTIME.md，本 PR 不实现通用 JSON Schema。

自动检查验证真实工具装配的合同、两种协议的整数行号读取和计划更新、嵌套缺参拒绝及原文件写入约束。未调用真实模型、外部 Agent 或生产服务。
