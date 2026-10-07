use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::ffi::OsString;
use std::fs::{create_dir_all, read, read_to_string, remove_file, rename, OpenOptions};
use std::io::{ErrorKind, Write};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, TcpListener};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{webview::PageLoadEvent, AppHandle, Manager, State};
use tauri_plugin_shell::ShellExt;
use url::{Host, Url};

const MAX_WORKSPACE_FILES: usize = 6_000;
const MAX_PREVIEW_BYTES: u64 = 1024 * 1024;
const MAX_DESKTOP_PROJECTS: usize = 30;
const MAX_DESKTOP_RUNTIMES: usize = 12;
const GENERAL_SCOPE: &str = "general";
const SCRATCH_SCOPE: &str = "scratch";
const PROJECT_SCOPE: &str = "project";
const DEFAULT_LLM_BASE_URL: &str = "https://token.vortotech.com/v1";
const DEFAULT_LLM_MODEL: &str = "mimo-v2.6-pro";

#[derive(Clone, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopLlmProfile {
    base_url: String,
    api_key: String,
    model: String,
    #[serde(default)]
    context_window: Option<u64>,
    #[serde(default)]
    context_window_source: Option<String>,
    /// 模型调度的快速档 / 强力档（可选）。没配时「自动」只在主模型上运行。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    fast_model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    strong_model: Option<String>,
    /// 用户自己添加的其他供应商（各带各的 Key）；聊天里以 `id:模型` 选用。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    providers: Vec<DesktopLlmProvider>,
    /// 经账号登录得到的中转站 Key 时，记下是谁、Key 从哪来（不含密码/会话）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    account: Option<DesktopAccount>,
    /// 已退出登录：默认服务没有 Key、不注入 runtime，但保留自定义供应商。
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    signed_out: bool,
    /// 默认服务 /models 返回的模型清单：输入框据此列出可选模型，runtime 据此放行点名。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    models: Vec<String>,
    /// 同一份清单带上服务端给的分类 / 能力 / 推荐档位（relay 2026-10-07 起提供，别家服务只有 id）。
    /// 旧配置没有这个字段：界面在启动引擎前补拉一次。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    model_info: Vec<ModelInfo>,
}

/// /models 里一个模型的描述。除 id 外都是可选的：OpenAI 兼容的别家服务不返回这些字段。
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
struct ModelInfo {
    id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    category: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    capabilities: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    tier: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    snapshot_of: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    context_window: Option<u64>,
}

/// 能对话的三类：纯文本 / 图片视频输入 / 全模态。其余（OCR、翻译、生图、语音……）不能跑 Agent。
const CHAT_MODEL_CATEGORIES: [&str; 3] = ["chat", "multimodal", "omni"];

impl ModelInfo {
    /// 能拿来跑 Agent：能对话，且支持工具调用。
    fn agent_ready(&self) -> bool {
        self.category.as_deref().is_some_and(|category| CHAT_MODEL_CATEGORIES.contains(&category))
            && self.capabilities.iter().any(|capability| capability == "tools")
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopAccount {
    username: String,
    display_name: String,
    /// "token_plan" = 账号的 Token Plan Key；"pay_as_you_go" = 桌面端建的按量 Key。
    key_source: String,
    #[serde(default)]
    plan_name: Option<String>,
    #[serde(default)]
    plan_expiry: Option<i64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopLlmProvider {
    id: String,
    name: String,
    base_url: String,
    api_key: String,
    #[serde(default)]
    models: Vec<String>,
}

/// 给网页层看的供应商信息：**不含 Key**。
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopLlmProviderStatus {
    id: String,
    name: String,
    base_url: String,
    models: Vec<String>,
    has_key: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopLlmProfileStatus {
    configured: bool,
    base_url: String,
    model: String,
    provider: String,
    requires_key: bool,
    context_window: Option<u64>,
    context_window_source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    fast_model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    strong_model: Option<String>,
    providers: Vec<DesktopLlmProviderStatus>,
    #[serde(skip_serializing_if = "Option::is_none")]
    account: Option<DesktopAccount>,
    models: Vec<String>,
    model_info: Vec<ModelInfo>,
    /// 本机配置文件位置；设置页显示它，方便用户直接编辑。
    #[serde(skip_serializing_if = "Option::is_none")]
    config_path: Option<String>,
}

#[derive(Default)]
struct CachedDesktopLlmProfile {
    loaded: bool,
    profile: Option<DesktopLlmProfile>,
}

#[derive(Default)]
struct DesktopLlmProfileStore(Mutex<CachedDesktopLlmProfile>);

impl DesktopLlmProfileStore {
    fn get_or_try_init<F>(&self, load: F) -> Result<Option<DesktopLlmProfile>, String>
    where
        F: FnOnce() -> Result<Option<DesktopLlmProfile>, String>,
    {
        // Keep the lock while loading so concurrent runtime starts share one
        // Keychain authorization/read instead of opening duplicate prompts.
        let mut cached = self
            .0
            .lock()
            .map_err(|_| "模型配置缓存锁已损坏".to_string())?;
        if cached.loaded {
            return Ok(cached.profile.clone());
        }
        let profile = load()?;
        cached.profile = profile.clone();
        cached.loaded = true;
        Ok(profile)
    }

    fn replace(&self, profile: Option<DesktopLlmProfile>) -> Result<(), String> {
        let mut cached = self
            .0
            .lock()
            .map_err(|_| "模型配置缓存锁已损坏".to_string())?;
        cached.profile = profile;
        cached.loaded = true;
        Ok(())
    }
}

const MIN_MODEL_CONTEXT_WINDOW: u64 = 1_024;
const MAX_MODEL_CONTEXT_WINDOW: u64 = 20_000_000;

fn official_model_context_window(model: &str) -> Option<u64> {
    let name = model.trim().to_ascii_lowercase();
    if name.contains("mimo-v2.5-base") {
        Some(256_000)
    } else if name.contains("mimo-v2.5") {
        Some(1_000_000)
    } else {
        None
    }
}

fn default_llm_profile() -> DesktopLlmProfile {
    DesktopLlmProfile {
        base_url: DEFAULT_LLM_BASE_URL.into(),
        api_key: String::new(),
        model: DEFAULT_LLM_MODEL.into(),
        // 默认模型的窗口只在官方资料有把握时填；没有就留空，保存时向服务探测。
        context_window: official_model_context_window(DEFAULT_LLM_MODEL),
        context_window_source: official_model_context_window(DEFAULT_LLM_MODEL).map(|_| "catalog".into()),
        fast_model: None,
        strong_model: None,
        providers: Vec::new(),
        account: None,
        signed_out: false,
        model_info: Vec::new(),
        models: Vec::new(),
    }
}

fn normalize_llm_profile(mut profile: DesktopLlmProfile) -> Result<DesktopLlmProfile, String> {
    profile.base_url = profile.base_url.trim().trim_end_matches('/').to_string();
    profile.api_key = profile.api_key.trim().to_string();
    profile.model = profile.model.trim().to_string();
    for tier in [&mut profile.fast_model, &mut profile.strong_model] {
        *tier = tier
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .map(str::to_string);
        if tier
            .as_deref()
            .is_some_and(|name| name.len() > 160 || name.chars().any(char::is_control))
        {
            return Err("调度模型名无效".into());
        }
    }
    if profile.base_url.len() > 512 || profile.model.is_empty() || profile.model.len() > 160 {
        return Err("模型服务地址或模型名无效".into());
    }
    if profile.model.chars().any(char::is_control) || profile.api_key.chars().any(char::is_control)
    {
        return Err("模型配置不能包含控制字符".into());
    }
    if profile.api_key.len() > 2_048 {
        return Err("模型服务 Key 过长".into());
    }
    if profile.context_window.is_some_and(|window| {
        !(MIN_MODEL_CONTEXT_WINDOW..=MAX_MODEL_CONTEXT_WINDOW).contains(&window)
    }) {
        return Err("模型上下文窗口必须在 1K–20M tokens 之间".into());
    }
    if profile.context_window.is_none() {
        profile.context_window = official_model_context_window(&profile.model);
        if profile.context_window.is_some() {
            profile.context_window_source = Some("catalog".into());
        }
    }
    profile.context_window_source = Some(match profile.context_window_source.as_deref() {
        Some("service" | "catalog" | "configured") => profile
            .context_window_source
            .clone()
            .unwrap_or_else(|| "unknown".into()),
        _ if profile.context_window.is_some() => "configured".into(),
        _ => "unknown".into(),
    });
    let parsed =
        Url::parse(&profile.base_url).map_err(|_| "模型服务地址不是有效 URL".to_string())?;
    if !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err("模型服务地址不能包含用户名、密码、查询参数或片段".into());
    }
    let host = parsed.host_str().unwrap_or_default();
    let local = matches!(host, "127.0.0.1" | "localhost");
    if parsed.scheme() != "https" && !(parsed.scheme() == "http" && local) {
        return Err("远程模型服务必须使用 HTTPS；HTTP 仅允许 127.0.0.1 / localhost".into());
    }
    if !local && profile.api_key.is_empty() && !profile.signed_out {
        return Err("远程模型服务需要 API Key".into());
    }
    // 配置文件可以手改：清单会拼进环境变量，读回来也要清洗一遍。
    profile.models = clean_model_list(std::mem::take(&mut profile.models));
    profile.model_info = clean_model_infos(std::mem::take(&mut profile.model_info));
    Ok(profile)
}

fn context_window_number(value: &serde_json::Value) -> Option<u64> {
    let number = value.as_u64().or_else(|| {
        value
            .as_str()
            .and_then(|raw| raw.trim().parse::<u64>().ok())
    })?;
    (MIN_MODEL_CONTEXT_WINDOW..=MAX_MODEL_CONTEXT_WINDOW)
        .contains(&number)
        .then_some(number)
}

fn context_window_from_metadata(value: &serde_json::Value) -> Option<u64> {
    let object = value.as_object()?;
    const DIRECT_KEYS: [&str; 8] = [
        "context_window",
        "context_length",
        "max_context_length",
        "max_model_len",
        "max_sequence_length",
        "input_token_limit",
        "max_input_tokens",
        "n_ctx",
    ];
    for key in DIRECT_KEYS {
        if let Some(window) = object.get(key).and_then(context_window_number) {
            return Some(window);
        }
    }
    for key in ["capabilities", "limits", "metadata", "model_info"] {
        if let Some(window) = object.get(key).and_then(context_window_from_metadata) {
            return Some(window);
        }
    }
    None
}

fn context_window_from_models_payload(payload: &serde_json::Value, model: &str) -> Option<u64> {
    let models = payload.get("data")?.as_array()?;
    let wanted = model.trim();
    models.iter().find_map(|item| {
        let id = item
            .get("id")
            .or_else(|| item.get("model"))
            .or_else(|| item.get("name"))
            .and_then(serde_json::Value::as_str)?;
        id.eq_ignore_ascii_case(wanted)
            .then(|| context_window_from_metadata(item))
            .flatten()
    })
}

const LLM_PROBE_TIMEOUT: Duration = Duration::from_secs(8);

async fn discover_model_context_window_with(
    profile: &DesktopLlmProfile,
    timeout: Duration,
) -> Option<u64> {
    // 探测收敛：只允许打 profile 声明的 base_url 本身——限时，且不跟随重定向，
    // 避免探测阶段被当跳板打别的 host、或 Bearer Key 随跳转外流。
    let client = reqwest::Client::builder()
        .timeout(timeout)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .ok()?;
    let mut request = client.get(format!("{}/models", profile.base_url));
    if !profile.api_key.is_empty() {
        request = request.bearer_auth(&profile.api_key);
    }
    let response = request.send().await.ok()?;
    if !response.status().is_success() {
        return None;
    }
    let payload = response.json::<serde_json::Value>().await.ok()?;
    context_window_from_models_payload(&payload, &profile.model)
}

fn llm_provider(base_url: &str) -> &'static str {
    if base_url == DEFAULT_LLM_BASE_URL {
        "vortocode"
    } else if Url::parse(base_url)
        .ok()
        .and_then(|url| url.host_str().map(str::to_string))
        .is_some_and(|host| matches!(host.as_str(), "127.0.0.1" | "localhost"))
    {
        "local"
    } else {
        "custom"
    }
}

// 系统 Keychain 底层：按 (service, account) 存取一条 generic password。LLM key 与远程连接
// token 都走它——共享一次 Security framework FFI，不各写一遍 unsafe。
#[cfg(target_os = "macos")]
mod platform_keychain {
    use std::ffi::c_void;
    use std::ptr::{null, null_mut};

    const ERR_SEC_ITEM_NOT_FOUND: i32 = -25300;

    #[link(name = "Security", kind = "framework")]
    extern "C" {
        fn SecKeychainFindGenericPassword(
            keychain_or_array: *const c_void,
            service_name_length: u32,
            service_name: *const i8,
            account_name_length: u32,
            account_name: *const i8,
            password_length: *mut u32,
            password_data: *mut *mut c_void,
            item_ref: *mut *mut c_void,
        ) -> i32;
        fn SecKeychainItemFreeContent(attr_list: *const c_void, data: *mut c_void) -> i32;
        fn SecKeychainAddGenericPassword(
            keychain: *mut c_void,
            service_name_length: u32,
            service_name: *const i8,
            account_name_length: u32,
            account_name: *const i8,
            password_length: u32,
            password_data: *const c_void,
            item_ref: *mut *mut c_void,
        ) -> i32;
        fn SecKeychainItemModifyAttributesAndData(
            item_ref: *mut c_void,
            attr_list: *const c_void,
            length: u32,
            data: *const c_void,
        ) -> i32;
        fn SecKeychainItemDelete(item_ref: *mut c_void) -> i32;
    }

    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CFRelease(value: *const c_void);
    }

    fn status_error(operation: &str, status: i32) -> String {
        format!("macOS Keychain {operation}失败（OSStatus {status}）")
    }

    unsafe fn find_item(service: &[u8], account: &[u8]) -> Result<Option<*mut c_void>, String> {
        let mut item = null_mut();
        let status = SecKeychainFindGenericPassword(
            null(),
            service.len() as u32,
            service.as_ptr().cast(),
            account.len() as u32,
            account.as_ptr().cast(),
            null_mut(),
            null_mut(),
            &mut item,
        );
        if status == ERR_SEC_ITEM_NOT_FOUND {
            return Ok(None);
        }
        if status != 0 {
            return Err(status_error("查询", status));
        }
        Ok(Some(item))
    }

    pub fn read(service: &[u8], account: &[u8]) -> Result<Option<String>, String> {
        unsafe {
            let mut password_length = 0_u32;
            let mut password_data = null_mut();
            let mut item = null_mut();
            let status = SecKeychainFindGenericPassword(
                null(),
                service.len() as u32,
                service.as_ptr().cast(),
                account.len() as u32,
                account.as_ptr().cast(),
                &mut password_length,
                &mut password_data,
                &mut item,
            );
            if status == ERR_SEC_ITEM_NOT_FOUND {
                return Ok(None);
            }
            if status != 0 {
                return Err(status_error("读取", status));
            }
            let payload =
                std::slice::from_raw_parts(password_data.cast::<u8>(), password_length as usize)
                    .to_vec();
            let _ = SecKeychainItemFreeContent(null(), password_data);
            if !item.is_null() {
                CFRelease(item);
            }
            String::from_utf8(payload)
                .map(Some)
                .map_err(|_| "macOS Keychain 中的数据不是有效 UTF-8".to_string())
        }
    }

    pub fn write(service: &[u8], account: &[u8], payload: &str) -> Result<(), String> {
        unsafe {
            if let Some(item) = find_item(service, account)? {
                let status = SecKeychainItemModifyAttributesAndData(
                    item,
                    null(),
                    payload.len() as u32,
                    payload.as_ptr().cast(),
                );
                CFRelease(item);
                return if status == 0 {
                    Ok(())
                } else {
                    Err(status_error("更新", status))
                };
            }
            let status = SecKeychainAddGenericPassword(
                null_mut(),
                service.len() as u32,
                service.as_ptr().cast(),
                account.len() as u32,
                account.as_ptr().cast(),
                payload.len() as u32,
                payload.as_ptr().cast(),
                null_mut(),
            );
            if status == 0 {
                Ok(())
            } else {
                Err(status_error("保存", status))
            }
        }
    }

    pub fn delete(service: &[u8], account: &[u8]) -> Result<(), String> {
        unsafe {
            let Some(item) = find_item(service, account)? else {
                return Ok(());
            };
            let status = SecKeychainItemDelete(item);
            CFRelease(item);
            if status == 0 {
                Ok(())
            } else {
                Err(status_error("删除", status))
            }
        }
    }
}

#[cfg(not(target_os = "macos"))]
mod platform_keychain {
    pub fn read(_service: &[u8], _account: &[u8]) -> Result<Option<String>, String> {
        Ok(None)
    }
    pub fn write(_service: &[u8], _account: &[u8], _payload: &str) -> Result<(), String> {
        Err("当前预览仅在 macOS 提供系统 Keychain".into())
    }
    pub fn delete(_service: &[u8], _account: &[u8]) -> Result<(), String> {
        Ok(())
    }
}

// 远程连接 token：与 LLM key 分开的 Keychain 服务，按项目 id 作 account 以支持多远端。
// read 只在 Rust 侧的远程代理里用——token 原文不进 webview（spec §连接层安全 2）。
mod remote_token_keychain {
    use super::platform_keychain;
    const SERVICE: &[u8] = b"com.vortocode.desktop.remote";
    pub fn read(account: &str) -> Result<Option<String>, String> {
        platform_keychain::read(SERVICE, account.as_bytes())
    }
    pub fn write(account: &str, token: &str) -> Result<(), String> {
        platform_keychain::write(SERVICE, account.as_bytes(), token)
    }
    pub fn delete(account: &str) -> Result<(), String> {
        platform_keychain::delete(SERVICE, account.as_bytes())
    }
}

// 模型服务配置存在 Desktop 配置目录下的 JSON 文件里（与 projects.json 同目录，权限 600），
// 不再放 macOS Keychain：自签名/开发构建每换一次二进制，Keychain 就要求重新授权一次。
// 文件可直接手动编辑，下次启动生效；格式与设置页保存的一致（camelCase）。
const LLM_PROFILE_FILE: &str = "llm-profile.json";

const DESKTOP_PREFERENCES_FILE: &str = "desktop-preferences.json";
const BROWSER_CANDIDATES: [&str; 4] = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
];

/// 本机偏好（不含任何凭据）。缺字段一律按最保守的默认值：浏览器操控关闭。
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopPreferences {
    #[serde(default)]
    browser_control: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct BrowserControlStatus {
    enabled: bool,
    /// 找到的 Chromium 系浏览器；没有则无法启用。
    browser_path: Option<String>,
}

fn desktop_preferences_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join(DESKTOP_PREFERENCES_FILE))
        .map_err(|error| format!("无法定位 Desktop 配置目录：{error}"))
}

fn read_desktop_preferences(path: &Path) -> DesktopPreferences {
    // 读不到或格式坏了都按默认（关闭）：偏好文件损坏不该让任何能力意外打开。
    std::fs::read_to_string(path)
        .ok()
        .and_then(|payload| serde_json::from_str(&payload).ok())
        .unwrap_or_default()
}

fn find_local_browser() -> Option<String> {
    BROWSER_CANDIDATES
        .iter()
        .find(|path| Path::new(path).is_file())
        .map(|path| path.to_string())
}

fn configure_browser_control(command: &mut Command, preferences: &DesktopPreferences) {
    match (preferences.browser_control, find_local_browser()) {
        (true, Some(browser)) => {
            command.env("VORTOCODE_ENABLE_BROWSER_CONTROL", "1");
            command.env("VORTOCODE_BROWSER_PATH", browser);
        }
        _ => {
            command.env_remove("VORTOCODE_ENABLE_BROWSER_CONTROL");
            command.env_remove("VORTOCODE_BROWSER_PATH");
        }
    }
}

#[tauri::command(async)]
fn get_browser_control(app: AppHandle) -> Result<BrowserControlStatus, String> {
    let preferences = read_desktop_preferences(&desktop_preferences_path(&app)?);
    Ok(BrowserControlStatus {
        enabled: preferences.browser_control,
        browser_path: find_local_browser(),
    })
}

#[tauri::command]
async fn set_browser_control(app: AppHandle, enabled: bool) -> Result<BrowserControlStatus, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    let browser_path = find_local_browser();
    if enabled && browser_path.is_none() {
        return Err("没有找到 Chrome / Edge / Chromium / Brave，无法启用浏览器操控".into());
    }
    if enabled {
        // 打开是放权：原生确认在 webview 之外，页面脚本无法替用户点「启用」。关闭是收权，不必问。
        let dialog_app = app.clone();
        let confirmed = tauri::async_runtime::spawn_blocking(move || {
            with_main_window_parent(&dialog_app, dialog_app.dialog().message(
                "启用后，Agent 可以在一个独立的浏览器窗口里打开网页、读取内容和截图；每次点击或输入都会先问你。\n\n这个窗口不含你日常浏览器的登录信息；支付、银行、邮箱、账号和云控制台等站点会被拦截。",
            ))
            .title("启用浏览器操控")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom("启用".into(), "取消".into()))
            .blocking_show()
        })
        .await
        .map_err(|error| format!("浏览器操控确认对话框失败：{error}"))?;
        if !confirmed {
            return Err("浏览器操控未获确认，保持关闭".into());
        }
    }
    let path = desktop_preferences_path(&app)?;
    let mut preferences = read_desktop_preferences(&path);
    preferences.browser_control = enabled;
    save_json_atomic_with_mode(&path, "desktop-preferences", &preferences, Some(0o600))?;
    Ok(BrowserControlStatus {
        enabled,
        browser_path,
    })
}

fn llm_profile_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join(LLM_PROFILE_FILE))
        .map_err(|error| format!("无法定位 Desktop 配置目录：{error}"))
}

fn read_llm_profile_file(path: &Path) -> Result<Option<DesktopLlmProfile>, String> {
    let payload = match std::fs::read_to_string(path) {
        Ok(payload) => payload,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("无法读取模型配置文件 {}：{error}", path.display())),
    };
    let profile: DesktopLlmProfile = serde_json::from_str(&payload).map_err(|_| {
        format!("模型配置文件格式有误：{}；请在 Desktop 设置中重新保存", path.display())
    })?;
    normalize_llm_profile(profile).map(Some)
}

fn save_llm_profile_file(path: &Path, profile: &DesktopLlmProfile) -> Result<(), String> {
    // 含 API Key：只允许当前账户读写。
    save_json_atomic_with_mode(path, "llm-profile", profile, Some(0o600))
}

fn delete_llm_profile_file(path: &Path) -> Result<(), String> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("无法删除模型配置文件 {}：{error}", path.display())),
    }
}

fn cached_desktop_llm_profile(
    app: &AppHandle,
    store: &DesktopLlmProfileStore,
) -> Result<Option<DesktopLlmProfile>, String> {
    let path = llm_profile_path(app)?;
    store.get_or_try_init(|| read_llm_profile_file(&path))
}

fn with_config_path(mut status: DesktopLlmProfileStatus, app: &AppHandle) -> DesktopLlmProfileStatus {
    status.config_path = llm_profile_path(app).ok().map(|path| path.display().to_string());
    status
}

fn desktop_llm_profile_status(profile: Option<&DesktopLlmProfile>) -> DesktopLlmProfileStatus {
    let fallback = default_llm_profile();
    let profile = profile.unwrap_or(&fallback);
    DesktopLlmProfileStatus {
        configured: !profile.signed_out
            && (profile.api_key.len() > 0 || llm_provider(&profile.base_url) == "local"),
        base_url: profile.base_url.clone(),
        model: profile.model.clone(),
        provider: llm_provider(&profile.base_url).into(),
        requires_key: llm_provider(&profile.base_url) != "local",
        context_window: profile.context_window,
        context_window_source: profile
            .context_window_source
            .clone()
            .unwrap_or_else(|| "unknown".into()),
        fast_model: profile.fast_model.clone(),
        strong_model: profile.strong_model.clone(),
        providers: profile
            .providers
            .iter()
            .map(|provider| DesktopLlmProviderStatus {
                id: provider.id.clone(),
                name: provider.name.clone(),
                base_url: provider.base_url.clone(),
                models: provider.models.clone(),
                has_key: !provider.api_key.is_empty(),
            })
            .collect(),
        account: profile.account.clone(),
        models: profile.models.clone(),
        model_info: profile.model_info.clone(),
        config_path: None,
    }
}

// 会做磁盘 / 进程 / Keychain 等可能阻塞操作的命令不能跑在主线程：阻塞期间整个窗口白屏
// （2026-10-06 真机复现：Keychain 授权弹窗等待期间白屏）。`async` 让 Tauri 在后台线程执行。
#[tauri::command(async)]
fn get_llm_profile(
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    let profile = cached_desktop_llm_profile(&app, &store)?;
    Ok(with_config_path(desktop_llm_profile_status(profile.as_ref()), &app))
}

/// 原生确认框必须挂到主窗口上（弹成 sheet）。rfd 在 macOS 上的无父窗口消息框会建出窗口却
/// 从不显示，`blocking_show` 因此永远挂起——2026-10-06 真机复现：确认框不出现、命令不返回。
fn with_main_window_parent(
    app: &AppHandle,
    dialog: tauri_plugin_dialog::MessageDialogBuilder<tauri::Wry>,
) -> tauri_plugin_dialog::MessageDialogBuilder<tauri::Wry> {
    match app.get_webview_window("main") {
        Some(window) => dialog.parent(&window),
        None => dialog,
    }
}

