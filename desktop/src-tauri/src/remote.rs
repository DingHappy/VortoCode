// 远程工作区：登记远端项目和常驻电脑上的项目、HTTP 代理、WebSocket 桥接（R3a）。
// 从 lib.rs 原样搬出；令牌只在 Rust 侧注入，webview 拿不到原文。
use super::*;

// ── 远程工作区注册（R3a）──────────────────────────────────────────────────────
// 与本地注册（remember_project_at）完全分开：local 流的强不变量（Git 根 + 本机 URL）一点
// 不动；remote 是独立、显式校验（validate_remote_server_url）的旁路。repo_root 存服务器侧
// 路径原样、base_url 存归一化后的远端 server_url、token 入独立 Keychain 服务（按 id 作 account）。

// 远程项目 id：以「remote\0url\0服务器侧根」哈希——与本地 id（哈希本机路径）不同源、不碰撞。
pub(crate) fn remote_project_id(normalized_url: &str, remote_repo_root: &str) -> String {
    format!(
        "{:x}",
        Sha256::digest(format!("remote\0{normalized_url}\0{remote_repo_root}").as_bytes())
    )[..20]
        .to_string()
}

/// 远程 token 字符集闸：只收可见 ASCII（0x21..=0x7e）。
/// 从网页/文档复制 token 时常混入 BOM(U+FEFF)、NBSP、全角、智能引号——这些字节
/// `HeaderValue::from_str` 会接受、但 `to_str()` 会拒，于是握手期报错、错误串里带上头值原文。
/// 在**注册期**就拦住，既避免"能注册却连不上"的静默坏配置，也不给泄漏留触发条件。
/// （注意 str::trim 吃得掉首尾 NBSP，却吃不掉 BOM 与串中间的污染字符。）
pub(crate) fn normalize_remote_token(token: &str) -> Result<String, String> {
    let trimmed = token.trim();
    if trimmed.is_empty() {
        return Err("远程工作区需要访问 token（服务器已设 VORTOCODE_API_TOKEN）".into());
    }
    if !trimmed.bytes().all(|byte| (0x21..=0x7e).contains(&byte)) {
        return Err(
            "远程 token 含不可见或非 ASCII 字符（从网页复制时常混入 BOM／全角／智能引号），\
             请重新粘贴纯 ASCII token"
                .into(),
        );
    }
    Ok(trimmed.to_string())
}

pub(crate) fn remote_project_name(name: &str, remote_repo_root: &str, normalized_url: &str) -> String {
    let trimmed = name.trim();
    if !trimmed.is_empty() {
        return trimmed.to_string();
    }
    remote_repo_root
        .trim_end_matches('/')
        .rsplit('/')
        .find(|segment| !segment.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| normalized_url.to_string())
}

pub(crate) fn remember_remote_at(
    path: &Path,
    normalized_url: &str,
    remote_repo_root: &str,
    name: &str,
    id: &str,
) -> Result<DesktopProjectProfile, String> {
    let profile = DesktopProjectProfile {
        id: id.to_string(),
        name: remote_project_name(name, remote_repo_root, normalized_url),
        repo_root: remote_repo_root.trim().to_string(),
        base_url: normalized_url.to_string(),
        last_opened_at: now_epoch_seconds()?,
        kind: PROJECT_KIND_REMOTE.into(),
    };
    let mut registry = load_project_registry(path)?;
    let profile = upsert_project(&mut registry, profile);
    save_project_registry(path, &registry)?;
    Ok(profile)
}

pub(crate) fn remember_remote_flow(
    path: &Path,
    normalized_url: &str,
    remote_repo_root: &str,
    name: &str,
    id: &str,
    known: bool,
    confirmed: bool,
) -> Result<DesktopProjectProfile, String> {
    // 与本地同理：新远端未获用户确认一律拒绝，且不留任何注册表痕迹（fail-closed）。
    if !known && !confirmed {
        return Err("新远程工作区注册未获用户确认，已取消".into());
    }
    remember_remote_at(path, normalized_url, remote_repo_root, name, id)
}

