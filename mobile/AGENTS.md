# 手机客户端开发约定

- 默认连接方案是登录与常驻助手相同的 VortoCode 账号，再从账号助手列表选择连接。手动核对码只保留为显式高级自托管/开发入口。
- 沿用现有 VortoCode 账号体系。账号与助手归属只能来自可信服务核验的 issuer + 稳定 subject，不从邮箱相同、手机输入或本机显示名推断授权。
- 没有完成账号客户端注册、助手登记/发现和访问授权前，明确说明接入状态，不把已有 Desktop 登录描述为手机自动连接已完成。
- 用户体验依据 `../docs/MOBILE_APP_DESIGN.md` 与 `../docs/MOBILE_ACCOUNT_ACCESS.md`。生产界面与 `../prototypes/mobile-assistant` 的模拟状态保持分离。
- 本目录独立构建，生成 `../web/mobile/`，不手改构建产物；改动后执行 `npm run build`。
- 复用 `../packages/mobile-client` 和现有领域记录。不要新建任务、目标、审批或调度数据库。
