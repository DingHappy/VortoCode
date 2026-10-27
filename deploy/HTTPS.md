# 常驻助手的手机 HTTPS 入口

先按 [DDNS](DDNS.md) 稳定域名与出口，再验证外网入站。路由器显示映射启用、同网访问成功或 DNS 地址正确，都不能代替公网验证。检查前保存原映射，临时测试后逐项比对恢复；不要启用 DMZ 或覆盖 NAS 的端口。

## 两种入口

公网 80/443 可达时，使用 `Caddyfile.mobile`：把公网 TCP 80/443 分别转发到常驻主机的 18080/18443。Caddy 以普通用户运行，使用标准 ACME 校验并管理证书。设置 `VORTOCODE_HTTPS_HOST=assistant.example.com`；不在变量里放协议、路径或端口。

若标准端口不可达而高位端口可达，可使用 `Caddyfile.mobile-certificate`：把选定的公网 TCP TLS 端口映射到主机 18443。该模板从 `VORTOCODE_TLS_CERT` 和 `VORTOCODE_TLS_KEY` 读取证书路径，证书需另外通过 DNS-01 签发和续期。示例入口为 `https://assistant.example.com:18443`；账号回调和 `VORTOCODE_MOBILE_PUBLIC_ORIGIN` 都必须保留实际公网端口。证书校验域名，不包含端口。

也可由已有云服务器提供标准 443 入口，再通过受控隧道转发至常驻主机。这会改变 DNS 指向和入口依赖，应先选定方案再修改现有 DDNS。

## 家庭网络使用同一个域名

公网可达不一定代表家庭内网能通过公网 IP 回访自己。先分别验证公网 TLS、局域网 IP 配合正式域名的 TLS、以及路由器实际 DNS 回答，区分服务问题与 NAT 回流问题。

若家庭设备本来就通过路由器解析 DNS，可仅为助手的完整域名新增一条静态 A 记录，指向主机固定的内网地址；公网 AliDNS A 记录继续由 DDNS 更新为 WAN 地址。手机使用的 HTTPS 域名和端口不变，证书也不需要换成内网 IP 证书。先保存现有静态解析表，确认没有同名冲突，再验证只增加目标条目、其它记录保留。不要为了这一条记录替换全家的 DNS 设置或修改公网记录。

验收时应直接查询路由器 DNS，并核对客户端实际解析和 TLS 连接。自定义 DNS、代理或缓存可能使用不同路径；浏览器成功不能单独证明所有家庭设备都已使用内网解析，手机仍需实机验收。

## 独立用户服务

`vortocode-https.user.service` 不启动、重启或更新 Agent。安装位置：

- 二进制：`~/.local/bin/vortocode-caddy`，从固定官方版本下载并校验。
- 配置、环境文件：`~/.config/vortocode-https/Caddyfile` 和 `environment`，目录 0700、文件 0600。
- 自动证书数据：`~/.local/share/vortocode-https/`，目录 0700；不放进仓库。
- 服务：`~/.config/systemd/user/vortocode-https.service`。

服务只监听普通用户可绑定的高位端口，并关闭 Caddy 管理 API。不将 DDNS 配置页、SSH、VNC 或助手管理员 API 直接代理到公网。使用现有用户服务的长期运行能力，需确认该用户已启用 linger。

两个模板现在都只提供准备页和独立 `/healthz`，**没有反向代理**。`backend_ready=false` 是固定的未就绪声明；HTTP 200 只证明此入口能够响应，不能证明账号或手机功能可用。未完成证书与接口配置时不应启用开机自启。

## DNS-01 与阿里云凭据

