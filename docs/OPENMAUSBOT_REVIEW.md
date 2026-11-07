# OpenMausBot 源码参考

2026-11-02 只读审阅，固定提交 `6af51394e98be9cebcb48d2047fb44148ef2f610`。没有安装或运行，也没有复制实现。

项目是本地 Agent 产品：Electron / React 桌面端、多个执行核心适配、电脑操作与应用连接。与 VortoCode 的交集主要是对话中的确认、执行状态和人工接管；无需用它替换现有内核。

## 可参考的机制

- [审批生命周期](https://github.com/milind-soni/OpenMausBot/blob/6af51394e98be9cebcb48d2047fb44148ef2f610/server/drivers/chat-tool-approval.ts)：先登记待响应请求再发布事件，决定后移除，超时/取消闭合；向用户提问要收到真正的用户回答，系统提示不能代答。VortoCode 本轮继续使用原 Future 与卡片列表，补上服务端期限复核及不可覆盖的终态。
- [电脑接管](https://github.com/milind-soni/OpenMausBot/blob/6af51394e98be9cebcb48d2047fb44148ef2f610/server/computer-control.ts)：人持有控制权时拒绝机器人动作，不把旧点击排队到交还后执行；机器人请求帮助不能给自己控制权。适合后续 C1/C2 接管设计。
- [统一事件总线](https://github.com/milind-soni/OpenMausBot/blob/6af51394e98be9cebcb48d2047fb44148ef2f610/server/harness/bus.ts)：按执行提供方身份核对事件，限制积压，批量处理流式增量，并区分实时发送与落盘不完整。后续观测改进应复用 VortoCode 原事件流，避免新建并行任务库。

## 不能直接照搬的边界

[SECURITY.md](https://github.com/milind-soni/OpenMausBot/blob/6af51394e98be9cebcb48d2047fb44148ef2f610/SECURITY.md) 明确列出 Slack worker 路径的已知权限缺口：有本机 shell 能力的机器人可能借这些路径触发 Full-access 会话中的工作，而没有新的逐次审批。Slack 是类似飞书、钉钉的团队聊天服务；我们当前 App / Desktop 主线不依赖 Slack。

同一安全说明也指出原生 CLI 使用本机用户权限，许可代理不能当作操作系统沙箱。[安全底线开关](https://github.com/milind-soni/OpenMausBot/blob/6af51394e98be9cebcb48d2047fb44148ef2f610/server/safety-floor-gate.ts) 默认关闭且 Windows 不启用。因此不能从项目具备审批 UI 推导出所有执行路径都受到强隔离。

保留 VortoCode 的污点态预授权失效、能力档案、敏感操作逐次确认和签名摘要复验。参考结论只针对固定提交的静态代码，不能证明项目当前运行、安全修复或真实电脑操作效果。