pub(crate) async fn confirm_remote_registration(app: &AppHandle, server_url: &str) -> Result<bool, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    // 原生对话框在 webview 进程之外：被注入的页面脚本无法替用户点确认，静默把 token 发往
    // 攻击者的 server_url 因此不生效。明示「token 将随每次请求发往该地址」。
    let message = format!(
        "网页层请求注册一个远程工作区：\n\n{server_url}\n\n注册后你的访问 token 将随每次请求发往该地址（存入系统 Keychain）。仅当这是你信任、且刚在界面上输入的服务器时才确认。",
    );
    let app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&app, app.dialog().message(message))
            .title("确认注册远程工作区")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom(
                "确认注册".into(),
                "取消".into(),
            ))
            .blocking_show()
    })
    .await
    .map_err(|error| format!("远程注册确认对话框失败：{error}"))
}

#[tauri::command]
pub(crate) async fn remember_remote_project(
    app: AppHandle,
    server_url: String,
    token: String,
    remote_repo_root: String,
    name: String,
) -> Result<DesktopProjectProfile, String> {
    let path = project_registry_path(&app)?;
    let normalized = validate_remote_server_url(&server_url)?;
    let token = normalize_remote_token(&token)?;
    let id = remote_project_id(&normalized, remote_repo_root.trim());
    let known = load_project_registry(&path)?
        .projects
        .iter()
        .any(|project| project.id == id);
    let confirmed = if known {
        false // 已注册：不弹窗，flow 靠 known 放行
    } else {
        confirm_remote_registration(&app, &normalized).await?
    };
    // 放行才写 token：未确认的新远端不留 Keychain 痕迹（与注册表 fail-closed 同口径）。
    if !known && !confirmed {
        return Err("新远程工作区注册未获用户确认，已取消".into());
    }
    remote_token_keychain::write(&id, token.trim())?;
    let result = remember_remote_flow(
        &path,
        &normalized,
        remote_repo_root.trim(),
        &name,
        &id,
        known,
        confirmed,
    );
    // 新远端落库失败 → 清掉刚写的 token，不留孤儿凭据。已注册的重注册不清（那条 token
    // 是刚更新的、且旧注册表条目仍在，删了会让在用条目失去凭据）。
    if result.is_err() && !known {
        let _ = remote_token_keychain::delete(&id);
    }
    result
}

/// 同一台常驻电脑上另一个端口的服务地址：只换端口，主机与协议不变（再过一遍网段校验）。
pub(crate) fn sibling_server_url(host_base: &str, port: u16) -> Result<String, String> {
    let base = validate_remote_server_url(host_base)?;
    let mut parsed = Url::parse(&base).map_err(|error| format!("常驻电脑地址无法解析：{error}"))?;
    if port < 1024 {
        return Err("常驻电脑上的项目端口必须在 1024 以上".into());
    }
    parsed
        .set_port(Some(port))
        .map_err(|_| "常驻电脑地址不能设置端口".to_string())?;
    validate_remote_server_url(parsed.as_str())
}

/// 登记常驻电脑上的一个项目运行时（「在哪做：常驻电脑」）。令牌**沿用常驻电脑那条登记**：
/// 在 Rust 内从钥匙串复制，不经过网页层；目的地只能是同一主机的另一个端口，网页层给不出别的主机。
/// 新登记仍要原生确认框（与 remember_remote_project 同口径，fail-closed）。
#[tauri::command]
pub(crate) async fn remember_resident_project(
    app: AppHandle,
    host_project_id: String,
    port: u16,
    remote_repo_root: String,
    name: String,
) -> Result<DesktopProjectProfile, String> {
    let path = project_registry_path(&app)?;
    let host_base = resolve_remote_base(&path, &host_project_id)?;
    let normalized = sibling_server_url(&host_base, port)?;
    let repo_root = remote_repo_root.trim();
    if !repo_root.starts_with('/') {
        return Err("常驻电脑上的项目目录必须是绝对路径".into());
    }
    let token = remote_token_keychain::read(&host_project_id)?
        .filter(|value| !value.trim().is_empty())
        .ok_or("常驻电脑的访问 token 不在钥匙串里，请重新连接常驻电脑")?;
    let id = remote_project_id(&normalized, repo_root);
    let known = load_project_registry(&path)?
        .projects
        .iter()
        .any(|project| project.id == id);
    let confirmed = if known {
        false
    } else {
        confirm_remote_registration(&app, &normalized).await?
    };
    if !known && !confirmed {
        return Err("新远程工作区注册未获用户确认，已取消".into());
    }
    remote_token_keychain::write(&id, token.trim())?;
    let result = remember_remote_flow(&path, &normalized, repo_root, &name, &id, known, confirmed);
    if result.is_err() && !known {
        let _ = remote_token_keychain::delete(&id);
    }
    result
}

