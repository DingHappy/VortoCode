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
use url::{Host, Url};

mod account;
mod account_runtime;
mod profile_store;
mod configuration_state;
use configuration_state::*;
use profile_store::*;
mod gateway_process;
mod remote;
mod resident_desktop;
mod workspace_files;
mod llm_profile;
mod llm_services;
mod updater;
mod diagnostics;
use llm_profile::*;
use llm_services::*;
use updater::*;
use diagnostics::*;
use gateway_process::*;
use remote::*;
use resident_desktop::*;
use workspace_files::*;

const MAX_WORKSPACE_FILES: usize = 6_000;
const MAX_PREVIEW_BYTES: u64 = 1024 * 1024;
const MAX_DESKTOP_PROJECTS: usize = 30;
const MAX_DESKTOP_RUNTIMES: usize = 12;
const GENERAL_SCOPE: &str = "general";
const SCRATCH_SCOPE: &str = "scratch";
const PROJECT_SCOPE: &str = "project";
/// 前端默认的本机 runtime 端口（被占用时 select_gateway_port 自动换一个空闲端口）。
const DEFAULT_GATEWAY_PORT: u16 = 8080;

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

/// Keychain 条目的 service 名。调试构建（`tauri dev`、`cargo test`）一律加 `.dev`，与正式版的条目分开：
/// 未签名、每次编译签名都变的二进制去读正式版的条目，macOS 每次都要钥匙串密码；要是在弹窗里点了
/// 「始终允许」，等于把正式凭据交给一个随时会变的程序。分开后 dev 版读不到正式凭据（账号显示未登录、
/// 远端缺令牌），要在 dev 版里测就在 dev 版里重新登录或登记，存进它自己的条目。正式版的名字不变。
pub(crate) fn keychain_service(base: &str) -> String {
    keychain_service_for(base, cfg!(debug_assertions))
}

fn keychain_service_for(base: &str, debug_build: bool) -> String {
    if debug_build {
        format!("{base}.dev")
    } else {
        base.to_string()
    }
}

