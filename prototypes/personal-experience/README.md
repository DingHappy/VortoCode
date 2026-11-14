# VortoOne 个人体验设计原型

完整方案见 [设计文档](../../docs/design/personal-experience/README.md)，验收见 [design-qa.md](design-qa.md)。这是独立实现的合成前端，不调用真实模型、运行端或安装器。

```sh
npm ci
npm run dev -- --host 127.0.0.1
npm run test:render
npm run build
npm run test:sites
```

顶部可选 15 个页面与浅深色，URL 支持 `?screen=welcome&theme=light`。刷新清空演示记录；安装说明不提供或下载真实安装包。角色和颜色引用仓库设计资产与 tokens，没有引入 agentes.chat 的源代码、CSS 或资产。

主人补充要求界面基本复刻参考产品，允许舍弃旧界面。更新后的视觉值以原型内 `--preview-*` 表达，尚未改生产 tokens。`?screen=chat&theme=dark&example=conversation` 展示连续消息、过程与文档卡、可关闭详情；普通打开仍为空白对话。顶部按钮可在两种合成状态间切换。

默认开发服务器只监听本机。模板保留 worker 与 Sites 产物合同，供以后审阅使用；本次未发布到托管服务。