/// 常驻电脑交付的分支名闸：只收 git 常见分支字符，挡掉选项注入（`-` 开头）与 refspec 花样（`..`、`@{`、`:`）。
pub(crate) fn valid_resident_branch(branch: &str) -> bool {
    !branch.is_empty()
        && branch.len() <= 200
        && branch
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '/' | '-'))
        && !branch.starts_with(['-', '/', '.'])
        && !branch.ends_with(['/', '.'])
        && !branch.ends_with(".lock")
        && !branch.contains("..")
        && !branch.contains("//")
}

/// 从登记信息拼出常驻电脑上那份 clone 的 SSH 地址：主机取自服务地址，目录取自登记的绝对路径。
pub(crate) fn resident_fetch_source(base_url: &str, repo_root: &str) -> Result<String, String> {
    let base = validate_remote_server_url(base_url)?;
    let parsed = Url::parse(&base).map_err(|error| format!("常驻电脑地址无法解析：{error}"))?;
    let host = parsed.host_str().ok_or("常驻电脑地址缺少主机名")?;
    let root = repo_root.trim_end_matches('/');
    let safe = root.starts_with('/')
        && root.len() > 1
        && !root.contains("..")
        && root
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '/' | '-'));
    if !safe {
        return Err("常驻电脑上的项目目录含有不支持的字符，无法通过 SSH 拉取".into());
    }
    let host = if host.contains(':') { format!("[{host}]") } else { host.to_string() };
    Ok(format!("ssh://{host}{root}"))
}

/// 把常驻电脑上交付的分支拉回本机同名项目（SSH）。常驻电脑没有 GitHub 推送权限：
/// 交付物由本机拉回、用本机的凭据开 PR。只快进或新建本地分支（refspec 不带 `+`），
/// 本地分支已分叉或正被检出时由 git 拒绝，不会覆盖你的提交。
#[tauri::command]
pub(crate) async fn fetch_resident_branch(
    app: AppHandle,
    resident_project_id: String,
    local_project_id: String,
    branch: String,
) -> Result<String, String> {
    if !valid_resident_branch(&branch) {
        return Err("分支名不合法，已拒绝拉取".into());
    }
    let path = project_registry_path(&app)?;
    let base = resolve_remote_base(&path, &resident_project_id)?;
    let registry = load_project_registry(&path)?;
    let resident = registry
        .projects
        .iter()
        .find(|item| item.id == resident_project_id)
        .ok_or("常驻电脑上的项目未登记")?;
    let local = registry
        .projects
        .iter()
        .find(|item| item.id == local_project_id && item.kind != PROJECT_KIND_REMOTE)
        .ok_or("本机项目未登记")?;
    let source = resident_fetch_source(&base, &resident.repo_root)?;
    let root = git_workspace_root(Path::new(&local.repo_root))?;
    let refspec = format!("refs/heads/{branch}:refs/heads/{branch}");
    tauri::async_runtime::spawn_blocking(move || {
        let output = Command::new("git")
            .args(["fetch", "--no-tags", "--", &source, &refspec])
            .current_dir(&root)
            // 不弹密码框、不无限等：GUI 里没有终端可以输入
            .env("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=10")
            .env("GIT_TERMINAL_PROMPT", "0")
            .output()
            .map_err(|error| format!("无法运行 git fetch：{error}"))?;
        if output.status.success() {
            Ok(format!("已拉到本机分支 {branch}"))
        } else {
            let detail = String::from_utf8_lossy(&output.stderr);
            Err(format!(
                "拉取失败：{}",
                detail.trim().chars().take(400).collect::<String>()
            ))
        }
    })
    .await
    .map_err(|error| format!("拉取任务失败：{error}"))?
}

