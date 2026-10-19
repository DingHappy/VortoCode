// VortoCode 账号登录：OAuth 2.0 授权码 + PKCE，按 RFC 8252 走系统浏览器 + 本机回环回调。
// - 密码只在账号网站上输入，Desktop 不经手；
// - 刷新令牌存 macOS Keychain，访问令牌（15 分钟）只在内存里，二者都不进 webview；
// - 刷新令牌每次轮换、旧的重放会被服务端判为泄露并吊销整条登录链，所以刷新必须串行（AccountState 的锁）。
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};
use tauri::{AppHandle, State};

use crate::platform_keychain;
use crate::{DesktopAccount, DesktopLlmProfileStatus, DesktopLlmProfileStore};

pub const DEFAULT_ACCOUNT_URL: &str = "https://account.vortotech.com";
/// 模型配置里标记「这把 Key 是 VortoCode 会员给的」：退出账号时一并清掉。
pub const MEMBER_KEY_SOURCE: &str = "vortocode_member";
const CLIENT_ID: &str = "vortocode-desktop";
const KEYCHAIN_SERVICE: &[u8] = b"com.vortocode.desktop.account";
const KEYCHAIN_ACCOUNT: &[u8] = b"session";
const LOGIN_TIMEOUT: Duration = Duration::from_secs(300);
// 访问令牌提前一分钟当作过期，免得请求在路上过期
const ACCESS_MARGIN: Duration = Duration::from_secs(60);

#[derive(Default)]
pub struct AccountState {
    access: tokio::sync::Mutex<Option<Access>>,
    login_busy: AtomicBool,
    login_cancel: AtomicBool,
}

struct Access {
    token: String,
    valid_until: Instant,
}

/// Keychain 里存的一条登录：带上签发它的账号服务地址，地址变了就不拿它去别处用。
#[derive(Serialize, Deserialize, Clone)]
struct StoredSession {
    base_url: String,
    refresh_token: String,
    #[serde(default)]
    email: String,
    #[serde(default)]
    device_name: String,
}

#[derive(Serialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AccountStatus {
    signed_in: bool,
    email: String,
    device_name: String,
    /// 连不上账号服务：显示上次已知的账号，不当作退出
    offline: bool,
    manage_url: String,
    /// 登录失效等需要告诉用户的话
    note: String,
}

impl AccountStatus {
    fn signed_out(base: &str, note: &str) -> Self {
        AccountStatus {
            signed_in: false,
            email: String::new(),
            device_name: String::new(),
            offline: false,
            manage_url: format!("{base}/account"),
            note: note.to_string(),
        }
    }
    fn from_session(base: &str, session: &StoredSession, offline: bool) -> Self {
        AccountStatus {
            signed_in: true,
            email: session.email.clone(),
            device_name: session.device_name.clone(),
            offline,
            manage_url: format!("{base}/account"),
            note: String::new(),
        }
    }
}

enum AuthError {
    /// 服务端明确拒绝（令牌失效、被吊销）：本地登录要清掉
    Rejected(String),
    /// 网络等临时问题：保留登录，稍后再试
    Network(String),
}

impl AuthError {
    fn message(&self) -> String {
        match self {
            AuthError::Rejected(message) | AuthError::Network(message) => message.clone(),
        }
    }
}

/// 账号服务地址：默认线上；开发时可用 VORTOCODE_ACCOUNT_URL 指到本机。只接受 https 或本机回环 http。
pub fn account_base_url(override_value: Option<&str>) -> Result<String, String> {
    let Some(raw) = override_value
        .map(str::trim)
        .filter(|value| !value.is_empty())
    else {
        return Ok(DEFAULT_ACCOUNT_URL.to_string());
    };
    let url = url::Url::parse(raw).map_err(|_| "VORTOCODE_ACCOUNT_URL 不是合法地址".to_string())?;
    let loopback = matches!(url.host_str(), Some("127.0.0.1") | Some("localhost"));
    if url.scheme() == "https" || (url.scheme() == "http" && loopback) {
        Ok(raw.trim_end_matches('/').to_string())
    } else {
        Err("VORTOCODE_ACCOUNT_URL 只能是 https 地址或本机 http 地址".into())
    }
}