async fn confirm_llm_profile_change(
    app: &AppHandle,
    profile: &DesktopLlmProfile,
) -> Result<bool, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    // 改档是敏感动作：base_url+Key 会落 Keychain 并注入后续 runtime 环境。原生对话框在
    // webview 进程之外，被注入的页面脚本无法替用户点「确认」，静默改档因此不生效。
    let tiers = match (&profile.fast_model, &profile.strong_model) {
        (None, None) => String::new(),
        (fast, strong) => format!(
            "\n快速模型：{}\n强力模型：{}",
            fast.as_deref().unwrap_or("（同主模型）"),
            strong.as_deref().unwrap_or("（同主模型）")
        ),
    };
    let message = format!(
        "网页层请求把模型服务改为：\n\n服务地址：{}\n模型：{}{}\n\n仅当这是你刚在设置页保存的配置时才确认。",
        profile.base_url, profile.model, tiers
    );
    let app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&app, app.dialog().message(message))
            .title("确认修改模型服务")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom(
                "确认修改".into(),
                "取消".into(),
            ))
            .blocking_show()
    })
    .await
    .map_err(|error| format!("模型服务确认对话框失败：{error}"))
}

async fn save_llm_profile_flow(
    profile: DesktopLlmProfile,
    confirmed: bool,
    probe_timeout: Duration,
    persist: impl FnOnce(&DesktopLlmProfile) -> Result<(), String>,
) -> Result<DesktopLlmProfile, String> {
    let mut profile = normalize_llm_profile(profile)?;
    // 确认门在探测之前：未获用户确认时连一次出网探测都不发生。
    if !confirmed {
        return Err("模型服务修改未获用户确认，已取消".into());
    }
    if let Some(window) = discover_model_context_window_with(&profile, probe_timeout).await {
        profile.context_window = Some(window);
        profile.context_window_source = Some("service".into());
    }
    persist(&profile)?;
    Ok(profile)
}

#[tauri::command]
async fn set_llm_profile(
    app: AppHandle,
    base_url: String,
    api_key: String,
    model: String,
    fast_model: Option<String>,
    strong_model: Option<String>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    // 只改模型名 / 调度档时不必重输 Key：留空则沿用已保存的 Key，规则同 test_llm_connection
    // （仅限同一地址，Key 绝不借给别的服务）。
    let saved = cached_desktop_llm_profile(&app, &store)?;
    let mut api_key = api_key.trim().to_string();
    if api_key.is_empty() {
        let wanted = base_url.trim().trim_end_matches('/');
        if let Some(saved) = saved.as_ref() {
            if saved.base_url == wanted {
                api_key = saved.api_key.clone();
            }
        }
    }
    // 地址和 Key 都没变（只改了模型名 / 调度档）时保留登录信息；换了 Key 或地址就不再是账号给的那把了。
    let wanted_base = base_url.trim().trim_end_matches('/').to_string();
    let account = saved
        .as_ref()
        .filter(|saved| saved.base_url == wanted_base && saved.api_key == api_key)
        .and_then(|saved| saved.account.clone());
    // 同一个服务地址：模型清单沿用（换地址后由界面重新拉取）。
    let (models, model_info) = saved
        .as_ref()
        .filter(|saved| saved.base_url == wanted_base)
        .map(|saved| (saved.models.clone(), saved.model_info.clone()))
        .unwrap_or_default();
    let profile = normalize_llm_profile(DesktopLlmProfile {
        base_url,
        api_key,
        model,
        context_window: None,
        context_window_source: None,
        fast_model,
        strong_model,
        // 改默认模型服务时保留已添加的其他供应商（它们各带各的 Key，与默认服务无关）。
        providers: saved.map(|saved| saved.providers).unwrap_or_default(),
        account,
        signed_out: false,
        models,
        model_info,
    })?;
    let path = llm_profile_path(&app)?;
    let confirmed = confirm_llm_profile_change(&app, &profile).await?;
    let profile = if confirmed { with_model_list(profile).await } else { profile };
    let profile = save_llm_profile_flow(profile, confirmed, LLM_PROBE_TIMEOUT, |profile| {
        save_llm_profile_file(&path, profile)
    })
    .await?;
    store.replace(Some(profile.clone()))?;
    Ok(with_config_path(desktop_llm_profile_status(Some(&profile)), &app))
}

// 可能阻塞：移出主线程，原因见 get_llm_profile。
#[tauri::command(async)]
fn clear_llm_profile(
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    delete_llm_profile_file(&llm_profile_path(&app)?)?;
    store.replace(None)?;
    Ok(with_config_path(desktop_llm_profile_status(None), &app))
}

fn configure_llm_profile(command: &mut Command, profile: Option<&DesktopLlmProfile>) {
    let Some(profile) = profile else {
        return;
    };
    if profile.signed_out {
        // 退出登录后默认服务不可用；自定义供应商仍各用各的 Key。
        for provider in &profile.providers {
            let id = provider.id.to_uppercase();
            command.env(format!("VORTOCODE_PROVIDER_{id}_BASE"), &provider.base_url);
            command.env(format!("VORTOCODE_PROVIDER_{id}_KEY"), &provider.api_key);
        }
        return;
    }
    command.env("OPENAI_API_BASE", &profile.base_url);
    command.env(
        "OPENAI_API_KEY",
        if profile.api_key.is_empty() {
            "not-required"
        } else {
            &profile.api_key
        },
    );
    command.env("DEFAULT_MODEL", &profile.model);
    // 默认服务上可点名的模型清单（同一端点、同一把 Key）。
    let choices = model_choice_ids(profile);
    if choices.is_empty() {
        command.env_remove("VORTOCODE_MODEL_CHOICES");
    } else {
        command.env("VORTOCODE_MODEL_CHOICES", choices.join(","));
    }
    // 调度三档：均衡档就是主模型；快速 / 强力没配时显式清掉，免得继承到父进程环境里的旧值。
    command.env("LLM_MODEL_BALANCED", &profile.model);
    match &profile.fast_model {
        Some(model) => command.env("LLM_MODEL_CHEAP", model),
        None => command.env_remove("LLM_MODEL_CHEAP"),
    };
    match &profile.strong_model {
        Some(model) => command.env("LLM_MODEL_POWERFUL", model),
        None => command.env_remove("LLM_MODEL_POWERFUL"),
    };
    // 自定义供应商：runtime 的 src/llm/providers.py 按这对变量登记端点，key 只随自己的端点走。
    for provider in &profile.providers {
        let id = provider.id.to_uppercase();
        command.env(format!("VORTOCODE_PROVIDER_{id}_BASE"), &provider.base_url);
        command.env(format!("VORTOCODE_PROVIDER_{id}_KEY"), &provider.api_key);
    }
    if let Some(window) = profile.context_window {
        command.env("VORTOCODE_MODEL_CONTEXT_WINDOW", window.to_string());
        command.env(
            "VORTOCODE_MODEL_CONTEXT_WINDOW_SOURCE",
            profile
                .context_window_source
                .as_deref()
                .unwrap_or("configured"),
        );
    }
}

#[derive(Default)]
struct GatewayProcess(Mutex<GatewaySupervisorInner>);

#[derive(Default)]
struct GatewaySupervisorInner {
    runtimes: HashMap<String, GatewayProcessInner>,
    active_runtime_id: Option<String>,
}

#[derive(Default)]
struct GatewayProcessInner {
    child: Option<Child>,
    runtime_id: String,
    project_id: Option<String>,
    workspace_id: Option<String>,
    scope: Option<String>,
    workspace_root: Option<PathBuf>,
    repo_root: Option<PathBuf>,
    command: Option<String>,
    base_url: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct GatewayProcessStatus {
    running: bool,
    pid: Option<u32>,
    runtime_id: Option<String>,
    project_id: Option<String>,
    workspace_id: Option<String>,
    command: Option<String>,
    scope: Option<String>,
    workspace_root: Option<String>,
    repo_root: Option<String>,
    base_url: Option<String>,
    message: String,
}

// 项目模式：local=本机 Git 工作区（现状）；remote=连服务器上的 runtime，repo_root 是
// 服务器侧路径、base_url 是远端 server_url。旧 projects.json 无 kind 字段 → serde 默认 local，
// 向后兼容。local 注册流（remember_project_at）强不变量一点不动，remote 走独立显式校验旁路。
const PROJECT_KIND_LOCAL: &str = "local";
const PROJECT_KIND_REMOTE: &str = "remote";

fn default_project_kind() -> String {
    PROJECT_KIND_LOCAL.into()
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopProjectProfile {
    id: String,
    name: String,
    repo_root: String,
    base_url: String,
    last_opened_at: u64,
    #[serde(default = "default_project_kind")]
    kind: String,
}

#[derive(Default, Deserialize, Serialize)]
struct DesktopProjectRegistry {
    projects: Vec<DesktopProjectProfile>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
struct GatewayRecoveryRecord {
    #[serde(default)]
    runtime_id: String,
    #[serde(default)]
    project_id: Option<String>,
    #[serde(default)]
    workspace_id: Option<String>,
    #[serde(default = "default_project_scope")]
    scope: String,
    #[serde(default)]
    workspace_root: String,
    repo_root: String,
    base_url: String,
    pid: Option<u32>,
    started_at: u64,
    updated_at: u64,
    status: String,
    message: String,
}

#[derive(Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct GatewayRecoveryRegistry {
    #[serde(default)]
    version: u8,
    #[serde(default)]
    runtimes: Vec<GatewayRecoveryRecord>,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum StoredGatewayRecovery {
    Legacy(GatewayRecoveryRecord),
    Registry(GatewayRecoveryRegistry),
}

fn default_project_scope() -> String {
    PROJECT_SCOPE.into()
}

fn normalize_runtime_scope(scope: &str) -> Result<&'static str, String> {
    match scope.trim().to_ascii_lowercase().as_str() {
        GENERAL_SCOPE => Ok(GENERAL_SCOPE),
        SCRATCH_SCOPE => Ok(SCRATCH_SCOPE),
        PROJECT_SCOPE => Ok(PROJECT_SCOPE),
        _ => Err("工作区范围必须是 general、scratch 或 project".into()),
    }
}

fn normalize_workspace_id(value: &str) -> Result<String, String> {
    let normalized = value.trim();
    if normalized.is_empty()
        || normalized.len() > 80
        || !normalized
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err("Scratch 会话标识无效".into());
    }
    Ok(normalized.into())
}

fn managed_workspace_root(
    app: &AppHandle,
    scope: &str,
    workspace_id: Option<&str>,
) -> Result<PathBuf, String> {
    let base = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位 Desktop 数据目录：{error}"))?
        .join("workspaces");
    match normalize_runtime_scope(scope)? {
        GENERAL_SCOPE => Ok(base.join(GENERAL_SCOPE)),
        SCRATCH_SCOPE => Ok(base
            .join(SCRATCH_SCOPE)
            .join(normalize_workspace_id(workspace_id.unwrap_or_default())?)),
        _ => Err("Project 工作区必须来自用户明确选择的 Git 项目".into()),
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFileList {
    root: String,
    files: Vec<String>,
    truncated: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFileContent {
    path: String,
    content: String,
    size: u64,
    sha256: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct OpenWorkspaceFileResult {
    launcher: String,
    line_aware: bool,
    message: String,
}

fn canonical_repo_root(repo_root: &str) -> Result<PathBuf, String> {
    let root = PathBuf::from(repo_root.trim())
        .canonicalize()
        .map_err(|error| format!("项目目录不可用：{error}"))?;
    if !root.is_dir() {
        return Err("项目路径不是目录".into());
    }
    Ok(root)
}

fn git_workspace_root(root: &Path) -> Result<PathBuf, String> {
    let output = Command::new("git")
        .args(["rev-parse", "--show-toplevel"])
        .current_dir(root)
        .output()
        .map_err(|error| format!("无法运行 git rev-parse：{error}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "请选择 Git 工作区：{}",
            detail.trim().chars().take(300).collect::<String>()
        ));
    }
    let top_level = String::from_utf8(output.stdout)
        .map_err(|_| "Git 返回的工作区路径不是有效 UTF-8".to_string())?;
    canonical_repo_root(top_level.trim())
}

fn normalize_local_base_url(base_url: &str) -> Result<String, String> {
    let normalized = base_url.trim().trim_end_matches('/');
    let port = ["http://127.0.0.1:", "http://localhost:"]
        .iter()
        .find_map(|prefix| normalized.strip_prefix(prefix));
    let Some(port) = port else {
        return Err("runtime 地址只允许 http://127.0.0.1:<端口> 或 http://localhost:<端口>".into());
    };
    if port.is_empty() || !port.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err("runtime 地址必须包含有效的本机端口，不能包含路径或查询参数".into());
    }
    let parsed = port
        .parse::<u16>()
        .map_err(|_| "runtime 端口必须在 1–65535 之间".to_string())?;
    if parsed == 0 {
        return Err("runtime 端口必须在 1–65535 之间".into());
    }
    Ok(normalized.to_string())
}

// CGNAT / Tailscale IPv4 段：100.64.0.0/10（Tailscale 的 100.x 落在此）。
fn is_cgnat_ipv4(ip: Ipv4Addr) -> bool {
    let octets = ip.octets();
    octets[0] == 100 && (64..=127).contains(&octets[1])
}

// Unique Local IPv6：fc00::/7——覆盖 Tailscale 的 fd7a:… ULA 地址。
fn is_unique_local_ipv6(ip: Ipv6Addr) -> bool {
    (ip.segments()[0] & 0xfe00) == 0xfc00
}

// 「可信内网/Tailscale」地址：RFC1918 私网、loopback、CGNAT/Tailscale 段。
fn is_trusted_private_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => v4.is_private() || v4.is_loopback() || is_cgnat_ipv4(v4),
        IpAddr::V6(v6) => v6.is_loopback() || is_unique_local_ipv6(v6),
    }
}

// 远程 server_url 网段校验（spec §连接层安全 3）：Desktop 不自实现 TLS 信任管理，故
// **非 https 且非 RFC1918/CGNAT/Tailscale 网段的地址一律拒绝注册**（fail-closed）。
// - https：假定传输层由 Tailscale/反代兜底，任意 host 放行；
// - http：只收字面私网/Tailscale IP——http+域名需 DNS 解析才能判段，存在 TOCTOU，直接拒
//   （话术指去装 Tailscale 或改用 https）。
// 归一化返回 `scheme://host[:port]`（去 path/query/fragment，供 gateway 拼 `${base}${path}`）。
fn validate_remote_server_url(raw: &str) -> Result<String, String> {
    let trimmed = raw.trim().trim_end_matches('/');
    let parsed = Url::parse(trimmed).map_err(|error| format!("服务器地址无法解析：{error}"))?;
    let scheme = parsed.scheme();
    if scheme != "http" && scheme != "https" {
        return Err(format!("服务器地址必须以 http:// 或 https:// 开头，收到 {scheme}://"));
    }
    if !parsed.path().is_empty() && parsed.path() != "/" {
        return Err("服务器地址不能包含路径（只填到主机与端口，如 https://host:8080）".into());
    }
    if parsed.query().is_some() || parsed.fragment().is_some() {
        return Err("服务器地址不能包含查询参数或片段".into());
    }
    let host = parsed.host().ok_or("服务器地址缺少主机名")?;
    let allowed = scheme == "https"
        || match host {
            Host::Ipv4(v4) => is_trusted_private_ip(IpAddr::V4(v4)),
            Host::Ipv6(v6) => is_trusted_private_ip(IpAddr::V6(v6)),
            Host::Domain(_) => false, // http+域名：DNS 判段有 TOCTOU，fail-closed
        };
    if !allowed {
        return Err(
            "非 https 地址只接受 RFC1918 内网或 100.64/10（Tailscale/CGNAT）IP；\
             公网请用 https，或经 Tailscale 内网访问"
                .into(),
        );
    }
    let host_str = parsed.host_str().ok_or("服务器地址缺少主机名")?;
    let mut base = format!("{scheme}://{host_str}");
    if let Some(port) = parsed.port() {
        base.push_str(&format!(":{port}"));
    }
    Ok(base)
}

fn project_registry_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join("projects.json"))
        .map_err(|error| format!("无法定位 Desktop 配置目录：{error}"))
}

fn gateway_recovery_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join("gateway-recovery.json"))
        .map_err(|error| format!("无法定位 Desktop 配置目录：{error}"))
}

fn load_project_registry(path: &Path) -> Result<DesktopProjectRegistry, String> {
    match read_to_string(path) {
        Ok(content) => serde_json::from_str(&content)
            .map_err(|error| format!("项目注册表已损坏，请检查 {}：{error}", path.display())),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(DesktopProjectRegistry::default()),
        Err(error) => Err(format!("无法读取项目注册表 {}：{error}", path.display())),
    }
}

fn save_json_atomic<T: Serialize>(path: &Path, prefix: &str, value: &T) -> Result<(), String> {
    save_json_atomic_with_mode(path, prefix, value, None)
}

/// 原子写 JSON：先写同目录临时文件再 rename。`mode` 给出时临时文件按该权限创建（rename
/// 保留权限），用于含凭据的文件。
fn save_json_atomic_with_mode<T: Serialize>(
    path: &Path,
    prefix: &str,
    value: &T,
    mode: Option<u32>,
) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "Desktop 状态路径缺少父目录".to_string())?;
    create_dir_all(parent).map_err(|error| format!("无法创建 Desktop 配置目录：{error}"))?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("系统时间不可用：{error}"))?
        .as_nanos();
    let temporary = parent.join(format!(".{prefix}-{}-{nonce}.tmp", std::process::id()));
    let payload = serde_json::to_vec_pretty(value)
        .map_err(|error| format!("无法序列化 Desktop 状态：{error}"))?;
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    if let Some(mode) = mode {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(mode);
    }
    #[cfg(not(unix))]
    let _ = mode;
    let mut file = options
        .open(&temporary)
        .map_err(|error| format!("无法创建 Desktop 状态临时文件：{error}"))?;
    file.write_all(&payload)
        .and_then(|_| file.sync_all())
        .map_err(|error| format!("无法写入 Desktop 状态：{error}"))?;
    rename(&temporary, path).map_err(|error| format!("无法原子更新 Desktop 状态：{error}"))
}

fn save_project_registry(path: &Path, registry: &DesktopProjectRegistry) -> Result<(), String> {
    save_json_atomic(path, "projects", registry)
}

fn normalize_recovery_record(mut record: GatewayRecoveryRecord) -> GatewayRecoveryRecord {
    if record.workspace_root.is_empty() {
        record.workspace_root = record.repo_root.clone();
    }
    let root = Path::new(&record.workspace_root);
    if record.runtime_id.is_empty() {
        record.runtime_id = runtime_id_for(&record.scope, root);
    }
    if record.project_id.is_none() && record.scope == PROJECT_SCOPE {
        record.project_id = Some(project_id(root));
    }
    record
}

fn load_gateway_recoveries(path: &Path) -> Result<GatewayRecoveryRegistry, String> {
    match read_to_string(path) {
        Ok(content) => {
            let stored: StoredGatewayRecovery =
                serde_json::from_str(&content).map_err(|error| {
                    format!("runtime 恢复记录已损坏，请检查 {}：{error}", path.display())
                })?;
            let mut registry = match stored {
                StoredGatewayRecovery::Registry(registry) => registry,
                StoredGatewayRecovery::Legacy(record) => GatewayRecoveryRegistry {
                    version: 1,
                    runtimes: vec![record],
                },
            };
            registry.version = 1;
            registry.runtimes = registry
                .runtimes
                .into_iter()
                .map(normalize_recovery_record)
                .collect();
            Ok(registry)
        }
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(GatewayRecoveryRegistry {
            version: 1,
            runtimes: Vec::new(),
        }),
        Err(error) => Err(format!(
            "无法读取 runtime 恢复记录 {}：{error}",
            path.display()
        )),
    }
}

fn load_gateway_recovery(
    path: &Path,
    runtime_id: Option<&str>,
) -> Result<Option<GatewayRecoveryRecord>, String> {
    let registry = load_gateway_recoveries(path)?;
    Ok(match runtime_id {
        Some(runtime_id) => registry
            .runtimes
            .into_iter()
            .find(|record| record.runtime_id == runtime_id),
        None => registry
            .runtimes
            .into_iter()
            .max_by_key(|record| record.updated_at),
    })
}

fn save_gateway_recovery(path: &Path, record: &GatewayRecoveryRecord) -> Result<(), String> {
    let record = normalize_recovery_record(record.clone());
    let mut registry = load_gateway_recoveries(path)?;
    registry
        .runtimes
        .retain(|existing| existing.runtime_id != record.runtime_id);
    registry.runtimes.push(record);
    registry
        .runtimes
        .sort_by(|left, right| right.updated_at.cmp(&left.updated_at));
    save_json_atomic(path, "gateway-recovery", &registry)
}

fn clear_gateway_recovery_at(path: &Path, runtime_id: Option<&str>) -> Result<(), String> {
    let Some(runtime_id) = runtime_id else {
        return match remove_file(path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
            Err(error) => Err(format!("无法清除 runtime 恢复记录：{error}")),
        };
    };
    let mut registry = load_gateway_recoveries(path)?;
    registry
        .runtimes
        .retain(|record| record.runtime_id != runtime_id);
    if registry.runtimes.is_empty() {
        clear_gateway_recovery_at(path, None)
    } else {
        save_json_atomic(path, "gateway-recovery", &registry)
    }
}

fn project_id(root: &Path) -> String {
    format!("{:x}", Sha256::digest(root.to_string_lossy().as_bytes()))[..20].to_string()
}

fn runtime_id_for(scope: &str, root: &Path) -> String {
    if scope == GENERAL_SCOPE {
        GENERAL_SCOPE.into()
    } else {
        format!("{scope}-{}", project_id(root))
    }
}

fn now_epoch_seconds() -> Result<u64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|error| format!("系统时间不可用：{error}"))
}

fn crashed_recovery_record(
    previous: Option<GatewayRecoveryRecord>,
    status: &GatewayProcessStatus,
) -> Option<GatewayRecoveryRecord> {
    let workspace_root = status.workspace_root.clone()?;
    let repo_root = status
        .repo_root
        .clone()
        .unwrap_or_else(|| workspace_root.clone());
    let now = now_epoch_seconds().ok()?;
    let started_at = previous
        .as_ref()
        .map(|record| record.started_at)
        .unwrap_or(now);
    let base_url = previous
        .as_ref()
        .map(|record| record.base_url.clone())
        .or_else(|| status.base_url.clone())
        .unwrap_or_default();
    Some(GatewayRecoveryRecord {
        runtime_id: status.runtime_id.clone()?,
        project_id: status.project_id.clone(),
        workspace_id: status.workspace_id.clone(),
        scope: status.scope.clone().unwrap_or_else(default_project_scope),
        workspace_root,
        repo_root,
        base_url,
        pid: None,
        started_at,
        updated_at: now,
        status: "crashed".into(),
        message: status.message.clone(),
    })
}

fn upsert_project(
    registry: &mut DesktopProjectRegistry,
    profile: DesktopProjectProfile,
) -> DesktopProjectProfile {
    registry.projects.retain(|item| item.id != profile.id);
    registry.projects.insert(0, profile.clone());
    registry
        .projects
        .sort_by(|left, right| right.last_opened_at.cmp(&left.last_opened_at));
    registry.projects.truncate(MAX_DESKTOP_PROJECTS);
    profile
}

fn remember_project_at(
    path: &Path,
    repo_root: &str,
    base_url: &str,
) -> Result<DesktopProjectProfile, String> {
    let selected = canonical_repo_root(repo_root)?;
    let root = git_workspace_root(&selected)?;
    let normalized_url = normalize_local_base_url(base_url)?;
    let profile = DesktopProjectProfile {
        id: project_id(&root),
        name: root
            .file_name()
            .map(|name| name.to_string_lossy().to_string())
            .filter(|name| !name.is_empty())
            .unwrap_or_else(|| root.to_string_lossy().to_string()),
        repo_root: root.to_string_lossy().to_string(),
        base_url: normalized_url,
        last_opened_at: now_epoch_seconds()?,
        kind: PROJECT_KIND_LOCAL.into(),
    };
    let mut registry = load_project_registry(path)?;
    let profile = upsert_project(&mut registry, profile);
    save_project_registry(path, &registry)?;
    Ok(profile)
}

fn forget_project_at(path: &Path, project_id: &str) -> Result<Vec<DesktopProjectProfile>, String> {
    let mut registry = load_project_registry(path)?;
    registry.projects.retain(|project| project.id != project_id);
    save_project_registry(path, &registry)?;
    Ok(registry.projects)
}

/// 注册前置门：把入参目录解析到 Git 根，并回答「这个根是否已在注册表里」。
/// 只有**新根**需要用户确认——已注册项目的重连续期不改变可读边界，零打扰。
fn project_registration_gate(path: &Path, repo_root: &str) -> Result<(PathBuf, bool), String> {
    let selected = canonical_repo_root(repo_root)?;
    let root = git_workspace_root(&selected)?;
    let id = project_id(&root);
    let known = load_project_registry(path)?
        .projects
        .iter()
        .any(|project| project.id == id);
    Ok((root, known))
}

fn remember_project_flow(
    path: &Path,
    repo_root: &str,
    base_url: &str,
    known: bool,
    confirmed: bool,
) -> Result<DesktopProjectProfile, String> {
    // 注册即扩权：项目根会并入 Desktop 文件读取围栏（workspace_fence_roots）。
    // 新根未获用户确认一律拒绝，且不能留下任何注册表痕迹。
    if !known && !confirmed {
        return Err("新项目目录注册未获用户确认，已取消".into());
    }
    remember_project_at(path, repo_root, base_url)
}