DNS-01 通过创建并清理 `_acme-challenge` TXT 记录完成验证，不依赖公网 80/443。可选用官方 acme.sh 的 `dns_ali` 适配器；本次核对其 [固定源码](https://github.com/acmesh-official/acme.sh/blob/447dc3c7e7e8fa45337980e2ad13e0185c1be4ff/dnsapi/dns_ali.sh) 调用 `DescribeDomainRecords`、`AddDomainRecord`、`DeleteDomainRecord`。现有 DDNS 更新策略不能直接当作证书策略。

`aliyun-certificate-policy.example.json` 给出对应的域级策略模板。将 `example.com` 替换为实际托管域，优先使用独立 RAM 用户和独立 AccessKey；不要为了续期直接授予 `AliyunDNSFullAccess`。这些权限仍可影响该域的其他记录，不能声称限定到了某个 TXT。要进一步隔离，可以将证书验证域委派到单独管理的 DNS 区域，并核验客户端的委派支持。

凭据只在专用主机的私有配置中填写，不写入仓库、设置命令参数、回执或聊天。签发器需要写私钥和自己的 ACME 状态；使用 0700 目录、0600 私有文件及 UMask 0077。先使用 CA 测试环境核验 DNS 添加、传播与清理；生产签发成功后安装证书。续期定时器需要验证实际证书轮换、代理重新加载和 TXT 清理，不能只根据 timer active 宣称自动续期已完成。

## 证书签发与续期组件

`scripts/manage-mobile-certificate.py` 可安装为 `~/.local/bin/vortocode-certificate`。它仅在 Linux/systemd 用户环境运行，依赖 Python 3.10+、PyYAML、OpenSSL，以及固定安装在 `~/.local/lib/vortocode-acme/` 的官方 acme.sh 核心与 `dnsapi/dns_ali.sh`。本次准备的版本是 3.1.6，固定提交 `807da6498377ee5e0cf43a78091f46f12dc59a89`；下载时核验 Git blob 和文件摘要，不执行在线安装脚本或启用自动升级。

私有 `~/.config/vortocode-https/certificate.json` 配置示例：

```json
{
  "schema_version": 1,
  "hostname": "assistant.example.com",
  "credential_source": "/home/your-user/.config/vortocode-ddns/config.yaml"
}
```

脚本只选择源配置中与此完整域名匹配的一个 AliDNS 项，不按配置顺序盲选。重用已有专用凭据需要为该用户单独授予证书策略；不修改原 DDNS 策略。若要求证书与 DDNS 使用不同密钥，可提供同结构的独立私有源文件。配置及凭据源必须属于当前用户、权限 0600、不是软链。脚本通过环境传递凭据，抑制签发器原始输出；适配器临时写入的凭据缓存会在每次执行结束时清理。

签发器使用自己的 `CURL_HOME` 和限时、有限重试配置，不读取原用户的 curl 配置，也不改变助手的代理或出站路由；保持系统 CA 校验。每个外部命令有 10 分钟总上限，超时终止本次命令的进程组，不会一直等待网络。错误只返回固定类别，不打印签名请求或签发器原始输出。相关选项见 [curl 官方说明](https://curl.se/docs/manpage.html)。

```sh
~/.local/bin/vortocode-certificate inspect
~/.local/bin/vortocode-certificate staging
~/.local/bin/vortocode-certificate staging-renew
~/.local/bin/vortocode-certificate issue
```

`inspect` 仅验证文件、域名匹配与填写状态，不证明 DNS 权限。测试与正式环境使用不同的 ACME 账号、证书目录和 CA。`staging-renew` 可强制演练测试环境续期；不提供强制正式续期。测试证书永远不会安装到正式入口。

正式证书需通过域名、剩余有效期、私钥匹配和系统 CA 链验证，才会安装到 `~/.local/share/vortocode-https/certificates/releases/`。`current` 软链原子切换整对证书与私钥；代理已运行时仅重启独立 HTTPS 服务，并检查本机实际提供的是新证书。失败恢复旧证书，不重启助手。未运行的 HTTPS 服务不会被签发脚本自行启动。

在 HTTPS 环境文件中配置 `VORTOCODE_TLS_CERT` 指向 `certificates/current/fullchain.pem`、`VORTOCODE_TLS_KEY` 指向 `certificates/current/privkey.pem` 的绝对路径。完成签发、TXT 清理和入口验收后，将 `vortocode-certificate.user.service` / `.timer` 安装为 `vortocode-certificate.service` / `.timer`；定时器每日检查两次，加入随机延迟、错过后补执行，不强制反复签发。手动 `vortocode-certificate renew` 走相同路径。组件安装、测试签发、正式签发、定时器启用和真实到期轮换应分别记录，不能混为同一个验收结果。

## 可选的证书专用出站通道

家庭出口可能能够访问测试 CA、却不能稳定访问正式 CA。先检查真实连接并完成失败清理；不能关闭 TLS 校验，也不能把测试证书安装到正式入口。

`vortocode-certificate-egress.user.service` 和 `certificate-egress.sshd.example.conf` 提供可选的受限出站方案，**默认不安装、不启用**。它会增加一个云主机依赖，部署前需明确选定。云主机只为证书请求提供 TCP 转发，手机入口、模型、聊天、浏览器和任务执行仍留在常驻主机。ACME 账号、DNS 凭据和证书私钥不复制到中转主机，端到端 HTTPS 保留正常证书校验。

使用新的专用 SSH 身份，不复用管理员私钥；云端有效配置必须确认仅允许向正式/测试 CA 与 AliDNS 的 443 建立连接，禁止交互 shell、子系统会话、远程监听、Unix socket 转发、代理转发和额外目的地。先核对实际 OpenSSH 版本、`sshd -t` 及专用用户的有效配置，再启用；不能假设放进 `sshd_config.d` 的文件一定会被旧版系统加载。常驻主机 SOCKS 仅监听回环地址。

经明确选用后，在 `certificate.json` 添加 `"certificate_proxy": "socks5h://127.0.0.1:17890"`。脚本只接受这个固定回环入口，不接受其它代理地址或代理账号密码；只为签发器子进程设置 `https_proxy`，不修改原助手环境。代理启动最多等待 10 秒，连接失败会返回固定错误。将 `vortocode-certificate-egress.dependency.conf` 安装为证书服务的 `egress.conf` drop-in，让续期检查拉起出站服务。

SSH 私钥由常驻主机生成并以 0600 保存；中转主机只接收带限制的公钥。主机公钥须经既有可信管理连接取得并固定到专用 `known_hosts`，不得关闭严格校验。对云主机的修改需有私有备份，并逐项验证原全局与管理员有效 SSH 配置不变，使用 reload 保留已有连接。除了验证配置，还应实际验证允许的 HTTPS 请求可达、其它目的地拒绝、命令会话拒绝、远程监听拒绝。

回滚时先停用证书定时器与专用出站服务，恢复证书设置中的直接连接模式，再移除本次专用用户及匹配块。若 SSH 配置之后有其它改动，不得用旧备份直接覆盖；先核对差异，完成 `sshd -t` 后再 reload。保留已签发的有效证书与原助手服务。仓库模板本身不代表某台主机的通道已部署或证书已签发，需以该次部署回执为准。

## 开放手机接口前的检查

1. 从公网主机验证端口、TLS 信任链和域名，不使用 `-k`；检查 HTTP 跳转不会漏端口或降级到明文。
2. 部署含 `/api/mobile/native/*` 的服务和已构建的 `web/mobile/`，明确记录发布源码、状态目录、回滚点和真实运行版本。
3. 按 [同账号接入](../docs/MOBILE_ACCOUNT_ACCESS.md) 配置精确 origin、账号回调与助手归属。代理保留 Host，只信任本机代理的协议转发。
4. 只开放手机所需的精确路由和静态资源，拒绝管理员、配对管理、命令执行、文档等其余端点；读取仍由原账号会话校验。
5. 验证同账号登录、异账号拒绝、刷新、退出及撤销，然后进行手机蜂窝网络验收。

完成准备阶段后，可切换到 `Caddyfile.mobile-proxy`，配合独立 `vortocode-mobile.user.service`。该网关在原助手状态目录运行，但不启动执行内核，仅监听本机 18081；HTTPS 入口只放行手机必要路由。切换前备份准备页配置，先验证配置、网关版本、状态根目录及未登录访问限制，失败恢复旧入口。账户尚未关联时，手机页面应明确显示未就绪，不将静态页面可访问描述为登录完成。详见 [首次账号关联](../docs/MOBILE_ACCOUNT_ACCESS.md#独立手机网关与首次关联)。

模板本身不表示证书已签发、手机后端已更新或 App 已可从外网使用，以实际部署回执和各阶段验收为准。

参考：[Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)、[Caddy 内部监听端口](https://caddyserver.com/docs/caddyfile/options)、[acme.sh DNS 适配器](https://github.com/acmesh-official/acme.sh/wiki/dnsapi)。