fn base_url() -> Result<String, String> {
    account_base_url(std::env::var("VORTOCODE_ACCOUNT_URL").ok().as_deref())
}

pub fn base64url(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let n = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        let symbols = chunk.len() + 1;
        for index in 0..symbols {
            out.push(TABLE[((n >> (18 - 6 * index)) & 63) as usize] as char);
        }
    }
    out
}

pub fn pkce_challenge(verifier: &str) -> String {
    base64url(&Sha256::digest(verifier.as_bytes()))
}

fn random_token(bytes: usize) -> Result<String, String> {
    let mut buffer = vec![0_u8; bytes];
    std::fs::File::open("/dev/urandom")
        .and_then(|mut source| source.read_exact(&mut buffer))
        .map_err(|error| format!("无法生成随机数：{error}"))?;
    Ok(base64url(&buffer))
}

pub fn authorize_url(
    base: &str,
    redirect_uri: &str,
    challenge: &str,
    state: &str,
    device_name: &str,
) -> String {
    let mut url = url::Url::parse(&format!("{base}/authorize")).expect("base url 已校验");
    url.query_pairs_mut()
        .append_pair("client_id", CLIENT_ID)
        .append_pair("redirect_uri", redirect_uri)
        .append_pair("code_challenge", challenge)
        .append_pair("code_challenge_method", "S256")
        .append_pair("state", state)
        .append_pair("device_name", device_name);
    url.to_string()
}

#[derive(Debug, PartialEq)]
pub enum Callback {
    Code(String),
    Denied,
    /// 不是这次登录的回调（路径不对、state 不符）：回 404，继续等
    Ignore,
}

/// 解析回调请求行 `GET /callback?code=…&state=… HTTP/1.1`。state 不符一律忽略，防别的网页伪造回调。
pub fn parse_callback(request_line: &str, expected_state: &str) -> Callback {
    let mut parts = request_line.split_whitespace();
    let (Some("GET"), Some(target)) = (parts.next(), parts.next()) else {
        return Callback::Ignore;
    };
    let Ok(url) = url::Url::parse(&format!("http://127.0.0.1{target}")) else {
        return Callback::Ignore;
    };
    if url.path() != "/callback" {
        return Callback::Ignore;
    }
    let query: std::collections::HashMap<String, String> = url.query_pairs().into_owned().collect();
    if query.get("state").map(String::as_str) != Some(expected_state) {
        return Callback::Ignore;
    }
    if query.contains_key("error") {
        return Callback::Denied;
    }
    match query.get("code") {
        Some(code) if !code.is_empty() => Callback::Code(code.clone()),
        _ => Callback::Ignore,
    }
}

const CALLBACK_PAGE: &str = "<!doctype html><meta charset=utf-8><title>VortoCode</title>\
<body style=\"font:16px -apple-system,sans-serif;display:flex;align-items:center;justify-content:center;height:90vh\">\
<p>{message}可以关掉这个页面，回到 VortoCode。</p>";