async fn confirm_project_registration(app: &AppHandle, root: &Path) -> Result<bool, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    // 与 confirm_llm_profile_change 同理：原生对话框在 webview 进程之外，被注入的
    // 页面脚本无法替用户点「确认」，静默把任意 Git 仓库并入读取围栏因此不生效。
    let message = format!(
        "网页层请求把以下目录注册为受信项目（Desktop 将允许读取其中的文件）：\n\n{}\n\n仅当这是你刚在界面上输入/选择的目录时才确认。",
        root.display()
    );
    let app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&app, app.dialog().message(message))
            .title("确认注册项目目录")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom(
                "确认注册".into(),
                "取消".into(),
            ))
            .blocking_show()
    })
    .await
    .map_err(|error| format!("项目注册确认对话框失败：{error}"))
}

const MAX_CONFIRM_MESSAGE_CHARS: usize = 2000;

fn confirm_action_message(message: &str) -> String {
    let mut chars = message.chars();
    let mut text: String = chars.by_ref().take(MAX_CONFIRM_MESSAGE_CHARS).collect();
    if chars.next().is_some() {
        text.push('…');
    }
    text
}

/// 前端破坏性操作的确认框（撤销修改、删除、push 等）。
///
/// 不能用 window.confirm：dialog 插件把它改写成调用已不存在的 `confirm` 命令的 async 函数，
/// 同步调用方拿到的 Promise 恒为真值，确认形同虚设。这里阻塞到用户点选为止。
/// 它是给真人的确认，不是对抗页面脚本的安全边界——那类敏感动作仍由各自命令在 Rust 侧弹框。
#[tauri::command]
async fn confirm_action(app: AppHandle, message: String) -> Result<bool, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    let message = confirm_action_message(&message);
    tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&app, app.dialog().message(message))
            .title("VortoCode")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom("确认".into(), "取消".into()))
            .blocking_show()
    })
    .await
    .map_err(|error| format!("确认对话框失败：{error}"))
}

#[tauri::command]
fn list_desktop_projects(app: AppHandle) -> Result<Vec<DesktopProjectListing>, String> {
    Ok(load_project_registry(&project_registry_path(&app)?)?
        .projects
        .into_iter()
        .map(project_listing)
        .collect())
}

const PROJECT_SESSIONS_PER_PROJECT: usize = 3;
const MAX_SESSION_FILE_BYTES: u64 = 8 * 1024 * 1024;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ProjectSessionSummary {
    project_id: String,
    sid: String,
    title: String,
    updated_at: u64,
}

/// 与 runtime 的 title_from_transcript 同口径：显式标题优先，否则取首条用户消息（单行、40 字截断）。
fn session_title(payload: &serde_json::Value) -> Option<String> {
    if let Some(title) = payload.get("title").and_then(serde_json::Value::as_str) {
        if !title.trim().is_empty() {
            return Some(title.trim().to_string());
        }
    }
    let first = payload
        .get("transcript")?
        .as_array()?
        .iter()
        .find(|item| item.get("role").and_then(serde_json::Value::as_str) == Some("user"))?
        .get("text")?
        .as_str()?
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    if first.is_empty() {
        return None;
    }
    let mut title: String = first.chars().take(40).collect();
    if first.chars().count() > 40 {
        title.push('…');
    }
    Some(title)
}

/// 一个项目最近的几个会话（只读磁盘上的会话档，不碰 runtime）。空会话不会落盘，所以都有内容。
fn recent_project_sessions(project: &DesktopProjectProfile) -> Vec<ProjectSessionSummary> {
    let directory = Path::new(&project.repo_root).join(".vortocode").join("web_sessions");
    let Ok(entries) = std::fs::read_dir(&directory) else {
        return Vec::new();
    };
    let mut files = entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let path = entry.path();
            let sid = path.file_stem()?.to_str()?.to_string();
            let valid = path.extension().and_then(|ext| ext.to_str()) == Some("json")
                && !sid.is_empty()
                && sid.len() <= 64
                && sid.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_');
            let metadata = entry.metadata().ok()?;
            if !valid || !metadata.is_file() || metadata.len() > MAX_SESSION_FILE_BYTES {
                return None;
            }
            let modified = metadata
                .modified()
                .ok()?
                .duration_since(UNIX_EPOCH)
                .ok()?
                .as_secs();
            Some((modified, sid, path))
        })
        .collect::<Vec<_>>();
    files.sort_by(|left, right| right.0.cmp(&left.0));
    files
        .into_iter()
        .filter_map(|(updated_at, sid, path)| {
            let payload: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()?;
            Some(ProjectSessionSummary {
                project_id: project.id.clone(),
                sid,
                title: session_title(&payload)?,
                updated_at,
            })
        })
        .take(PROJECT_SESSIONS_PER_PROJECT)
        .collect()
}

/// 侧边栏「项目」下直接列出各项目最近的会话：只看已登记、目录还在的本机项目。
#[tauri::command(async)]
fn list_project_sessions(app: AppHandle) -> Result<Vec<ProjectSessionSummary>, String> {
    Ok(load_project_registry(&project_registry_path(&app)?)?
        .projects
        .iter()
        .filter(|project| project.kind != "remote" && Path::new(&project.repo_root).is_dir())
        .flat_map(recent_project_sessions)
        .collect())
}

/// 列表里额外带上「本机目录还在不在」：被清理的临时目录、移走的仓库不该在启动时被反复恢复。
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopProjectListing {
    #[serde(flatten)]
    profile: DesktopProjectProfile,
    missing: bool,
}

fn project_listing(profile: DesktopProjectProfile) -> DesktopProjectListing {
    // 远端项目的路径在服务器上，本机检查不了，一律当作在。
    let missing = profile.kind != "remote" && !Path::new(&profile.repo_root).is_dir();
    DesktopProjectListing { profile, missing }
}

#[tauri::command]
async fn remember_desktop_project(
    app: AppHandle,
    repo_root: String,
    base_url: String,
) -> Result<DesktopProjectProfile, String> {
    let path = project_registry_path(&app)?;
    let (root, known) = project_registration_gate(&path, &repo_root)?;
    let confirmed = if known {
        false // 已注册：不弹窗，flow 靠 known 放行
    } else {
        confirm_project_registration(&app, &root).await?
    };
    remember_project_flow(&path, &repo_root, &base_url, known, confirmed)
}

#[tauri::command]
async fn pick_desktop_project(
    app: AppHandle,
    base_url: String,
) -> Result<Option<DesktopProjectProfile>, String> {
    use tauri_plugin_dialog::DialogExt;
    // 目录选择整体收在后端：webview 只能「请求弹出选择器」，没有命名任意路径的通道；
    // 人在 OS 对话框里点选目录本身就是授权，无需再弹一次确认。
    let picker = app.clone();
    let picked = tauri::async_runtime::spawn_blocking(move || {
        picker
            .dialog()
            .file()
            .set_title("选择 Git 项目目录")
            .blocking_pick_folder()
    })
    .await
    .map_err(|error| format!("目录选择对话框失败：{error}"))?;
    let Some(choice) = picked else {
        return Ok(None);
    };
    let folder = choice
        .into_path()
        .map_err(|error| format!("所选目录不可用：{error}"))?;
    remember_project_at(
        &project_registry_path(&app)?,
        &folder.to_string_lossy(),
        &base_url,
    )
    .map(Some)
}

// 可能阻塞：移出主线程，原因见 get_llm_profile。
#[tauri::command(async)]
fn forget_desktop_project(
    app: AppHandle,
    project_id: String,
) -> Result<Vec<DesktopProjectProfile>, String> {
    let path = project_registry_path(&app)?;
    // 若被遗忘的是远程项目，一并清掉它在 Keychain 里的连接 token——不留凭据残迹。
    let registry = load_project_registry(&path)?;
    if let Some(project) = registry.projects.iter().find(|item| item.id == project_id) {
        if project.kind == PROJECT_KIND_REMOTE {
            remote_token_keychain::delete(&project_id)?;
        }
    }
    forget_project_at(&path, &project_id)
}

// ── 远程工作区注册（R3a）──────────────────────────────────────────────────────
// 与本地注册（remember_project_at）完全分开：local 流的强不变量（Git 根 + 本机 URL）一点
// 不动；remote 是独立、显式校验（validate_remote_server_url）的旁路。repo_root 存服务器侧
// 路径原样、base_url 存归一化后的远端 server_url、token 入独立 Keychain 服务（按 id 作 account）。