// ── 远程传输层（R3a-2a）：HTTP 代理 ──────────────────────────────────────────
// 方案 A 的落点：**webview 从不提供目标 URL**，只给注册过的 project_id——由 Rust 查注册表取
// base_url、从 Keychain 取 token 并代注 Authorization。于是「只连注册过的 server_url」不再是
// 一道可被绕过的校验，而是结构上不可能（没有 URL 入参可供伪造）；token 原文也不进 webview。

pub(crate) const MAX_REMOTE_RESPONSE_BYTES: usize = 32 * 1024 * 1024;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RemoteHttpResponse {
    status: u16,
    ok: bool,
    body: String,
}

/// 远程请求路径闸：只收远端 gateway HTTP 面所在的 `/api/` 前缀。
/// 前缀白名单同时压缩「混淆代理人」可达面——webview 侧一旦被注入内容驱动，能借这条通道
/// 触达的远端路由被限死在 /api/ 内（副作用路由的确认门归 R3a-2c 接线时设计）。
pub(crate) fn sanitize_remote_path(path: &str) -> Result<String, String> {
    let trimmed = path.trim();
    if !trimmed.starts_with('/') {
        return Err("远程请求路径必须以 / 开头".into());
    }
    if trimmed.chars().any(|ch| ch.is_control() || ch == ' ') {
        return Err("远程请求路径不能包含空白或控制字符".into());
    }
    // 只在查询串之前查协议：`?next=https://…` 是合法查询值，整串 contains 会误杀。
    let before_query = trimmed.split('?').next().unwrap_or(trimmed);
    if before_query.contains("://") {
        return Err("远程请求路径不能包含协议".into());
    }
    if !trimmed.starts_with("/api/") {
        return Err("远程请求路径必须在 /api/ 下".into());
    }
    Ok(trimmed.to_string())
}

/// 两个 URL 是否同源（scheme/host/port 逐项相等）。
/// **单独抽出来是为了能被直接测**——它是 build_remote_url 的兜底防线，在当前路径闸下不可达，
/// 若只在 build_remote_url 里内联，任何针对它的测试都会先被路径闸拦下而成为安慰剂
/// （F-6 mutation 实测：删掉内联版判断，原测试全绿）。
pub(crate) fn same_remote_origin(target: &Url, registered: &Url) -> bool {
    target.scheme() == registered.scheme()
        && target.host_str() == registered.host_str()
        && target.port_or_known_default() == registered.port_or_known_default()
}

/// 拼远程 URL。终局不变量：拼出来的 scheme/host/port 必须与注册的服务器逐项相等。
/// 诚实说明：在当前路径闸（强制 `/api/` 前缀）下这条不变量**是不可达兜底**，留着是为了
/// 将来放宽路径闸或改用 `Url::join` 时仍有最后一道锁，不是当下的主防线。
pub(crate) fn build_remote_url(base: &str, path: &str) -> Result<Url, String> {
    let sanitized = sanitize_remote_path(path)?;
    let target = Url::parse(&format!("{base}{sanitized}"))
        .map_err(|error| format!("远程请求地址无法解析：{error}"))?;
    let registered =
        Url::parse(base).map_err(|error| format!("已注册的服务器地址无法解析：{error}"))?;
    // 归一化之后再查协议相对形状：`\` 在 WHATWG special scheme 下等价 `/`，故
    // `/api/\evil.com` 原始串不含 `//`、归一化后的 path 却是 `//evil.com`——改用
    // Url::join 时那就是换 host 的真洞。对归一化结果断言才真正钉住它。
    if target.path().starts_with("//") {
        return Err("远程请求路径归一化后成了协议相对地址，已拒绝".into());
    }
    if !same_remote_origin(&target, &registered) {
        return Err("远程请求目标与已注册服务器不一致，已拒绝".into());
    }
    Ok(target)
}

