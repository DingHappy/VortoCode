use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::ffi::OsString;
use std::fs::{create_dir_all, read, read_to_string, remove_file, rename, OpenOptions};
use std::io::{ErrorKind, Write};
use std::net::{Ipv4Addr, TcpListener};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{webview::PageLoadEvent, AppHandle, Manager, State};
use tauri_plugin_shell::ShellExt;
use url::Url;

const MAX_WORKSPACE_FILES: usize = 6_000;
const MAX_PREVIEW_BYTES: u64 = 1024 * 1024;
const MAX_DESKTOP_PROJECTS: usize = 30;
const MAX_DESKTOP_RUNTIMES: usize = 12;
const GENERAL_SCOPE: &str = "general";
const SCRATCH_SCOPE: &str = "scratch";
const PROJECT_SCOPE: &str = "project";
const DEFAULT_LLM_BASE_URL: &str = "https://token.vortotech.com/v1";
const DEFAULT_LLM_MODEL: &str = "mimo-v2.5";

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
        context_window: Some(1_000_000),
        context_window_source: Some("catalog".into()),
    }
}

fn normalize_llm_profile(mut profile: DesktopLlmProfile) -> Result<DesktopLlmProfile, String> {
    profile.base_url = profile.base_url.trim().trim_end_matches('/').to_string();
    profile.api_key = profile.api_key.trim().to_string();
    profile.model = profile.model.trim().to_string();
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
    if !local && profile.api_key.is_empty() {
        return Err("远程模型服务需要 API Key".into());
    }
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

#[cfg(target_os = "macos")]
mod llm_keychain {
    use std::ffi::c_void;
    use std::ptr::{null, null_mut};

    const SERVICE: &[u8] = b"com.vortocode.desktop.llm";
    const ACCOUNT: &[u8] = b"default";
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

    unsafe fn find_item() -> Result<Option<*mut c_void>, String> {
        let mut item = null_mut();
        let status = SecKeychainFindGenericPassword(
            null(),
            SERVICE.len() as u32,
            SERVICE.as_ptr().cast(),
            ACCOUNT.len() as u32,
            ACCOUNT.as_ptr().cast(),
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

    pub fn read() -> Result<Option<String>, String> {
        unsafe {
            let mut password_length = 0_u32;
            let mut password_data = null_mut();
            let mut item = null_mut();
            let status = SecKeychainFindGenericPassword(
                null(),
                SERVICE.len() as u32,
                SERVICE.as_ptr().cast(),
                ACCOUNT.len() as u32,
                ACCOUNT.as_ptr().cast(),
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
                .map_err(|_| "macOS Keychain 中的模型配置不是有效 UTF-8".to_string())
        }
    }

    pub fn write(payload: &str) -> Result<(), String> {
        unsafe {
            if let Some(item) = find_item()? {
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
                SERVICE.len() as u32,
                SERVICE.as_ptr().cast(),
                ACCOUNT.len() as u32,
                ACCOUNT.as_ptr().cast(),
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

    pub fn delete() -> Result<(), String> {
        unsafe {
            let Some(item) = find_item()? else {
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
mod llm_keychain {
    pub fn read() -> Result<Option<String>, String> {
        Ok(None)
    }
    pub fn write(_payload: &str) -> Result<(), String> {
        Err("当前预览仅在 macOS 提供系统 Keychain".into())
    }
    pub fn delete() -> Result<(), String> {
        Ok(())
    }
}

fn read_desktop_llm_profile() -> Result<Option<DesktopLlmProfile>, String> {
    let Some(payload) = llm_keychain::read()? else {
        return Ok(None);
    };
    let profile: DesktopLlmProfile = serde_json::from_str(&payload)
        .map_err(|_| "macOS Keychain 中的模型配置已损坏，请在 Desktop 中重新保存".to_string())?;
    normalize_llm_profile(profile).map(Some)
}

fn cached_desktop_llm_profile(
    store: &DesktopLlmProfileStore,
) -> Result<Option<DesktopLlmProfile>, String> {
    store.get_or_try_init(read_desktop_llm_profile)
}

fn desktop_llm_profile_status(profile: Option<&DesktopLlmProfile>) -> DesktopLlmProfileStatus {
    let fallback = default_llm_profile();
    let profile = profile.unwrap_or(&fallback);
    DesktopLlmProfileStatus {
        configured: profile.api_key.len() > 0 || llm_provider(&profile.base_url) == "local",
        base_url: profile.base_url.clone(),
        model: profile.model.clone(),
        provider: llm_provider(&profile.base_url).into(),
        requires_key: llm_provider(&profile.base_url) != "local",
        context_window: profile.context_window,
        context_window_source: profile
            .context_window_source
            .clone()
            .unwrap_or_else(|| "unknown".into()),
    }
}

#[tauri::command]
fn get_llm_profile(
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    let profile = cached_desktop_llm_profile(&store)?;
    Ok(desktop_llm_profile_status(profile.as_ref()))
}

async fn confirm_llm_profile_change(
    app: &AppHandle,
    profile: &DesktopLlmProfile,
) -> Result<bool, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    // 改档是敏感动作：base_url+Key 会落 Keychain 并注入后续 runtime 环境。原生对话框在
    // webview 进程之外，被注入的页面脚本无法替用户点「确认」，静默改档因此不生效。
    let message = format!(
        "网页层请求把模型服务改为：\n\n服务地址：{}\n模型：{}\n\n仅当这是你刚在设置页保存的配置时才确认。",
        profile.base_url, profile.model
    );
    let app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .message(message)
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
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    let profile = normalize_llm_profile(DesktopLlmProfile {
        base_url,
        api_key,
        model,
        context_window: None,
        context_window_source: None,
    })?;
    let confirmed = confirm_llm_profile_change(&app, &profile).await?;
    let profile = save_llm_profile_flow(profile, confirmed, LLM_PROBE_TIMEOUT, |profile| {
        let payload =
            serde_json::to_string(profile).map_err(|_| "无法序列化模型配置".to_string())?;
        llm_keychain::write(&payload)
    })
    .await?;
    store.replace(Some(profile.clone()))?;
    Ok(desktop_llm_profile_status(Some(&profile)))
}

#[tauri::command]
fn clear_llm_profile(
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    llm_keychain::delete()?;
    store.replace(None)?;
    Ok(desktop_llm_profile_status(None))
}

fn configure_llm_profile(command: &mut Command, profile: Option<&DesktopLlmProfile>) {
    let Some(profile) = profile else {
        return;
    };
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

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopProjectProfile {
    id: String,
    name: String,
    repo_root: String,
    base_url: String,
    last_opened_at: u64,
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
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
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

#[tauri::command]
fn list_desktop_projects(app: AppHandle) -> Result<Vec<DesktopProjectProfile>, String> {
    Ok(load_project_registry(&project_registry_path(&app)?)?.projects)
}

#[tauri::command]
fn remember_desktop_project(
    app: AppHandle,
    repo_root: String,
    base_url: String,
) -> Result<DesktopProjectProfile, String> {
    remember_project_at(&project_registry_path(&app)?, &repo_root, &base_url)
}

#[tauri::command]
fn forget_desktop_project(
    app: AppHandle,
    project_id: String,
) -> Result<Vec<DesktopProjectProfile>, String> {
    forget_project_at(&project_registry_path(&app)?, &project_id)
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

#[tauri::command]
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
    let llm_profile = cached_desktop_llm_profile(&llm_profile_store)?;

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
        .manage(GatewayProcess::default())
        .manage(DesktopLlmProfileStore::default())
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
            list_desktop_projects,
            remember_desktop_project,
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
        })
        .expect("local profile");
        assert_eq!(llm_provider(&local.base_url), "local");

        assert!(normalize_llm_profile(DesktopLlmProfile {
            base_url: "http://models.example.com/v1".into(),
            api_key: "test-key".into(),
            model: "model".into(),
            context_window: None,
            context_window_source: None,
        })
        .is_err());
        assert!(normalize_llm_profile(DesktopLlmProfile {
            base_url: "https://models.example.com/v1".into(),
            api_key: String::new(),
            model: "model".into(),
            context_window: None,
            context_window_source: None,
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
    fn llm_profile_store_loads_keychain_at_most_once_per_process() {
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
                },
                DesktopProjectProfile {
                    id: "two".into(),
                    name: "two".into(),
                    repo_root: "/tmp/two".into(),
                    base_url: "http://127.0.0.1:8081".into(),
                    last_opened_at: 1,
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
}