// 远程项目 id：以「remote\0url\0服务器侧根」哈希——与本地 id（哈希本机路径）不同源、不碰撞。
fn remote_project_id(normalized_url: &str, remote_repo_root: &str) -> String {
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
fn normalize_remote_token(token: &str) -> Result<String, String> {
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

fn remote_project_name(name: &str, remote_repo_root: &str, normalized_url: &str) -> String {
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

fn remember_remote_at(
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

fn remember_remote_flow(
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

async fn confirm_remote_registration(app: &AppHandle, server_url: &str) -> Result<bool, String> {
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
async fn remember_remote_project(
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

// ── 远程传输层（R3a-2a）：HTTP 代理 ──────────────────────────────────────────
// 方案 A 的落点：**webview 从不提供目标 URL**，只给注册过的 project_id——由 Rust 查注册表取
// base_url、从 Keychain 取 token 并代注 Authorization。于是「只连注册过的 server_url」不再是
// 一道可被绕过的校验，而是结构上不可能（没有 URL 入参可供伪造）；token 原文也不进 webview。

const MAX_REMOTE_RESPONSE_BYTES: usize = 32 * 1024 * 1024;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RemoteHttpResponse {
    status: u16,
    ok: bool,
    body: String,
}

/// 远程请求路径闸：只收远端 gateway HTTP 面所在的 `/api/` 前缀。
/// 前缀白名单同时压缩「混淆代理人」可达面——webview 侧一旦被注入内容驱动，能借这条通道
/// 触达的远端路由被限死在 /api/ 内（副作用路由的确认门归 R3a-2c 接线时设计）。
fn sanitize_remote_path(path: &str) -> Result<String, String> {
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
fn same_remote_origin(target: &Url, registered: &Url) -> bool {
    target.scheme() == registered.scheme()
        && target.host_str() == registered.host_str()
        && target.port_or_known_default() == registered.port_or_known_default()
}

/// 拼远程 URL。终局不变量：拼出来的 scheme/host/port 必须与注册的服务器逐项相等。
/// 诚实说明：在当前路径闸（强制 `/api/` 前缀）下这条不变量**是不可达兜底**，留着是为了
/// 将来放宽路径闸或改用 `Url::join` 时仍有最后一道锁，不是当下的主防线。
fn build_remote_url(base: &str, path: &str) -> Result<Url, String> {
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
fn resolve_remote_base(registry_path: &Path, project_id: &str) -> Result<String, String> {
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

fn normalize_http_method(method: &str) -> Result<reqwest::Method, String> {
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
async fn remote_http_request(
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
fn normalize_ws_sid(value: &str) -> Result<String, String> {
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
fn remote_ws_url(base: &str, sid: &str) -> Result<Url, String> {
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

const MAX_REMOTE_WS_CONNECTIONS: usize = 8;
const REMOTE_WS_CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
const REMOTE_WS_SEND_QUEUE: usize = 256;
// 与本地路径（gateway.ts 显式设的 16/32 MiB）及 HTTP 侧 32MB 封顶同口径——
// tungstenite 默认 64 MiB 比两者都松，不能就这么用。
const MAX_REMOTE_WS_MESSAGE_BYTES: usize = 32 * 1024 * 1024;
const MAX_REMOTE_WS_FRAME_BYTES: usize = 16 * 1024 * 1024;

struct RemoteWsHandle {
    sender: tokio::sync::mpsc::Sender<String>,
    // 读侧必须留 abort 句柄：stream.split() 的两半共用 BiLock，**只 drop 写半边不会释放
    // socket**；对端不回 Close 也不 EOF 时读任务永久挂起，没句柄就再也拆不掉它。
    reader: Option<tauri::async_runtime::JoinHandle<()>>,
}

#[derive(Default)]
struct RemoteWsRegistry {
    connections: Mutex<HashMap<String, RemoteWsHandle>>,
    counter: Mutex<u64>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct RemoteWsFrame {
    conn_id: String,
    data: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct RemoteWsClosed {
    conn_id: String,
    reason: String,
}

/// 底层握手错误**不能原样回给 webview**：tungstenite 会把请求头值的 Debug 写进错误串，
/// token 就这么泄出去（F-6 实测可 100% 还原）。这里只回粗粒度原因，明细进应用日志。
fn remote_ws_failure_reason(error: &tokio_tungstenite::tungstenite::Error) -> String {
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
fn finish_remote_ws(app: &AppHandle, conn_id: &str, reason: &str) -> Option<RemoteWsHandle> {
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
async fn remote_ws_connect(
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
fn remote_ws_send(
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
fn remote_ws_close(app: AppHandle, conn_id: String) -> Result<(), String> {
    // 摘表并通报；**abort 读任务**才真正释放 socket（只摘 sender 拆不掉，见 RemoteWsHandle 注释）。
    if let Some(handle) = finish_remote_ws(&app, &conn_id, "closed-by-client") {
        if let Some(reader) = handle.reader {
            reader.abort();
        }
    }
    Ok(())
}

#[tauri::command]
fn get_gateway_recovery(
    app: AppHandle,
    runtime_id: Option<String>,
) -> Result<Option<GatewayRecoveryRecord>, String> {
    load_gateway_recovery(&gateway_recovery_path(&app)?, runtime_id.as_deref())
}

#[tauri::command]
fn list_gateway_recoveries(app: AppHandle) -> Result<Vec<GatewayRecoveryRecord>, String> {
    Ok(load_gateway_recoveries(&gateway_recovery_path(&app)?)?.runtimes)
}

#[tauri::command]
fn clear_gateway_recovery(app: AppHandle, runtime_id: Option<String>) -> Result<(), String> {
    clear_gateway_recovery_at(&gateway_recovery_path(&app)?, runtime_id.as_deref())
}

fn smoke_marker_path() -> Result<Option<PathBuf>, String> {
    let raw = std::env::var("VORTOCODE_DESKTOP_SMOKE_FILE").unwrap_or_default();
    if raw.trim().is_empty() {
        return Ok(None);
    }
    let requested = PathBuf::from(raw.trim());
    let file_name = requested
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| "GUI smoke 标记文件名无效".to_string())?;
    if !file_name.starts_with("vortocode-desktop-smoke-") || !file_name.ends_with(".json") {
        return Err("GUI smoke 标记必须使用 vortocode-desktop-smoke-*.json".into());
    }
    let parent = requested
        .parent()
        .ok_or_else(|| "GUI smoke 标记缺少父目录".to_string())?
        .canonicalize()
        .map_err(|error| format!("GUI smoke 临时目录不可用：{error}"))?;
    let temp = std::env::temp_dir()
        .canonicalize()
        .map_err(|error| format!("系统临时目录不可用：{error}"))?;
    if !parent.starts_with(&temp) {
        return Err("GUI smoke 标记只能写入系统临时目录".into());
    }
    Ok(Some(parent.join(file_name)))
}

#[tauri::command]
fn desktop_smoke_ready(app: AppHandle) -> Result<bool, String> {
    let Some(path) = smoke_marker_path()? else {
        return Ok(false);
    };
    let sidecar = app
        .shell()
        .sidecar("vortocode-runtime")
        .map_err(|error| format!("GUI smoke 无法定位内置 runtime：{error}"))?;
    let mut sidecar_command: Command = sidecar.into();
    let sidecar_output = sidecar_command
        .arg("--version")
        .output()
        .map_err(|error| format!("GUI smoke 无法启动内置 runtime：{error}"))?;
    let sidecar_version = String::from_utf8_lossy(&sidecar_output.stdout)
        .trim()
        .to_string();
    if !sidecar_output.status.success() || !sidecar_version.starts_with("vortocode-runtime ") {
        return Err(format!(
            "GUI smoke 内置 runtime 自检失败：{}",
            String::from_utf8_lossy(&sidecar_output.stderr).trim()
        ));
    }
    let payload = serde_json::json!({
        "uiMounted": true,
        "pid": std::process::id(),
        "sidecarReady": true,
        "sidecarVersion": sidecar_version,
        "timestamp": now_epoch_seconds()?,
    });
    let encoded = serde_json::to_vec_pretty(&payload)
        .map_err(|error| format!("无法序列化 GUI smoke 结果：{error}"))?;
    let mut marker = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&path)
        .map_err(|error| format!("无法安全创建 GUI smoke 标记：{error}"))?;
    marker
        .write_all(&encoded)
        .and_then(|_| marker.sync_all())
        .map_err(|error| format!("无法写入 GUI smoke 标记：{error}"))?;
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(150));
        app.exit(0);
    });
    Ok(true)
}

fn normalize_workspace_relative(relative_path: &str) -> Result<PathBuf, String> {
    let raw = Path::new(relative_path.trim());
    if raw.as_os_str().is_empty() || raw.is_absolute() {
        return Err("文件路径必须是项目内的相对路径".into());
    }

    let mut normalized = PathBuf::new();
    for component in raw.components() {
        match component {
            Component::Normal(part) => normalized.push(part),
            Component::CurDir => {}
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err("文件路径不能离开项目目录".into());
            }
        }
    }
    if normalized.as_os_str().is_empty() {
        return Err("文件路径不能为空".into());
    }
    Ok(normalized)
}

fn resolve_workspace_file(root: &Path, relative_path: &str) -> Result<(PathBuf, PathBuf), String> {
    let relative = normalize_workspace_relative(relative_path)?;
    let resolved = root
        .join(&relative)
        .canonicalize()
        .map_err(|error| format!("文件不可用：{error}"))?;
    if !resolved.starts_with(root) {
        return Err("拒绝读取项目目录之外的文件".into());
    }
    if !resolved.is_file() {
        return Err("选择的路径不是文件".into());
    }
    Ok((relative, resolved))
}

// ---- 工作区命令的路径围栏 ----
// repo_root 由 webview 传入，不可信：只放行「用户显式登记过 / Desktop 自己创建过」的根
// 及其子目录——注册项目集（projects.json）∪ Desktop 自管工作区子树（general/scratch）∪
// 在管 runtime 根 ∪ 恢复记录根。子目录只会缩小可读集，故允许；比对在 canonicalize 之后
// 按路径组件进行，symlink 会先被解析再判定。

fn workspace_fence_roots(
    registry: &DesktopProjectRegistry,
    recoveries: &GatewayRecoveryRegistry,
    managed_base: Option<PathBuf>,
    supervisor: &GatewaySupervisorInner,
) -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();
    roots.extend(
        registry
            .projects
            .iter()
            // 只并入 local 条目：remote 的 repo_root 是**服务器侧**路径（webview 传入、未经本地
            // 校验），绝不能当本机受信根喂进本地文件读取围栏——否则 repo_root="/" 之类会把本地读
            // 面撑到整个文件系统（远程文件走 R3a-2 的 server API，不经本地围栏）。
            .filter(|project| project.kind != PROJECT_KIND_REMOTE)
            .map(|project| PathBuf::from(&project.repo_root)),
    );
    roots.extend(managed_base);
    for record in &recoveries.runtimes {
        for root in [&record.workspace_root, &record.repo_root] {
            if !root.is_empty() {
                roots.push(PathBuf::from(root));
            }
        }
    }
    for runtime in supervisor.runtimes.values() {
        roots.extend(runtime.workspace_root.clone());
        roots.extend(runtime.repo_root.clone());
    }
    roots
}

fn fence_workspace_root(trusted: &[PathBuf], repo_root: &str) -> Result<PathBuf, String> {
    let requested = canonical_repo_root(repo_root)?;
    let authorized = trusted.iter().any(|root| {
        root.canonicalize()
            .is_ok_and(|canonical| requested.starts_with(&canonical))
    });
    if authorized {
        Ok(requested)
    } else {
        Err("该目录不属于 Desktop 已登记的项目或托管工作区，已拒绝访问".into())
    }
}

fn desktop_workspace_fence(app: &AppHandle, state: &GatewayProcess) -> Result<Vec<PathBuf>, String> {
    // 注册表/恢复记录读取失败时按「收窄」处理（fail-closed）：宁可拒绝合法预览，不放宽围栏。
    let registry = project_registry_path(app)
        .and_then(|path| load_project_registry(&path))
        .unwrap_or_default();
    let recoveries = gateway_recovery_path(app)
        .and_then(|path| load_gateway_recoveries(&path))
        .unwrap_or_default();
    let managed_base = app
        .path()
        .app_data_dir()
        .ok()
        .map(|directory| directory.join("workspaces"));
    let supervisor = state
        .0
        .lock()
        .map_err(|_| "runtime 状态锁已损坏".to_string())?;
    Ok(workspace_fence_roots(
        &registry,
        &recoveries,
        managed_base,
        &supervisor,
    ))
}

#[tauri::command]
fn list_workspace_files(
    app: AppHandle,
    state: State<'_, GatewayProcess>,
    repo_root: String,
) -> Result<WorkspaceFileList, String> {
    list_workspace_files_within(&desktop_workspace_fence(&app, state.inner())?, &repo_root)
}

fn list_workspace_files_within(
    trusted: &[PathBuf],
    repo_root: &str,
) -> Result<WorkspaceFileList, String> {
    let root = fence_workspace_root(trusted, repo_root)?;
    let output = Command::new("git")
        .args([
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ])
        .current_dir(&root)
        .output()
        .map_err(|error| format!("无法运行 git ls-files：{error}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "源码工作区需要 Git 仓库：{}",
            detail.trim().chars().take(300).collect::<String>()
        ));
    }

    let mut files = output
        .stdout
        .split(|byte| *byte == 0)
        .filter_map(|raw| std::str::from_utf8(raw).ok())
        .filter(|path| !path.is_empty())
        .filter_map(|path| {
            normalize_workspace_relative(path)
                .ok()
                .map(|relative| relative.to_string_lossy().replace('\\', "/"))
        })
        .collect::<Vec<_>>();
    files.sort_unstable();
    files.dedup();
    let truncated = files.len() > MAX_WORKSPACE_FILES;
    files.truncate(MAX_WORKSPACE_FILES);
    Ok(WorkspaceFileList {
        root: root.to_string_lossy().to_string(),
        files,
        truncated,
    })
}

#[tauri::command]
fn read_workspace_file(
    app: AppHandle,
    state: State<'_, GatewayProcess>,
    repo_root: String,
    relative_path: String,
) -> Result<WorkspaceFileContent, String> {
    read_workspace_file_within(
        &desktop_workspace_fence(&app, state.inner())?,
        &repo_root,
        &relative_path,
    )
}

fn read_workspace_file_within(
    trusted: &[PathBuf],
    repo_root: &str,
    relative_path: &str,
) -> Result<WorkspaceFileContent, String> {
    let root = fence_workspace_root(trusted, repo_root)?;
    let (relative, resolved) = resolve_workspace_file(&root, relative_path)?;
    let size = resolved
        .metadata()
        .map_err(|error| format!("无法读取文件信息：{error}"))?
        .len();
    if size > MAX_PREVIEW_BYTES {
        return Err(format!(
            "文件超过源码预览上限（{} KiB）",
            MAX_PREVIEW_BYTES / 1024
        ));
    }

    let bytes = read(&resolved).map_err(|error| format!("读取文件失败：{error}"))?;
    if bytes.contains(&0) {
        return Err("二进制文件不能在源码预览中打开".into());
    }
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    let content = String::from_utf8(bytes).map_err(|_| "文件不是有效 UTF-8 文本".to_string())?;
    Ok(WorkspaceFileContent {
        path: relative.to_string_lossy().replace('\\', "/"),
        content,
        size,
        sha256,
    })
}

fn editor_candidates(path: &Path, line: u32) -> Vec<(String, Vec<OsString>, bool)> {
    let goto = OsString::from(format!("{}:{line}", path.to_string_lossy()));
    let direct = path.as_os_str().to_os_string();
    let mut candidates = vec![
        (
            "code".into(),
            vec![OsString::from("--goto"), goto.clone()],
            true,
        ),
        ("cursor".into(), vec![OsString::from("--goto"), goto], true),
    ];

    #[cfg(target_os = "macos")]
    candidates.push(("open".into(), vec![direct], false));
    #[cfg(target_os = "linux")]
    candidates.push(("xdg-open".into(), vec![direct], false));
    #[cfg(target_os = "windows")]
    candidates.push(("explorer".into(), vec![direct], false));

    candidates
}

#[tauri::command]
fn open_workspace_file(
    app: AppHandle,
    state: State<'_, GatewayProcess>,
    repo_root: String,
    relative_path: String,
    line: Option<u32>,
) -> Result<OpenWorkspaceFileResult, String> {
    open_workspace_file_within(
        &desktop_workspace_fence(&app, state.inner())?,
        &repo_root,
        &relative_path,
        line,
    )
}

fn open_workspace_file_within(
    trusted: &[PathBuf],
    repo_root: &str,
    relative_path: &str,
    line: Option<u32>,
) -> Result<OpenWorkspaceFileResult, String> {
    let root = fence_workspace_root(trusted, repo_root)?;
    let (_relative, resolved) = resolve_workspace_file(&root, relative_path)?;
    let line = line.unwrap_or(1);
    if line == 0 {
        return Err("源码行号必须从 1 开始".into());
    }

    let mut failures = Vec::new();
    for (launcher, args, line_aware) in editor_candidates(&resolved, line) {
        let mut command = Command::new(&launcher);
        command
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        match command.spawn() {
            Ok(mut child) => {
                std::thread::spawn(move || {
                    let _ = child.wait();
                });
                let message = if line_aware {
                    format!("已用 {launcher} 打开第 {line} 行")
                } else {
                    format!(
                        "未找到 VS Code/Cursor CLI，已用系统默认应用打开（请定位到第 {line} 行）"
                    )
                };
                return Ok(OpenWorkspaceFileResult {
                    launcher,
                    line_aware,
                    message,
                });
            }
            Err(error) if error.kind() == ErrorKind::NotFound => continue,
            Err(error) => failures.push(format!("{launcher}: {error}")),
        }
    }

    let detail = if failures.is_empty() {
        "系统中未找到可用启动器".into()
    } else {
        failures.join("；")
    };
    Err(format!("无法打开外部编辑器：{detail}"))
}

fn process_status(inner: &mut GatewayProcessInner) -> GatewayProcessStatus {
    let checked = inner
        .child
        .as_mut()
        .map(|child| (child.id(), child.try_wait()));

    match checked {
        Some((pid, Ok(None))) => GatewayProcessStatus {
            running: true,
            pid: Some(pid),
            runtime_id: Some(inner.runtime_id.clone()),
            project_id: inner.project_id.clone(),
            workspace_id: inner.workspace_id.clone(),
            command: inner.command.clone(),
            scope: inner.scope.clone(),
            workspace_root: inner
                .workspace_root
                .as_ref()
                .map(|path| path.to_string_lossy().to_string()),
            repo_root: inner
                .repo_root
                .as_ref()
                .map(|path| path.to_string_lossy().to_string()),
            base_url: inner.base_url.clone(),
            message: "Desktop 启动的本机 runtime 正在运行".into(),
        },
        Some((_pid, Ok(Some(exit)))) => {
            inner.child = None;
            GatewayProcessStatus {
                running: false,
                pid: None,
                runtime_id: Some(inner.runtime_id.clone()),
                project_id: inner.project_id.clone(),
                workspace_id: inner.workspace_id.clone(),
                command: inner.command.clone(),
                scope: inner.scope.clone(),
                workspace_root: inner
                    .workspace_root
                    .as_ref()
                    .map(|path| path.to_string_lossy().to_string()),
                repo_root: inner
                    .repo_root
                    .as_ref()
                    .map(|path| path.to_string_lossy().to_string()),
                base_url: inner.base_url.clone(),
                message: format!("runtime 已退出（{exit}）"),
            }
        }
        Some((_pid, Err(error))) => GatewayProcessStatus {
            running: false,
            pid: None,
            runtime_id: Some(inner.runtime_id.clone()),
            project_id: inner.project_id.clone(),
            workspace_id: inner.workspace_id.clone(),
            command: inner.command.clone(),
            scope: inner.scope.clone(),
            workspace_root: inner
                .workspace_root
                .as_ref()
                .map(|path| path.to_string_lossy().to_string()),
            repo_root: inner
                .repo_root
                .as_ref()
                .map(|path| path.to_string_lossy().to_string()),
            base_url: inner.base_url.clone(),
            message: format!("无法读取 runtime 状态：{error}"),
        },
        None => GatewayProcessStatus {
            running: false,
            pid: None,
            runtime_id: None,
            project_id: None,
            workspace_id: None,
            command: None,
            scope: None,
            workspace_root: None,
            repo_root: None,
            base_url: None,
            message: "Desktop 尚未启动本机 runtime".into(),
        },
    }
}

fn empty_process_status(message: impl Into<String>) -> GatewayProcessStatus {
    GatewayProcessStatus {
        running: false,
        pid: None,
        runtime_id: None,
        project_id: None,
        workspace_id: None,
        command: None,
        scope: None,
        workspace_root: None,
        repo_root: None,
        base_url: None,
        message: message.into(),
    }
}

fn select_gateway_port_with<F, G>(
    preferred: u16,
    is_available: F,
    allocate: G,
) -> Result<u16, String>
where
    F: FnOnce(u16) -> bool,
    G: FnOnce() -> Result<u16, String>,
{
    if preferred >= 1024 && is_available(preferred) {
        return Ok(preferred);
    }

    let port = allocate()?;
    if port < 1024 {
        return Err("系统分配的本地引擎端口无效".into());
    }
    Ok(port)
}

fn select_gateway_port(preferred: u16) -> Result<u16, String> {
    select_gateway_port_with(
        preferred,
        |port| TcpListener::bind((Ipv4Addr::LOCALHOST, port)).is_ok(),
        || {
            let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
                .map_err(|error| format!("无法为本地引擎分配端口：{error}"))?;
            listener
                .local_addr()
                .map(|address| address.port())
                .map_err(|error| format!("无法读取本地引擎端口：{error}"))
        },
    )
}

fn prepare_gateway_process(command: &mut Command) {
    // PyInstaller one-file runtime 会再派生真正的 Python server。只杀直接 Child 会留下仍在
    // 监听的孙进程；独立进程组让 Desktop 能把整个托管 runtime 作为一个生命周期单元回收。
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
}

#[cfg(unix)]
fn process_group_alive(pid: u32) -> bool {
    let result = unsafe { libc::kill(-(pid as i32), 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

fn terminate_gateway_child(child: &mut Child) -> Result<(), String> {
    let pid = child.id();
    #[cfg(unix)]
    {
        let terminated = unsafe { libc::kill(-(pid as i32), libc::SIGTERM) };
        if terminated != 0 {
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(format!("无法停止 runtime 进程组：{error}"));
            }
        }
        for _ in 0..20 {
            let _ = child.try_wait();
            if !process_group_alive(pid) {
                let _ = child.wait();
                return Ok(());
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        let killed = unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
        if killed != 0 && std::io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH) {
            return Err(format!(
                "runtime 未在宽限期内退出，且无法终止进程组：{}",
                std::io::Error::last_os_error()
            ));
        }
        let _ = child.wait();
        return Ok(());
    }
    #[cfg(not(unix))]
    {
        child
            .kill()
            .map_err(|error| format!("停止 runtime 失败：{error}"))?;
        let _ = child.wait();
        Ok(())
    }
}

fn build_command(
    executable: &str,
    module: bool,
    repo_root: &Path,
    port: u16,
    token: &str,
    scope: &str,
) -> Command {
    configure_gateway_command(
        Command::new(executable),
        module,
        repo_root,
        port,
        token,
        scope,
    )
}

fn configure_gateway_command(
    mut command: Command,
    module: bool,
    repo_root: &Path,
    port: u16,
    token: &str,
    scope: &str,
) -> Command {
    if module {
        command.args(["-m", "src.cli"]);
    }
    command.args(["server", "--host", "127.0.0.1", "--port"]);
    command.arg(port.to_string());
    command.current_dir(repo_root);
    command.env("VORTOCODE_WORKSPACE_SCOPE", scope);
    // 监护进程 pid：runtime 据此在我们被强杀/崩溃时自己退出。正常退出走 RunEvent::Exit 的
    // terminate_gateway_child；强杀时那条回调根本不会执行，于是 runtime 会继续占着端口、
    // 持着一个能调模型的 agent，而界面上再无入口（2026-09-17 真机诊断留下两对孤儿进程）。
    command.env("VORTOCODE_SUPERVISOR_PID", std::process::id().to_string());
    if token.is_empty() {
        command.env_remove("VORTOCODE_API_TOKEN");
        command.env_remove("AUTODEV_API_TOKEN");
    } else {
        command.env("VORTOCODE_API_TOKEN", token);
    }
    command
}

fn build_bundled_runtime_command(
    app: &AppHandle,
    repo_root: &Path,
    port: u16,
    token: &str,
    scope: &str,
) -> Result<Command, String> {
    let sidecar = app
        .shell()
        .sidecar("vortocode-runtime")
        .map_err(|error| format!("无法定位 Desktop 内置 runtime：{error}"))?;
    Ok(configure_gateway_command(
        sidecar.into(),
        false,
        repo_root,
        port,
        token,
        scope,
    ))
}

fn is_vortocode_development_root(root: &Path) -> bool {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .is_ok_and(|source_root| source_root == root)
        && root.join("src/cli.py").is_file()
}

#[tauri::command]
fn gateway_process_status(
    app: AppHandle,
    runtime_id: Option<String>,
    state: State<'_, GatewayProcess>,
) -> Result<GatewayProcessStatus, String> {
    let mut supervisor = state
        .0
        .lock()
        .map_err(|_| "runtime 状态锁已损坏".to_string())?;
    let target = runtime_id.or_else(|| supervisor.active_runtime_id.clone());
    let Some(target) = target else {
        return Ok(empty_process_status("Desktop 尚未启动本机 runtime"));
    };
    let Some(inner) = supervisor.runtimes.get_mut(&target) else {
        if supervisor.active_runtime_id.as_deref() == Some(target.as_str()) {
            supervisor.active_runtime_id = None;
        }
        return Ok(empty_process_status("该 runtime 当前未由 Desktop 托管"));
    };
    let had_child = inner.child.is_some();
    let status = process_status(inner);
    let crashed = had_child && !status.running;
    if crashed {
        supervisor.runtimes.remove(&target);
        if supervisor.active_runtime_id.as_deref() == Some(target.as_str()) {
            supervisor.active_runtime_id = None;
        }
        if let Ok(path) = gateway_recovery_path(&app) {
            let previous = load_gateway_recovery(&path, Some(&target)).ok().flatten();
            if let Some(record) = crashed_recovery_record(previous, &status) {
                let _ = save_gateway_recovery(&path, &record);
            }
        }
    }
    Ok(status)
}

#[tauri::command]
fn list_gateway_processes(
    app: AppHandle,
    state: State<'_, GatewayProcess>,
) -> Result<Vec<GatewayProcessStatus>, String> {
    let mut supervisor = state
        .0
        .lock()
        .map_err(|_| "runtime 状态锁已损坏".to_string())?;
    let runtime_ids = supervisor.runtimes.keys().cloned().collect::<Vec<_>>();
    let mut statuses = Vec::new();
    let mut crashed = Vec::new();
    for runtime_id in runtime_ids {
        let Some(inner) = supervisor.runtimes.get_mut(&runtime_id) else {
            continue;
        };
        let had_child = inner.child.is_some();
        let status = process_status(inner);
        if status.running {
            statuses.push(status);
        } else if had_child {
            crashed.push((runtime_id, status));
        }
    }
    for (runtime_id, status) in crashed {
        supervisor.runtimes.remove(&runtime_id);
        if supervisor.active_runtime_id.as_deref() == Some(runtime_id.as_str()) {
            supervisor.active_runtime_id = None;
        }
        if let Ok(path) = gateway_recovery_path(&app) {
            let previous = load_gateway_recovery(&path, Some(&runtime_id))
                .ok()
                .flatten();
            if let Some(record) = crashed_recovery_record(previous, &status) {
                let _ = save_gateway_recovery(&path, &record);
            }
        }
    }
    statuses.sort_by(|left, right| left.runtime_id.cmp(&right.runtime_id));
    Ok(statuses)
}

// 可能阻塞：移出主线程，原因见 get_llm_profile。
#[tauri::command(async)]
fn start_gateway(
    app: AppHandle,
    repo_root: Option<String>,
    scope: String,
    workspace_id: Option<String>,
    port: u16,
    token: Option<String>,
    state: State<'_, GatewayProcess>,
    llm_profile_store: State<'_, DesktopLlmProfileStore>,
) -> Result<GatewayProcessStatus, String> {
    let scope = normalize_runtime_scope(&scope)?;
    let runtime_workspace_id = if scope == SCRATCH_SCOPE {
        Some(normalize_workspace_id(
            workspace_id.as_deref().unwrap_or_default(),
        )?)
    } else {
        None
    };
    let (root, project_root) = if scope == PROJECT_SCOPE {
        let selected = canonical_repo_root(repo_root.as_deref().unwrap_or_default())?;
        let root = git_workspace_root(&selected)?;
        (root.clone(), Some(root))
    } else {
        let root = managed_workspace_root(&app, scope, runtime_workspace_id.as_deref())?;
        create_dir_all(&root).map_err(|error| format!("无法创建 {scope} 工作区：{error}"))?;
        if scope == SCRATCH_SCOPE && !root.join(".git").is_dir() {
            let output = Command::new("git")
                .args(["init", "-q"])
                .current_dir(&root)
                .output()
                .map_err(|error| format!("无法初始化 Scratch Git 工作区：{error}"))?;
            if !output.status.success() {
                return Err(format!(
                    "无法初始化 Scratch Git 工作区：{}",
                    String::from_utf8_lossy(&output.stderr).trim()
                ));
            }
        }
        (root, None)
    };

    let runtime_id = runtime_id_for(scope, &root);
    let runtime_project_id = project_root.as_ref().map(|root| project_id(root));

    // 先读模型配置、再拿 runtime 锁：读配置是 I/O，持锁期间做会让 gateway_process_status
    // 等命令一起排队。结果先留着，失败到原位置再报，保持"已在运行就直接返回"的语义不变。
    let llm_profile = cached_desktop_llm_profile(&app, &llm_profile_store);
    let preferences = desktop_preferences_path(&app)
        .map(|path| read_desktop_preferences(&path))
        .unwrap_or_default();

    let mut supervisor = state
        .0
        .lock()
        .map_err(|_| "runtime 状态锁已损坏".to_string())?;
    if let Some(existing) = supervisor.runtimes.get_mut(&runtime_id) {
        let status = process_status(existing);
        if status.running {
            supervisor.active_runtime_id = Some(runtime_id);
            return Ok(status);
        }
        supervisor.runtimes.remove(&runtime_id);
    }
    if supervisor.runtimes.len() >= MAX_DESKTOP_RUNTIMES {
        return Err(format!(
            "Desktop 已托管 {MAX_DESKTOP_RUNTIMES} 个后台 runtime；请先停止不再需要的工作区"
        ));
    }
    let port = select_gateway_port(port)?;
    let base_url = format!("http://127.0.0.1:{port}");

    let log_dir = app
        .path()
        .app_config_dir()
        .map_err(|error| format!("无法定位 Desktop 日志目录：{error}"))?
        .join("logs");
    create_dir_all(&log_dir).map_err(|error| format!("无法创建 runtime 日志目录：{error}"))?;
    let log_path = log_dir.join(format!("runtime-{runtime_id}.log"));
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|error| format!("无法打开 runtime 日志：{error}"))?;
    let token = token.unwrap_or_default().trim().to_string();
    let llm_profile = llm_profile?;

    let mut candidates = Vec::new();
    let mut sidecar_setup_error = None;
    match build_bundled_runtime_command(&app, &root, port, &token, scope) {
        Ok(command) => candidates.push((command, "Desktop 内置 runtime".to_string())),
        Err(error) => sidecar_setup_error = Some(error),
    }
    candidates.push((
        build_command("vc", false, &root, port, &token, scope),
        "系统 vc server".to_string(),
    ));
    if is_vortocode_development_root(&root) {
        candidates.extend([("python3", "python3"), ("python", "python")].map(
            |(executable, label)| {
                (
                    build_command(executable, true, &root, port, &token, scope),
                    format!("{label} -m src.cli server"),
                )
            },
        ));
    }
    let mut last_not_found = None;
    for (mut command, label) in candidates {
        configure_llm_profile(&mut command, llm_profile.as_ref());
        configure_browser_control(&mut command, &preferences);
        prepare_gateway_process(&mut command);
        command
            .stdout(Stdio::from(log.try_clone().map_err(|error| {
                format!("无法复制 runtime 日志句柄：{error}")
            })?));
        command
            .stderr(Stdio::from(log.try_clone().map_err(|error| {
                format!("无法复制 runtime 日志句柄：{error}")
            })?));

        match command.spawn() {
            Ok(mut child) => {
                let now = match now_epoch_seconds() {
                    Ok(now) => now,
                    Err(error) => {
                        let _ = terminate_gateway_child(&mut child);
                        return Err(format!("runtime 已启动，但无法建立安全恢复记录：{error}"));
                    }
                };
                let recovery = GatewayRecoveryRecord {
                    runtime_id: runtime_id.clone(),
                    project_id: runtime_project_id.clone(),
                    workspace_id: runtime_workspace_id.clone(),
                    scope: scope.into(),
                    workspace_root: root.to_string_lossy().to_string(),
                    repo_root: root.to_string_lossy().to_string(),
                    base_url: base_url.clone(),
                    pid: Some(child.id()),
                    started_at: now,
                    updated_at: now,
                    status: "running".into(),
                    message: "Desktop 启动的本机 runtime 正在运行".into(),
                };
                if let Err(error) = gateway_recovery_path(&app)
                    .and_then(|path| save_gateway_recovery(&path, &recovery))
                {
                    let _ = terminate_gateway_child(&mut child);
                    return Err(format!("runtime 已启动，但无法建立安全恢复记录：{error}"));
                }
                let mut inner = GatewayProcessInner {
                    child: Some(child),
                    runtime_id: runtime_id.clone(),
                    project_id: runtime_project_id.clone(),
                    workspace_id: runtime_workspace_id.clone(),
                    scope: Some(scope.into()),
                    workspace_root: Some(root.clone()),
                    repo_root: project_root.clone(),
                    command: Some(label),
                    base_url: Some(base_url.clone()),
                };
                let status = process_status(&mut inner);
                supervisor.runtimes.insert(runtime_id.clone(), inner);
                supervisor.active_runtime_id = Some(runtime_id);
                return Ok(status);
            }
            Err(error) if error.kind() == ErrorKind::NotFound => {
                last_not_found = Some(error);
            }
            Err(error) => return Err(format!("启动 {label} 失败：{error}")),
        }
    }

    Err(format!(
        "找不到 Desktop 内置 runtime 或已安装的 vc 命令，无法为该项目启动 runtime：{}{}",
        last_not_found
            .map(|error| error.to_string())
            .unwrap_or_else(|| "unknown error".into()),
        sidecar_setup_error
            .map(|error| format!("；{error}"))
            .unwrap_or_default(),
    ))
}

#[tauri::command]
fn stop_gateway(
    app: AppHandle,
    runtime_id: Option<String>,
    state: State<'_, GatewayProcess>,
) -> Result<GatewayProcessStatus, String> {
    let mut supervisor = state
        .0
        .lock()
        .map_err(|_| "runtime 状态锁已损坏".to_string())?;
    let target = runtime_id.or_else(|| supervisor.active_runtime_id.clone());
    let Some(target) = target else {
        return Ok(empty_process_status("Desktop 尚未启动本机 runtime"));
    };
    if !supervisor.runtimes.contains_key(&target) {
        if supervisor.active_runtime_id.as_deref() == Some(target.as_str()) {
            supervisor.active_runtime_id = None;
        }
        clear_gateway_recovery_at(&gateway_recovery_path(&app)?, Some(&target))?;
        return Ok(empty_process_status("该 runtime 已停止"));
    }
    stop_supervised_runtime(&mut supervisor, &target)?;
    clear_gateway_recovery_at(&gateway_recovery_path(&app)?, Some(&target))?;
    Ok(empty_process_status("runtime 已停止"))
}

fn stop_supervised_runtime(
    supervisor: &mut GatewaySupervisorInner,
    runtime_id: &str,
) -> Result<bool, String> {
    let Some(mut inner) = supervisor.runtimes.remove(runtime_id) else {
        return Ok(false);
    };
    if let Some(mut child) = inner.child.take() {
        if let Err(error) = terminate_gateway_child(&mut child) {
            inner.child = Some(child);
            supervisor.runtimes.insert(runtime_id.into(), inner);
            return Err(error);
        }
        let _ = child.wait();
    }
    if supervisor.active_runtime_id.as_deref() == Some(runtime_id) {
        supervisor.active_runtime_id = None;
    }
    Ok(true)
}

fn present_main_window(app: &AppHandle) {
    let Some(window) = app.get_webview_window("main") else {
        eprintln!("VortoCode Desktop 主窗口不存在，无法切换到前台");
        return;
    };

    let result = (|| -> tauri::Result<()> {
        window.show()?;
        if window.is_minimized()? {
            window.unminimize()?;
        }
        window.set_focus()?;
        Ok(())
    })();

    if let Err(error) = result {
        eprintln!("VortoCode Desktop 切换到前台失败：{error}");
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            present_main_window(app);
        }))
        .on_page_load(|webview, payload| {
            if webview.label() == "main" && matches!(payload.event(), PageLoadEvent::Finished) {
                present_main_window(webview.app_handle());
            }
        })
        // 窗口在配置里默认隐藏，等页面加载完才显示（否则启动时先闪一下白窗口，真机 2026-10-06）。
        // 兜底：页面万一没加载成功，几秒后也把窗口显示出来，绝不能让 App 看起来没打开。
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(Duration::from_secs(4)).await;
                if let Some(window) = handle.get_webview_window("main") {
                    if !window.is_visible().unwrap_or(true) {
                        present_main_window(&handle);
                    }
                }
            });
            Ok(())
        })
        .manage(GatewayProcess::default())
        .manage(DesktopLlmProfileStore::default())
        .manage(RemoteWsRegistry::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_websocket::init())
        .invoke_handler(tauri::generate_handler![
            get_llm_profile,
            set_llm_profile,
            clear_llm_profile,
            confirm_action,
            test_llm_connection,
            get_llm_usage,
            get_user_instructions,
            set_user_instructions,
            get_browser_control,
            set_browser_control,
            save_llm_provider,
            remove_llm_provider,
            refresh_llm_providers,
            relay_login,
            relay_logout,
            reveal_desktop_config,
            list_desktop_projects,
            list_project_sessions,
            remember_desktop_project,
            remember_remote_project,
            remote_http_request,
            remote_ws_connect,
            remote_ws_send,
            remote_ws_close,
            pick_desktop_project,
            forget_desktop_project,
            get_gateway_recovery,
            list_gateway_recoveries,
            clear_gateway_recovery,
            desktop_smoke_ready,
            gateway_process_status,
            list_gateway_processes,
            start_gateway,
            stop_gateway,
            list_workspace_files,
            read_workspace_file,
            open_workspace_file
        ])
        .build(tauri::generate_context!())
        .expect("error while building VortoCode Desktop");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Ready) {
            present_main_window(app_handle);
        }

        #[cfg(target_os = "macos")]
        if matches!(event, tauri::RunEvent::Reopen { .. }) {
            present_main_window(app_handle);
        }

        if matches!(event, tauri::RunEvent::Exit) {
            let state = app_handle.state::<GatewayProcess>();
            if let Ok(mut supervisor) = state.0.lock() {
                let runtimes = std::mem::take(&mut supervisor.runtimes);
                for (runtime_id, mut runtime) in runtimes {
                    if let Some(mut child) = runtime.child.take() {
                        let stopped = terminate_gateway_child(&mut child).is_ok();
                        if stopped {
                            if let Ok(path) = gateway_recovery_path(app_handle) {
                                let _ = clear_gateway_recovery_at(&path, Some(&runtime_id));
                            }
                        }
                    }
                }
                supervisor.active_runtime_id = None;
            };
        }
    });
}

// ── 设置页补齐：测试连接 / 额度查询 / 全局指令 / 配置文件定位 ─────────────────────────────
// 出网请求与 discover_model_context_window_with 同口径：只打配置声明的 base_url 本身，限时、
// 不跟随重定向；Key 不外借——留空时只有与已保存配置同一地址才借用已保存的 Key。

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct LlmConnectionTest {
    ok: bool,
    status: Option<u16>,
    model_count: Option<usize>,
    model_available: Option<bool>,
    /// 服务返回的模型 id（最多 MAX_LISTED_MODELS 个），供设置页给模型名做候选。
    models: Vec<String>,
    message: String,
}

const MAX_LISTED_MODELS: usize = 200;

fn model_ids(payload: &serde_json::Value) -> Vec<String> {
    payload
        .get("data")
        .and_then(serde_json::Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.get("id").or_else(|| item.get("model")).and_then(serde_json::Value::as_str))
                .take(MAX_LISTED_MODELS)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// 模型描述里的短标签（类别 / 能力 / 档位）：小写、限长、不含控制字符，否则当没给。
fn model_label(value: Option<&serde_json::Value>) -> Option<String> {
    let label = value?.as_str()?.trim().to_ascii_lowercase();
    (!label.is_empty() && label.len() <= 40 && !label.chars().any(char::is_control)).then_some(label)
}

/// 解析 /models 的完整描述。只要服务给任何一个模型标了 category，就按它的分类走：
/// 不能跑 Agent 的（专用模型、非对话模型、没有 tools 的）**先**剔掉再截断，免得被挤出上限。
fn model_infos(payload: &serde_json::Value) -> Vec<ModelInfo> {
    let Some(items) = payload.get("data").and_then(serde_json::Value::as_array) else {
        return Vec::new();
    };
    let infos = items.iter().filter_map(|item| {
        let id = item.get("id").or_else(|| item.get("model")).and_then(serde_json::Value::as_str)?;
        Some(ModelInfo {
            id: id.to_string(),
            category: model_label(item.get("category")),
            capabilities: item
                .get("capabilities")
                .and_then(serde_json::Value::as_array)
                .map(|values| values.iter().filter_map(|value| model_label(Some(value))).take(16).collect())
                .unwrap_or_default(),
            tier: model_label(item.get("tier")),
            snapshot_of: item
                .get("snapshot_of")
                .and_then(serde_json::Value::as_str)
                .map(str::trim)
                .filter(|base| !base.is_empty() && base.len() <= 160 && !base.chars().any(char::is_control))
                .map(str::to_string),
            context_window: item
                .get("context_window")
                .and_then(serde_json::Value::as_u64)
                .filter(|window| (MIN_MODEL_CONTEXT_WINDOW..=MAX_MODEL_CONTEXT_WINDOW).contains(window)),
        })
    });
    let infos: Vec<ModelInfo> = infos.collect();
    let classified = infos.iter().any(|info| info.category.is_some());
    clean_model_infos(infos.into_iter().filter(|info| !classified || info.agent_ready()).collect())
}

/// 同 clean_model_list 的口径清洗 id 并去重。
fn clean_model_infos(infos: Vec<ModelInfo>) -> Vec<ModelInfo> {
    let mut seen = std::collections::HashSet::new();
    infos
        .into_iter()
        .map(|mut info| {
            info.id = info.id.trim().to_string();
            info
        })
        .filter(|info| {
            !info.id.is_empty()
                && info.id.len() <= 160
                && !info.id.contains(',')
                && !info.id.chars().any(char::is_control)
        })
        .filter(|info| seen.insert(info.id.clone()))
        .take(MAX_LISTED_MODELS)
        .collect()
}

/// runtime 允许点名的模型：服务给了分类就只放能跑 Agent 的；没给分类（别家服务、旧配置）沿用整份清单。
fn model_choice_ids(profile: &DesktopLlmProfile) -> Vec<String> {
    if profile.model_info.iter().any(|info| info.category.is_some()) {
        return profile
            .model_info
            .iter()
            .filter(|info| info.agent_ready())
            .map(|info| info.id.clone())
            .collect();
    }
    profile.models.clone()
}

fn set_model_list(profile: &mut DesktopLlmProfile, infos: Vec<ModelInfo>) {
    profile.models = infos.iter().map(|info| info.id.clone()).collect();
    profile.model_info = infos;
}

fn summarize_models_payload(payload: &serde_json::Value, model: &str) -> (usize, Option<bool>) {
    let Some(models) = payload.get("data").and_then(serde_json::Value::as_array) else {
        return (0, None);
    };
    let wanted = model.trim();
    let available = (!wanted.is_empty()).then(|| {
        models.iter().any(|item| {
            item.get("id")
                .or_else(|| item.get("model"))
                .and_then(serde_json::Value::as_str)
                .is_some_and(|id| id.eq_ignore_ascii_case(wanted))
        })
    });
    (models.len(), available)
}

fn probe_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(LLM_PROBE_TIMEOUT)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|error| format!("无法创建网络客户端：{error}"))
}

const MAX_CUSTOM_PROVIDERS: usize = 12;
const RESERVED_PROVIDER_IDS: [&str; 2] = ["default", "anthropic"];

/// 校验一个自定义供应商：ID 只能是小写字母开头的 `[a-z0-9_]`（会拼进环境变量名），
/// 地址与 Key 沿用主模型服务的同一套规则（远程必须 HTTPS 且带 Key）。
fn normalize_llm_provider(mut provider: DesktopLlmProvider) -> Result<DesktopLlmProvider, String> {
    provider.id = provider.id.trim().to_lowercase();
    let id_ok = provider.id.len() <= 24
        && provider.id.chars().next().is_some_and(|c| c.is_ascii_lowercase())
        && provider
            .id
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_');
    if !id_ok {
        return Err("供应商 ID 只能用小写字母、数字和下划线，并以字母开头（最长 24 位）".into());
    }
    if RESERVED_PROVIDER_IDS.contains(&provider.id.as_str()) {
        return Err(format!("供应商 ID 不能用 {}", provider.id));
    }
    provider.name = provider.name.trim().to_string();
    if provider.name.is_empty() {
        provider.name = provider.id.clone();
    }
    if provider.name.chars().count() > 40 || provider.name.chars().any(char::is_control) {
        return Err("供应商名称无效".into());
    }
    let checked = normalize_llm_profile(DesktopLlmProfile {
        base_url: provider.base_url.clone(),
        api_key: provider.api_key.clone(),
        model: "-".into(),
        context_window: None,
        context_window_source: None,
        fast_model: None,
        strong_model: None,
        providers: Vec::new(),
        account: None,
        signed_out: false,
        model_info: Vec::new(),
        models: Vec::new(),
    })?;
    provider.base_url = checked.base_url;
    provider.api_key = checked.api_key;
    provider.models = provider
        .models
        .into_iter()
        .map(|model| model.trim().to_string())
        .filter(|model| !model.is_empty() && model.len() <= 160 && !model.chars().any(char::is_control))
        .take(MAX_LISTED_MODELS)
        .collect();
    Ok(provider)
}

async fn fetch_provider_models(provider: &DesktopLlmProvider) -> Option<Vec<ModelInfo>> {
    let mut request = probe_client().ok()?.get(format!("{}/models", provider.base_url));
    if !provider.api_key.is_empty() {
        request = request.bearer_auth(&provider.api_key);
    }
    let response = request.send().await.ok()?;
    if !response.status().is_success() {
        return None;
    }
    let payload = response.json::<serde_json::Value>().await.ok()?;
    Some(model_infos(&payload))
}

/// 配置里还没有模型清单（或只有旧版的纯模型名）时，用它自己的地址和 Key 拉一份（只访问这个默认服务）。
async fn with_model_list(mut profile: DesktopLlmProfile) -> DesktopLlmProfile {
    if profile.model_info.is_empty() && !profile.signed_out && !profile.api_key.is_empty() {
        let main = DesktopLlmProvider {
            id: "default".into(),
            name: String::new(),
            base_url: profile.base_url.clone(),
            api_key: profile.api_key.clone(),
            models: Vec::new(),
        };
        if let Some(models) = fetch_provider_models(&main).await {
            set_model_list(&mut profile, models);
        }
    }
    profile
}

fn clean_model_list(models: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    models
        .into_iter()
        .map(|model| model.trim().to_string())
        .filter(|model| {
            !model.is_empty()
                && model.len() <= 160
                && !model.contains(',')
                && !model.chars().any(char::is_control)
        })
        .filter(|model| seen.insert(model.clone()))
        .take(MAX_LISTED_MODELS)
        .collect()
}

fn saved_profile_for_providers(
    app: &AppHandle,
    store: &State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfile, String> {
    cached_desktop_llm_profile(app, store)?
        .ok_or_else(|| "请先在上方配置并保存默认模型服务，再添加其他供应商".to_string())
}

fn persist_profile(
    app: &AppHandle,
    store: &State<'_, DesktopLlmProfileStore>,
    profile: DesktopLlmProfile,
) -> Result<DesktopLlmProfileStatus, String> {
    save_llm_profile_file(&llm_profile_path(app)?, &profile)?;
    store.replace(Some(profile.clone()))?;
    Ok(with_config_path(desktop_llm_profile_status(Some(&profile)), app))
}

/// 添加或更新一个自定义供应商。Key 留空 = 沿用同一 ID、同一地址下已保存的 Key。
#[tauri::command]
async fn save_llm_provider(
    app: AppHandle,
    id: String,
    name: String,
    base_url: String,
    api_key: String,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    let mut profile = saved_profile_for_providers(&app, &store)?;
    let wanted_id = id.trim().to_lowercase();
    let wanted_base = base_url.trim().trim_end_matches('/').to_string();
    let mut api_key = api_key.trim().to_string();
    if api_key.is_empty() {
        if let Some(existing) = profile
            .providers
            .iter()
            .find(|provider| provider.id == wanted_id && provider.base_url == wanted_base)
        {
            api_key = existing.api_key.clone();
        }
    }
    let mut provider = normalize_llm_provider(DesktopLlmProvider {
        id,
        name,
        base_url,
        api_key,
        models: Vec::new(),
    })?;
    let replacing = profile.providers.iter().any(|existing| existing.id == provider.id);
    if !replacing && profile.providers.len() >= MAX_CUSTOM_PROVIDERS {
        return Err(format!("最多添加 {MAX_CUSTOM_PROVIDERS} 个供应商"));
    }
    // 原生确认在 webview 之外：页面脚本不能悄悄把请求和 Key 指向别的地址。
    let message = format!(
        "{}模型供应商：\n\n名称：{}\nID：{}\n接口地址：{}\n\n在聊天里选用它的模型时，请求会带着你填写的 Key 发到这个地址。",
        if replacing { "更新" } else { "添加" },
        provider.name,
        provider.id,
        provider.base_url
    );
    let dialog_app = app.clone();
    let confirmed = tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&dialog_app, dialog_app.dialog().message(message))
            .title("确认模型供应商")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom("确认".into(), "取消".into()))
            .blocking_show()
    })
    .await
    .map_err(|error| format!("供应商确认对话框失败：{error}"))?;
    if !confirmed {
        return Err("未确认，供应商没有保存".into());
    }
    provider.models = fetch_provider_models(&provider)
        .await
        .map(|infos| infos.into_iter().map(|info| info.id).collect())
        .unwrap_or_default();
    profile.providers.retain(|existing| existing.id != provider.id);
    profile.providers.push(provider);
    persist_profile(&app, &store, profile)
}

#[tauri::command(async)]
fn remove_llm_provider(
    app: AppHandle,
    id: String,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    let mut profile = saved_profile_for_providers(&app, &store)?;
    let before = profile.providers.len();
    profile.providers.retain(|provider| provider.id != id);
    if profile.providers.len() == before {
        return Err("没有这个供应商".into());
    }
    persist_profile(&app, &store, profile)
}

/// 重新拉取默认服务和各供应商的模型列表；拉取失败的保留原列表。
#[tauri::command]
async fn refresh_llm_providers(
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    let mut profile = saved_profile_for_providers(&app, &store)?;
    if !profile.signed_out && !profile.api_key.is_empty() {
        let main = DesktopLlmProvider {
            id: "default".into(),
            name: String::new(),
            base_url: profile.base_url.clone(),
            api_key: profile.api_key.clone(),
            models: Vec::new(),
        };
        if let Some(models) = fetch_provider_models(&main).await {
            set_model_list(&mut profile, models);
        }
    }
    for provider in profile.providers.iter_mut() {
        if let Some(models) = fetch_provider_models(provider).await {
            provider.models = models.into_iter().map(|info| info.id).collect();
        }
    }
    persist_profile(&app, &store, profile)
}

const RELAY_ORIGIN: &str = "https://token.vortotech.com";
const RELAY_PLAN_URL: &str = "https://token.vortotech.com/panel/plan";
const DESKTOP_TOKEN_NAME: &str = "VortoCode Desktop";

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RelayLoginOutcome {
    /// 登录并保存成功后的模型配置；需要先选套餐时为 None。
    status: Option<DesktopLlmProfileStatus>,
    /// 账号还没有 Token Plan：界面让用户去购买，或明确选择改用按量 Key。
    needs_plan: bool,
    plan_url: String,
    message: String,
}

/// 只认 One API 的 `{success, message, data}` 外壳；success=false 时把服务端原话带回去。
fn relay_data(payload: serde_json::Value, failure: &str) -> Result<serde_json::Value, String> {
    if payload.get("success").and_then(serde_json::Value::as_bool) == Some(true) {
        return Ok(payload.get("data").cloned().unwrap_or(serde_json::Value::Null));
    }
    let message = payload
        .get("message")
        .and_then(serde_json::Value::as_str)
        .filter(|message| !message.trim().is_empty())
        .unwrap_or(failure);
    Err(message.chars().take(200).collect())
}

/// 从 Token Plan Key 列表里挑一把启用中的（托管的那把排在最前）。
fn pick_plan_key(data: &serde_json::Value) -> Option<String> {
    data.as_array()?
        .iter()
        .find(|item| item.get("status").and_then(serde_json::Value::as_i64) == Some(1))
        .and_then(|item| item.get("key").and_then(serde_json::Value::as_str))
        .filter(|key| !key.trim().is_empty())
        .map(str::to_string)
}

/// 在令牌搜索结果里找桌面端自己建的那把按量 Key（同名、启用、非套餐）。
fn pick_desktop_token(data: &serde_json::Value) -> Option<String> {
    data.as_array()?
        .iter()
        .find(|item| {
            item.get("name").and_then(serde_json::Value::as_str) == Some(DESKTOP_TOKEN_NAME)
                && item.get("status").and_then(serde_json::Value::as_i64) == Some(1)
                && item.get("billing_type").and_then(serde_json::Value::as_str) != Some("token_plan")
        })
        .and_then(|item| item.get("key").and_then(serde_json::Value::as_str))
        .filter(|key| !key.trim().is_empty())
        .map(str::to_string)
}

fn with_sk_prefix(key: &str) -> String {
    if key.starts_with("sk-") {
        key.to_string()
    } else {
        format!("sk-{key}")
    }
}

/// 用 VortoCode 中转站账号登录，取用账号的 Token Plan Key（Desktop 套餐即 Token Plan）。
/// 没有套餐时默认不建 Key，交给用户决定；`allow_pay_as_you_go` 为真才建/复用一把按量 Key。
/// 只连固定的中转站地址；密码和登录会话只在本次调用里用，结束即登出，不落盘。
#[tauri::command]
async fn relay_login(
    app: AppHandle,
    username: String,
    password: String,
    allow_pay_as_you_go: bool,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<RelayLoginOutcome, String> {
    let username = username.trim().to_string();
    if username.is_empty() || password.is_empty() || username.len() > 64 || password.len() > 256 {
        return Err("请输入用户名和密码".into());
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(20))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|error| format!("无法创建网络客户端：{error}"))?;
    let unreachable = |error: reqwest::Error| {
        if error.is_timeout() { "连接中转站超时".to_string() } else { "无法连接中转站".to_string() }
    };
    let login = client
        .post(format!("{RELAY_ORIGIN}/api/user/login"))
        .json(&serde_json::json!({ "username": username, "password": password }))
        .send()
        .await
        .map_err(unreachable)?;
    let cookie = login
        .headers()
        .get_all(reqwest::header::SET_COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .filter_map(|value| value.split(';').next())
        .map(str::trim)
        .filter(|pair| pair.contains('='))
        .collect::<Vec<_>>()
        .join("; ");
    let user = relay_data(login.json().await.unwrap_or_default(), "登录失败")?;
    if cookie.is_empty() {
        return Err("中转站没有返回登录会话".into());
    }
    let get = |path: &str| {
        client
            .get(format!("{RELAY_ORIGIN}{path}"))
            .header(reqwest::header::COOKIE, cookie.clone())
    };

    let plan_keys = relay_data(
        get("/api/subscription/key").send().await.map_err(unreachable)?.json().await.unwrap_or_default(),
        "读取套餐 Key 失败",
    )?;
    let (key, key_source) = match pick_plan_key(&plan_keys) {
        Some(key) => (key, "token_plan"),
        None if !allow_pay_as_you_go => {
            let _ = get("/api/user/logout").send().await;
            return Ok(RelayLoginOutcome {
                status: None,
                needs_plan: true,
                plan_url: RELAY_PLAN_URL.into(),
                message: "这个账号还没有 Token Plan（Desktop 套餐）".into(),
            });
        }
        None => {
            let found = relay_data(
                get(&format!("/api/token/search?keyword={}", DESKTOP_TOKEN_NAME.replace(' ', "%20")))
                    .send()
                    .await
                    .map_err(unreachable)?
                    .json()
                    .await
                    .unwrap_or_default(),
                "读取令牌失败",
            )?;
            let key = match pick_desktop_token(&found) {
                Some(key) => key,
                None => {
                    let created = relay_data(
                        client
                            .post(format!("{RELAY_ORIGIN}/api/token/"))
                            .header(reqwest::header::COOKIE, cookie.clone())
                            .json(&serde_json::json!({
                                "name": DESKTOP_TOKEN_NAME,
                                "expired_time": -1,
                                "unlimited_quota": true,
                                "remain_quota": 0,
                            }))
                            .send()
                            .await
                            .map_err(unreachable)?
                            .json()
                            .await
                            .unwrap_or_default(),
                        "创建按量 Key 失败",
                    )?;
                    created
                        .get("key")
                        .and_then(serde_json::Value::as_str)
                        .map(str::to_string)
                        .ok_or_else(|| "中转站没有返回新 Key".to_string())?
                }
            };
            (key, "pay_as_you_go")
        }
    };

    // 套餐名称和到期时间只用于展示；读不到不影响登录。
    let subscription = match get("/api/subscription/self").send().await {
        Ok(response) => response.json::<serde_json::Value>().await.unwrap_or_default(),
        Err(_) => serde_json::Value::Null,
    };
    let active = subscription.get("data").and_then(|data| data.get("active")).cloned().unwrap_or_default();
    let _ = get("/api/user/logout").send().await;

    let saved = cached_desktop_llm_profile(&app, &store)?;
    let base_url = format!("{RELAY_ORIGIN}/v1");
    let same_base = saved.as_ref().is_some_and(|saved| saved.base_url == base_url);
    let profile = normalize_llm_profile(DesktopLlmProfile {
        base_url,
        api_key: with_sk_prefix(&key),
        model: saved
            .as_ref()
            .filter(|_| same_base)
            .map(|saved| saved.model.clone())
            .unwrap_or_else(|| DEFAULT_LLM_MODEL.into()),
        context_window: None,
        context_window_source: None,
        fast_model: saved.as_ref().filter(|_| same_base).and_then(|saved| saved.fast_model.clone()),
        strong_model: saved.as_ref().filter(|_| same_base).and_then(|saved| saved.strong_model.clone()),
        models: saved.as_ref().filter(|_| same_base).map(|saved| saved.models.clone()).unwrap_or_default(),
        model_info: saved.as_ref().filter(|_| same_base).map(|saved| saved.model_info.clone()).unwrap_or_default(),
        providers: saved.map(|saved| saved.providers).unwrap_or_default(),
        account: Some(DesktopAccount {
            username: user
                .get("username")
                .and_then(serde_json::Value::as_str)
                .unwrap_or(&username)
                .to_string(),
            display_name: user
                .get("display_name")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string(),
            key_source: key_source.into(),
            plan_name: active.get("plan_name").and_then(serde_json::Value::as_str).map(str::to_string),
            plan_expiry: active.get("expiry_time").and_then(serde_json::Value::as_i64),
        }),
        signed_out: false,
    })?;
    let profile = with_model_list(profile).await;
    let profile = save_llm_profile_flow(profile, true, LLM_PROBE_TIMEOUT, |profile| {
        save_llm_profile_file(&llm_profile_path(&app)?, profile)
    })
    .await?;
    store.replace(Some(profile.clone()))?;
    Ok(RelayLoginOutcome {
        status: Some(with_config_path(desktop_llm_profile_status(Some(&profile)), &app)),
        needs_plan: false,
        plan_url: RELAY_PLAN_URL.into(),
        message: if key_source == "token_plan" {
            "已登录，正在使用 Token Plan".into()
        } else {
            "已登录，正在使用按量 Key".into()
        },
    })
}

/// 退出登录：删掉本机保存的中转站 Key 和账号信息，保留自定义供应商。
#[tauri::command(async)]
fn relay_logout(
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    let Some(mut profile) = cached_desktop_llm_profile(&app, &store)? else {
        return Ok(with_config_path(desktop_llm_profile_status(None), &app));
    };
    if profile.providers.is_empty() {
        delete_llm_profile_file(&llm_profile_path(&app)?)?;
        store.replace(None)?;
        return Ok(with_config_path(desktop_llm_profile_status(None), &app));
    }
    profile.api_key.clear();
    profile.account = None;
    profile.signed_out = true;
    persist_profile(&app, &store, profile)
}

#[tauri::command]
async fn test_llm_connection(
    base_url: String,
    api_key: String,
    model: String,
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<LlmConnectionTest, String> {
    // Key 留空时先借用已保存的 Key（仅限同一地址），再做完整校验——否则远程服务会因为
    // "需要 API Key" 在借用之前就被拦下。地址按 normalize_llm_profile 的同一规则比较。
    let mut api_key = api_key.trim().to_string();
    if api_key.is_empty() {
        let wanted = base_url.trim().trim_end_matches('/');
        if let Some(saved) = cached_desktop_llm_profile(&app, &store)? {
            if saved.base_url == wanted {
                api_key = saved.api_key;
            }
        }
    }
    let profile = normalize_llm_profile(DesktopLlmProfile {
        base_url,
        api_key,
        model: if model.trim().is_empty() { "-".into() } else { model },
        context_window: None,
        context_window_source: None,
        fast_model: None,
        strong_model: None,
        providers: Vec::new(),
        account: None,
        signed_out: false,
        model_info: Vec::new(),
        models: Vec::new(),
    })?;
    let mut request = probe_client()?.get(format!("{}/models", profile.base_url));
    if !profile.api_key.is_empty() {
        request = request.bearer_auth(&profile.api_key);
    }
    let response = match request.send().await {
        Ok(response) => response,
        Err(error) => {
            return Ok(LlmConnectionTest {
                ok: false,
                status: None,
                model_count: None,
                model_available: None,
                models: Vec::new(),
                message: if error.is_timeout() { "连接超时".into() } else { "无法连接到该地址".into() },
            })
        }
    };
    let status = response.status();
    if !status.is_success() {
        let message = match status.as_u16() {
            401 | 403 => "API Key 无效或没有权限".to_string(),
            404 => "该地址不是 OpenAI 兼容接口（/models 不存在）".to_string(),
            code => format!("服务返回 HTTP {code}"),
        };
        return Ok(LlmConnectionTest {
            ok: false,
            status: Some(status.as_u16()),
            model_count: None,
            model_available: None,
            models: Vec::new(),
            message,
        });
    }
    let payload = response.json::<serde_json::Value>().await.unwrap_or_default();
    let wanted = if profile.model == "-" { "" } else { profile.model.as_str() };
    let (count, available) = summarize_models_payload(&payload, wanted);
    let message = match available {
        Some(false) => format!("连接成功，但服务的 {count} 个模型里没有 {wanted}"),
        _ => format!("连接成功，服务提供 {count} 个模型"),
    };
    Ok(LlmConnectionTest {
        ok: available != Some(false),
        status: Some(status.as_u16()),
        model_count: Some(count),
        model_available: available,
        models: model_ids(&payload),
        message,
    })
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct LlmUsageSummary {
    available: bool,
    unlimited: bool,
    hard_limit_usd: Option<f64>,
    used_usd: Option<f64>,
    period_days: u32,
    message: String,
}

const USAGE_PERIOD_DAYS: i64 = 7;
// One API 对"不限额度"的令牌返回一个极大的上限（实测 1e8 美元）。
const UNLIMITED_QUOTA_USD: f64 = 10_000_000.0;

/// 自 1970-01-01 起的天数 → (年, 月, 日)。公历，Howard Hinnant 的 civil_from_days。
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

fn iso_date(days: i64) -> String {
    let (year, month, day) = civil_from_days(days);
    format!("{year:04}-{month:02}-{day:02}")
}

fn usage_from_billing(subscription: &serde_json::Value, usage: Option<&serde_json::Value>) -> LlmUsageSummary {
    let hard_limit = subscription
        .get("hard_limit_usd")
        .or_else(|| subscription.get("system_hard_limit_usd"))
        .and_then(serde_json::Value::as_f64);
    // OpenAI 兼容的 total_usage 单位是美分。
    let used = usage
        .and_then(|value| value.get("total_usage"))
        .and_then(serde_json::Value::as_f64)
        .map(|cents| cents / 100.0);
    let unlimited = hard_limit.is_some_and(|limit| limit >= UNLIMITED_QUOTA_USD);
    LlmUsageSummary {
        available: hard_limit.is_some() || used.is_some(),
        unlimited,
        hard_limit_usd: hard_limit.filter(|_| !unlimited),
        used_usd: used,
        period_days: USAGE_PERIOD_DAYS as u32,
        message: String::new(),
    }
}

#[tauri::command]
async fn get_llm_usage(
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<LlmUsageSummary, String> {
    let unavailable = |message: &str| LlmUsageSummary {
        available: false,
        unlimited: false,
        hard_limit_usd: None,
        used_usd: None,
        period_days: USAGE_PERIOD_DAYS as u32,
        message: message.into(),
    };
    let Some(profile) = cached_desktop_llm_profile(&app, &store)? else {
        return Ok(unavailable("还没有配置模型服务"));
    };
    if profile.api_key.is_empty() {
        return Ok(unavailable("当前模型服务未使用 API Key，没有额度信息"));
    }
    let client = probe_client()?;
    let fetch = |path: String| {
        client
            .get(format!("{}{}", profile.base_url, path))
            .bearer_auth(&profile.api_key)
            .send()
    };
    let subscription = match fetch("/dashboard/billing/subscription".into()).await {
        Ok(response) if response.status().is_success() => {
            response.json::<serde_json::Value>().await.unwrap_or_default()
        }
        Ok(_) => return Ok(unavailable("该服务不提供额度查询")),
        Err(_) => return Ok(unavailable("暂时无法连接模型服务")),
    };
    let today = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("系统时间不可用：{error}"))?
        .as_secs() as i64
        / 86_400;
    let usage = match fetch(format!(
        "/dashboard/billing/usage?start_date={}&end_date={}",
        iso_date(today - USAGE_PERIOD_DAYS + 1),
        iso_date(today + 1)
    ))
    .await
    {
        Ok(response) if response.status().is_success() => response.json::<serde_json::Value>().await.ok(),
        _ => None,
    };
    let mut summary = usage_from_billing(&subscription, usage.as_ref());
    if !summary.available {
        summary.message = "该服务没有返回额度信息".into();
    }
    Ok(summary)
}

const MAX_USER_INSTRUCTIONS_CHARS: usize = 20_000;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct UserInstructions {
    path: String,
    content: String,
    exists: bool,
}

fn user_instructions_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .home_dir()
        .map(|home| home.join(".vortocode").join("AGENTS.md"))
        .map_err(|error| format!("无法定位用户目录：{error}"))
}

// 读写本地文件：移出主线程，原因见 get_llm_profile。
#[tauri::command(async)]
fn get_user_instructions(app: AppHandle) -> Result<UserInstructions, String> {
    let path = user_instructions_path(&app)?;
    let content = match std::fs::read_to_string(&path) {
        Ok(content) => Some(content),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(format!("无法读取全局指令 {}：{error}", path.display())),
    };
    Ok(UserInstructions {
        path: path.display().to_string(),
        exists: content.is_some(),
        content: content.unwrap_or_default(),
    })
}

#[tauri::command]
async fn set_user_instructions(app: AppHandle, content: String) -> Result<UserInstructions, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    if content.chars().count() > MAX_USER_INSTRUCTIONS_CHARS {
        return Err(format!("全局指令过长（上限 {MAX_USER_INSTRUCTIONS_CHARS} 字）"));
    }
    let path = user_instructions_path(&app)?;
    // 全局指令会进之后所有新会话的系统提示，与改模型服务同等敏感：原生确认在 webview 之外。
    let dialog_app = app.clone();
    let confirmed = tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&dialog_app, dialog_app.dialog().message(
            "保存后，这段全局指令会附加到之后所有新会话（含通用会话与自动化任务）的系统提示里。确认保存？",
        ))
        .title("确认保存全局指令")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom("确认保存".into(), "取消".into()))
        .blocking_show()
    })
    .await
    .map_err(|error| format!("全局指令确认对话框失败：{error}"))?;
    if !confirmed {
        return Err("全局指令修改未获确认，已取消".into());
    }
    let parent = path.parent().ok_or_else(|| "全局指令路径缺少父目录".to_string())?;
    create_dir_all(parent).map_err(|error| format!("无法创建 {}：{error}", parent.display()))?;
    let temporary = parent.join(format!(".AGENTS.md-{}.tmp", std::process::id()));
    std::fs::write(&temporary, content.as_bytes())
        .and_then(|_| rename(&temporary, &path))
        .map_err(|error| format!("无法保存全局指令：{error}"))?;
    Ok(UserInstructions {
        path: path.display().to_string(),
        exists: true,
        content,
    })
}