fn respond(stream: &mut TcpStream, status: &str, message: &str) {
    let body = CALLBACK_PAGE.replace("{message}", message);
    let _ = write!(
        stream,
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
}

/// 在回环端口上等这次登录的回调；超时或被取消就放弃。
pub fn wait_for_callback(
    listener: TcpListener,
    expected_state: &str,
    timeout: Duration,
    cancelled: &AtomicBool,
) -> Result<String, String> {
    listener
        .set_nonblocking(true)
        .map_err(|error| error.to_string())?;
    let deadline = Instant::now() + timeout;
    loop {
        if cancelled.load(Ordering::SeqCst) {
            return Err("已取消登录".into());
        }
        if Instant::now() >= deadline {
            return Err("等了 5 分钟没有在浏览器里完成登录，请重新点「登录」".into());
        }
        let mut stream = match listener.accept() {
            Ok((stream, _)) => stream,
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(150));
                continue;
            }
            Err(error) => return Err(format!("登录回调出错：{error}")),
        };
        let _ = stream.set_nonblocking(false);
        let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
        let mut buffer = [0_u8; 8192];
        let read = stream.read(&mut buffer).unwrap_or(0);
        let request = String::from_utf8_lossy(&buffer[..read]);
        match parse_callback(request.lines().next().unwrap_or(""), expected_state) {
            Callback::Code(code) => {
                respond(&mut stream, "200 OK", "登录成功，");
                return Ok(code);
            }
            Callback::Denied => {
                respond(&mut stream, "200 OK", "已取消登录，");
                return Err("你在浏览器里取消了登录".into());
            }
            Callback::Ignore => respond(&mut stream, "404 Not Found", ""),
        }
    }
}

fn device_name() -> String {
    let name = std::process::Command::new("scutil")
        .args(["--get", "ComputerName"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .unwrap_or_default();
    let name: String = name.chars().filter(|c| !c.is_control()).take(60).collect();
    if name.is_empty() {
        "Mac".into()
    } else {
        name
    }
}

fn platform() -> String {
    format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH)
}

fn http_client(version: &str) -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .user_agent(format!("VortoCode-Desktop/{version}"))
        .build()
        .map_err(|error| error.to_string())
}

fn http(app: &AppHandle) -> Result<reqwest::Client, String> {
    http_client(&app.package_info().version.to_string())
}

fn load_session() -> Result<Option<StoredSession>, String> {
    let Some(raw) = platform_keychain::read(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)? else {
        return Ok(None);
    };
    Ok(serde_json::from_str(&raw).ok())
}

fn save_session(session: &StoredSession) -> Result<(), String> {
    let raw = serde_json::to_string(session).map_err(|error| error.to_string())?;
    platform_keychain::write(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, &raw)
}