/// 按 project_id 解析出远程 base_url：必须已注册、必须是 remote 条目，且 base_url **每次都重新
/// 过网段校验**——projects.json 是磁盘文件可能被改，出网一律以当下校验为准（fail-closed）。
pub(crate) fn resolve_remote_base(registry_path: &Path, project_id: &str) -> Result<String, String> {
    let registry = load_project_registry(registry_path)?;
    let project = registry
        .projects
        .iter()
        .find(|item| item.id == project_id)
        .ok_or("该项目未在 Desktop 注册，已拒绝远程请求")?;
    if project.kind != PROJECT_KIND_REMOTE {
        return Err("该项目不是远程工作区，已拒绝远程请求".into());
    }
    let base = validate_remote_server_url(&project.base_url)?;
    // id ↔ (base_url, repo_root) 绑定回验。id 就是这两者的哈希，故改了 base_url 必然改 id。
    // 堵的是：能写 projects.json 的本地进程把已注册条目的 base_url 换成 https://attacker.tld
    // 而**保持 id 不变**，让 Keychain 里那把（按 id 取的）真 token 被发给攻击者——原生确认门
    // 只守注册路径，这里是运行期的第二道锁。注意 validate 对 https 放行任意 host，单靠它挡不住。
    if remote_project_id(&base, &project.repo_root) != project.id {
        return Err("远程工作区注册信息与其标识不一致（注册表可能被篡改），已拒绝".into());
    }
    Ok(base)
}

pub(crate) fn normalize_http_method(method: &str) -> Result<reqwest::Method, String> {
    match method.trim().to_ascii_uppercase().as_str() {
        "GET" => Ok(reqwest::Method::GET),
        "POST" => Ok(reqwest::Method::POST),
        "PUT" => Ok(reqwest::Method::PUT),
        "PATCH" => Ok(reqwest::Method::PATCH),
        "DELETE" => Ok(reqwest::Method::DELETE),
        other => Err(format!("远程请求不支持的方法：{other}")),
    }
}

#[tauri::command]
pub(crate) async fn remote_http_request(
    app: AppHandle,
    project_id: String,
    method: String,
    path: String,
    body: Option<String>,
) -> Result<RemoteHttpResponse, String> {
    let base = resolve_remote_base(&project_registry_path(&app)?, &project_id)?;
    let url = build_remote_url(&base, &path)?;
    let verb = normalize_http_method(&method)?;
    let token = remote_token_keychain::read(&project_id)?
        .filter(|value| !value.trim().is_empty())
        .ok_or("未找到该远程工作区的访问 token，请重新注册")?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        // 禁跟随重定向：一次 302 就能把带着 Authorization 的请求引去第三方 host。
        .redirect(reqwest::redirect::Policy::none())
        // **禁环境变量代理**：reqwest 默认 auto_sys_proxy，即便关了 system-proxy feature，
        // 底层仍会读 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY——那样真实 TCP 目的地由 connector 决定，
        // 上面所有 URL 层校验（含终局 host 不变量）全部形同虚设；且 http 目标走 forward 代理时，
        // 带 Authorization 的完整请求会**明文**交给代理主机。与本仓 child_env「不信任环境变量」同口径。
        .no_proxy()
        .build()
        .map_err(|error| format!("无法创建远程请求客户端：{error}"))?;
    let mut request = client.request(verb, url).bearer_auth(&token);
    if let Some(payload) = body {
        request = request
            .header("content-type", "application/json")
            .body(payload);
    }
    let mut response = request
        .send()
        .await
        .map_err(|error| format!("远程请求失败：{error}"))?;
    let status = response.status();
    // 响应体封顶：只有 30s 总 deadline 兜着的话，内网千兆下一个被攻陷/发疯的已注册 server
    // 就能把数 GB 灌进内存（再经 IPC 序列化成 JSON 还要放大几倍）→ 单请求打爆 Desktop。
    let mut body = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|error| format!("远程响应读取失败：{error}"))?
    {
        if body.len() + chunk.len() > MAX_REMOTE_RESPONSE_BYTES {
            return Err(format!(
                "远程响应超过 {} MB 上限，已中止",
                MAX_REMOTE_RESPONSE_BYTES / (1024 * 1024)
            ));
        }
        body.extend_from_slice(&chunk);
    }
    Ok(RemoteHttpResponse {
        status: status.as_u16(),
        ok: status.is_success(),
        body: String::from_utf8_lossy(&body).into_owned(),
    })
}

