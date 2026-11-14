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

默认开发服务器只监听本机。模板保留 worker 与 Sites 产物合同，供以后审阅使用；本次未发布到托管服务。