// 远程连接 token：与 LLM key 分开的 Keychain 服务，按项目 id 作 account 以支持多远端。
// read 只在 Rust 侧的远程代理里用——token 原文不进 webview（spec §连接层安全 2）。
mod remote_token_keychain {
    use super::{keychain_service, platform_keychain};
    const SERVICE: &str = "com.vortocode.desktop.remote";
    pub fn read(account: &str) -> Result<Option<String>, String> {
        platform_keychain::read(keychain_service(SERVICE).as_bytes(), account.as_bytes())
    }
    pub fn write(account: &str, token: &str) -> Result<(), String> {
        platform_keychain::write(keychain_service(SERVICE).as_bytes(), account.as_bytes(), token)
    }
    pub fn delete(account: &str) -> Result<(), String> {
        platform_keychain::delete(keychain_service(SERVICE).as_bytes(), account.as_bytes())
    }
}

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
    /// 实验功能：把实现 / 审查分发给本机 Codex / Claude Code CLI。默认关闭。
    #[serde(default)]
    experimental_external_workers: bool,
    /// 实验功能：Linux X11 桌面操作。默认关闭；目标与可打开的应用随偏好保存。
    #[serde(default)]
    desktop_control: bool,
    #[serde(default)]
    desktop_target: String,
    #[serde(default)]
    desktop_apps: String,
    /// 关掉启动时检查更新（默认开：每天最多问一次 GitHub 最新版本）。
    #[serde(default)]
    update_check_disabled: bool,
    /// 关掉「让助理查看 Codex」（默认开：登记了常驻电脑时，本机引擎把 Codex 进度报给常驻助理）。
    #[serde(default)]
    codex_watch_disabled: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopControlStatus {
    enabled: bool,
    target: String,
    apps: String,
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

/// 实验开关只在引擎启动处按偏好打开；configure_llm_profile 那边一律先清掉，不从父进程继承。
fn configure_experimental_workers(command: &mut Command, preferences: &DesktopPreferences) {
    if preferences.experimental_external_workers {
        command.env("VORTOCODE_EXPERIMENTAL_EXTERNAL_WORKERS", "1");
    } else {
        command.env_remove("VORTOCODE_EXPERIMENTAL_EXTERNAL_WORKERS");
    }
}

/// 与引擎 `src/agents/tools/desktop.py` 同一口径：`local`（仅 Linux）或 `ssh:用户@主机`。
/// 主机名首字符必须是字母或数字，否则 `-oProxyCommand=…` 一类会被 ssh 当成选项。
fn normalize_desktop_target(raw: &str) -> Result<String, String> {
    let target = raw.trim();
    if target.is_empty() || target == "local" {
        return if cfg!(target_os = "linux") { Ok("local".into()) }
            else { Err("这台电脑不是 Linux 桌面：目标请填 ssh:用户@主机".into()) };
    }
    let host = target.strip_prefix("ssh:").ok_or("桌面目标只接受 local 或 ssh:用户@主机")?;
    let (user, machine) = match host.split_once('@') { Some((user, machine)) => (Some(user), machine), None => (None, host) };
    let word = |part: &str, extra: &str| part.chars().next().is_some_and(|c| c.is_ascii_alphanumeric())
        && part.chars().all(|c| c.is_ascii_alphanumeric() || extra.contains(c));
    if user.is_some_and(|user| !word(user, "._-")) || !word(machine, ".-") {
        return Err("桌面目标只接受 local 或 ssh:用户@主机".into());
    }
    Ok(target.into())
}

/// 允许 Agent 打开的应用：逗号分隔的命令名，和引擎侧白名单同一规则。
fn normalize_desktop_apps(raw: &str) -> Result<String, String> {
    let mut apps = Vec::new();
    for name in raw.split([',', '，']).map(str::trim).filter(|name| !name.is_empty()) {
        let valid = name.chars().next().is_some_and(|c| c.is_ascii_alphanumeric())
            && name.chars().all(|c| c.is_ascii_alphanumeric() || "._-".contains(c));
        if !valid {
            return Err(format!("应用名「{name}」无效：只填命令名，如 firefox、mousepad"));
        }
        if !apps.contains(&name) { apps.push(name); }
    }
    if apps.len() > 20 {
        return Err("可打开的应用最多 20 个".into());
    }
    Ok(apps.join(","))
}

/// 常驻电脑在注册表里的「目录」标记（与网页层 ASSISTANT_HOST_ROOT 一致）。
const ASSISTANT_HOST_ROOT: &str = "@assistant";

/// 登记了常驻电脑、且没关掉「让助理查看 Codex」时，本机通用引擎带上常驻助理的地址和令牌：
/// 引擎里的 Codex 观察员据此把 Codex 进度报过去。只给通用范围那一个引擎，免得几个引擎重复报。
fn configure_codex_watch(command: &mut Command, app: &AppHandle, scope: &str, preferences: &DesktopPreferences) {
    command.env_remove("VORTOCODE_CODEX_REPORT_URL");
    command.env_remove("VORTOCODE_CODEX_REPORT_TOKEN");
    if scope != GENERAL_SCOPE || preferences.codex_watch_disabled {
        return;
    }
    let Some(host) = project_registry_path(app)
        .and_then(|path| load_project_registry(&path))
        .ok()
        .and_then(|registry| {
            registry
                .projects
                .into_iter()
                .find(|project| project.kind == "remote" && project.repo_root == ASSISTANT_HOST_ROOT)
        })
    else {
        return;
    };
    let Some(token) = remote_token_keychain::read(&host.id).ok().flatten().filter(|token| !token.trim().is_empty())
    else {
        return;
    };
    command.env("VORTOCODE_CODEX_REPORT_URL", host.base_url.trim_end_matches('/'));
    command.env("VORTOCODE_CODEX_REPORT_TOKEN", token.trim());
}

#[tauri::command(async)]
fn get_codex_watch_enabled(app: AppHandle) -> Result<bool, String> {
    Ok(!read_desktop_preferences(&desktop_preferences_path(&app)?).codex_watch_disabled)
}

#[tauri::command(async)]
fn set_codex_watch_enabled(app: AppHandle, enabled: bool) -> Result<bool, String> {
    let path = desktop_preferences_path(&app)?;
    let mut preferences = read_desktop_preferences(&path);
    preferences.codex_watch_disabled = !enabled;
    save_json_atomic_with_mode(&path, "desktop-preferences", &preferences, Some(0o600))?;
    Ok(enabled)
}

/// 桌面操作只在引擎启动处按偏好打开；偏好里的目标不合法（比如手改坏了）就按关闭处理。
fn configure_desktop_control(command: &mut Command, preferences: &DesktopPreferences) {
    for key in ["VORTOCODE_ENABLE_DESKTOP_CONTROL", "VORTOCODE_DESKTOP_TARGET", "VORTOCODE_DESKTOP_DISPLAY", "VORTOCODE_DESKTOP_APPS"] {
        command.env_remove(key);
    }
    if !preferences.desktop_control {
        return;
    }
    let (Ok(target), Ok(apps)) = (normalize_desktop_target(&preferences.desktop_target),
        normalize_desktop_apps(&preferences.desktop_apps)) else { return };
    command.env("VORTOCODE_ENABLE_DESKTOP_CONTROL", "1");
    command.env("VORTOCODE_DESKTOP_TARGET", target);
    command.env("VORTOCODE_DESKTOP_APPS", apps);
}

fn desktop_control_status(preferences: &DesktopPreferences) -> DesktopControlStatus {
    DesktopControlStatus { enabled: preferences.desktop_control, target: preferences.desktop_target.clone(),
        apps: preferences.desktop_apps.clone() }
}

#[tauri::command(async)]
fn get_desktop_control(app: AppHandle) -> Result<DesktopControlStatus, String> {
    Ok(desktop_control_status(&read_desktop_preferences(&desktop_preferences_path(&app)?)))
}

#[tauri::command]
async fn set_desktop_control(app: AppHandle, enabled: bool, target: String, apps: String) -> Result<DesktopControlStatus, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    let apps = normalize_desktop_apps(&apps)?;
    let target = if enabled { normalize_desktop_target(&target)? } else { target.trim().to_string() };
    let path = desktop_preferences_path(&app)?;
    let mut preferences = read_desktop_preferences(&path);
    // 打开、或者在开着时换了目标机器 / 应用名单，都是放权，走 webview 之外的原生确认；关闭不必问。
    let widened = enabled && (!preferences.desktop_control || preferences.desktop_target != target
        || preferences.desktop_apps != apps);
    if widened {
        let label = if target == "local" { "这台电脑的桌面".to_string() } else { format!("{} 的桌面", &target[4..]) };
        let message = format!("实验功能：启用后，Agent 可以截取{label}的屏幕、列出窗口；点击、输入、按键和打开应用每一次都会先问你，即使授权级别是「完全信任」。\n\n只在 Desktop 会话中可用；IM 和自动化任务不会获得这项能力。不会输入疑似密码或密钥的内容。");
        let dialog_app = app.clone();
        let confirmed = tauri::async_runtime::spawn_blocking(move || {
            with_main_window_parent(&dialog_app, dialog_app.dialog().message(message))
            .title("启用实验功能：桌面操作")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom("启用".into(), "取消".into()))
            .blocking_show()
        })
        .await
        .map_err(|error| format!("桌面操作确认对话框失败：{error}"))?;
        if !confirmed {
            return Err("未确认，桌面操作设置没有改变".into());
        }
    }
    preferences.desktop_control = enabled;
    preferences.desktop_target = target;
    preferences.desktop_apps = apps;
    save_json_atomic_with_mode(&path, "desktop-preferences", &preferences, Some(0o600))?;
    Ok(desktop_control_status(&preferences))
}

#[tauri::command(async)]
fn get_experimental_workers(app: AppHandle) -> Result<bool, String> {
    Ok(read_desktop_preferences(&desktop_preferences_path(&app)?).experimental_external_workers)
}