// ── 远程 WS 桥接（R3a-2b）────────────────────────────────────────────────────
// 为什么必须自建、不能用 tauri-plugin-websocket：那个插件的 connect(url, {headers}) 由 JS 传
// headers → token 必须先进 webview，直接违背 spec「webview 拿不到 token 原文」；且它的
// capability 无 URL scope，被注入的页面脚本能连任意 host。自建桥接后：**URL 由注册表决定、
// token 由 Rust 从 Keychain 取并注入握手头**，webview 只见 conn_id 与帧内容，与 HTTP 代理同构。

/// 会话 id 闸：sid 会进 WS 查询串，只收保守字符集（同 normalize_workspace_id 口径）。
pub(crate) fn normalize_ws_sid(value: &str) -> Result<String, String> {
    let trimmed = value.trim();
    if trimmed.is_empty()
        || trimmed.len() > 80
        || !trimmed
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err("远程会话标识无效".into());
    }
    Ok(trimmed.into())
}

/// 由**已注册的** base_url 推导 WS 地址：https→wss、http→ws，路径固定 /ws。
/// 手工拼 authority 而不用 Url::set_scheme，避开该 API 在 special scheme 间切换的边界行为。
pub(crate) fn remote_ws_url(base: &str, sid: &str) -> Result<Url, String> {
    let sid = normalize_ws_sid(sid)?;
    let parsed = Url::parse(base).map_err(|error| format!("已注册的服务器地址无法解析：{error}"))?;
    let scheme = match parsed.scheme() {
        "https" => "wss",
        "http" => "ws",
        other => return Err(format!("不支持的服务器协议：{other}")),
    };
    let host = parsed.host_str().ok_or("已注册的服务器地址缺少主机名")?;
    let mut authority = host.to_string();
    if let Some(port) = parsed.port() {
        authority.push_str(&format!(":{port}"));
    }
    Url::parse(&format!("{scheme}://{authority}/ws?sid={sid}"))
        .map_err(|error| format!("远程 WebSocket 地址无法构造：{error}"))
}

pub(crate) const MAX_REMOTE_WS_CONNECTIONS: usize = 8;
pub(crate) const REMOTE_WS_CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
pub(crate) const REMOTE_WS_SEND_QUEUE: usize = 256;
// 与本地路径（gateway.ts 显式设的 16/32 MiB）及 HTTP 侧 32MB 封顶同口径——
// tungstenite 默认 64 MiB 比两者都松，不能就这么用。
pub(crate) const MAX_REMOTE_WS_MESSAGE_BYTES: usize = 32 * 1024 * 1024;
pub(crate) const MAX_REMOTE_WS_FRAME_BYTES: usize = 16 * 1024 * 1024;

pub(crate) struct RemoteWsHandle {
    sender: tokio::sync::mpsc::Sender<String>,
    // 读侧必须留 abort 句柄：stream.split() 的两半共用 BiLock，**只 drop 写半边不会释放
    // socket**；对端不回 Close 也不 EOF 时读任务永久挂起，没句柄就再也拆不掉它。
    reader: Option<tauri::async_runtime::JoinHandle<()>>,
}