// 只允许在 Finder 中显示这两个固定文件，不接受任意路径。
#[tauri::command(async)]
fn reveal_desktop_config(app: AppHandle, kind: String) -> Result<(), String> {
    let path = match kind.as_str() {
        "llmProfile" => llm_profile_path(&app)?,
        "userInstructions" => user_instructions_path(&app)?,
        _ => return Err("未知的配置文件".into()),
    };
    let target = if path.exists() { path } else { path.parent().map(Path::to_path_buf).unwrap_or(path) };
    tauri_plugin_opener::reveal_item_in_dir(&target).map_err(|error| format!("无法在 Finder 中显示：{error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;
    use std::fs::{create_dir_all, remove_dir_all, write};
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn temp_workspace(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "vortocode-desktop-{label}-{}-{nonce}",
            std::process::id()
        ));
        create_dir_all(&root).expect("create temp workspace");
        root
    }

    fn init_git(root: &Path) {
        let status = Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .expect("run git init");
        assert!(status.success());
    }

    #[test]
    fn llm_profiles_allow_https_custom_and_loopback_http_only() {
        let relay = normalize_llm_profile(DesktopLlmProfile {
            base_url: "https://token.vortotech.com/v1/".into(),
            api_key: "test-key".into(),
            model: "mimo-v2.5".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        })
        .expect("relay profile");
        assert_eq!(relay.base_url, DEFAULT_LLM_BASE_URL);
        assert_eq!(llm_provider(&relay.base_url), "vortocode");
        assert_eq!(relay.context_window, Some(1_000_000));
        assert_eq!(relay.context_window_source.as_deref(), Some("catalog"));

        let local = normalize_llm_profile(DesktopLlmProfile {
            base_url: "http://127.0.0.1:11434/v1".into(),
            api_key: String::new(),
            model: "local-model".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        })
        .expect("local profile");
        assert_eq!(llm_provider(&local.base_url), "local");

        assert!(normalize_llm_profile(DesktopLlmProfile {
            base_url: "http://models.example.com/v1".into(),
            api_key: "test-key".into(),
            model: "model".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        })
        .is_err());
        assert!(normalize_llm_profile(DesktopLlmProfile {
            base_url: "https://models.example.com/v1".into(),
            api_key: String::new(),
            model: "model".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        })
        .is_err());
    }

    #[test]
    fn llm_profile_is_injected_only_into_child_runtime_environment() {
        let profile = DesktopLlmProfile {
            base_url: "https://models.example.com/v1".into(),
            api_key: "test-key".into(),
            model: "model-1".into(),
            context_window: Some(65_536),
            context_window_source: Some("service".into()),
            fast_model: None,
            strong_model: None,
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        };
        let mut command = Command::new("vc");
        configure_llm_profile(&mut command, Some(&profile));
        let env = command
            .get_envs()
            .map(|(key, value)| (key.to_owned(), value.map(OsStr::to_owned)))
            .collect::<HashMap<_, _>>();
        assert_eq!(
            env.get(OsStr::new("OPENAI_API_BASE"))
                .and_then(Option::as_deref),
            Some(OsStr::new("https://models.example.com/v1"))
        );
        assert_eq!(
            env.get(OsStr::new("OPENAI_API_KEY"))
                .and_then(Option::as_deref),
            Some(OsStr::new("test-key"))
        );
        assert_eq!(
            env.get(OsStr::new("DEFAULT_MODEL"))
                .and_then(Option::as_deref),
            Some(OsStr::new("model-1"))
        );
        assert_eq!(
            env.get(OsStr::new("VORTOCODE_MODEL_CONTEXT_WINDOW"))
                .and_then(Option::as_deref),
            Some(OsStr::new("65536"))
        );
        // 没配调度档时显式移除（值为 None），不继承父进程里的旧值。
        assert_eq!(env.get(OsStr::new("LLM_MODEL_CHEAP")), Some(&None));
        assert_eq!(env.get(OsStr::new("LLM_MODEL_POWERFUL")), Some(&None));
        assert_eq!(
            env.get(OsStr::new("LLM_MODEL_BALANCED"))
                .and_then(Option::as_deref),
            Some(OsStr::new("model-1"))
        );

        let routed = DesktopLlmProfile {
            fast_model: Some("model-fast".into()),
            strong_model: Some("model-strong".into()),
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
            ..profile
        };
        let mut command = Command::new("vc");
        configure_llm_profile(&mut command, Some(&routed));
        let env = command
            .get_envs()
            .map(|(key, value)| (key.to_owned(), value.map(OsStr::to_owned)))
            .collect::<HashMap<_, _>>();
        assert_eq!(
            env.get(OsStr::new("LLM_MODEL_CHEAP"))
                .and_then(Option::as_deref),
            Some(OsStr::new("model-fast"))
        );
        assert_eq!(
            env.get(OsStr::new("LLM_MODEL_POWERFUL"))
                .and_then(Option::as_deref),
            Some(OsStr::new("model-strong"))
        );
    }

    #[test]
    fn browser_control_defaults_off_and_is_removed_from_child_env_when_off() {
        let directory = std::env::temp_dir().join(format!("vc-prefs-{}", std::process::id()));
        let path = directory.join(DESKTOP_PREFERENCES_FILE);
        assert!(!read_desktop_preferences(&path).browser_control);
        create_dir_all(&directory).unwrap();
        std::fs::write(&path, "{not json").unwrap();
        assert!(!read_desktop_preferences(&path).browser_control);
        std::fs::write(&path, r#"{"browserControl": true}"#).unwrap();
        assert!(read_desktop_preferences(&path).browser_control);
        let _ = std::fs::remove_dir_all(&directory);

        let mut command = Command::new("vc");
        configure_browser_control(&mut command, &DesktopPreferences::default());
        let env = command
            .get_envs()
            .map(|(key, value)| (key.to_owned(), value.map(OsStr::to_owned)))
            .collect::<HashMap<_, _>>();
        assert_eq!(env.get(OsStr::new("VORTOCODE_ENABLE_BROWSER_CONTROL")), Some(&None));
    }

    fn sample_provider(id: &str) -> DesktopLlmProvider {
        DesktopLlmProvider {
            id: id.into(),
            name: String::new(),
            base_url: "https://api.example.com/v1/".into(),
            api_key: " key ".into(),
            models: vec!["m1".into(), "  ".into(), "bad\u{7}".into()],
        }
    }

    #[test]
    fn custom_provider_ids_and_endpoints_are_validated() {
        let provider = normalize_llm_provider(sample_provider("DeepSeek_2")).unwrap();
        assert_eq!(provider.id, "deepseek_2");
        assert_eq!(provider.name, "deepseek_2");
        assert_eq!(provider.base_url, "https://api.example.com/v1");
        assert_eq!(provider.api_key, "key");
        assert_eq!(provider.models, vec!["m1".to_string()]);
        for bad in ["my-provider", "1abc", "", "default", "anthropic", "a b"] {
            assert!(normalize_llm_provider(sample_provider(bad)).is_err(), "{bad}");
        }
        let mut insecure = sample_provider("plain");
        insecure.base_url = "http://api.example.com/v1".into();
        assert!(normalize_llm_provider(insecure).is_err());
        let mut keyless = sample_provider("keyless");
        keyless.api_key = String::new();
        assert!(normalize_llm_provider(keyless).is_err());
    }

    #[test]
    fn custom_providers_reach_the_runtime_env_but_not_the_status() {
        let profile = DesktopLlmProfile {
            base_url: "https://models.example.com/v1".into(),
            api_key: "main-key".into(),
            model: "main".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            providers: vec![normalize_llm_provider(sample_provider("deepseek")).unwrap()],
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        };
        let mut command = Command::new("vc");
        configure_llm_profile(&mut command, Some(&profile));
        let env = command
            .get_envs()
            .map(|(key, value)| (key.to_owned(), value.map(OsStr::to_owned)))
            .collect::<HashMap<_, _>>();
        assert_eq!(
            env.get(OsStr::new("VORTOCODE_PROVIDER_DEEPSEEK_BASE")).and_then(Option::as_deref),
            Some(OsStr::new("https://api.example.com/v1"))
        );
        assert_eq!(
            env.get(OsStr::new("VORTOCODE_PROVIDER_DEEPSEEK_KEY")).and_then(Option::as_deref),
            Some(OsStr::new("key"))
        );
        let status = serde_json::to_string(&desktop_llm_profile_status(Some(&profile))).unwrap();
        assert!(status.contains("\"hasKey\":true") && !status.contains("main-key") && !status.contains("\"key\""));
    }

    #[test]
    fn relay_login_helpers_pick_the_right_keys() {
        let plan = serde_json::json!([
            {"key": "paused", "status": 2},
            {"key": "plan-key", "status": 1, "managed": true}
        ]);
        assert_eq!(pick_plan_key(&plan).as_deref(), Some("plan-key"));
        assert_eq!(pick_plan_key(&serde_json::json!([])), None);
        let tokens = serde_json::json!([
            {"name": "VortoCode Desktop", "key": "plan", "status": 1, "billing_type": "token_plan"},
            {"name": "VortoCode Desktop", "key": "off", "status": 2, "billing_type": "pay_as_you_go"},
            {"name": "VortoCode Desktop 2", "key": "other", "status": 1, "billing_type": "pay_as_you_go"},
            {"name": "VortoCode Desktop", "key": "mine", "status": 1, "billing_type": "pay_as_you_go"}
        ]);
        assert_eq!(pick_desktop_token(&tokens).as_deref(), Some("mine"));
        assert_eq!(with_sk_prefix("abc"), "sk-abc");
        assert_eq!(with_sk_prefix("sk-abc"), "sk-abc");
        assert_eq!(
            relay_data(serde_json::json!({"success": false, "message": "用户名或密码错误"}), "登录失败"),
            Err("用户名或密码错误".to_string())
        );
        assert_eq!(relay_data(serde_json::json!({"success": true, "data": 1}), "x"), Ok(serde_json::json!(1)));
    }

    #[test]
    fn signed_out_profile_keeps_providers_but_drops_the_default_key() {
        let profile = normalize_llm_profile(DesktopLlmProfile {
            base_url: "https://token.vortotech.com/v1".into(),
            api_key: String::new(),
            model: "mimo".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            providers: vec![normalize_llm_provider(sample_provider("deepseek")).unwrap()],
            account: None,
            signed_out: true,
            model_info: Vec::new(),
            models: Vec::new(),
        })
        .expect("signed-out profiles are allowed without a key");
        assert!(!desktop_llm_profile_status(Some(&profile)).configured);
        let mut command = Command::new("vc");
        configure_llm_profile(&mut command, Some(&profile));
        let names = command
            .get_envs()
            .map(|(key, _)| key.to_string_lossy().to_string())
            .collect::<Vec<_>>();
        assert!(names.contains(&"VORTOCODE_PROVIDER_DEEPSEEK_KEY".to_string()));
        assert!(!names.contains(&"OPENAI_API_KEY".to_string()));
    }

    #[test]
    fn default_service_models_are_cleaned_and_injected() {
        assert_eq!(
            clean_model_list(vec![" a ".into(), "a".into(), "b,c".into(), "".into(), "d".into()]),
            vec!["a".to_string(), "d".to_string()]
        );
        let mut profile = normalize_llm_profile(DesktopLlmProfile {
            base_url: "https://models.example.com/v1".into(),
            api_key: "k".into(),
            model: "main".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: vec!["main".into(), "other".into()],
        })
        .unwrap();
        let mut command = Command::new("vc");
        configure_llm_profile(&mut command, Some(&profile));
        let env = command
            .get_envs()
            .map(|(key, value)| (key.to_owned(), value.map(OsStr::to_owned)))
            .collect::<HashMap<_, _>>();
        assert_eq!(
            env.get(OsStr::new("VORTOCODE_MODEL_CHOICES")).and_then(Option::as_deref),
            Some(OsStr::new("main,other"))
        );
        profile.models.clear();
        let mut command = Command::new("vc");
        configure_llm_profile(&mut command, Some(&profile));
        assert!(command
            .get_envs()
            .any(|(key, value)| key == OsStr::new("VORTOCODE_MODEL_CHOICES") && value.is_none()));
    }

    #[test]
    fn classified_model_list_keeps_only_agent_ready_models() {
        let payload = serde_json::json!({"data": [
            {"id": "mimo-v2.6-pro", "category": "omni", "capabilities": ["vision", "audio_input", "tools", "reasoning"],
             "context_window": 1048576, "tier": "coding"},
            {"id": "qwen3.7-max-2026-05-17", "category": "chat", "capabilities": ["tools"], "snapshot_of": "qwen3.7-max"},
            {"id": "qwen-mt-plus", "category": "translation", "capabilities": ["tools"]},
            {"id": "wan2.7-t2v", "category": "video"},
            {"id": "no-tools-chat", "category": "chat", "capabilities": ["reasoning"]},
            {"id": "unlabelled"},
            {"id": "bad,name", "category": "chat", "capabilities": ["tools"]},
            {"id": "mimo-v2.6-pro", "category": "omni", "capabilities": ["tools"]}
        ]});
        let infos = model_infos(&payload);
        assert_eq!(
            infos.iter().map(|info| info.id.as_str()).collect::<Vec<_>>(),
            vec!["mimo-v2.6-pro", "qwen3.7-max-2026-05-17"]
        );
        assert_eq!(infos[0].tier.as_deref(), Some("coding"));
        assert_eq!(infos[0].context_window, Some(1_048_576));
        assert_eq!(infos[1].snapshot_of.as_deref(), Some("qwen3.7-max"));

        // 别家服务不带分类：整份清单原样保留（只做 id 清洗）。
        let plain = model_infos(&serde_json::json!({"data": [{"id": "gpt-4o"}, {"id": "whisper-1"}]}));
        assert_eq!(plain.iter().map(|info| info.id.as_str()).collect::<Vec<_>>(), vec!["gpt-4o", "whisper-1"]);
        assert!(plain.iter().all(|info| info.category.is_none() && info.capabilities.is_empty()));
    }

    #[test]
    fn model_choices_env_only_names_agent_ready_models() {
        let info = |id: &str, category: Option<&str>, tools: bool| ModelInfo {
            id: id.into(),
            category: category.map(str::to_string),
            capabilities: if tools { vec!["tools".into()] } else { Vec::new() },
            ..ModelInfo::default()
        };
        let mut profile = normalize_llm_profile(DesktopLlmProfile {
            base_url: "https://models.example.com/v1".into(),
            api_key: "k".into(),
            model: "main".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            providers: Vec::new(),
            account: None,
            signed_out: false,
            models: vec!["main".into(), "ocr".into(), "no-tools".into()],
            // 配置文件可以手改：即便存了不能跑 Agent 的条目，也不会被放行点名。
            model_info: vec![
                info("main", Some("chat"), true),
                info("ocr", Some("ocr"), true),
                info("no-tools", Some("multimodal"), false),
            ],
        })
        .unwrap();
        let choices = |profile: &DesktopLlmProfile| {
            let mut command = Command::new("vc");
            configure_llm_profile(&mut command, Some(profile));
            command
                .get_envs()
                .find(|(key, _)| *key == OsStr::new("VORTOCODE_MODEL_CHOICES"))
                .and_then(|(_, value)| value.map(OsStr::to_owned))
        };
        assert_eq!(choices(&profile).as_deref(), Some(OsStr::new("main")));
        // 没有分类（旧配置 / 别家服务）：沿用整份模型名清单。
        profile.model_info = vec![info("main", None, false), info("ocr", None, false)];
        assert_eq!(choices(&profile).as_deref(), Some(OsStr::new("main,ocr,no-tools")));
        profile.model_info.clear();
        assert_eq!(choices(&profile).as_deref(), Some(OsStr::new("main,ocr,no-tools")));
    }

    #[test]
    fn legacy_profile_without_model_info_still_loads() {
        let payload = r#"{"baseUrl":"https://models.example.com/v1","apiKey":"k","model":"main","models":["main","other"]}"#;
        let profile: DesktopLlmProfile = serde_json::from_str(payload).unwrap();
        assert_eq!(profile.models, vec!["main".to_string(), "other".to_string()]);
        assert!(profile.model_info.is_empty());
        let saved = serde_json::to_value(&profile).unwrap();
        assert!(saved.get("modelInfo").is_none());
    }

    #[test]
    fn project_sessions_use_title_or_first_user_message() {
        assert_eq!(session_title(&serde_json::json!({"title": " 改名后 ", "transcript": []})).as_deref(), Some("改名后"));
        let long = "一".repeat(45);
        let title = session_title(&serde_json::json!({"transcript": [
            {"role": "assistant", "text": "hi"}, {"role": "user", "text": format!("  {long}\n ")}
        ]}))
        .unwrap();
        assert_eq!(title.chars().count(), 41);
        assert!(title.ends_with('…'));
        assert_eq!(session_title(&serde_json::json!({"transcript": []})), None);

        let root = std::env::temp_dir().join(format!("vc-sessions-{}", std::process::id()));
        let sessions = root.join(".vortocode").join("web_sessions");
        create_dir_all(&sessions).unwrap();
        std::fs::write(sessions.join("abc-1.json"), r#"{"transcript":[{"role":"user","text":"你好"}]}"#).unwrap();
        std::fs::write(sessions.join("../escape.json"), "{}").unwrap();
        std::fs::write(sessions.join("bad name.json"), r#"{"title":"x"}"#).unwrap();
        let project = DesktopProjectProfile {
            id: "p1".into(),
            name: "demo".into(),
            repo_root: root.display().to_string(),
            base_url: String::new(),
            last_opened_at: 0,
            kind: "local".into(),
        };
        let found = recent_project_sessions(&project);
        assert_eq!(found.len(), 1);
        assert_eq!((found[0].sid.as_str(), found[0].title.as_str()), ("abc-1", "你好"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn routing_tier_names_are_trimmed_and_blank_means_unset() {
        let profile = normalize_llm_profile(DesktopLlmProfile {
            base_url: "https://models.example.com/v1".into(),
            api_key: "k".into(),
            model: "main".into(),
            context_window: None,
            context_window_source: None,
            fast_model: Some("  fast  ".into()),
            strong_model: Some("   ".into()),
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        })
        .unwrap();
        assert_eq!(profile.fast_model.as_deref(), Some("fast"));
        assert_eq!(profile.strong_model, None);
        assert!(normalize_llm_profile(DesktopLlmProfile {
            strong_model: Some("bad\u{7}".into()),
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
            ..profile
        })
        .is_err());
    }

    #[test]
    fn model_context_window_prefers_matching_service_metadata() {
        let payload = serde_json::json!({
            "data": [
                {"id": "other", "context_window": 4096},
                {"id": "mimo-v2.5", "capabilities": {"max_context_length": 900000}}
            ]
        });
        assert_eq!(
            context_window_from_models_payload(&payload, "mimo-v2.5"),
            Some(900_000)
        );
        assert_eq!(
            context_window_from_models_payload(&payload, "missing"),
            None
        );
    }

    #[test]
    fn official_mimo_context_distinguishes_base_from_chat_model() {
        assert_eq!(official_model_context_window("mimo-v2.5"), Some(1_000_000));
        assert_eq!(
            official_model_context_window("XiaomiMiMo/MiMo-V2.5-Base"),
            Some(256_000)
        );
        assert_eq!(official_model_context_window("unknown"), None);
    }

    #[test]
    fn llm_profile_store_loads_profile_at_most_once_per_process() {
        let store = DesktopLlmProfileStore::default();
        let reads = AtomicUsize::new(0);
        let load = || {
            reads.fetch_add(1, Ordering::SeqCst);
            Ok(Some(DesktopLlmProfile {
                base_url: "https://models.example.com/v1".into(),
                api_key: "secret".into(),
                model: "model-1".into(),
                context_window: Some(65_536),
                context_window_source: Some("configured".into()),
                fast_model: None,
                strong_model: None,
                providers: Vec::new(),
                account: None,
                signed_out: false,
                model_info: Vec::new(),
                models: Vec::new(),
            }))
        };

        let first = store.get_or_try_init(load).expect("initial load");
        let second = store
            .get_or_try_init(|| {
                reads.fetch_add(1, Ordering::SeqCst);
                Err("must not read twice".into())
            })
            .expect("cached load");

        assert_eq!(reads.load(Ordering::SeqCst), 1);
        assert_eq!(
            first.as_ref().map(|profile| profile.model.as_str()),
            Some("model-1")
        );
        assert_eq!(
            second.as_ref().map(|profile| profile.model.as_str()),
            Some("model-1")
        );
    }

    #[test]
    fn llm_profile_store_caches_missing_profile_but_not_read_errors() {
        let store = DesktopLlmProfileStore::default();
        let reads = AtomicUsize::new(0);

        assert!(store
            .get_or_try_init(|| {
                reads.fetch_add(1, Ordering::SeqCst);
                Err("authorization denied".into())
            })
            .is_err());
        assert!(store
            .get_or_try_init(|| {
                reads.fetch_add(1, Ordering::SeqCst);
                Ok(None)
            })
            .expect("retry after error")
            .is_none());
        assert!(store
            .get_or_try_init(|| {
                reads.fetch_add(1, Ordering::SeqCst);
                Err("missing profile should have been cached".into())
            })
            .expect("cached missing profile")
            .is_none());
        assert_eq!(reads.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn llm_profile_store_replaces_cache_after_save_and_clear() {
        let store = DesktopLlmProfileStore::default();
        let profile = DesktopLlmProfile {
            base_url: "https://models.example.com/v1".into(),
            api_key: "secret".into(),
            model: "model-2".into(),
            context_window: Some(128_000),
            context_window_source: Some("configured".into()),
            fast_model: None,
            strong_model: None,
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        };

        store.replace(Some(profile)).expect("save cache");
        let saved = store
            .get_or_try_init(|| Err("cache should be warm".into()))
            .expect("read saved cache");
        assert_eq!(
            saved.as_ref().map(|profile| profile.model.as_str()),
            Some("model-2")
        );

        store.replace(None).expect("clear cache");
        assert!(store
            .get_or_try_init(|| Err("cleared cache should stay warm".into()))
            .expect("read cleared cache")
            .is_none());
    }

    #[test]
    fn project_registry_normalizes_git_root_and_updates_in_place() {
        let root = temp_workspace("registry");
        init_git(&root);
        create_dir_all(root.join("nested")).expect("create nested directory");
        let registry_path = root.join("config/projects.json");

        let first = remember_project_at(
            &registry_path,
            &root.join("nested").to_string_lossy(),
            "http://localhost:8080/",
        )
        .expect("remember project");
        let updated = remember_project_at(
            &registry_path,
            &root.to_string_lossy(),
            "http://127.0.0.1:8123",
        )
        .expect("update project");
        let registry = load_project_registry(&registry_path).expect("load registry");

        assert_eq!(first.id, updated.id);
        assert_eq!(registry.projects.len(), 1);
        assert_eq!(registry.projects[0].base_url, "http://127.0.0.1:8123");
        assert_eq!(
            PathBuf::from(&registry.projects[0].repo_root),
            root.canonicalize().expect("canonical root")
        );
        assert!(registry.projects[0].last_opened_at > 0);

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn project_registry_rejects_remote_urls_and_plain_directories() {
        let root = temp_workspace("registry-boundary");
        let registry_path = root.join("projects.json");
        assert!(remember_project_at(
            &registry_path,
            &root.to_string_lossy(),
            "https://example.com:8080",
        )
        .is_err());
        assert!(remember_project_at(
            &registry_path,
            &root.to_string_lossy(),
            "http://127.0.0.1:8080/api",
        )
        .is_err());
        init_git(&root);
        assert!(remember_project_at(
            &registry_path,
            &root.to_string_lossy(),
            "http://127.0.0.1:8080",
        )
        .is_ok());

        remove_dir_all(root).expect("remove temp workspace");
    }

    // ── R3a 远程工作区：网段校验 + 注册流 + 向后兼容 ────────────────────────────

    #[test]
    fn validate_remote_server_url_accepts_https_and_private_http() {
        // https：任意 host 放行（传输层假定 Tailscale/反代兜底），归一化去尾斜杠。
        assert_eq!(
            validate_remote_server_url("https://vorto.example.com/").unwrap(),
            "https://vorto.example.com"
        );
        assert_eq!(
            validate_remote_server_url("https://100.107.1.2:8443").unwrap(),
            "https://100.107.1.2:8443"
        );
        // http + 私网/CGNAT/Tailscale IP：放行。
        for url in [
            "http://192.168.1.10:8080",
            "http://10.0.0.5:8080",
            "http://172.16.3.4:8080",
            "http://127.0.0.1:8080",
            "http://100.64.0.1:8080", // CGNAT/Tailscale 100.64/10 下沿
            "http://100.127.9.9:8080",
        ] {
            assert!(validate_remote_server_url(url).is_ok(), "{url} 应放行");
        }
    }

    #[test]
    fn validate_remote_server_url_rejects_public_http_domains_and_paths() {
        for url in [
            "http://8.8.8.8:8080",           // 公网 IP
            "http://93.184.216.34",          // 公网 IP
            "http://vorto.example.com:8080", // http+域名：DNS 判段有 TOCTOU，拒
            "http://100.128.0.1",            // 100.128 已超出 100.64/10
            "http://172.32.0.1",             // 172.32 超出 172.16/12
            "ftp://192.168.1.10",            // 非 http/https
            "https://host:8080/api",         // 带路径
            "https://host:8080?x=1",         // 带查询
            "not a url",
        ] {
            assert!(validate_remote_server_url(url).is_err(), "{url} 应被拒");
        }
    }

    #[test]
    fn validate_remote_server_url_normalizes_default_ports() {
        assert_eq!(
            validate_remote_server_url("https://host/").unwrap(),
            "https://host"
        );
        assert_eq!(
            validate_remote_server_url("https://host:443").unwrap(),
            "https://host" // https 默认端口省略
        );
        assert_eq!(
            validate_remote_server_url("http://192.168.0.1:80").unwrap(),
            "http://192.168.0.1" // http 默认端口省略
        );
        assert_eq!(
            validate_remote_server_url("https://host:9000").unwrap(),
            "https://host:9000"
        );
    }

    #[test]
    fn remote_project_id_stable_and_distinct_from_local() {
        let a = remote_project_id("https://s.example", "/srv/repo");
        assert_eq!(a, remote_project_id("https://s.example", "/srv/repo")); // 确定性
        assert_ne!(a, remote_project_id("https://s.example", "/srv/other")); // 换根
        assert_ne!(a, remote_project_id("https://t.example", "/srv/repo")); // 换 url
        assert_ne!(a, project_id(Path::new("/srv/repo"))); // 与本地 id 不同源
        assert_eq!(a.len(), 20);
    }

    #[test]
    fn remember_remote_at_writes_remote_entry_and_forget_removes() {
        let root = temp_workspace("remote-registry");
        let registry_path = root.join("projects.json");
        let id = remote_project_id("https://srv.ts.net:8080", "/work/vortocode");
        let profile = remember_remote_at(
            &registry_path,
            "https://srv.ts.net:8080",
            "/work/vortocode",
            "", // 空名 → 从 repo_root 末段派生
            &id,
        )
        .expect("remote entry");
        assert_eq!(profile.kind, PROJECT_KIND_REMOTE);
        assert_eq!(profile.base_url, "https://srv.ts.net:8080");
        assert_eq!(profile.repo_root, "/work/vortocode"); // 服务器侧路径原样，未 canonicalize
        assert_eq!(profile.name, "vortocode");
        assert_eq!(profile.id, id);
        let loaded = load_project_registry(&registry_path).unwrap();
        assert_eq!(loaded.projects.len(), 1);
        assert_eq!(loaded.projects[0].kind, PROJECT_KIND_REMOTE);
        let remaining = forget_project_at(&registry_path, &id).unwrap();
        assert!(remaining.is_empty());
        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn remember_remote_flow_rejects_unconfirmed_new_leaves_no_trace() {
        let root = temp_workspace("remote-flow");
        let registry_path = root.join("projects.json");
        let id = remote_project_id("https://srv", "/r");
        // 新 + 未确认 → 拒绝，且注册表零痕迹（fail-closed）。
        assert!(
            remember_remote_flow(&registry_path, "https://srv", "/r", "n", &id, false, false)
                .is_err()
        );
        assert!(load_project_registry(&registry_path)
            .unwrap()
            .projects
            .is_empty());
        // 新 + 已确认 → 落库。
        assert!(
            remember_remote_flow(&registry_path, "https://srv", "/r", "n", &id, false, true).is_ok()
        );
        assert_eq!(
            load_project_registry(&registry_path).unwrap().projects.len(),
            1
        );
        // 已注册（known）→ 无需再确认也放行（续期）。
        assert!(
            remember_remote_flow(&registry_path, "https://srv", "/r", "n", &id, true, false).is_ok()
        );
        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn project_profile_without_kind_field_deserializes_as_local() {
        // 旧 projects.json 无 kind 字段 → serde 默认 local（向后兼容）。
        let json = r#"{"id":"x","name":"n","repoRoot":"/tmp/x","baseUrl":"http://127.0.0.1:8080","lastOpenedAt":1}"#;
        let profile: DesktopProjectProfile =
            serde_json::from_str(json).expect("deserialize legacy");
        assert_eq!(profile.kind, PROJECT_KIND_LOCAL);
    }

    #[test]
    fn local_and_remote_entries_coexist_in_registry() {
        let root = temp_workspace("coexist");
        init_git(&root);
        let registry_path = root.join("projects.json");
        remember_project_at(&registry_path, &root.to_string_lossy(), "http://127.0.0.1:8080")
            .expect("local");
        let rid = remote_project_id("https://srv", "/r");
        remember_remote_at(&registry_path, "https://srv", "/r", "remote-one", &rid).expect("remote");
        let loaded = load_project_registry(&registry_path).unwrap();
        assert_eq!(loaded.projects.len(), 2);
        let kinds: Vec<&str> = loaded.projects.iter().map(|item| item.kind.as_str()).collect();
        assert!(kinds.contains(&PROJECT_KIND_LOCAL));
        assert!(kinds.contains(&PROJECT_KIND_REMOTE));
        remove_dir_all(root).expect("remove temp workspace");
    }

    // ── R3a-2a 远程传输层：路径闸 / URL 终局不变量 / 目标解析 ────────────────────

    #[test]
    fn sanitize_remote_path_enforces_api_prefix_and_rejects_escapes() {
        assert_eq!(sanitize_remote_path("/api/runs").unwrap(), "/api/runs");
        assert_eq!(
            sanitize_remote_path("/api/x?limit=10&q=a").unwrap(),
            "/api/x?limit=10&q=a"
        );
        // 查询串里的 https:// 是合法值，不该误杀（整串 contains("://") 会）。
        assert_eq!(
            sanitize_remote_path("/api/x?next=https://ok.example").unwrap(),
            "/api/x?next=https://ok.example"
        );
        for bad in [
            "api/runs",               // 不以 / 开头
            "//evil.com/x",           // 协议相对地址
            "https://evil.com/x",     // 带协议
            "/api/x y",               // 含空格
            "/api/x\nHost: evil.com", // 控制字符（请求走私形状）
            "/ws",                    // 非 /api/ 前缀：收窄混淆代理人可达面
            "/健康",                  // 同上
        ] {
            assert!(sanitize_remote_path(bad).is_err(), "{bad:?} 应被拒");
        }
    }

    #[test]
    fn build_remote_url_keeps_backslash_paths_on_registered_host() {
        let url = build_remote_url("https://srv.ts.net:8443", "/api/runs").unwrap();
        assert_eq!(url.as_str(), "https://srv.ts.net:8443/api/runs");
        assert_eq!(url.host_str(), Some("srv.ts.net"));
        // 真正会换 host 的协议相对形状（`//…` 与首字符后紧跟 `\`，后者在 WHATWG special
        // scheme 下等价 `//`）被 /api/ 前缀闸挡在门外。
        for bad in ["//evil.com/x", "/\\evil.com/x"] {
            assert!(
                build_remote_url("https://srv.ts.net", bad).is_err(),
                "{bad:?} 应被拒"
            );
        }
        // 而 `/api/` **之后**的反斜杠只是被归一成 `/`（path 变 `/api//evil.com/x`），host 不变
        // ——它不是换目的地的形状，放行且安全。钉住真实行为，不写成假的"已拦下"。
        let normalized = build_remote_url("https://srv.ts.net", "/api/\\evil.com/x").unwrap();
        assert_eq!(normalized.host_str(), Some("srv.ts.net"));
        assert_eq!(normalized.path(), "/api//evil.com/x");
    }

    #[test]
    fn same_remote_origin_compares_scheme_host_port() {
        // 直接测兜底防线本身——它在当前路径闸下不可达，内联进 build_remote_url 的话
        // 任何测试都会先被路径闸拦下，判断删掉也全绿（F-6 mutation 实测过的安慰剂形状）。
        let registered = Url::parse("https://srv.ts.net:8443").unwrap();
        assert!(same_remote_origin(
            &Url::parse("https://srv.ts.net:8443/api/x").unwrap(),
            &registered
        ));
        for other in [
            "http://srv.ts.net:8443/api/x",  // scheme 不同
            "https://evil.com:8443/api/x",   // host 不同
            "https://srv.ts.net:9999/api/x", // port 不同
        ] {
            assert!(
                !same_remote_origin(&Url::parse(other).unwrap(), &registered),
                "{other} 应判为异源"
            );
        }
        // 默认端口等价：https 显式 443 与省略应同源。
        assert!(same_remote_origin(
            &Url::parse("https://a.example:443/api/x").unwrap(),
            &Url::parse("https://a.example").unwrap()
        ));
    }

    #[test]
    fn resolve_remote_base_requires_registered_remote_entry() {
        let root = temp_workspace("resolve-remote");
        let registry_path = root.join("projects.json");
        let rid = remote_project_id("https://srv.ts.net", "/work/repo");
        remember_remote_at(&registry_path, "https://srv.ts.net", "/work/repo", "r", &rid)
            .expect("remote entry");

        // 已注册的 remote → 放行并回归一化 base。
        assert_eq!(
            resolve_remote_base(&registry_path, &rid).unwrap(),
            "https://srv.ts.net"
        );
        // 未注册 id → 拒（webview 编个 id 也拿不到出网）。
        assert!(resolve_remote_base(&registry_path, "not-registered").is_err());

        // local 条目的 id → 拒（远程通道不给本地项目用）。
        init_git(&root);
        let local = remember_project_at(&registry_path, &root.to_string_lossy(), "http://127.0.0.1:8080")
            .expect("local entry");
        assert!(resolve_remote_base(&registry_path, &local.id).is_err());

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn resolve_remote_base_revalidates_tampered_registry_url() {
        // projects.json 是磁盘文件，可能被改成公网 http；每次出网都重新过网段校验 → 拒。
        let root = temp_workspace("resolve-tampered");
        let registry_path = root.join("projects.json");
        let registry = DesktopProjectRegistry {
            projects: vec![DesktopProjectProfile {
                id: "tampered".into(),
                name: "t".into(),
                repo_root: "/work/repo".into(),
                base_url: "http://8.8.8.8:8080".into(), // 篡改成公网 http
                last_opened_at: 1,
                kind: PROJECT_KIND_REMOTE.into(),
            }],
        };
        save_project_registry(&registry_path, &registry).expect("save");
        assert!(resolve_remote_base(&registry_path, "tampered").is_err());
        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn resolve_remote_base_rejects_id_base_url_mismatch() {
        // 真正的篡改场景：把 base_url 换成攻击者的 **https** 域名（网段校验放行任意 https，
        // 单靠它挡不住）而**保持 id 不变**，好让 Keychain 里按 id 存的真 token 被发去攻击者。
        // id↔(base_url, repo_root) 绑定回验必须拦下它。
        let root = temp_workspace("resolve-rebind");
        let registry_path = root.join("projects.json");
        let original_id = remote_project_id("https://srv.ts.net", "/work/repo");
        let registry = DesktopProjectRegistry {
            projects: vec![DesktopProjectProfile {
                id: original_id.clone(), // id 保持原样
                name: "t".into(),
                repo_root: "/work/repo".into(),
                base_url: "https://attacker.tld".into(), // 目的地被换
                last_opened_at: 1,
                kind: PROJECT_KIND_REMOTE.into(),
            }],
        };
        save_project_registry(&registry_path, &registry).expect("save");
        let result = resolve_remote_base(&registry_path, &original_id);
        assert!(result.is_err(), "改了 base_url 却保持 id 的条目必须被拒");
        assert!(result.unwrap_err().contains("篡改"));

        // 对照：未篡改的条目照常放行。
        let clean_root = temp_workspace("resolve-rebind-ok");
        let clean_path = clean_root.join("projects.json");
        remember_remote_at(&clean_path, "https://srv.ts.net", "/work/repo", "t", &original_id)
            .expect("clean entry");
        assert_eq!(
            resolve_remote_base(&clean_path, &original_id).unwrap(),
            "https://srv.ts.net"
        );

        remove_dir_all(root).expect("remove temp workspace");
        remove_dir_all(clean_root).expect("remove temp workspace");
    }

    #[test]
    fn normalize_http_method_allows_only_known_verbs() {
        assert_eq!(normalize_http_method("get").unwrap(), reqwest::Method::GET);
        assert_eq!(normalize_http_method(" POST ").unwrap(), reqwest::Method::POST);
        for bad in ["CONNECT", "TRACE", "", "GET /x HTTP/1.1"] {
            assert!(normalize_http_method(bad).is_err(), "{bad:?} 应被拒");
        }
    }

    // ── R3a-2b WS 桥接：sid 闸 / WS 地址推导 ──────────────────────────────────

    #[test]
    fn normalize_ws_sid_rejects_query_injection_shapes() {
        assert_eq!(normalize_ws_sid(" sess-1_A ").unwrap(), "sess-1_A");
        for bad in [
            "",                 // 空
            "a&admin=1",        // 追加查询参数
            "a#frag",           // 片段
            "a?b",              // 再开查询
            "a/../x",           // 路径穿越形状
            "a b",              // 空格
            "会话",             // 非 ASCII
            &"x".repeat(81),    // 超长
        ] {
            assert!(normalize_ws_sid(bad).is_err(), "{bad:?} 应被拒");
        }
    }

    #[test]
    fn remote_ws_url_derives_scheme_and_keeps_registered_authority() {
        // https→wss、http→ws；authority（host+非默认端口）原样保留；路径固定 /ws。
        assert_eq!(
            remote_ws_url("https://srv.ts.net", "s1").unwrap().as_str(),
            "wss://srv.ts.net/ws?sid=s1"
        );
        assert_eq!(
            remote_ws_url("https://srv.ts.net:8443", "s1")
                .unwrap()
                .as_str(),
            "wss://srv.ts.net:8443/ws?sid=s1"
        );
        assert_eq!(
            remote_ws_url("http://192.168.1.10:8080", "s1")
                .unwrap()
                .as_str(),
            "ws://192.168.1.10:8080/ws?sid=s1"
        );
        // sid 闸拦在构造之前——注入形状不会进查询串。
        assert!(remote_ws_url("https://srv.ts.net", "a&admin=1").is_err());
        // 非 http/https 的 base 不接受。
        assert!(remote_ws_url("ftp://srv.ts.net", "s1").is_err());
    }

    #[test]
    fn normalize_remote_token_rejects_non_visible_ascii() {
        assert_eq!(normalize_remote_token("  sk-abc123  ").unwrap(), "sk-abc123");
        for bad in [
            "",
            "   ",
            "sk-\u{feff}abc",  // BOM（trim 吃不掉）
            "sk-abc\u{a0}def", // NBSP 夹在串中间
            "sk-\u{2019}abc",  // 智能引号
            "sk-秘密",         // 非 ASCII
            "sk-abc\u{7f}",    // DEL
            "sk abc",          // 空格
        ] {
            assert!(normalize_remote_token(bad).is_err(), "{bad:?} 应被拒");
        }
    }

    #[test]
    fn remote_ws_failure_reason_never_leaks_underlying_detail() {
        use tokio_tungstenite::tungstenite::Error;
        // 底层 error 可能带头值原文（tungstenite 握手失败会把 HeaderValue 的 Debug 写进去），
        // 回给 webview 的串必须只剩粗粒度原因。
        let leaky = Error::Io(std::io::Error::new(
            std::io::ErrorKind::Other,
            format!("Bearer {}{}", "sk-", "must-not-leak-0123456789"),
        ));
        let reason = remote_ws_failure_reason(&leaky);
        assert!(
            !reason.contains("sk-must-not-leak"),
            "错误串泄漏了 token：{reason}"
        );
        assert!(reason.contains("网络不可达"));
    }

    #[test]
    fn project_registration_gate_flags_only_new_roots() {
        let root = temp_workspace("registry-gate");
        init_git(&root);
        create_dir_all(root.join("nested")).expect("create nested directory");
        let registry_path = root.join("config/projects.json");

        let (resolved, known) = project_registration_gate(&registry_path, &root.to_string_lossy())
            .expect("gate new root");
        assert_eq!(resolved, root.canonicalize().expect("canonical root"));
        assert!(!known);

        remember_project_at(
            &registry_path,
            &root.to_string_lossy(),
            "http://127.0.0.1:8080",
        )
        .expect("register root");
        let (_, known) =
            project_registration_gate(&registry_path, &root.join("nested").to_string_lossy())
                .expect("gate nested path");
        assert!(known, "子目录应归一化到已注册的 Git 根，不再当作新根");

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn unconfirmed_new_project_registration_leaves_no_trace() {
        let root = temp_workspace("registry-unconfirmed");
        init_git(&root);
        let registry_path = root.join("projects.json");

        let error = remember_project_flow(
            &registry_path,
            &root.to_string_lossy(),
            "http://127.0.0.1:8080",
            false,
            false,
        )
        .err()
        .expect("must reject unconfirmed new root");
        assert!(error.contains("未获用户确认"));
        assert!(!registry_path.exists(), "拒绝路径不得留下注册表文件");

        remember_project_flow(
            &registry_path,
            &root.to_string_lossy(),
            "http://127.0.0.1:8080",
            false,
            true,
        )
        .expect("confirmed new root registers");
        assert!(registry_path.exists());

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn known_project_reregistration_skips_confirmation() {
        let root = temp_workspace("registry-known");
        init_git(&root);
        let registry_path = root.join("projects.json");
        remember_project_at(
            &registry_path,
            &root.to_string_lossy(),
            "http://127.0.0.1:8080",
        )
        .expect("seed registry");

        let profile = remember_project_flow(
            &registry_path,
            &root.to_string_lossy(),
            "http://127.0.0.1:9090",
            true,
            false,
        )
        .expect("known root re-registers without confirmation");
        assert_eq!(profile.base_url, "http://127.0.0.1:9090");

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn corrupt_registry_is_not_silently_overwritten() {
        let root = temp_workspace("registry-corrupt");
        let registry_path = root.join("projects.json");
        write(&registry_path, "not-json").expect("write corrupt registry");

        let error = load_project_registry(&registry_path)
            .err()
            .expect("reject corrupt registry");
        assert!(error.contains("已损坏"));

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn project_registry_forget_removes_only_the_selected_project() {
        let root = temp_workspace("registry-forget");
        let registry_path = root.join("projects.json");
        let registry = DesktopProjectRegistry {
            projects: vec![
                DesktopProjectProfile {
                    id: "one".into(),
                    name: "one".into(),
                    repo_root: "/tmp/one".into(),
                    base_url: "http://127.0.0.1:8080".into(),
                    last_opened_at: 2,
                    kind: PROJECT_KIND_LOCAL.into(),
                },
                DesktopProjectProfile {
                    id: "two".into(),
                    name: "two".into(),
                    repo_root: "/tmp/two".into(),
                    base_url: "http://127.0.0.1:8081".into(),
                    last_opened_at: 1,
                    kind: PROJECT_KIND_LOCAL.into(),
                },
            ],
        };
        save_project_registry(&registry_path, &registry).expect("save registry");

        let remaining = forget_project_at(&registry_path, "one").expect("forget project");
        assert_eq!(remaining.len(), 1);
        assert_eq!(remaining[0].id, "two");

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn project_registry_keeps_only_the_thirty_most_recent_projects() {
        let mut registry = DesktopProjectRegistry::default();
        for index in 0..=MAX_DESKTOP_PROJECTS {
            upsert_project(
                &mut registry,
                DesktopProjectProfile {
                    id: format!("project-{index}"),
                    name: format!("project-{index}"),
                    repo_root: format!("/tmp/project-{index}"),
                    base_url: "http://127.0.0.1:8080".into(),
                    last_opened_at: index as u64,
                    kind: PROJECT_KIND_LOCAL.into(),
                },
            );
        }

        assert_eq!(registry.projects.len(), MAX_DESKTOP_PROJECTS);
        assert_eq!(
            registry.projects[0].id,
            format!("project-{MAX_DESKTOP_PROJECTS}")
        );
        assert!(!registry
            .projects
            .iter()
            .any(|project| project.id == "project-0"));
    }

    #[test]
    fn gateway_recovery_round_trips_and_can_be_cleared() {
        let root = temp_workspace("gateway-recovery");
        let path = root.join("state/gateway-recovery.json");
        let record = GatewayRecoveryRecord {
            runtime_id: "project-test".into(),
            project_id: Some("test".into()),
            workspace_id: None,
            scope: PROJECT_SCOPE.into(),
            workspace_root: "/tmp/project".into(),
            repo_root: "/tmp/project".into(),
            base_url: "http://127.0.0.1:8123".into(),
            pid: Some(1234),
            started_at: 10,
            updated_at: 20,
            status: "running".into(),
            message: "running".into(),
        };

        save_gateway_recovery(&path, &record).expect("save recovery record");
        assert_eq!(
            load_gateway_recovery(&path, Some("project-test")).expect("load recovery record"),
            Some(record)
        );
        clear_gateway_recovery_at(&path, Some("project-test")).expect("clear recovery record");
        assert_eq!(
            load_gateway_recovery(&path, Some("project-test"))
                .expect("load cleared recovery record"),
            None
        );

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn gateway_recovery_keeps_independent_runtime_records() {
        let root = temp_workspace("gateway-recovery-multiple");
        let path = root.join("state/gateway-recovery.json");
        for (runtime_id, repo_root, updated_at) in [
            ("project-one", "/tmp/one", 10),
            ("project-two", "/tmp/two", 20),
        ] {
            save_gateway_recovery(
                &path,
                &GatewayRecoveryRecord {
                    runtime_id: runtime_id.into(),
                    project_id: Some(runtime_id.into()),
                    workspace_id: None,
                    scope: PROJECT_SCOPE.into(),
                    workspace_root: repo_root.into(),
                    repo_root: repo_root.into(),
                    base_url: format!("http://127.0.0.1:81{updated_at}"),
                    pid: Some(updated_at as u32),
                    started_at: updated_at,
                    updated_at,
                    status: "running".into(),
                    message: "running".into(),
                },
            )
            .expect("save runtime recovery");
        }

        assert_eq!(
            load_gateway_recoveries(&path)
                .expect("load recovery registry")
                .runtimes
                .len(),
            2
        );
        assert_eq!(
            load_gateway_recovery(&path, None)
                .expect("load newest recovery")
                .expect("newest recovery")
                .runtime_id,
            "project-two"
        );
        clear_gateway_recovery_at(&path, Some("project-one")).expect("clear one runtime");
        assert!(load_gateway_recovery(&path, Some("project-one"))
            .expect("load cleared runtime")
            .is_none());
        assert!(load_gateway_recovery(&path, Some("project-two"))
            .expect("load retained runtime")
            .is_some());

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn legacy_gateway_recovery_is_migrated_without_losing_identity() {
        let root = temp_workspace("gateway-recovery-legacy");
        let path = root.join("gateway-recovery.json");
        write(
            &path,
            r#"{
              "scope": "project",
              "repoRoot": "/tmp/legacy",
              "baseUrl": "http://127.0.0.1:8123",
              "pid": 1234,
              "startedAt": 10,
              "updatedAt": 20,
              "status": "running",
              "message": "running"
            }"#,
        )
        .expect("write legacy recovery");

        let recovered = load_gateway_recovery(&path, None)
            .expect("load legacy recovery")
            .expect("legacy recovery");
        assert_eq!(recovered.scope, PROJECT_SCOPE);
        assert_eq!(recovered.workspace_root, "/tmp/legacy");
        assert!(recovered.runtime_id.starts_with("project-"));
        assert!(recovered.project_id.is_some());

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn crashed_recovery_drops_stale_pid_and_preserves_runtime_location() {
        let previous = GatewayRecoveryRecord {
            runtime_id: "project-test".into(),
            project_id: Some("test".into()),
            workspace_id: None,
            scope: PROJECT_SCOPE.into(),
            workspace_root: "/tmp/project".into(),
            repo_root: "/tmp/project".into(),
            base_url: "http://127.0.0.1:8123".into(),
            pid: Some(1234),
            started_at: 10,
            updated_at: 20,
            status: "running".into(),
            message: "running".into(),
        };
        let status = GatewayProcessStatus {
            running: false,
            pid: None,
            runtime_id: Some("project-test".into()),
            project_id: Some("test".into()),
            workspace_id: None,
            command: Some("vc server".into()),
            scope: Some(PROJECT_SCOPE.into()),
            workspace_root: Some("/tmp/project".into()),
            repo_root: Some("/tmp/project".into()),
            base_url: Some("http://127.0.0.1:8123".into()),
            message: "runtime 已退出（exit status: 1）".into(),
        };

        let crashed = crashed_recovery_record(Some(previous), &status).expect("crash record");

        assert_eq!(crashed.repo_root, "/tmp/project");
        assert_eq!(crashed.base_url, "http://127.0.0.1:8123");
        assert_eq!(crashed.started_at, 10);
        assert!(crashed.updated_at >= crashed.started_at);
        assert_eq!(crashed.pid, None);
        assert_eq!(crashed.status, "crashed");
        assert_eq!(crashed.message, status.message);
    }

    #[test]
    fn python_fallback_uses_only_the_fixed_gateway_command() {
        let command = build_command(
            "python3",
            true,
            Path::new("/tmp/vortocode"),
            8765,
            "test-token",
            PROJECT_SCOPE,
        );

        assert_eq!(command.get_program(), OsStr::new("python3"));
        assert_eq!(
            command.get_args().collect::<Vec<_>>(),
            [
                "-m",
                "src.cli",
                "server",
                "--host",
                "127.0.0.1",
                "--port",
                "8765",
            ]
            .map(OsStr::new)
        );
        assert_eq!(command.get_current_dir(), Some(Path::new("/tmp/vortocode")));
        assert!(command.get_envs().any(|(key, value)| {
            key == OsStr::new("VORTOCODE_API_TOKEN") && value == Some(OsStr::new("test-token"))
        }));
        assert!(command.get_envs().any(|(key, value)| {
            key == OsStr::new("VORTOCODE_WORKSPACE_SCOPE")
                && value == Some(OsStr::new(PROJECT_SCOPE))
        }));
    }

    #[test]
    fn gateway_command_declares_this_process_as_the_supervisor() {
        // runtime 的看门狗据此在我们被强杀时自行退出；不申报就会留下孤儿 runtime
        // （2026-09-17 真机诊断：两对 runtime 在 Desktop 被强杀后继续跑，占着端口）。
        let command = configure_gateway_command(
            Command::new("vc"),
            false,
            Path::new("/tmp/vortocode"),
            8765,
            "",
            PROJECT_SCOPE,
        );
        let expected = std::process::id().to_string();
        assert!(command.get_envs().any(|(key, value)| {
            key == OsStr::new("VORTOCODE_SUPERVISOR_PID")
                && value == Some(OsStr::new(expected.as_str()))
        }));
    }

    #[test]
    fn bundled_runtime_uses_only_the_fixed_gateway_command() {
        let command = configure_gateway_command(
            Command::new("/bundle/vortocode-runtime"),
            false,
            Path::new("/tmp/project"),
            8123,
            "",
            PROJECT_SCOPE,
        );

        assert_eq!(
            command.get_program(),
            OsStr::new("/bundle/vortocode-runtime")
        );
        assert_eq!(
            command.get_args().collect::<Vec<_>>(),
            ["server", "--host", "127.0.0.1", "--port", "8123"].map(OsStr::new)
        );
        assert_eq!(command.get_current_dir(), Some(Path::new("/tmp/project")));
        assert!(command
            .get_envs()
            .any(|(key, value)| { key == OsStr::new("VORTOCODE_API_TOKEN") && value.is_none() }));
        assert!(command.get_envs().any(|(key, value)| {
            key == OsStr::new("VORTOCODE_WORKSPACE_SCOPE")
                && value == Some(OsStr::new(PROJECT_SCOPE))
        }));
    }

    #[test]
    fn runtime_scopes_and_scratch_ids_are_strict() {
        assert_eq!(normalize_runtime_scope("general").unwrap(), GENERAL_SCOPE);
        assert_eq!(normalize_runtime_scope(" scratch ").unwrap(), SCRATCH_SCOPE);
        assert_eq!(normalize_runtime_scope("project").unwrap(), PROJECT_SCOPE);
        assert!(normalize_runtime_scope("home").is_err());
        assert_eq!(
            normalize_workspace_id("desktop-123_ab").unwrap(),
            "desktop-123_ab"
        );
        assert!(normalize_workspace_id("../escape").is_err());
        assert!(normalize_workspace_id("").is_err());
    }

    #[cfg(unix)]
    #[test]
    fn managed_runtime_termination_reaps_the_whole_process_group() {
        let mut command = Command::new("/bin/sh");
        command
            .args(["-c", "sleep 30 & wait"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        prepare_gateway_process(&mut command);
        let mut child = command.spawn().expect("spawn process group");
        let pid = child.id();
        assert!(process_group_alive(pid));

        terminate_gateway_child(&mut child).expect("terminate process group");

        assert!(!process_group_alive(pid));
    }

    #[cfg(unix)]
    #[test]
    fn stopping_one_supervised_runtime_keeps_the_other_running() {
        fn runtime(runtime_id: &str) -> GatewayProcessInner {
            let mut command = Command::new("sh");
            command.args(["-c", "sleep 30"]);
            prepare_gateway_process(&mut command);
            GatewayProcessInner {
                child: Some(command.spawn().expect("spawn supervised runtime")),
                runtime_id: runtime_id.into(),
                project_id: Some(runtime_id.into()),
                workspace_id: None,
                scope: Some(PROJECT_SCOPE.into()),
                workspace_root: Some(PathBuf::from(format!("/tmp/{runtime_id}"))),
                repo_root: Some(PathBuf::from(format!("/tmp/{runtime_id}"))),
                command: Some("test runtime".into()),
                base_url: Some("http://127.0.0.1:8123".into()),
            }
        }

        let mut supervisor = GatewaySupervisorInner::default();
        supervisor
            .runtimes
            .insert("project-one".into(), runtime("project-one"));
        supervisor
            .runtimes
            .insert("project-two".into(), runtime("project-two"));
        supervisor.active_runtime_id = Some("project-one".into());

        assert!(stop_supervised_runtime(&mut supervisor, "project-one").expect("stop one"));
        assert!(!supervisor.runtimes.contains_key("project-one"));
        let remaining = supervisor
            .runtimes
            .get_mut("project-two")
            .expect("second runtime retained");
        assert!(process_status(remaining).running);

        assert!(stop_supervised_runtime(&mut supervisor, "project-two").expect("stop two"));
        assert!(supervisor.runtimes.is_empty());
    }

    #[test]
    fn gateway_port_selection_falls_back_when_preferred_port_is_busy() {
        let selected =
            select_gateway_port_with(8080, |_| false, || Ok(49_152)).expect("select fallback port");
        assert_eq!(selected, 49_152);
    }

    #[test]
    fn gateway_port_selection_keeps_an_available_preference() {
        let selected = select_gateway_port_with(8123, |port| port == 8123, || Ok(49_152))
            .expect("select preferred port");
        assert_eq!(selected, 8123);
    }

    #[test]
    fn python_fallback_is_limited_to_the_vortocode_development_checkout() {
        let source_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .expect("canonical source root");
        let unrelated = temp_workspace("runtime-boundary");

        assert!(is_vortocode_development_root(&source_root));
        assert!(!is_vortocode_development_root(&unrelated));

        remove_dir_all(unrelated).expect("remove temp workspace");
    }

    #[test]
    fn empty_process_state_does_not_leak_old_metadata() {
        let mut inner = GatewayProcessInner {
            child: None,
            runtime_id: "project-old".into(),
            project_id: Some("old".into()),
            workspace_id: None,
            scope: Some(PROJECT_SCOPE.into()),
            workspace_root: Some(PathBuf::from("/tmp/old")),
            repo_root: Some(PathBuf::from("/tmp/old")),
            command: Some("vc server".into()),
            base_url: Some("http://127.0.0.1:8080".into()),
        };

        let status = process_status(&mut inner);

        assert!(!status.running);
        assert!(status.pid.is_none());
        assert!(status.command.is_none());
        assert!(status.scope.is_none());
        assert!(status.workspace_root.is_none());
        assert!(status.repo_root.is_none());
    }

    #[test]
    fn workspace_preview_stays_inside_repo_and_rejects_binary() {
        let root = temp_workspace("preview");
        write(root.join("main.rs"), "fn main() {}\n").expect("write source");
        write(root.join("asset.bin"), [1_u8, 0, 2]).expect("write binary");

        let trusted = vec![root.clone()];
        let source = read_workspace_file_within(&trusted, &root.to_string_lossy(), "./main.rs")
            .expect("read source");
        assert_eq!(source.path, "main.rs");
        assert_eq!(source.content, "fn main() {}\n");
        assert_eq!(source.sha256.len(), 64);
        assert!(read_workspace_file_within(&trusted, &root.to_string_lossy(), "../outside").is_err());
        assert!(read_workspace_file_within(&trusted, &root.to_string_lossy(), "asset.bin").is_err());

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[cfg(unix)]
    #[test]
    fn workspace_preview_rejects_symlink_escape() {
        use std::os::unix::fs::symlink;

        let root = temp_workspace("symlink-root");
        let outside = temp_workspace("symlink-outside");
        write(outside.join("secret.txt"), "secret").expect("write outside file");
        symlink(outside.join("secret.txt"), root.join("escape.txt")).expect("create symlink");

        let result =
            read_workspace_file_within(&[root.clone()], &root.to_string_lossy(), "escape.txt");
        assert!(result.is_err());

        remove_dir_all(root).expect("remove root");
        remove_dir_all(outside).expect("remove outside");
    }

    #[test]
    fn workspace_listing_uses_git_scope_and_ignores_ignored_files() {
        let root = temp_workspace("listing");
        init_git(&root);
        create_dir_all(root.join("src")).expect("create src");
        write(root.join("src/main.rs"), "fn main() {}\n").expect("write source");
        write(root.join("ignored.log"), "noise\n").expect("write ignored");
        write(root.join(".gitignore"), "*.log\n").expect("write gitignore");

        let listed = list_workspace_files_within(&[root.clone()], &root.to_string_lossy())
            .expect("list files");
        assert!(listed.files.contains(&"src/main.rs".to_string()));
        assert!(listed.files.contains(&".gitignore".to_string()));
        assert!(!listed.files.contains(&"ignored.log".to_string()));
        assert!(!listed.truncated);
        assert_eq!(
            PathBuf::from(listed.root),
            root.canonicalize().expect("canonical root")
        );

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[test]
    fn editor_jump_uses_fixed_programs_and_argument_boundaries() {
        let candidates = editor_candidates(Path::new("/tmp/repo/a file.rs"), 42);
        assert_eq!(candidates[0].0, "code");
        assert_eq!(
            candidates[0].1,
            [
                OsString::from("--goto"),
                OsString::from("/tmp/repo/a file.rs:42")
            ]
        );
        assert!(candidates[0].2);
        assert_eq!(candidates[1].0, "cursor");
        assert!(candidates
            .iter()
            .all(|(program, _, _)| !program.contains(' ')));
    }

    #[test]
    fn workspace_commands_reject_roots_outside_the_fence() {
        let trusted_root = temp_workspace("fence-trusted");
        init_git(&trusted_root);
        write(trusted_root.join("main.rs"), "fn main() {}\n").expect("write source");
        let untrusted = temp_workspace("fence-untrusted");
        init_git(&untrusted);
        write(untrusted.join("secret.txt"), "secret").expect("write secret");
        let trusted = vec![trusted_root.clone()];
        let untrusted_str = untrusted.to_string_lossy().into_owned();

        assert!(list_workspace_files_within(&trusted, &untrusted_str)
            .err()
            .expect("untrusted root must be rejected")
            .contains("拒绝"));
        assert!(
            read_workspace_file_within(&trusted, &untrusted_str, "secret.txt")
                .err()
                .expect("untrusted read must be rejected")
                .contains("拒绝")
        );
        assert!(
            open_workspace_file_within(&trusted, &untrusted_str, "secret.txt", Some(1))
                .err()
                .expect("untrusted open must be rejected")
                .contains("拒绝")
        );

        let listed = list_workspace_files_within(&trusted, &trusted_root.to_string_lossy())
            .expect("trusted root stays allowed");
        assert!(listed.files.contains(&"main.rs".to_string()));

        remove_dir_all(trusted_root).expect("remove trusted");
        remove_dir_all(untrusted).expect("remove untrusted");
    }

    #[test]
    fn workspace_fence_allows_subdirectories_of_trusted_roots() {
        let root = temp_workspace("fence-subdir");
        init_git(&root);
        create_dir_all(root.join("nested")).expect("create nested");
        write(root.join("nested/inner.rs"), "fn inner() {}\n").expect("write nested source");

        let listed = list_workspace_files_within(
            &[root.clone()],
            &root.join("nested").to_string_lossy(),
        )
        .expect("subdirectory of trusted root allowed");
        assert!(listed.files.contains(&"inner.rs".to_string()));

        remove_dir_all(root).expect("remove temp workspace");
    }

    #[cfg(unix)]
    #[test]
    fn workspace_fence_resolves_symlinks_before_judging() {
        use std::os::unix::fs::symlink;

        let trusted_root = temp_workspace("fence-symlink-trusted");
        let outside = temp_workspace("fence-symlink-outside");
        init_git(&outside);
        write(outside.join("secret.txt"), "secret").expect("write outside file");
        // 受信根内的 symlink 指向围栏外目录：canonicalize 解析后落在围栏外，必须被拒。
        symlink(&outside, trusted_root.join("escape")).expect("create symlink");

        let result = list_workspace_files_within(
            &[trusted_root.clone()],
            &trusted_root.join("escape").to_string_lossy(),
        );
        assert!(result
            .err()
            .expect("symlink escape must be rejected").contains("拒绝"));

        remove_dir_all(trusted_root).expect("remove trusted");
        remove_dir_all(outside).expect("remove outside");
    }

    #[test]
    fn workspace_fence_roots_cover_registry_supervisor_and_recovery() {
        let registry = DesktopProjectRegistry {
            projects: vec![DesktopProjectProfile {
                id: "reg".into(),
                name: "reg".into(),
                repo_root: "/tmp/fence-registry".into(),
                base_url: "http://127.0.0.1:8080".into(),
                last_opened_at: 1,
                kind: PROJECT_KIND_LOCAL.into(),
            }],
        };
        let recoveries = GatewayRecoveryRegistry {
            version: 1,
            runtimes: vec![GatewayRecoveryRecord {
                runtime_id: "scratch-a".into(),
                project_id: None,
                workspace_id: Some("a".into()),
                scope: SCRATCH_SCOPE.into(),
                workspace_root: "/tmp/fence-recovery-ws".into(),
                repo_root: "/tmp/fence-recovery-repo".into(),
                base_url: "http://127.0.0.1:8123".into(),
                pid: None,
                started_at: 1,
                updated_at: 2,
                status: "crashed".into(),
                message: "crashed".into(),
            }],
        };
        let mut supervisor = GatewaySupervisorInner::default();
        supervisor.runtimes.insert(
            "project-live".into(),
            GatewayProcessInner {
                workspace_root: Some(PathBuf::from("/tmp/fence-live-ws")),
                repo_root: Some(PathBuf::from("/tmp/fence-live-repo")),
                ..GatewayProcessInner::default()
            },
        );

        let roots = workspace_fence_roots(
            &registry,
            &recoveries,
            Some(PathBuf::from("/tmp/fence-managed/workspaces")),
            &supervisor,
        );

        for expected in [
            "/tmp/fence-registry",
            "/tmp/fence-managed/workspaces",
            "/tmp/fence-recovery-ws",
            "/tmp/fence-recovery-repo",
            "/tmp/fence-live-ws",
            "/tmp/fence-live-repo",
        ] {
            assert!(
                roots.contains(&PathBuf::from(expected)),
                "fence roots missing {expected}"
            );
        }
    }

    #[test]
    fn workspace_fence_roots_exclude_remote_entries() {
        // remote 条目的 repo_root 是服务器侧路径（webview 传入、未校验）——绝不能进本地读围栏，
        // 否则 repo_root="/" 之类会把本地读面撑到整个文件系统。回归钉死 F-6 查实的 HIGH 洞。
        let registry = DesktopProjectRegistry {
            projects: vec![
                DesktopProjectProfile {
                    id: "local".into(),
                    name: "local".into(),
                    repo_root: "/tmp/fence-local".into(),
                    base_url: "http://127.0.0.1:8080".into(),
                    last_opened_at: 2,
                    kind: PROJECT_KIND_LOCAL.into(),
                },
                DesktopProjectProfile {
                    id: "remote".into(),
                    name: "remote".into(),
                    repo_root: "/".into(), // 恶意/巧合的服务器侧路径
                    base_url: "https://srv.ts.net".into(),
                    last_opened_at: 1,
                    kind: PROJECT_KIND_REMOTE.into(),
                },
            ],
        };
        let roots = workspace_fence_roots(
            &registry,
            &GatewayRecoveryRegistry::default(),
            None,
            &GatewaySupervisorInner::default(),
        );
        assert!(roots.contains(&PathBuf::from("/tmp/fence-local")));
        assert!(
            !roots.contains(&PathBuf::from("/")),
            "remote 条目的 repo_root 不得进本地读围栏"
        );
        // 端到端：即便围栏只含 local 根，请求 remote 的 "/" 也不获授权。
        assert!(fence_workspace_root(&roots, "/").is_err());
    }

    fn local_http_server(
        response: Option<Vec<u8>>,
        hits: std::sync::Arc<AtomicUsize>,
    ) -> u16 {
        use std::io::Read as _;

        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test server");
        let port = listener.local_addr().expect("server addr").port();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                hits.fetch_add(1, Ordering::SeqCst);
                let mut buffer = [0_u8; 2048];
                let _ = stream.read(&mut buffer);
                match &response {
                    // 挂起模式：不回任何字节，逼客户端走自己的超时预算。
                    None => std::thread::sleep(Duration::from_secs(5)),
                    Some(payload) => {
                        let _ = stream.write_all(payload);
                    }
                }
            }
        });
        port
    }

    fn probe_profile(port: u16) -> DesktopLlmProfile {
        DesktopLlmProfile {
            base_url: format!("http://127.0.0.1:{port}"),
            api_key: String::new(),
            model: "mimo-v2.5".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        }
    }

    #[test]
    fn llm_probe_refuses_redirects_and_stays_on_declared_base_url() {
        let leaked_hits = std::sync::Arc::new(AtomicUsize::new(0));
        let leak_port = local_http_server(
            Some(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}".to_vec()),
            leaked_hits.clone(),
        );
        let redirect_hits = std::sync::Arc::new(AtomicUsize::new(0));
        let redirect_port = local_http_server(
            Some(
                format!(
                    "HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{leak_port}/models\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                .into_bytes(),
            ),
            redirect_hits.clone(),
        );

        let window = tauri::async_runtime::block_on(discover_model_context_window_with(
            &probe_profile(redirect_port),
            Duration::from_secs(2),
        ));

        assert_eq!(window, None);
        assert_eq!(redirect_hits.load(Ordering::SeqCst), 1);
        assert_eq!(
            leaked_hits.load(Ordering::SeqCst),
            0,
            "probe must not follow redirects off the declared base_url"
        );
    }

    #[test]
    fn llm_probe_gives_up_within_its_timeout_budget() {
        let hits = std::sync::Arc::new(AtomicUsize::new(0));
        let port = local_http_server(None, hits.clone());

        let started = std::time::Instant::now();
        let window = tauri::async_runtime::block_on(discover_model_context_window_with(
            &probe_profile(port),
            Duration::from_millis(300),
        ));

        assert_eq!(window, None);
        assert!(
            started.elapsed() < Duration::from_secs(3),
            "probe must give up within its timeout budget"
        );
        assert_eq!(hits.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn llm_profile_change_without_consent_neither_probes_nor_persists() {
        let probe_hits = std::sync::Arc::new(AtomicUsize::new(0));
        let port = local_http_server(
            Some(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}".to_vec()),
            probe_hits.clone(),
        );
        let persisted = AtomicUsize::new(0);

        let result = tauri::async_runtime::block_on(save_llm_profile_flow(
            probe_profile(port),
            false,
            Duration::from_secs(1),
            |_| {
                persisted.fetch_add(1, Ordering::SeqCst);
                Ok(())
            },
        ));

        assert!(result
            .err()
            .expect("unconfirmed change must fail")
            .contains("未获用户确认"));
        assert_eq!(persisted.load(Ordering::SeqCst), 0, "must not persist");
        assert_eq!(
            probe_hits.load(Ordering::SeqCst),
            0,
            "must not even probe before consent"
        );
    }

    #[test]
    fn llm_profile_change_with_consent_probes_then_persists() {
        let body =
            serde_json::json!({"data": [{"id": "mimo-v2.5", "context_window": 500000}]}).to_string();
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let probe_hits = std::sync::Arc::new(AtomicUsize::new(0));
        let port = local_http_server(Some(response.into_bytes()), probe_hits.clone());
        let persisted = AtomicUsize::new(0);

        let saved = tauri::async_runtime::block_on(save_llm_profile_flow(
            probe_profile(port),
            true,
            Duration::from_secs(2),
            |profile| {
                persisted.fetch_add(1, Ordering::SeqCst);
                assert_eq!(profile.context_window, Some(500_000));
                Ok(())
            },
        ))
        .expect("confirmed change saves");

        assert_eq!(saved.context_window, Some(500_000));
        assert_eq!(saved.context_window_source.as_deref(), Some("service"));
        assert_eq!(persisted.load(Ordering::SeqCst), 1);
        assert_eq!(probe_hits.load(Ordering::SeqCst), 1);
    }
    #[test]
    fn confirm_action_message_keeps_short_text_and_caps_long_text() {
        assert_eq!(confirm_action_message("删除目标草稿“x”？"), "删除目标草稿“x”？");
        let exact = "确".repeat(MAX_CONFIRM_MESSAGE_CHARS);
        assert_eq!(confirm_action_message(&exact), exact);
        let long = "确".repeat(MAX_CONFIRM_MESSAGE_CHARS + 5);
        let capped = confirm_action_message(&long);
        assert_eq!(capped.chars().count(), MAX_CONFIRM_MESSAGE_CHARS + 1);
        assert!(capped.ends_with('…'));
    }

    fn sample_llm_profile() -> DesktopLlmProfile {
        DesktopLlmProfile {
            base_url: "https://models.example.com/v1".into(),
            api_key: "sk-file-test".into(),
            model: "model-1".into(),
            context_window: Some(65_536),
            context_window_source: Some("configured".into()),
            fast_model: None,
            strong_model: None,
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        }
    }

    #[test]
    fn llm_profile_file_missing_means_not_configured() {
        let root = temp_workspace("llm-missing");
        let path = root.join(LLM_PROFILE_FILE);
        assert!(read_llm_profile_file(&path).expect("missing file is not an error").is_none());
        delete_llm_profile_file(&path).expect("deleting a missing file is a no-op");
        let _ = remove_dir_all(root);
    }

    #[test]
    fn llm_profile_file_round_trips_and_is_owner_only() {
        let root = temp_workspace("llm-roundtrip");
        let path = root.join(LLM_PROFILE_FILE);
        // 先放一个权限更宽的旧文件：保存后必须收紧到 600。
        write(&path, "{}").expect("seed old file");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).expect("chmod");
        }
        save_llm_profile_file(&path, &sample_llm_profile()).expect("save profile");
        let loaded = read_llm_profile_file(&path).expect("read").expect("profile present");
        assert!(loaded == sample_llm_profile());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&path).expect("metadata").permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "profile file holds the API key and must be owner-only");
        }
        delete_llm_profile_file(&path).expect("delete");
        assert!(read_llm_profile_file(&path).expect("read after delete").is_none());
        let _ = remove_dir_all(root);
    }

    #[test]
    fn llm_profile_file_with_bad_json_reports_a_readable_error() {
        let root = temp_workspace("llm-corrupt");
        let path = root.join(LLM_PROFILE_FILE);
        write(&path, "{not json").expect("seed corrupt file");
        let error = read_llm_profile_file(&path).err().expect("corrupt file must fail");
        assert!(error.contains("格式有误") && error.contains(LLM_PROFILE_FILE), "{error}");
        let _ = remove_dir_all(root);
    }

    #[test]
    fn models_payload_summary_counts_and_matches_case_insensitively() {
        let payload = serde_json::json!({"data": [{"id": "mimo-v2.5"}, {"id": "gpt-4o"}]});
        assert_eq!(summarize_models_payload(&payload, "MiMo-V2.5"), (2, Some(true)));
        assert_eq!(summarize_models_payload(&payload, "missing"), (2, Some(false)));
        assert_eq!(summarize_models_payload(&payload, ""), (2, None));
        assert_eq!(summarize_models_payload(&serde_json::json!({}), "x"), (0, None));
        assert_eq!(model_ids(&payload), vec!["mimo-v2.5".to_string(), "gpt-4o".to_string()]);
    }

    #[test]
    fn civil_dates_match_known_days() {
        assert_eq!(iso_date(0), "1970-01-01");
        assert_eq!(iso_date(19_723), "2024-01-01");
        assert_eq!(iso_date(20_517), "2026-03-05");
        assert_eq!(iso_date(-1), "1969-12-31");
    }

    #[test]
    fn billing_treats_huge_quota_as_unlimited_and_cents_as_dollars() {
        let unlimited = usage_from_billing(
            &serde_json::json!({"hard_limit_usd": 100_000_000.0}),
            Some(&serde_json::json!({"total_usage": 1234.0})),
        );
        assert!(unlimited.available && unlimited.unlimited && unlimited.hard_limit_usd.is_none());
        assert_eq!(unlimited.used_usd, Some(12.34));
        let limited = usage_from_billing(&serde_json::json!({"hard_limit_usd": 50.0}), None);
        assert!(limited.available && !limited.unlimited);
        assert_eq!(limited.hard_limit_usd, Some(50.0));
        assert_eq!(limited.used_usd, None);
        assert!(!usage_from_billing(&serde_json::json!({}), None).available);
    }
}