#[tauri::command]
async fn set_experimental_workers(app: AppHandle, enabled: bool) -> Result<bool, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    if enabled {
        // 打开是放权：原生确认在 webview 之外，页面脚本无法替用户点「启用」。关闭是收权，不必问。
        let dialog_app = app.clone();
        let confirmed = tauri::async_runtime::spawn_blocking(move || {
            with_main_window_parent(&dialog_app, dialog_app.dialog().message(
                "实验功能：开启后，隔离开发里的「实现 / 审查」可以按你在模型分工里选的 cli:codex / cli:claude，交给这台电脑上你本人登录的 CLI 执行（消耗你的订阅额度）。\n\n拆解、隔离、验证、确认和开 PR 仍由 VortoCode 负责；CLI 拿不到 GitHub 令牌。默认关闭，随时可以关掉。",
            ))
            .title("开启实验功能：外部执行者")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom("开启".into(), "取消".into()))
            .blocking_show()
        })
        .await
        .map_err(|error| format!("实验功能确认对话框失败：{error}"))?;
        if !confirmed {
            return Err("未确认，实验功能保持关闭".into());
        }
    }
    let path = desktop_preferences_path(&app)?;
    let mut preferences = read_desktop_preferences(&path);
    preferences.experimental_external_workers = enabled;
    save_json_atomic_with_mode(&path, "desktop-preferences", &preferences, Some(0o600))?;
    Ok(enabled)
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
    configuration_revision: Option<String>,
    configuration_launch_id: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct GatewayProcessStatus {
    launch_id: Option<String>,
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
    let sidecar = bundled_runtime_path(&app).map_err(|error| format!("GUI smoke {error}"))?;
    let mut sidecar_command = Command::new(sidecar);
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

#[derive(Default)]
struct MainWindowPresentation(std::sync::atomic::AtomicBool);

impl MainWindowPresentation {
    fn user_hidden(&self) -> bool {
        self.0.load(std::sync::atomic::Ordering::SeqCst)
    }

    fn set_user_hidden(&self, hidden: bool) {
        self.0.store(hidden, std::sync::atomic::Ordering::SeqCst);
    }
}

fn present_main_window(app: &AppHandle, explicit: bool) {
    let handle = app.clone();
    // Recheck the close intent on the UI thread: a late page load or startup
    // fallback must not reopen a window that the user has just hidden.
    if let Err(error) = app.run_on_main_thread(move || {
        let state = handle.state::<MainWindowPresentation>();
        if !explicit && state.user_hidden() {
            return;
        }
        let Some(window) = handle.get_webview_window("main") else {
            diag("warn", "window.missing", "主窗口不存在，无法切换到前台");
            return;
        };
        if !explicit && window.is_minimized().unwrap_or(true) {
            return;
        }
        let result = (|| -> tauri::Result<()> {
            window.show()?;
            state.set_user_hidden(false);
            if window.is_minimized()? {
                window.unminimize()?;
            }
            window.set_focus()?;
            Ok(())
        })();
        if let Err(error) = result {
            diag("warn", "window.focus_failed", &format!("切换到前台失败：{error}"));
        }
    }) {
        diag("warn", "window.present_failed", &format!("窗口呈现调度失败：{error}"));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            present_main_window(app, true);
        }))
        .on_window_event(|window, event| {
            #[cfg(target_os = "macos")]
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    // Red close button preserves the session and its original
                    // runtime. Explicit Quit still follows RunEvent::Exit.
                    api.prevent_close();
                    let state = window.app_handle().state::<MainWindowPresentation>();
                    state.set_user_hidden(true);
                    if let Err(error) = window.hide() {
                        state.set_user_hidden(false);
                        diag("warn", "window.hide_failed", &format!("隐藏失败，窗口保持打开：{error}"));
                    }
                }
            }
            #[cfg(not(target_os = "macos"))]
            let _ = (window, event);
        })
        .on_page_load(|webview, payload| {
            if webview.label() == "main" && matches!(payload.event(), PageLoadEvent::Finished) {
                if let Some(window) = webview.app_handle().get_webview_window("main") {
                    if !window.is_minimized().unwrap_or(true) {
                        present_main_window(webview.app_handle(), false);
                    }
                }
            }
        })
        // 窗口在配置里默认隐藏，等页面加载完才显示（否则启动时先闪一下白窗口，真机 2026-10-06）。
        // 兜底：页面万一没加载成功，几秒后也把窗口显示出来，绝不能让 App 看起来没打开。
        .setup(|app| {
            // 冷启动时页面加载、读配置要一秒多，前端之后才会调 start_gateway。这段时间里先把
            // 通用会话的 runtime 拉起来：前端每次打开都从通用会话开始，到时 start_gateway 直接复用
            //（同一 runtime_id 已在运行就返回，监督锁保证不会起两份）。GUI smoke 不起 runtime。
            if !matches!(smoke_marker_path(), Ok(Some(_))) {
                let handle = app.handle().clone();
                std::thread::spawn(move || {
                    let _ = start_gateway(
                        handle.clone(),
                        None,
                        GENERAL_SCOPE.into(),
                        None,
                        DEFAULT_GATEWAY_PORT,
                        None,
                        handle.state(),
                        handle.state(),
                    );
                });
            }
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(Duration::from_secs(4)).await;
                if let Some(window) = handle.get_webview_window("main") {
                    if !window.is_visible().unwrap_or(true)
                        && !window.is_minimized().unwrap_or(true)
                    {
                        present_main_window(&handle, false);
                    }
                }
            });
            Ok(())
        })
        .manage(MainWindowPresentation::default())
        .manage(GatewayProcess::default())
        .manage(DesktopLlmProfileStore::default())
        .manage(RemoteWsRegistry::default())
        .manage(ResidentDesktopManager::default())
        .manage(account::AccountState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_websocket::init())
        .invoke_handler(tauri::generate_handler![
            get_llm_profile,
            get_model_configuration_state,
            set_llm_profile,
            clear_llm_profile,
            confirm_action,
            test_llm_connection,
            get_llm_usage,
            get_user_instructions,
            set_user_instructions,
            get_browser_control,
            set_browser_control,
            get_experimental_workers,
            set_experimental_workers,
            get_desktop_control,
            get_resident_desktop,
            refresh_resident_desktop,
            save_resident_desktop_target,
            check_resident_desktop,
            open_resident_desktop,
            open_resident_desktop_workspace,
            release_resident_desktop_workspace,
            close_resident_desktop,
            check_for_update,
            install_update,
            restart_after_update,
            relaunch_app,
            account::account_status,
            account_runtime::resident_account_status,
            account_runtime::resident_account_update,
            account::account_login,
            account::account_login_cancel,
            account::account_logout,
            account::account_membership,
            account::account_use_membership,
            get_update_check_enabled,
            set_update_check_enabled,
            set_desktop_control,
            save_llm_provider,
            remove_llm_provider,
            promote_llm_provider,
            get_codex_watch_enabled,
            set_codex_watch_enabled,
            refresh_llm_providers,
            relay_login,
            relay_logout,
            reveal_desktop_config,
            list_desktop_projects,
            list_project_sessions,
            remember_desktop_project,
            remember_remote_project,
            remember_resident_project,
            fetch_resident_branch,
            remote_http_request,
            remote_ws_connect,
            remote_reachable,
            diag_log,
            reveal_diagnostics_log,
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
            present_main_window(app_handle, false);
        }

        #[cfg(target_os = "macos")]
        if matches!(event, tauri::RunEvent::Reopen { .. }) {
            present_main_window(app_handle, true);
        }

        if matches!(event, tauri::RunEvent::Exit) {
            app_handle.state::<ResidentDesktopManager>().close(None);
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
mod keychain_service_tests {
    use super::keychain_service_for;

    #[test]
    fn release_builds_keep_the_existing_item_names() {
        assert_eq!(keychain_service_for("com.vortocode.desktop.remote", false), "com.vortocode.desktop.remote");
        assert_eq!(keychain_service_for("com.vortocode.desktop.account", false), "com.vortocode.desktop.account");
    }

    #[test]
    fn debug_builds_never_touch_the_release_items() {
        let dev = keychain_service_for("com.vortocode.desktop.remote", true);
        assert_eq!(dev, "com.vortocode.desktop.remote.dev");
        assert_ne!(dev, keychain_service_for("com.vortocode.desktop.remote", false));
    }
}

#[cfg(test)]
mod promote_tests {
    use super::*;

    fn local_default() -> DesktopLlmProfile {
        DesktopLlmProfile {
            revision: None,
            base_url: "http://127.0.0.1:11500/v1".into(),
            api_key: String::new(),
            model: "qwen-local".into(),
            context_window: Some(262_144),
            context_window_source: Some("service".into()),
            fast_model: Some("qwen-small".into()),
            strong_model: None,
            role_models: HashMap::from([("assess".to_string(), "qwen-local".to_string())]),
            providers: vec![DesktopLlmProvider {
                id: "deepseek".into(),
                name: "DeepSeek".into(),
                base_url: "https://api.deepseek.com/v1".into(),
                api_key: "sk-ds".into(),
                models: vec!["deepseek-chat".into(), "deepseek-reasoner".into()],
            }],
            account: None,
            signed_out: false,
            models: vec!["qwen-small".into(), "qwen-local".into()],
            model_info: Vec::new(),
        }
    }

    #[test]
    fn promoting_keeps_the_old_default_in_the_list() {
        let profile = promote_provider(local_default(), "deepseek").unwrap();
        assert_eq!(profile.base_url, "https://api.deepseek.com/v1");
        assert_eq!(profile.api_key, "sk-ds");
        assert_eq!(profile.model, "deepseek-chat");
        assert_eq!(profile.fast_model, None);
        assert!(profile.role_models.is_empty());
        assert_eq!(profile.context_window, None);
        assert_eq!(profile.providers.len(), 1);
        let old = &profile.providers[0];
        assert_eq!((old.id.as_str(), old.name.as_str()), ("local", "本机模型"));
        assert_eq!(old.base_url, "http://127.0.0.1:11500/v1");
        assert_eq!(old.models, vec!["qwen-local".to_string(), "qwen-small".to_string()]);
    }

    #[test]
    fn promoting_refuses_member_and_empty_providers() {
        let mut profile = local_default();
        profile.providers.push(DesktopLlmProvider {
            id: MEMBER_PROVIDER_ID.into(), name: "VortoCode 会员".into(),
            base_url: format!("{RELAY_ORIGIN}/v1"), api_key: "sk-m".into(), models: vec!["m".into()],
        });
        assert!(promote_provider(profile.clone(), MEMBER_PROVIDER_ID).is_err());
        profile.providers[0].models.clear();
        assert!(promote_provider(profile.clone(), "deepseek").is_err());
        assert!(promote_provider(profile, "missing").is_err());
    }

    #[test]
    fn demoted_ids_are_valid_and_unique() {
        assert_eq!(provider_id_from_host("api.deepseek.com"), "deepseek");
        assert_eq!(provider_id_from_host("open.bigmodel.cn"), "open");
        assert_eq!(provider_id_from_host("123.example.com"), "p_123");
        let mut profile = local_default();
        profile.base_url = "https://api.deepseek.com/v1".into();
        profile.api_key = "sk-own".into();
        let taken = profile.providers.clone();
        let old = default_as_provider(&profile, &taken).unwrap();
        assert_eq!(old.id, "deepseek_2");
        assert!(normalize_llm_provider(old).is_ok());
        profile.signed_out = true;
        assert!(default_as_provider(&profile, &taken).is_none());
    }
}

#[cfg(test)]
mod member_menu_tests {
    use super::*;

    fn info(id: &str, category: Option<&str>, tools: bool, snapshot: bool) -> ModelInfo {
        ModelInfo {
            id: id.into(),
            category: category.map(Into::into),
            capabilities: if tools { vec!["tools".into()] } else { Vec::new() },
            tier: None,
            snapshot_of: snapshot.then(|| "base".into()),
            context_window: None,
        }
    }

    #[test]
    fn member_menu_keeps_agent_models_with_the_default_first() {
        let models = member_menu_models(vec![
            info("qwen-plus", Some("chat"), true, false),
            info("mimo-v2.5-tts", Some("tts"), false, false),
            info("qwen-plus-2025-01-01", Some("chat"), true, true),
            info(RELAY_DEFAULT_MODEL, Some("omni"), true, false),
        ]);
        assert_eq!(models, vec![RELAY_DEFAULT_MODEL.to_string(), "qwen-plus".to_string()]);
    }

    #[test]
    fn member_menu_falls_back_to_the_default_model() {
        assert_eq!(member_menu_models(Vec::new()), vec![RELAY_DEFAULT_MODEL.to_string()]);
        // 别家服务不给分类：原样保留
        assert_eq!(member_menu_models(vec![info("a", None, false, false)]), vec!["a".to_string()]);
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn membership_refresh_preserves_order_catalog_and_revision_when_unchanged() {
        let root = std::env::temp_dir().join(format!("vorto-member-noop-{}", new_profile_revision()));
        let path = root.join("profile.json");
        let store = DesktopLlmProfileStore::default();
        let initial = store.snapshot(|| read_llm_profile_file(&path)).unwrap();
        let mut profile = default_llm_profile();
        profile.api_key = "test-default-key".into();
        let member = DesktopLlmProvider { id: MEMBER_PROVIDER_ID.into(), name: "VortoCode 会员".into(),
            base_url: RELAY_BASE_URL.into(), api_key: "sk-test-member".into(), models: vec![RELAY_DEFAULT_MODEL.into(), "other-member-model".into()] };
        let other = DesktopLlmProvider { id: "other".into(), name: "Other".into(), base_url: "https://other.example.test/v1".into(),
            api_key: "test-other-key".into(), models: vec!["other-model".into()] };
        profile.providers = vec![member.clone(), other];
        let saved = store.commit(&path, &initial.revision, Some(profile)).unwrap();
        let before = std::fs::read(&path).unwrap();
        let mut refreshed = member.clone(); refreshed.models.clear();
        let next = refreshed_member_profile(saved.profile.clone().unwrap(), refreshed.clone(), None);
        assert_eq!(next.providers[0].id, MEMBER_PROVIDER_ID);
        assert_eq!(next.providers[0].models, member.models);
        let same = store.commit(&path, &saved.revision, Some(next)).unwrap();
        assert_eq!(same.revision, saved.revision);
        assert_eq!(std::fs::read(&path).unwrap(), before);
        let models = member.models.iter().map(|id| ModelInfo { id: id.clone(), ..Default::default() }).collect();
        let next = refreshed_member_profile(same.profile.clone().unwrap(), refreshed.clone(), Some(models));
        assert_eq!(store.commit(&path, &same.revision, Some(next)).unwrap().revision, saved.revision);
        refreshed.api_key = "sk-test-rotated".into();
        let next = refreshed_member_profile(same.profile.unwrap(), refreshed, None);
        let rotated = store.commit(&path, &saved.revision, Some(next)).unwrap();
        assert_ne!(rotated.revision, saved.revision);
        assert_eq!(rotated.profile.unwrap().providers[0].api_key, "sk-test-rotated");
        let _ = std::fs::remove_dir_all(root);
    }

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
            revision: None,
            base_url: "https://token.vortotech.com/v1/".into(),
            api_key: "test-key".into(),
            model: "mimo-v2.5".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        })
        .expect("relay profile");
        assert_eq!(relay.base_url, RELAY_BASE_URL);
        assert_eq!(llm_provider(&relay.base_url), "vortocode");
        assert_eq!(relay.context_window, Some(1_000_000));
        assert_eq!(relay.context_window_source.as_deref(), Some("catalog"));

        let local = normalize_llm_profile(DesktopLlmProfile {
            revision: None,
            base_url: "http://127.0.0.1:11434/v1".into(),
            api_key: String::new(),
            model: "local-model".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        })
        .expect("local profile");
        assert_eq!(llm_provider(&local.base_url), "local");

        assert!(normalize_llm_profile(DesktopLlmProfile {
            revision: None,
            base_url: "http://models.example.com/v1".into(),
            api_key: "test-key".into(),
            model: "model".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        })
        .is_err());
        assert!(normalize_llm_profile(DesktopLlmProfile {
            revision: None,
            base_url: "https://models.example.com/v1".into(),
            api_key: String::new(),
            model: "model".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
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
            revision: None,
            base_url: "https://models.example.com/v1".into(),
            api_key: "test-key".into(),
            model: "model-1".into(),
            context_window: Some(65_536),
            context_window_source: Some("service".into()),
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
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
            role_models: HashMap::new(),
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

    fn probe_key_fixture() -> DesktopLlmProfile {
        let mut profile = sample_llm_profile();
        profile.base_url = "https://default.example.test/v1".into();
        profile.api_key = "synthetic-default-key".into();
        profile.providers = vec![DesktopLlmProvider {
            id: "cloud".into(), name: "Cloud".into(),
            base_url: "https://cloud.example.test/v1".into(),
            api_key: "synthetic-cloud-key".into(), models: vec!["m".into()],
        }];
        profile
    }

    #[test]
    fn connection_probe_selects_only_the_requested_providers_key() {
        let mut profile = probe_key_fixture();
        profile.providers.push(DesktopLlmProvider {
            id: "other".into(), name: "Other".into(),
            base_url: profile.providers[0].base_url.clone(),
            api_key: "synthetic-other-key".into(), models: vec!["m".into()],
        });
        let base = profile.providers[0].base_url.as_str();
        assert_eq!(saved_connection_probe_key(Some(&profile), Some("cloud"), base), Some("synthetic-cloud-key"));
        assert_eq!(saved_connection_probe_key(Some(&profile), Some(" OTHER "), &format!(" {base}/ ")), Some("synthetic-other-key"));
        assert_eq!(saved_connection_probe_key(Some(&profile), None, base), None);
        for id in ["unknown", "", "default"] {
            assert_eq!(saved_connection_probe_key(Some(&profile), Some(id), base), None);
        }
    }

    #[test]
    fn connection_probe_never_reuses_a_saved_key_at_a_changed_endpoint() {
        let profile = probe_key_fixture();
        for base in ["https://other.example.test/v1", "https://cloud.example.test/v2",
            "https://cloud.example.test.evil.test/v1", "https://cloud.example.test/v1?x=1"] {
            assert_eq!(saved_connection_probe_key(Some(&profile), Some("cloud"), base), None);
        }
        assert_eq!(saved_connection_probe_key(Some(&profile), Some("cloud"), &profile.base_url), None);
        assert_eq!(saved_connection_probe_key(None, Some("cloud"), "https://cloud.example.test/v1"), None);
    }

    #[test]
    fn connection_probe_keeps_legacy_default_service_key_selection() {
        let profile = probe_key_fixture();
        assert_eq!(saved_connection_probe_key(Some(&profile), None, &format!("{}/", profile.base_url)), Some(profile.api_key.as_str()));
        assert_eq!(saved_connection_probe_key(Some(&profile), Some("unknown"), &profile.base_url), None);
        assert_eq!(saved_connection_probe_key(None, None, &profile.base_url), None);
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
            revision: None,
            base_url: "https://models.example.com/v1".into(),
            api_key: "main-key".into(),
            model: "main".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
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
            revision: None,
            base_url: "https://token.vortotech.com/v1".into(),
            api_key: String::new(),
            model: "mimo".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
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
            revision: None,
            base_url: "https://models.example.com/v1".into(),
            api_key: "k".into(),
            model: "main".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
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
            revision: None,
            base_url: "https://models.example.com/v1".into(),
            api_key: "k".into(),
            model: "main".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
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
            revision: None,
            base_url: "https://models.example.com/v1".into(),
            api_key: "k".into(),
            model: "main".into(),
            context_window: None,
            context_window_source: None,
            fast_model: Some("  fast  ".into()),
            strong_model: Some("   ".into()),
            role_models: HashMap::new(),
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
            role_models: HashMap::new(),
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
                revision: None,
                base_url: "https://models.example.com/v1".into(),
                api_key: "secret".into(),
                model: "model-1".into(),
                context_window: Some(65_536),
                context_window_source: Some("configured".into()),
                fast_model: None,
                strong_model: None,
                role_models: HashMap::new(),
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
            revision: None,
            base_url: "https://models.example.com/v1".into(),
            api_key: "secret".into(),
            model: "model-2".into(),
            context_window: Some(128_000),
            context_window_source: Some("configured".into()),
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
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
    fn resident_branch_gate_rejects_option_and_refspec_tricks() {
        for good in ["vorto/fix-login", "feature/a.b_c", "v1"] {
            assert!(valid_resident_branch(good), "{good}");
        }
        for bad in ["", "-u", "--upload-pack=x", "a..b", "a:b", "+main", "a b", "x.lock", "/a", "a/", "a//b", "@{0}", ".hidden"] {
            assert!(!valid_resident_branch(bad), "{bad}");
        }
    }

    #[test]
    fn resident_fetch_source_uses_registered_host_and_dir() {
        assert_eq!(
            resident_fetch_source("http://192.168.1.20:8082", "/home/me/projects/VortoCode/").unwrap(),
            "ssh://192.168.1.20/home/me/projects/VortoCode"
        );
        assert!(resident_fetch_source("http://192.168.1.20:8082", "projects/x").is_err());
        assert!(resident_fetch_source("http://192.168.1.20:8082", "/home/me/a b").is_err());
        assert!(resident_fetch_source("http://192.168.1.20:8082", "/home/../etc").is_err());
        assert!(resident_fetch_source("http://8.8.8.8:8082", "/srv/x").is_err());
    }

    #[test]
    fn sibling_server_url_only_changes_the_port() {
        assert_eq!(
            sibling_server_url("http://192.168.1.20:8081", 8082).unwrap(),
            "http://192.168.1.20:8082"
        );
        assert_eq!(
            sibling_server_url("https://srv.ts.net", 8443).unwrap(),
            "https://srv.ts.net:8443"
        );
        assert!(sibling_server_url("http://192.168.1.20:8081", 22).is_err());
        assert!(sibling_server_url("http://8.8.8.8:8081", 8082).is_err()); // 公网 http 照样拒
    }

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
    fn network_failure_reason_points_at_local_network_permission() {
        use std::io::{Error, ErrorKind};
        let blocked = network_failure_reason(&Error::from_raw_os_error(65));
        assert!(blocked.contains("本地网络") && blocked.contains("隐私与安全性") && blocked.contains("重新打开"));
        assert!(network_failure_reason(&Error::from_raw_os_error(1)).contains("本地网络"));
        assert!(network_failure_reason(&Error::from(ErrorKind::ConnectionRefused)).contains("没在运行"));
        assert!(network_failure_reason(&Error::from(ErrorKind::TimedOut)).contains("超时"));
        assert_eq!(network_failure_reason(&Error::new(ErrorKind::Other, "Bearer sk-x")), "网络不可达或被拒绝");
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
            launch_id: None,
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
    fn managed_gateway_pins_resident_services_off() {
        let command = configure_gateway_command(
            Command::new("python"), false, Path::new("/tmp/project"),
            8123, "", PROJECT_SCOPE,
        );
        for (name, expected) in [
            ("VORTOCODE_IM", ""), ("VORTOCODE_CRON", "0"), ("VORTOCODE_HEARTBEAT", "0"),
        ] {
            assert!(command.get_envs().any(|(key, value)| {
                key == OsStr::new(name) && value == Some(OsStr::new(expected))
            }));
        }
    }

    #[test]
    fn bundled_runtime_lives_in_the_resources_runtime_directory() {
        let expected = if cfg!(windows) { "vortocode-runtime.exe" } else { "vortocode-runtime" };
        assert_eq!(
            bundled_runtime_in(Path::new("/App/Contents/Resources")),
            Path::new("/App/Contents/Resources/runtime").join(expected)
        );
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
                configuration_revision: None,
                configuration_launch_id: None,
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
            configuration_revision: None,
            configuration_launch_id: None,
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
            revision: None,
            base_url: format!("http://127.0.0.1:{port}"),
            api_key: String::new(),
            model: "mimo-v2.5".into(),
            context_window: None,
            context_window_source: None,
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
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
            revision: None,
            base_url: "https://models.example.com/v1".into(),
            api_key: "sk-file-test".into(),
            model: "model-1".into(),
            context_window: Some(65_536),
            context_window_source: Some("configured".into()),
            fast_model: None,
            strong_model: None,
            role_models: HashMap::new(),
            providers: Vec::new(),
            account: None,
            signed_out: false,
            model_info: Vec::new(),
            models: Vec::new(),
        }
    }

    #[test]
    fn assessor_role_is_explicit_and_uses_existing_role_launch_contract() {
        let mut profile = sample_llm_profile();
        profile.role_models.insert("assess".into(), "judge-model".into());
        let roles = normalize_role_models(&profile).unwrap();
        assert_eq!(roles.get("assess").map(String::as_str), Some("default:judge-model"));
        profile.role_models.insert("assess".into(), "missing:judge-model".into());
        assert!(normalize_role_models(&profile).is_err());
        profile.role_models.insert("assess".into(), "".into());
        assert!(normalize_role_models(&profile).unwrap().is_empty());
    }

    #[test]
    fn role_models_roundtrip_and_launch_with_separate_provider_credentials() {
        let mut profile = sample_llm_profile();
        profile.providers.push(DesktopLlmProvider {
            id: "worker".into(), name: "Worker".into(),
            base_url: "https://worker.example.test/v1".into(), api_key: "synthetic-worker-key".into(),
            models: vec!["small:tag".into()],
        });
        profile.role_models = HashMap::from([
            ("research".into(), "worker:small:tag".into()),
            ("implement".into(), "default:code-model".into()),
            ("review".into(), "default:review-model".into()),
        ]);
        let profile = normalize_llm_profile(profile).unwrap();
        let raw = serde_json::to_vec(&profile).unwrap();
        let reopened: DesktopLlmProfile = serde_json::from_slice(&raw).unwrap();
        assert_eq!(reopened.role_models, profile.role_models);
        let status = serde_json::to_string(&desktop_llm_profile_status(Some(&profile))).unwrap();
        assert!(status.contains("roleModels"));
        assert!(!status.contains("synthetic-worker-key"));
        let mut command = Command::new("fixture");
        configure_llm_profile(&mut command, Some(&profile));
        let env = command.get_envs().map(|(key, value)| (key.to_str().unwrap().to_string(), value.map(|value| value.to_str().unwrap().to_string()))).collect::<HashMap<_, _>>();
        assert_eq!(env["VORTOCODE_MODEL_ROLES"].as_deref(), Some("implement=default:code-model,research=worker:small:tag,review=default:review-model"));
        assert_eq!(env["VORTOCODE_PROVIDER_WORKER_KEY"].as_deref(), Some("synthetic-worker-key"));
        assert_eq!(env["OPENAI_API_KEY"].as_deref(), Some(profile.api_key.as_str()));
    }

    #[test]
    fn role_models_reject_unknown_channels_and_environment_injection() {
        for (role, target) in [("research", "missing:small"), ("research", "small,review=large"),
            ("research", "small\nTOKEN=value"), ("unknown", "small"), ("research", "default:")] {
            let mut profile = sample_llm_profile();
            profile.role_models.insert(role.into(), target.into());
            assert!(normalize_llm_profile(profile).is_err());
        }
    }

    #[test]
    fn role_models_preserve_default_model_tags_and_clear_ambient_roles() {
        let mut profile = sample_llm_profile();
        profile.models.push("qwen:7b".into());
        profile.role_models.insert("research".into(), "qwen:7b".into());
        profile.role_models.insert("review".into(), "  ".into());
        let profile = normalize_llm_profile(profile).unwrap();
        assert_eq!(profile.role_models["research"], "default:qwen:7b");
        assert!(!profile.role_models.contains_key("review"));
        let mut command = Command::new("fixture");
        configure_llm_profile(&mut command, Some(&sample_llm_profile()));
        assert_eq!(command.get_envs().find(|(key, _)| *key == "VORTOCODE_MODEL_ROLES").unwrap().1, Some(std::ffi::OsStr::new("")));
    }

    #[test]
    fn external_cli_roles_split_from_model_roles() {
        let mut profile = sample_llm_profile();
        profile.role_models = HashMap::from([
            ("implement".into(), "cli:codex".into()),
            ("review".into(), "cli:claude".into()),
            ("research".into(), "default:small".into()),
        ]);
        let profile = normalize_llm_profile(profile).unwrap();
        assert_eq!(profile.role_models["implement"], "cli:codex");
        let mut command = Command::new("fixture");
        configure_external_workers(&mut command, &profile);
        let env = command.get_envs().map(|(key, value)| (key.to_str().unwrap().to_string(), value.map(|value| value.to_str().unwrap().to_string()))).collect::<HashMap<_, _>>();
        assert_eq!(env["VORTOCODE_MODEL_ROLES"].as_deref(), Some("research=default:small"));
        assert_eq!(env["VORTOCODE_EXTERNAL_WORKERS"].as_deref(), Some("implement=codex,review=claude"));
    }

    #[test]
    fn experimental_workers_flag_is_off_by_default_and_never_inherited() {
        assert!(!DesktopPreferences::default().experimental_external_workers);
        let old: DesktopPreferences = serde_json::from_str(r#"{"browserControl": true}"#).unwrap();
        assert!(!old.experimental_external_workers);                        // 旧偏好文件：关闭
        let mut command = Command::new("fixture");
        command.env("VORTOCODE_EXPERIMENTAL_EXTERNAL_WORKERS", "1");      // 假装从父进程继承了
        configure_llm_profile(&mut command, Some(&sample_llm_profile()));
        configure_experimental_workers(&mut command, &DesktopPreferences::default());
        assert_eq!(command.get_envs().find(|(k, _)| *k == "VORTOCODE_EXPERIMENTAL_EXTERNAL_WORKERS").unwrap().1, None);
        let on = DesktopPreferences { experimental_external_workers: true, ..Default::default() };
        configure_experimental_workers(&mut command, &on);
        assert_eq!(command.get_envs().find(|(k, _)| *k == "VORTOCODE_EXPERIMENTAL_EXTERNAL_WORKERS").unwrap().1,
                   Some(std::ffi::OsStr::new("1")));
    }

    fn env_of<'a>(command: &'a Command, key: &str) -> Option<Option<&'a std::ffi::OsStr>> {
        command.get_envs().find(|(k, _)| *k == key).map(|(_, value)| value)
    }

    #[test]
    fn desktop_control_is_off_by_default_and_never_inherited() {
        let old: DesktopPreferences = serde_json::from_str(r#"{"browserControl": true}"#).unwrap();
        assert!(!old.desktop_control);                                     // 旧偏好文件：关闭
        let mut command = Command::new("fixture");
        command.env("VORTOCODE_ENABLE_DESKTOP_CONTROL", "1");             // 假装从父进程继承了
        command.env("VORTOCODE_DESKTOP_TARGET", "ssh:someone@elsewhere");
        configure_desktop_control(&mut command, &DesktopPreferences::default());
        assert_eq!(env_of(&command, "VORTOCODE_ENABLE_DESKTOP_CONTROL"), Some(None));
        assert_eq!(env_of(&command, "VORTOCODE_DESKTOP_TARGET"), Some(None));

        let on = DesktopPreferences { desktop_control: true, desktop_target: "ssh:dev@desktop-vm.lan".into(),
            desktop_apps: "firefox,mousepad".into(), ..Default::default() };
        configure_desktop_control(&mut command, &on);
        assert_eq!(env_of(&command, "VORTOCODE_ENABLE_DESKTOP_CONTROL"), Some(Some(std::ffi::OsStr::new("1"))));
        assert_eq!(env_of(&command, "VORTOCODE_DESKTOP_TARGET"), Some(Some(std::ffi::OsStr::new("ssh:dev@desktop-vm.lan"))));
        assert_eq!(env_of(&command, "VORTOCODE_DESKTOP_APPS"), Some(Some(std::ffi::OsStr::new("firefox,mousepad"))));

        // 偏好文件被手改出非法目标：按关闭处理，不把它交给引擎
        let broken = DesktopPreferences { desktop_target: "ssh:-oProxyCommand=x".into(), ..on };
        configure_desktop_control(&mut command, &broken);
        assert_eq!(env_of(&command, "VORTOCODE_ENABLE_DESKTOP_CONTROL"), Some(None));
    }

    #[test]
    fn update_check_compares_versions_and_builds_our_release_page() {
        let manifest = |version: &str| serde_json::json!({
            "version": version, "pub_date": "2026-10-20T00:00:00Z", "notes": "x",
            "platforms": { "darwin-aarch64": { "url": "https://evil.example/download" } } });
        let newer = update_from_manifest("0.2.0", &manifest("0.2.1")).unwrap();
        assert!(newer.newer && newer.latest == "0.2.1" && newer.published_at == "2026-10-20T00:00:00Z");
        assert_eq!(newer.url, "https://github.com/DingHappy/VortoCode/releases/tag/v0.2.1");   // 不用清单里的链接
        assert!(!update_from_manifest("0.2.0", &manifest("0.2.0")).unwrap().newer);
        assert!(!update_from_manifest("0.10.0", &manifest("0.9.9")).unwrap().newer);   // 数字比较，不是字符串
        assert!(update_from_manifest("0.2.0", &manifest("v1.0")).unwrap().newer);
        assert!(update_from_manifest("0.2.0", &manifest("nightly")).is_err());
        assert!(update_from_manifest("0.2.0", &serde_json::json!({})).is_err());
        assert_eq!(parse_version("v0.2.0-beta.1"), Some((0, 2, 0)));
        assert_eq!(parse_version("0.2.0.1"), None);
        let old: DesktopPreferences = serde_json::from_str(r#"{"browserControl": true}"#).unwrap();
        assert!(!old.update_check_disabled);                                  // 旧偏好文件：默认检查
    }

    #[test]
    fn relaunch_after_update_reopens_the_whole_app_bundle() {
        assert_eq!(app_bundle_of(Path::new("/Applications/VortoCode.app/Contents/MacOS/vortocode-desktop")),
            Some(PathBuf::from("/Applications/VortoCode.app")));
        // 开发构建不在 .app 里：退回 Tauri 默认重启
        assert_eq!(app_bundle_of(Path::new("/repo/desktop/src-tauri/target/debug/vortocode-desktop")), None);
        assert_eq!(app_bundle_of(Path::new("/x/Contents/MacOS/vortocode-desktop")), None);
        assert_eq!(app_bundle_of(Path::new("/x/VortoCode.app/MacOS/vortocode-desktop")), None);
    }

    #[cfg(unix)]
    #[test]
    fn relaunch_script_waits_for_the_old_process_then_opens_the_bundle() {
        // 只跑等待段（不真的 open）：旧进程还在就等，退出后马上往下走
        let waiter = RELAUNCH_SCRIPT.split("; exec ").next().unwrap();
        let mut sleeper = Command::new("/bin/sleep").arg("0.4").spawn().unwrap();
        let pid = sleeper.id();
        let reaper = std::thread::spawn(move || sleeper.wait());   // 不回收的话僵尸进程对 kill -0 仍然有回应
        let started = std::time::Instant::now();
        let status = Command::new("/bin/sh").args(["-c", waiter])
            .env("VC_RELAUNCH_PID", pid.to_string()).status().unwrap();
        reaper.join().unwrap().unwrap();
        assert!(status.success());
        assert!(started.elapsed() >= Duration::from_millis(300), "应等到旧进程退出");
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn desktop_target_and_apps_are_validated() {
        for good in ["ssh:dev@desktop-vm.lan", "ssh:desktop-vm", "ssh:user_1@10.0.0.5"] {
            assert_eq!(normalize_desktop_target(good).unwrap(), good);
        }
        for bad in ["ssh:", "ssh:-oProxyCommand=x", "ssh:dev@-v", "ssh:dev@host;rm", "ssh:a b@host", "desktop-vm", "ssh:@host"] {
            assert!(normalize_desktop_target(bad).is_err(), "{bad}");
        }
        assert_eq!(normalize_desktop_target("").is_ok(), cfg!(target_os = "linux"));   // local 只在 Linux 上可用
        assert_eq!(normalize_desktop_apps(" firefox， mousepad,firefox ,").unwrap(), "firefox,mousepad");
        assert_eq!(normalize_desktop_apps("").unwrap(), "");
        for bad in ["firefox --new", "-x", "rm;ls", "a/b"] {
            assert!(normalize_desktop_apps(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn external_cli_roles_are_limited_to_implement_and_review() {
        for (role, target) in [("research", "cli:codex"), ("assess", "cli:claude"), ("implement", "cli:gpt"),
            ("review", "cli:")] {
            let mut profile = sample_llm_profile();
            profile.role_models.insert(role.into(), target.into());
            assert!(normalize_llm_profile(profile).is_err(), "{role}={target}");
        }
        let mut command = Command::new("fixture");
        configure_external_workers(&mut command, &sample_llm_profile());
        assert_eq!(command.get_envs().find(|(key, _)| *key == "VORTOCODE_EXTERNAL_WORKERS").unwrap().1,
                   Some(std::ffi::OsStr::new("")));
    }

    #[test]
    fn provider_id_cli_is_reserved_for_external_workers() {
        // 否则 `cli:codex` 既可能是外部 CLI、也可能是名叫 cli 的供应商上的 codex 模型
        assert!(normalize_llm_provider(sample_provider("cli")).is_err());
        assert!(normalize_llm_provider(sample_provider("clix")).is_ok());
    }

    #[test]
    fn old_profile_without_roles_remains_compatible() {
        let mut raw = serde_json::to_value(sample_llm_profile()).unwrap();
        raw.as_object_mut().unwrap().remove("roleModels");
        let profile: DesktopLlmProfile = serde_json::from_value(raw).unwrap();
        assert!(profile.role_models.is_empty());
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