fn clear_session() -> Result<(), String> {
    platform_keychain::delete(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    refresh_token: String,
    expires_in: u64,
}

async fn token_request(
    client: &reqwest::Client,
    base: &str,
    form: &[(&str, &str)],
) -> Result<TokenResponse, AuthError> {
    let response = client
        .post(format!("{base}/oauth/token"))
        .form(form)
        .send()
        .await
        .map_err(|_| AuthError::Network("连不上账号服务，稍后再试".into()))?;
    let status = response.status();
    if status.is_success() {
        return response
            .json::<TokenResponse>()
            .await
            .map_err(|_| AuthError::Network("账号服务的响应无法解析".into()));
    }
    let body: serde_json::Value = response.json().await.unwrap_or_default();
    let message = body["error_description"]
        .as_str()
        .unwrap_or("账号服务拒绝了请求")
        .to_string();
    if status.as_u16() == 400 || status.as_u16() == 401 {
        Err(AuthError::Rejected(message))
    } else {
        Err(AuthError::Network(message))
    }
}

/// 拿一个有效的访问令牌：内存里的没过期就用，否则用 Keychain 里的刷新令牌换（换完立刻落盘新刷新令牌）。
async fn ensure_access(
    app: &AppHandle,
    base: &str,
    slot: &mut Option<Access>,
    session: &mut StoredSession,
) -> Result<String, AuthError> {
    if let Some(access) = slot.as_ref() {
        if Instant::now() + ACCESS_MARGIN < access.valid_until {
            return Ok(access.token.clone());
        }
    }
    let version = app.package_info().version.to_string();
    let client = http(app).map_err(AuthError::Network)?;
    let pair = token_request(
        &client,
        base,
        &[
            ("grant_type", "refresh_token"),
            ("client_id", CLIENT_ID),
            ("refresh_token", &session.refresh_token),
            ("app_version", &version),
        ],
    )
    .await?;
    session.refresh_token = pair.refresh_token;
    save_session(session).map_err(AuthError::Network)?;
    *slot = Some(Access {
        token: pair.access_token.clone(),
        valid_until: Instant::now() + Duration::from_secs(pair.expires_in),
    });
    Ok(pair.access_token)
}

async fn fetch_me(
    client: &reqwest::Client,
    base: &str,
    access: &str,
) -> Result<(String, String), AuthError> {
    let response = client
        .get(format!("{base}/api/me"))
        .bearer_auth(access)
        .send()
        .await
        .map_err(|_| AuthError::Network("连不上账号服务".into()))?;
    if response.status().as_u16() == 401 {
        return Err(AuthError::Rejected("登录已失效，请重新登录".into()));
    }
    let body: serde_json::Value = response
        .json()
        .await
        .map_err(|_| AuthError::Network("账号服务的响应无法解析".into()))?;
    Ok((
        body["email"].as_str().unwrap_or_default().to_string(),
        body["device"]["name"]
            .as_str()
            .unwrap_or_default()
            .to_string(),
    ))
}

#[tauri::command]
pub async fn account_status(
    app: AppHandle,
    state: State<'_, AccountState>,
) -> Result<AccountStatus, String> {
    let base = base_url()?;
    let mut slot = state.access.lock().await;
    let Some(mut session) = load_session()? else {
        return Ok(AccountStatus::signed_out(&base, ""));
    };
    if session.base_url != base {
        return Ok(AccountStatus::signed_out(
            &base,
            "账号服务地址变了，请重新登录",
        ));
    }
    let result = match ensure_access(&app, &base, &mut slot, &mut session).await {
        Ok(access) => match http(&app) {
            Ok(client) => fetch_me(&client, &base, &access).await,
            Err(error) => Err(AuthError::Network(error)),
        },
        Err(error) => Err(error),
    };
    match result {
        Ok((email, device)) => {
            if email != session.email || device != session.device_name {
                session.email = email;
                session.device_name = device;
                let _ = save_session(&session);
            }
            Ok(AccountStatus::from_session(&base, &session, false))
        }
        Err(AuthError::Network(_)) => Ok(AccountStatus::from_session(&base, &session, true)),
        Err(AuthError::Rejected(_)) => {
            *slot = None;
            clear_session()?;
            Ok(AccountStatus::signed_out(
                &base,
                "登录已失效（可能在网页上退出了这台设备），请重新登录",
            ))
        }
    }
}

#[tauri::command]
pub async fn account_login(
    app: AppHandle,
    state: State<'_, AccountState>,
) -> Result<AccountStatus, String> {
    if state.login_busy.swap(true, Ordering::SeqCst) {
        return Err("正在登录中：请在浏览器里完成，或先取消".into());
    }
    state.login_cancel.store(false, Ordering::SeqCst);
    let result = login_flow(&app, &state).await;
    state.login_busy.store(false, Ordering::SeqCst);
    result
}

async fn login_flow(
    app: &AppHandle,
    state: &State<'_, AccountState>,
) -> Result<AccountStatus, String> {
    let base = base_url()?;
    let verifier = random_token(32)?;
    let challenge = pkce_challenge(&verifier);
    let login_state = random_token(16)?;
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|error| format!("无法在本机开登录回调：{error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| error.to_string())?
        .port();
    let redirect_uri = format!("http://127.0.0.1:{port}/callback");
    let device = device_name();
    tauri_plugin_opener::open_url(
        authorize_url(&base, &redirect_uri, &challenge, &login_state, &device),
        None::<&str>,
    )
    .map_err(|error| format!("无法打开浏览器：{error}"))?;

    let watcher = app.clone();
    let expected = login_state.clone();
    let code = tauri::async_runtime::spawn_blocking(move || {
        let state = tauri::Manager::state::<AccountState>(&watcher);
        wait_for_callback(listener, &expected, LOGIN_TIMEOUT, &state.login_cancel)
    })
    .await
    .map_err(|error| error.to_string())??;

    let version = app.package_info().version.to_string();
    let platform = platform();
    let client = http(app)?;
    let pair = token_request(
        &client,
        &base,
        &[
            ("grant_type", "authorization_code"),
            ("client_id", CLIENT_ID),
            ("code", &code),
            ("code_verifier", &verifier),
            ("redirect_uri", &redirect_uri),
            ("platform", &platform),
            ("app_version", &version),
        ],
    )
    .await
    .map_err(|error| error.message())?;

    let mut slot = state.access.lock().await;
    let mut session = StoredSession {
        base_url: base.clone(),
        refresh_token: pair.refresh_token,
        email: String::new(),
        device_name: device,
    };
    if let Ok((email, device)) = fetch_me(&client, &base, &pair.access_token).await {
        session.email = email;
        if !device.is_empty() {
            session.device_name = device;
        }
    }
    save_session(&session)?;
    *slot = Some(Access {
        token: pair.access_token,
        valid_until: Instant::now() + Duration::from_secs(pair.expires_in),
    });
    Ok(AccountStatus::from_session(&base, &session, false))
}

#[tauri::command]
pub fn account_login_cancel(state: State<'_, AccountState>) {
    state.login_cancel.store(true, Ordering::SeqCst);
}

/// 退出：先请服务端吊销这台设备（失败也继续），再删本机 Keychain 里的登录。
#[tauri::command]
pub async fn account_logout(
    app: AppHandle,
    state: State<'_, AccountState>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<AccountStatus, String> {
    let base = base_url()?;
    let mut slot = state.access.lock().await;
    if let Some(mut session) = load_session()?.filter(|session| session.base_url == base) {
        if let Ok(access) = ensure_access(&app, &base, &mut slot, &mut session).await {
            if let Ok(client) = http(&app) {
                let _ = client
                    .post(format!("{base}/api/logout"))
                    .bearer_auth(access)
                    .send()
                    .await;
            }
        }
    }
    *slot = None;
    clear_session()?;
    // 退出账号时，账号给的会员 Key 也不该留在本机
    crate::remove_member_provider(&app, &store)?;
    if member_key_in_use(&app, &store) {
        crate::sign_out_default_service(&app, &store)?;
    }
    Ok(AccountStatus::signed_out(&base, ""))
}

#[derive(Deserialize, Default)]
struct ModelAccess {
    #[serde(default)]
    available: bool,
    #[serde(default)]
    member: bool,
    #[serde(default)]
    plan_name: String,
    #[serde(default)]
    expires_at: i64,
    #[serde(default)]
    credits_available: f64,
    #[serde(default)]
    credits_per_period: f64,
    #[serde(default)]
    renewed_until: i64,
    #[serde(default)]
    key_enabled: bool,
    base_url: Option<String>,
    api_key: Option<String>,
}

/// 给界面看的会员状态：不含 Key。
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Membership {
    available: bool,
    member: bool,
    plan_name: String,
    expires_at: i64,
    credits_available: f64,
    /// 每期总额；0 = 不知道（不做「额度不足」提醒）
    credits_per_period: f64,
    /// 已续费到哪天（排在当前之后的套餐的最晚到期时间）；0 = 没有续费
    renewed_until: i64,
    key_enabled: bool,
    /// 当前默认模型服务用的就是会员 Key
    in_use: bool,
    /// 会员模型作为供应商加在模型列表里（默认服务是别的，比如本机模型）
    listed: bool,
}

async fn fetch_model_access(client: &reqwest::Client, base: &str, access: &str) -> Result<ModelAccess, AuthError> {
    let response = client
        .get(format!("{base}/api/model-access"))
        .bearer_auth(access)
        .send()
        .await
        .map_err(|_| AuthError::Network("连不上账号服务".into()))?;
    let status = response.status().as_u16();
    let body: serde_json::Value = response.json().await.unwrap_or_default();
    match status {
        200 => serde_json::from_value(body).map_err(|_| AuthError::Network("账号服务的响应无法解析".into())),
        401 => Err(AuthError::Rejected("登录已失效，请重新登录".into())),
        _ => Err(AuthError::Network(body["error"].as_str().unwrap_or("暂时读不到会员信息").to_string())),
    }
}

/// 会员 Key 只接受固定的中转站地址：账号服务即使被冒充，也不能把 Desktop 引到别的模型服务上。
pub fn member_base_url_ok(base_url: &str) -> bool {
    base_url.trim_end_matches('/') == format!("{}/v1", crate::RELAY_ORIGIN)
}

async fn signed_in_access(
    app: &AppHandle,
    state: &State<'_, AccountState>,
) -> Result<(String, StoredSession, ModelAccess), String> {
    let base = base_url()?;
    let mut slot = state.access.lock().await;
    let Some(mut session) = load_session()?.filter(|session| session.base_url == base) else {
        return Err("还没有登录 VortoCode 账号".into());
    };
    let access = ensure_access(app, &base, &mut slot, &mut session).await.map_err(|error| error.message())?;
    let client = http(app)?;
    let model_access = fetch_model_access(&client, &base, &access).await.map_err(|error| error.message())?;
    Ok((base, session, model_access))
}

fn member_key_in_use(app: &AppHandle, store: &State<'_, DesktopLlmProfileStore>) -> bool {
    crate::cached_desktop_llm_profile(app, store)
        .ok()
        .flatten()
        .and_then(|profile| profile.account)
        .is_some_and(|account| account.key_source == MEMBER_KEY_SOURCE)
}

#[tauri::command]
pub async fn account_membership(
    app: AppHandle,
    state: State<'_, AccountState>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<Membership, String> {
    let (_, _, access) = signed_in_access(&app, &state).await?;
    // 会员 Key 换过（比如在中转站轮换）时，悄悄更新列表里那一份，免得切过去才发现用不了
    let listed_key = crate::member_provider_key(&app, &store);
    if let (Some(saved), Some(fresh), Some(base)) = (&listed_key, &access.api_key, &access.base_url) {
        if access.member && member_base_url_ok(base) && saved.trim_start_matches("sk-") != fresh.trim_start_matches("sk-") {
            let _ = crate::upsert_member_provider(&app, &store, fresh).await;
        }
    }
    Ok(Membership {
        available: access.available,
        member: access.member,
        plan_name: access.plan_name,
        expires_at: access.expires_at,
        credits_available: access.credits_available,
        credits_per_period: access.credits_per_period,
        renewed_until: access.renewed_until,
        key_enabled: access.key_enabled,
        in_use: member_key_in_use(&app, &store),
        listed: listed_key.is_some(),
    })
}

/// 把会员的中转站 Key 设为默认模型服务（Key 只在 Rust 和配置文件里，不回给界面）。
#[tauri::command]
pub async fn account_use_membership(
    app: AppHandle,
    state: State<'_, AccountState>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    let (_, session, access) = signed_in_access(&app, &state).await?;
    if !access.member {
        return Err("这个账号还不是会员".into());
    }
    let (Some(base_url), Some(key)) = (access.base_url, access.api_key) else {
        return Err("会员的模型 Key 已停用：在中转站套餐页启用后再试".into());
    };
    if !member_base_url_ok(&base_url) {
        return Err("账号服务给的模型地址不对，已拒绝使用".into());
    }
    let account = DesktopAccount {
        username: session.email.clone(),
        display_name: session.email,
        key_source: MEMBER_KEY_SOURCE.into(),
        plan_name: Some(access.plan_name).filter(|name| !name.is_empty()),
        plan_expiry: Some(access.expires_at).filter(|expiry| *expiry > 0),
    };
    // 已经配了别的默认模型（本机模型、自己的 Key）：加进模型列表，不替换它，输入框里两边切换
    let keeps_default = crate::cached_desktop_llm_profile(&app, &store)?
        .is_some_and(|profile| !profile.signed_out && !profile.model.trim().is_empty()
            && profile.account.as_ref().map_or(true, |account| account.key_source != MEMBER_KEY_SOURCE));
    if keeps_default {
        return crate::upsert_member_provider(&app, &store, &key).await;
    }
    let profile = crate::save_relay_plan_profile(&app, &store, &key, account).await?;
    Ok(crate::with_config_path(crate::desktop_llm_profile_status(Some(&profile)), &app))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pkce_matches_rfc7636_example() {
        assert_eq!(
            pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
        assert_eq!(base64url(b"f"), "Zg");
        assert_eq!(base64url(b"fo"), "Zm8");
        assert_eq!(base64url(b"foo"), "Zm9v");
        assert_eq!(base64url(&[0xfb, 0xff]), "-_8");
        assert_eq!(random_token(32).unwrap().len(), 43);
    }

    #[test]
    fn account_url_defaults_to_production_and_rejects_plain_http_elsewhere() {
        assert_eq!(account_base_url(None).unwrap(), DEFAULT_ACCOUNT_URL);
        assert_eq!(account_base_url(Some(" ")).unwrap(), DEFAULT_ACCOUNT_URL);
        assert_eq!(
            account_base_url(Some("http://127.0.0.1:8700/")).unwrap(),
            "http://127.0.0.1:8700"
        );
        assert!(account_base_url(Some("http://account.example.com")).is_err());
        assert!(account_base_url(Some("file:///etc/passwd")).is_err());
    }

    #[test]
    fn authorize_url_carries_pkce_and_loopback_redirect() {
        let url = url::Url::parse(&authorize_url(
            "https://account.example.com",
            "http://127.0.0.1:5000/callback",
            "chal",
            "st",
            "我的 Mac",
        ))
        .unwrap();
        let query: std::collections::HashMap<_, _> = url.query_pairs().into_owned().collect();
        assert_eq!(url.path(), "/authorize");
        assert_eq!(query["client_id"], "vortocode-desktop");
        assert_eq!(query["code_challenge_method"], "S256");
        assert_eq!(query["redirect_uri"], "http://127.0.0.1:5000/callback");
        assert_eq!(query["device_name"], "我的 Mac");
    }

    #[test]
    fn member_key_only_goes_to_the_fixed_relay() {
        assert!(member_base_url_ok(&format!("{}/v1", crate::RELAY_ORIGIN)));
        assert!(member_base_url_ok(&format!("{}/v1/", crate::RELAY_ORIGIN)));
        assert!(!member_base_url_ok("https://evil.example.com/v1"));
        assert!(!member_base_url_ok(crate::RELAY_ORIGIN));
    }

    #[test]
    fn callback_requires_matching_state() {
        assert_eq!(
            parse_callback("GET /callback?code=abc&state=s1 HTTP/1.1", "s1"),
            Callback::Code("abc".into())
        );
        assert_eq!(
            parse_callback("GET /callback?code=abc&state=evil HTTP/1.1", "s1"),
            Callback::Ignore
        );
        assert_eq!(
            parse_callback("GET /callback?code=abc HTTP/1.1", "s1"),
            Callback::Ignore
        );
        assert_eq!(
            parse_callback("GET /favicon.ico HTTP/1.1", "s1"),
            Callback::Ignore
        );
        assert_eq!(
            parse_callback("POST /callback?code=abc&state=s1 HTTP/1.1", "s1"),
            Callback::Ignore
        );
        assert_eq!(
            parse_callback("GET /callback?error=access_denied&state=s1 HTTP/1.1", "s1"),
            Callback::Denied
        );
    }

    #[test]
    fn loopback_listener_skips_strays_and_returns_the_code() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let client = std::thread::spawn(move || {
            for target in [
                "/favicon.ico",
                "/callback?code=x&state=forged",
                "/callback?code=real&state=s1",
            ] {
                let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
                write!(stream, "GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n").unwrap();
                let mut reply = String::new();
                let _ = stream.read_to_string(&mut reply);
            }
        });
        let cancelled = AtomicBool::new(false);
        assert_eq!(
            wait_for_callback(listener, "s1", Duration::from_secs(10), &cancelled).unwrap(),
            "real"
        );
        client.join().unwrap();
    }

    #[test]
    fn loopback_listener_stops_when_cancelled() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let cancelled = AtomicBool::new(true);
        assert_eq!(
            wait_for_callback(listener, "s1", Duration::from_secs(10), &cancelled).unwrap_err(),
            "已取消登录"
        );
    }

    /// 对着真实账号服务走一遍（手动跑，不进 CI）：
    /// VORTOCODE_ACCOUNT_E2E=http://127.0.0.1:8700 cargo test --lib account::tests::live -- --ignored --nocapture
    /// 浏览器那一步由外部脚本扮演：读 $VORTOCODE_ACCOUNT_E2E_URLFILE 里的授权地址，登录并点「允许」。
    #[test]
    #[ignore]
    fn live_login_refresh_logout() {
        let base =
            account_base_url(std::env::var("VORTOCODE_ACCOUNT_E2E").ok().as_deref()).unwrap();
        let url_file = std::env::var("VORTOCODE_ACCOUNT_E2E_URLFILE").unwrap();
        let verifier = random_token(32).unwrap();
        let state = random_token(16).unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let redirect = format!(
            "http://127.0.0.1:{}/callback",
            listener.local_addr().unwrap().port()
        );
        std::fs::write(
            &url_file,
            authorize_url(
                &base,
                &redirect,
                &pkce_challenge(&verifier),
                &state,
                "E2E Mac",
            ),
        )
        .unwrap();
        let code = wait_for_callback(
            listener,
            &state,
            Duration::from_secs(60),
            &AtomicBool::new(false),
        )
        .unwrap();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async {
            let client = http_client("0.0.0-e2e").unwrap();
            let pair = token_request(
                &client,
                &base,
                &[
                    ("grant_type", "authorization_code"),
                    ("client_id", CLIENT_ID),
                    ("code", &code),
                    ("code_verifier", &verifier),
                    ("redirect_uri", &redirect),
                    ("platform", &platform()),
                    ("app_version", "0.0.0-e2e"),
                ],
            )
            .await
            .map_err(|e| e.message())
            .unwrap();
            let (email, device) = fetch_me(&client, &base, &pair.access_token)
                .await
                .map_err(|e| e.message())
                .unwrap();
            println!("登录成功：{email} / {device}");
            let refresh = |token: String| {
                let client = client.clone();
                let base = base.clone();
                async move {
                    token_request(
                        &client,
                        &base,
                        &[
                            ("grant_type", "refresh_token"),
                            ("client_id", CLIENT_ID),
                            ("refresh_token", &token),
                            ("app_version", "0.0.0-e2e"),
                        ],
                    )
                    .await
                }
            };
            let second = refresh(pair.refresh_token.clone())
                .await
                .map_err(|e| e.message())
                .unwrap();
            assert!(
                matches!(
                    refresh(pair.refresh_token.clone()).await,
                    Err(AuthError::Rejected(_))
                ),
                "旧刷新令牌重放必须被拒"
            );
            assert!(
                matches!(
                    fetch_me(&client, &base, &second.access_token).await,
                    Err(AuthError::Rejected(_))
                ),
                "重放后整条登录链作废"
            );
            println!("刷新轮换与重放检测正常");
        });
    }
}