#[derive(Default)]
pub(crate) struct RemoteWsRegistry {
    connections: Mutex<HashMap<String, RemoteWsHandle>>,
    counter: Mutex<u64>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RemoteWsFrame {
    conn_id: String,
    data: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RemoteWsClosed {
    conn_id: String,
    reason: String,
}

/// 底层握手错误**不能原样回给 webview**：tungstenite 会把请求头值的 Debug 写进错误串，
/// token 就这么泄出去（F-6 实测可 100% 还原）。这里只回粗粒度原因，明细进应用日志。
pub(crate) fn remote_ws_failure_reason(error: &tokio_tungstenite::tungstenite::Error) -> String {
    use tokio_tungstenite::tungstenite::Error;
    match error {
        Error::Http(response) => {
            format!("远程 WebSocket 连接失败：服务器返回 HTTP {}", response.status())
        }
        Error::Io(_) => "远程 WebSocket 连接失败：网络不可达或被拒绝".into(),
        Error::Url(_) => "远程 WebSocket 连接失败：地址无效".into(),
        Error::Protocol(_) => "远程 WebSocket 连接失败：协议握手失败".into(),
        _ => "远程 WebSocket 连接失败（详情见应用日志）".into(),
    }
}

/// 收尾：摘表 + 通报，**恰好一次**——谁先摘掉表项谁负责发事件，避免重复通报或漏报。
/// 返回被摘下的句柄交调用方决定是否 abort（读任务自己收尾时不该 abort 自己）。
pub(crate) fn finish_remote_ws(app: &AppHandle, conn_id: &str, reason: &str) -> Option<RemoteWsHandle> {
    use tauri::Emitter;
    let removed = app.try_state::<RemoteWsRegistry>().and_then(|state| {
        state
            .connections
            .lock()
            .ok()
            .and_then(|mut connections| connections.remove(conn_id))
    });
    if removed.is_some() {
        let _ = app.emit(
            "remote-ws-closed",
            RemoteWsClosed {
                conn_id: conn_id.to_string(),
                reason: reason.to_string(),
            },
        );
    }
    removed
}

#[tauri::command]
pub(crate) async fn remote_ws_connect(
    app: AppHandle,
    registry: State<'_, RemoteWsRegistry>,
    project_id: String,
    sid: String,
) -> Result<String, String> {
    use futures_util::{SinkExt, StreamExt};
    use tauri::Emitter;
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    use tokio_tungstenite::tungstenite::http::HeaderValue;
    use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
    use tokio_tungstenite::tungstenite::Message;

    // 连接配额：webview 一个循环就能把 fd/内存耗尽，闸要设在建连之前。
    {
        let connections = registry
            .connections
            .lock()
            .map_err(|_| "远程连接注册表已中毒".to_string())?;
        if connections.len() >= MAX_REMOTE_WS_CONNECTIONS {
            return Err(format!(
                "远程连接数已达上限（{MAX_REMOTE_WS_CONNECTIONS}），请先关闭不用的连接"
            ));
        }
    }

    // 与 HTTP 代理同一条锁链：已注册 + remote 条目 + 重过网段校验 + id↔base_url 绑定回验。
    let base = resolve_remote_base(&project_registry_path(&app)?, &project_id)?;
    let url = remote_ws_url(&base, &sid)?;
    let token = remote_token_keychain::read(&project_id)?
        .filter(|value| !value.trim().is_empty())
        .ok_or("未找到该远程工作区的访问 token，请重新注册")?;

    let mut request = url
        .as_str()
        .into_client_request()
        .map_err(|_| "远程 WebSocket 请求无法构造".to_string())?;
    let mut auth = HeaderValue::from_str(&format!("Bearer {token}"))
        .map_err(|_| "远程 token 含非法字符，无法作为请求头".to_string())?;
    // **必须置敏**：否则头值的 Debug 会被 tungstenite 写进握手错误串并回到 webview。
    auth.set_sensitive(true);
    request.headers_mut().insert("authorization", auth);

    let config = WebSocketConfig::default()
        .max_message_size(Some(MAX_REMOTE_WS_MESSAGE_BYTES))
        .max_frame_size(Some(MAX_REMOTE_WS_FRAME_BYTES));
    let connected = tokio::time::timeout(
        REMOTE_WS_CONNECT_TIMEOUT,
        tokio_tungstenite::connect_async_with_config(request, Some(config), false),
    )
    .await
    .map_err(|_| "远程 WebSocket 连接超时".to_string())?;
    let (stream, _response) = connected.map_err(|error| {
        eprintln!("[remote-ws] connect failed: {error}");
        remote_ws_failure_reason(&error)
    })?;
    let (mut sink, mut source) = stream.split();

    let conn_id = {
        let mut counter = registry
            .counter
            .lock()
            .map_err(|_| "远程连接注册表已中毒".to_string())?;
        *counter += 1;
        format!("{project_id}-{counter}")
    };
    let (tx, mut rx) = tokio::sync::mpsc::channel::<String>(REMOTE_WS_SEND_QUEUE);
    // 先登记再起读任务：否则读侧可能在登记前就自行收尾，留下永不清理的僵尸表项。
    registry
        .connections
        .lock()
        .map_err(|_| "远程连接注册表已中毒".to_string())?
        .insert(
            conn_id.clone(),
            RemoteWsHandle {
                sender: tx,
                reader: None,
            },
        );

    tauri::async_runtime::spawn(async move {
        while let Some(text) = rx.recv().await {
            if sink.send(Message::Text(text.into())).await.is_err() {
                break;
            }
        }
        let _ = sink.close().await;
    });

    let reader_app = app.clone();
    let reader_id = conn_id.clone();
    let reader = tauri::async_runtime::spawn(async move {
        let mut reason = "closed";
        while let Some(message) = source.next().await {
            match message {
                Ok(Message::Text(text)) => {
                    let _ = reader_app.emit(
                        "remote-ws-message",
                        RemoteWsFrame {
                            conn_id: reader_id.clone(),
                            data: text.to_string(),
                        },
                    );
                }
                Ok(Message::Close(_)) => break,
                Ok(_) => {} // 二进制/ping/pong：gateway 协议是文本 JSON，忽略
                Err(_) => {
                    // 不外泄底层 error 串——它可能带帧载荷原文。
                    reason = "error";
                    break;
                }
            }
        }
        finish_remote_ws(&reader_app, &reader_id, reason);
    });

    // 把读句柄补进表项；若读侧已自行收尾摘表，则直接 abort 这个（已结束的）句柄。
    match registry.connections.lock() {
        Ok(mut connections) => match connections.get_mut(&conn_id) {
            Some(entry) => entry.reader = Some(reader),
            None => reader.abort(),
        },
        Err(_) => reader.abort(),
    }
    Ok(conn_id)
}

#[tauri::command]
pub(crate) fn remote_ws_send(
    registry: State<'_, RemoteWsRegistry>,
    conn_id: String,
    data: String,
) -> Result<(), String> {
    use tokio::sync::mpsc::error::TrySendError;
    let connections = registry
        .connections
        .lock()
        .map_err(|_| "远程连接注册表已中毒".to_string())?;
    let handle = connections
        .get(&conn_id)
        .ok_or("该远程连接不存在或已关闭")?;
    handle.sender.try_send(data).map_err(|error| match error {
        TrySendError::Full(_) => "远程连接发送队列已满，请稍后重试".to_string(),
        TrySendError::Closed(_) => "远程连接已关闭，发送失败".to_string(),
    })
}

#[tauri::command]
pub(crate) fn remote_ws_close(app: AppHandle, conn_id: String) -> Result<(), String> {
    // 摘表并通报；**abort 读任务**才真正释放 socket（只摘 sender 拆不掉，见 RemoteWsHandle 注释）。
    if let Some(handle) = finish_remote_ws(&app, &conn_id, "closed-by-client") {
        if let Some(reader) = handle.reader {
            reader.abort();
        }
    }
    Ok(())
}
