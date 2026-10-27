// 检查更新与一键更新。从 lib.rs 原样搬出。
use super::*;

// ── 检查更新：问 GitHub 最新正式版，比版本号；不下载、不安装，只把下载页交给用户 ──
pub(crate) const RELEASES_API: &str = "https://api.github.com/repos/DingHappy/VortoCode/releases/latest";
pub(crate) const RELEASES_PAGE_PREFIX: &str = "https://github.com/DingHappy/VortoCode/releases/";

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct UpdateCheck {
    pub(crate) current: String,
    pub(crate) latest: String,
    pub(crate) newer: bool,
    pub(crate) url: String,
    pub(crate) published_at: String,
}

pub(crate) fn parse_version(raw: &str) -> Option<(u64, u64, u64)> {
    let core = raw.trim().trim_start_matches('v').split(['-', '+']).next()?;
    let mut parts = core.split('.').map(|part| part.parse::<u64>().ok());
    let version = (parts.next()??, parts.next()??, parts.next().unwrap_or(Some(0))?);
    parts.next().is_none().then_some(version)
}

/// GitHub 的 releases/latest 已经排除了草稿和预发布；这里只认本仓库的发布页链接，
/// 响应异常时宁可当没有新版，也不把别的地址交给用户去打开。
pub(crate) fn update_from_release(current: &str, release: &serde_json::Value) -> Result<UpdateCheck, String> {
    let tag = release.get("tag_name").and_then(|value| value.as_str()).unwrap_or_default();
    let latest = parse_version(tag).ok_or_else(|| format!("最新版本号无法识别：{tag}"))?;
    let installed = parse_version(current).ok_or_else(|| format!("当前版本号无法识别：{current}"))?;
    let url = release.get("html_url").and_then(|value| value.as_str()).unwrap_or_default();
    if !url.starts_with(RELEASES_PAGE_PREFIX) {
        return Err("发布页地址不是本项目的 GitHub Releases，已忽略".into());
    }
    Ok(UpdateCheck {
        current: current.into(),
        latest: format!("{}.{}.{}", latest.0, latest.1, latest.2),
        newer: latest > installed,
        url: url.into(),
        published_at: release.get("published_at").and_then(|value| value.as_str()).unwrap_or_default().into(),
    })
}

#[tauri::command]
pub(crate) async fn check_for_update(app: AppHandle) -> Result<UpdateCheck, String> {
    let current = app.package_info().version.to_string();
    let response = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .user_agent(format!("VortoCode-Desktop/{current}"))
        .build()
        .map_err(|error| format!("无法检查更新：{error}"))?
        .get(RELEASES_API)
        .header("Accept", "application/vnd.github+json")
        .send()
        .await
        .map_err(|error| format!("连不上 GitHub，稍后再试：{error}"))?;
    if !response.status().is_success() {
        return Err(format!("GitHub 返回 {}，稍后再试", response.status()));
    }
    let release: serde_json::Value = response.json().await.map_err(|error| format!("GitHub 的响应无法解析：{error}"))?;
    update_from_release(&current, &release)
}

// ── 一键更新：发布页上的 latest.json 指向更新包，用内置公钥验签通过才替换自己，然后走正常退出流程重启 ──
// 公钥和地址写在 tauri.conf.json（plugins.updater），网页侧改不了；签名私钥只在发版的那台机器上。
#[tauri::command]
pub(crate) async fn install_update(app: AppHandle) -> Result<String, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    use tauri_plugin_updater::UpdaterExt;
    let update = app
        .updater()
        .map_err(|error| format!("更新组件不可用：{error}"))?
        .check()
        .await
        .map_err(|error| format!("检查更新失败：{error}"))?
        .ok_or_else(|| "已经是最新版本".to_string())?;
    let version = update.version.clone();
    // 装完要重启：原生确认在 webview 之外，说清楚正在进行的对话会中断
    let dialog_app = app.clone();
    let prompt = format!(
        "下载并安装 VortoCode {version}？\n\n装好后 App 会自动重启，本机正在进行的对话会中断；常驻电脑上的助理和后台任务不受影响。"
    );
    let confirmed = tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&dialog_app, dialog_app.dialog().message(prompt))
            .title("更新 VortoCode")
            .kind(MessageDialogKind::Info)
            .buttons(MessageDialogButtons::OkCancelCustom("更新并重启".into(), "稍后".into()))
            .blocking_show()
    })
    .await
    .map_err(|error| format!("更新确认对话框失败：{error}"))?;
    if !confirmed {
        return Err("已取消，稍后可在设置里更新".into());
    }
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|error| format!("下载或安装失败（签名不符也会在这里拒绝）：{error}"))?;
    Ok(version)
}

/// 装好更新后重启：走 RunEvent::Exit，托管的引擎进程照常回收。
#[tauri::command]
pub(crate) fn restart_after_update(app: AppHandle) {
    app.request_restart();
}

#[tauri::command(async)]
pub(crate) fn get_update_check_enabled(app: AppHandle) -> Result<bool, String> {
    Ok(!read_desktop_preferences(&desktop_preferences_path(&app)?).update_check_disabled)
}

#[tauri::command(async)]
pub(crate) fn set_update_check_enabled(app: AppHandle, enabled: bool) -> Result<bool, String> {
    let path = desktop_preferences_path(&app)?;
    let mut preferences = read_desktop_preferences(&path);
    preferences.update_check_disabled = !enabled;
    save_json_atomic_with_mode(&path, "desktop-preferences", &preferences, Some(0o600))?;
    Ok(enabled)
}
