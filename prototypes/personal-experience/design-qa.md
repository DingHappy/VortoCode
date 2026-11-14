# 个人体验设计稿验收

final result: passed

验收日期：2026-11-14。结论只适用于这套离线合成设计稿，不代表原生安装、真实模型、后台权限执行或手机实机验收通过。

## 对照对象与证据

- 视觉参考：主人提供的 agentes.chat「Your own server」截图，1440 × 1202；角色参考为 [`role-family-reference.png`](../../docs/design/personal-experience/assets/role-family-reference.png)，1568 × 1003。
- 运行位置实现：[`where-comparison.png`](../../docs/design/personal-experience/screenshots/where-comparison.png)，1440 × 1202 CSS px、deviceScaleFactor 1，深色、未配置状态。源图与实现使用相同像素尺寸，未对原图做图像编辑。
- 完整并排证据保留在本地导出 `exports/vortoone-personal-experience-design-2026-11-14/comparison-runtime.png` 与 `comparison-roles.png`；参考应用截图不纳入公开仓库。两张图在同一浏览器页面内一起捕获，比较时实际打开了合并后的画面。
- 原参考有 macOS 标题栏，实现有设计审阅工具栏；主体是对应的本机／已有助手选择。VortoOne 使用中文、既有设计变量和自己的品牌，是产品适配，不以逐像素复制为目标。
- 其它浏览器证据：[`chat.png`](../../docs/design/personal-experience/screenshots/chat.png)、[`settings.png`](../../docs/design/personal-experience/screenshots/settings.png)、[`settings-dark.png`](../../docs/design/personal-experience/screenshots/settings-dark.png)、[`staff-approval.png`](../../docs/design/personal-experience/screenshots/staff-approval.png)、[`recovery-720.png`](../../docs/design/personal-experience/screenshots/recovery-720.png)。默认 1440 × 1000、1x；另检查 960 与 720 宽度，均为 Desktop 小窗口，不当作手机原生验收。
- 并排角色画面可辨认珊瑚总助手、紫色眼镜、绿色耳机、黄色围巾；另单独打开专员页面核对配饰、透明背景与完整身体。没有用 CSS、SVG 或 emoji 替代角色。

## 五项视觉核对

| 项目 | 结果与有意差异 |
| --- | --- |
| 字体与排版 | 中文采用现有 sans 字体与正文变量；对话欢迎标题沿用既有 display 字体。英文参考的超大标题未逐像素照搬。标题、正文、帮助三级可辨，表单标签及错误保持可读。 |
| 留白与布局 | 两条运行路径使用同级并排卡片，说明和主动作分开；日常使用以侧栏和对话为主，设置切为自己的两栏。1440、960、720 宽度所查主页面无水平溢出。 |
| 颜色与变量 | 直接引入产品 tokens；浅深色使用中性背景、边框和文字。角色色只用于身份，连接中断同时有文字与图标。与原图色值有意不同。 |
| 图片与图标 | 四个生成角色保留所选家族的颜色、体积与配饰；Lucide 为功能图标。1254 × 1254 RGBA 素材在头像尺寸显示，无截断或不透明方底。微量半透明边缘属于 P3。 |
| 文案与内容 | 安装、启动、使用分开；配置已保存不宣称验证通过；专员建议不冒充已登记。原型顶部明确标记合成内容，示例表单不连接真实服务。 |

## 已发现问题与修复复查

1. [P1，早期运行检查] 位置页面缺少图标导入导致白屏。补齐导入，并增加 15 页离线渲染检查；最终浏览器重新遍历全部页面，无 Runtime.exceptionThrown。
2. [P2，状态一致性] 恢复页面早期仍显示侧栏已连接。改为连接中断／当前连接未确认，重新捕获 recovery 与 recovery-720；最终画面中的标题、卡片和侧栏一致。
3. [P2，交互] 早期平面弹层缺少原生焦点约束。换为 dialog.showModal，关闭后恢复入口或页面标题。实测 Tab 留在确认弹窗，Escape 关闭未保存提示后，等待下一次浏览器绘制，焦点回到「返回个人空间」按钮。
4. [P2，状态表达] 例行开关暂停后仍用「已启用」作可访问名称。改为稳定的「是否启用」标签并由 aria-checked 表达状态。实测重新开启前仍为 false，拒绝后仍为 false。
5. [P2，流程] 初稿恢复排期直接开关、设置离开不提示、空间内合成记录未完全分隔。分别补主人确认、未保存离开提示和按空间保存演示记录；实测拒绝恢复保持暂停、分类切换保留资料草稿、切换空间恢复各自对话草稿。

上述修复后重新捕获最终各页及深色画面，并查看运行位置并排图、角色并排图、对话、设置和小窗口恢复画面。未发现仍需处理的 P0/P1/P2；没有把原生窗口栏差异或中文适配当作偏差。

## 实际走过的交互

- 本机模型表单 → 复制同意 → 安装 → 启动 → 使用；未勾同意时安装按钮不可用。所有回执均为合成。
- 空间 A 写草稿 → 切到 B 为独立草稿 → 切回 A 内容恢复。
- 研究专员建议 → 提交提议 → 独立主人批准 → 新建例行工作 → 核对排期 → 批准。
- 暂停排期 → 重新开启弹出确认 → 拒绝，排期仍暂停。
- 设置搜索 → 修改资料 → 切分类再返回草稿保留 → 离开提示 → 继续编辑／放弃离开。
- 资料类型空状态 → 文档预览 → v1 历史版 → 关闭预览。
- 浏览器遍历 15 个页面，检查过浅深色；960、720 宽度检查运行位置、对话、专员、定时、资料库、设置、确认入口与恢复。最终遍历未捕获运行异常。

未完整键盘审计所有控件、所有设置字段或浏览器读屏，也未验证真实服务、远程连接与模型。合成批准不能作为安全合同测试的替代。

## 自动检查与后续

- `npm run test:render`：15 页通过。
- `npm run build`：通过。
- `npm run test:sites`：4 项通过，只验证模板产物与本地 worker 合同，未发布。
- 仓库 `./scripts/ci-local.sh`：ruff、mypy、Python 7261、e2e 30、Desktop TypeScript、Vitest 1629、Rust 201 与 cargo check 通过。Python 11 项跳过，Rust 2 项 ignored；跳过不算通过，分类见 PR 描述。
- P3：角色透明边缘和不同留白可在生产头像迁移时再微调；尚未制作思考／招呼动画，不阻塞静态方案审阅。
- 下一步按设计文档 UX-1～UX-4 分步接入原组件；原型的内存状态不得带入生产权限和任务执行。
