// 检查更新与一键更新。从 lib.rs 原样搬出。
use super::*;

// ── 检查更新：交给一键更新用的同一个更新组件去读发布页上的 latest.json；只看版本，不下载、不安装 ──
// 不走 api.github.com：未登录每个出口 IP 每小时只有 60 次，代理出口共用时很快用完，用户看到 403。
// latest.json 走 releases/latest/download/ 下载链接，不计 API 限额，「latest」同样排除了草稿和预发布；
// 和「更新并重启」同一份清单、同一套平台匹配，不会出现「检查说有新版、点了却装不上」。
// 清单地址按 tauri.conf.json 的顺序：先阿里云 OSS 镜像（国内连 GitHub 下载更新包只有几十 KB/s），
// 取不到再 GitHub。镜像上的包同样要过内置公钥验签。
pub(crate) const RELEASES_PAGE_PREFIX: &str = "https://github.com/DingHappy/VortoOne/releases/";

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

/// 清单里只取版本号和发布时间；发布页地址由版本号拼出，不用清单里的任何链接。
pub(crate) fn update_from_manifest(current: &str, manifest: &serde_json::Value) -> Result<UpdateCheck, String> {
    let raw = manifest.get("version").and_then(|value| value.as_str()).unwrap_or_default();
    let latest = parse_version(raw).ok_or_else(|| format!("最新版本号无法识别：{raw}"))?;
    let installed = parse_version(current).ok_or_else(|| format!("当前版本号无法识别：{current}"))?;
    let latest_text = format!("{}.{}.{}", latest.0, latest.1, latest.2);
    Ok(UpdateCheck {
        current: current.into(),
        url: format!("{RELEASES_PAGE_PREFIX}tag/v{latest_text}"),
        latest: latest_text,
        newer: latest > installed,
        published_at: manifest.get("pub_date").and_then(|value| value.as_str()).unwrap_or_default().into(),
    })
}

// ── 网络：GitHub 的下载在有些网络下时快时慢，偶尔卡住不动 ──
// 连接 15 秒连不上、或连上后 30 秒一个字节都没收到，就当这次失败；不设整体时限，慢网也能下完 38MB。
// 检查和下载失败会重试，每次失败的原文都写进 desktop.log，用户说「更新失败」时有据可查。
const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
const READ_TIMEOUT: Duration = Duration::from_secs(30);
pub(crate) const CHECK_ATTEMPTS: u32 = 2;
pub(crate) const DOWNLOAD_ATTEMPTS: u32 = 3;
const PROGRESS_EVENT: &str = "update-progress";
/// 有多个下载来源时，开头一起下这么久，然后留下收到字节最多的那个。
const RACE_WINDOW: Duration = Duration::from_secs(3);

fn updater(app: &AppHandle) -> Result<tauri_plugin_updater::Updater, String> {
    use tauri_plugin_updater::UpdaterExt;
    app.updater_builder()
        .configure_client(|client| client.connect_timeout(CONNECT_TIMEOUT).read_timeout(READ_TIMEOUT))
        .build()
        .map_err(|error| format!("更新组件不可用：{error}"))
}

/// 网络类的失败值得再试；验签不过、解包或替换失败重试也没用，直接报。
pub(crate) fn retryable(error: &tauri_plugin_updater::Error) -> bool {
    use tauri_plugin_updater::Error;
    matches!(error, Error::Reqwest(_) | Error::Network(_) | Error::ReleaseNotFound)
}

/// 第 n 次失败后等多久再试：2 秒、5 秒。
pub(crate) fn retry_delay(failed_attempt: u32) -> Duration {
    Duration::from_secs(if failed_attempt <= 1 { 2 } else { 5 })
}

/// 写进日志和界面的错误：去掉链接里的查询参数（下载地址跳转后带有临时签名）。
pub(crate) fn update_error_text(error: &impl std::fmt::Display) -> String {
    let text = error.to_string();
    let mut out = String::with_capacity(text.len());
    let mut rest = text.as_str();
    while let Some(start) = rest.find("http") {
        out.push_str(&rest[..start]);
        let tail = &rest[start..];
        let end = tail.find(|c: char| c.is_whitespace() || "()\"'<>".contains(c)).unwrap_or(tail.len());
        let url = &tail[..end];
        out.push_str(url.split('?').next().unwrap_or(url));
        rest = &tail[end..];
    }
    out.push_str(rest);
    out
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct UpdateProgress {
    pub(crate) downloaded: u64,
    pub(crate) total: Option<u64>,
    pub(crate) attempt: u32,
}

/// 进度别每个数据块都发：百分比变了才发；不知道总大小时每 1MB 发一次。
pub(crate) fn progress_step(downloaded: u64, total: Option<u64>) -> u64 {
    match total {
        Some(total) if total > 0 => downloaded.min(total) * 100 / total,
        _ => downloaded / (1024 * 1024),
    }
}

#[tauri::command]
pub(crate) async fn check_for_update(app: AppHandle) -> Result<UpdateCheck, String> {
    let current = app.package_info().version.to_string();
    let updater = updater(&app)?;
    let mut attempt = 1;
    let found = loop {
        match updater.check().await {
            Ok(found) => break found,
            Err(error) => {
                let text = update_error_text(&error);
                diag("warn", "update.check_failed", &format!("第 {attempt} 次：{text}"));
                if attempt >= CHECK_ATTEMPTS || !retryable(&error) {
                    return Err(format!("连不上 GitHub，稍后再试：{text}"));
                }
                tokio::time::sleep(retry_delay(attempt)).await;
                attempt += 1;
            }
        }
    };
    match found {
        Some(update) => update_from_manifest(&current, &update.raw_json),
        None => Ok(UpdateCheck {
            url: format!("{RELEASES_PAGE_PREFIX}tag/v{current}"),
            latest: current.clone(),
            current,
            newer: false,
            published_at: String::new(),
        }),
    }
}

// ── 一键更新：发布页上的 latest.json 指向更新包，用内置公钥验签通过才替换自己，然后走正常退出流程重启 ──
// 公钥和地址写在 tauri.conf.json（plugins.updater），网页侧改不了；签名私钥只在发版的那台机器上。
#[tauri::command]
pub(crate) async fn install_update(app: AppHandle) -> Result<String, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    let update = updater(&app)?
        .check()
        .await
        .map_err(|error| {
            let text = update_error_text(&error);
            diag("warn", "update.check_failed", &text);
            format!("检查更新失败：{text}")
        })?
        .ok_or_else(|| "已经是最新版本".to_string())?;
    let version = update.version.clone();
    // 装完要重启：原生确认在 webview 之外，说清楚正在进行的对话会中断
    let dialog_app = app.clone();
    let prompt = format!(
        "下载并安装 VortoOne {version}？\n\n装好后 App 会自动重启，本机正在进行的对话会中断；常驻电脑上的助理和后台任务不受影响。"
    );
    let confirmed = tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&dialog_app, dialog_app.dialog().message(prompt))
            .title("更新 VortoOne")
            .kind(MessageDialogKind::Info)
            .buttons(MessageDialogButtons::OkCancelCustom("更新并重启".into(), "稍后".into()))
            .blocking_show()
    })
    .await
    .map_err(|error| format!("更新确认对话框失败：{error}"))?;
    if !confirmed {
        return Err("已取消，稍后可在设置里更新".into());
    }
    let bytes = download_with_retry(&app, &update).await?;
    update.install(bytes).map_err(|error| {
        let text = update_error_text(&error);
        diag("error", "update.install_failed", &text);
        telemetry::report_error("update.install_failed", &text, &version);
        format!("下载好了，但替换 App 失败：{text}")
    })?;
    diag("info", "update.installed", &version);
    telemetry::count_usage_event("update.installed");
    // 装好就在这里重启，不等网页回调：新版替换磁盘上的 .app 后，旧进程的网页进程可能随之退出、窗口变空白，
    // 网页侧的「装完再请求重启」就永远发不出来（真机 2026-11-09，0.2.54 → 0.2.55 停在空白窗口）。
    relaunch(app);
    Ok(version)
}

/// 下载并验签，网络失败最多试 DOWNLOAD_ATTEMPTS 次；每次从头下（不续传），进度经 update-progress 事件给界面。
/// 这一版能从哪些地址下载：清单里的主地址在前，再加清单 `mirrors` 里列出的其它地址（发版脚本写进去的 OSS 镜像和
/// GitHub）。只收 https、且文件名和主地址相同的；去重。来源不影响安全：不管从哪下，都要过内置公钥验签。
pub(crate) fn download_sources(primary: &tauri::Url, raw_json: &serde_json::Value) -> Vec<tauri::Url> {
    let file = |url: &tauri::Url| url.path_segments().and_then(|mut segments| segments.next_back()).map(str::to_owned);
    let mut sources = vec![primary.clone()];
    for candidate in raw_json.get("mirrors").and_then(|value| value.as_array()).into_iter().flatten() {
        let Some(url) = candidate.as_str().and_then(|text| tauri::Url::parse(text).ok()) else { continue };
        if url.scheme() == "https" && file(&url) == file(primary) && !sources.contains(&url) {
            sources.push(url);
        }
    }
    sources
}

/// 赛跑时谁领先：收到字节最多的；一样多时取靠前的（清单主地址）。
pub(crate) fn race_leader(received: &[u64]) -> usize {
    received.iter().enumerate().fold(0, |best, (index, &bytes)| if bytes > received[best] { index } else { best })
}

/// 一次下载尝试。只有一个来源就直接下；两个来源时开头一起下，RACE_WINDOW 后留下收到字节最多的、断开另一个，
/// 窗口内哪个先下完就用哪个、哪个先报错就只剩另一个。返回包和实际用的来源序号；失败时带上收到的最多字节数。
async fn download_racing(
    app: &AppHandle,
    update: &tauri_plugin_updater::Update,
    sources: &[tauri::Url],
    attempt: u32,
) -> Result<(Vec<u8>, usize), (tauri_plugin_updater::Error, u64)> {
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering::Relaxed};
    use std::sync::Arc;
    use tauri::Emitter;
    const RACING: usize = usize::MAX;
    let counters: Arc<Vec<AtomicU64>> = Arc::new(sources.iter().map(|_| AtomicU64::new(0)).collect());
    let chosen = Arc::new(AtomicUsize::new(if sources.len() > 1 { RACING } else { 0 }));
    let start = |index: usize| {
        let mut source = update.clone();
        source.download_url = sources[index].clone();
        let (counters, chosen, app) = (counters.clone(), chosen.clone(), app.clone());
        Box::pin(async move {
            let mut last_step = None;
            source
                .download(
                    move |chunk, total| {
                        let mine = counters[index].fetch_add(chunk as u64, Relaxed) + chunk as u64;
                        // 进度只报领先（或已选定）的那个来源，界面上的数字不会来回跳
                        let leading = match chosen.load(Relaxed) {
                            RACING => counters.iter().all(|other| other.load(Relaxed) <= mine),
                            winner => winner == index,
                        };
                        let step = progress_step(mine, total);
                        if leading && last_step != Some(step) {
                            last_step = Some(step);
                            let _ = app.emit(PROGRESS_EVENT, UpdateProgress { downloaded: mine, total, attempt });
                        }
                    },
                    || {},
                )
                .await
        })
    };
    let most = |counters: &[AtomicU64]| counters.iter().map(|count| count.load(Relaxed)).max().unwrap_or(0);
    let host = |index: usize| sources[index].host_str().unwrap_or("?").to_owned();

    let first = start(0);
    if sources.len() == 1 {
        return first.await.map(|bytes| (bytes, 0)).map_err(|error| (error, most(&counters)));
    }
    let received = || counters.iter().map(|count| count.load(Relaxed)).collect::<Vec<u64>>();
    let (result, index) = race_two(first, start(1), RACE_WINDOW, received, |index, why| {
        chosen.store(index, Relaxed);
        diag(if matches!(why, RaceChoice::FailedOver(_)) { "warn" } else { "info" }, "update.source", &match why {
            RaceChoice::FinishedEarly => format!("{} 在赛跑窗口内就下完了", host(index)),
            RaceChoice::FailedOver(error) => format!("{} 失败，改用 {}：{error}", host(1 - index), host(index)),
            RaceChoice::Leader(got) => format!(
                "{RACE_WINDOW:?} 内 {} 收到 {} 字节、{} 收到 {} 字节，用 {}", host(0), got[0], host(1), got[1], host(index)
            ),
        });
    })
    .await;
    result.map(|bytes| (bytes, index)).map_err(|error| (error, most(&counters)))
}

/// 赛跑怎么选出来的，记日志用。
pub(crate) enum RaceChoice {
    /// 窗口内就下完了
    FinishedEarly,
    /// 另一个先失败了（带它的错误，已去掉链接里的签名参数）
    FailedOver(String),
    /// 窗口到了，这个收到的字节最多（带两边各收到多少）
    Leader(Vec<u64>),
}

/// 两个下载一起跑：窗口内谁先成功就用谁，谁先失败就只剩另一个；窗口到了留下 received() 里领先的、丢掉另一个
/// （丢掉即断开连接），领先的接着跑完。选定时先调 choose（在等剩下那个之前，进度就只报它）。返回结果和用的是哪个。
pub(crate) async fn race_two<T, E: std::fmt::Display, F: std::future::Future<Output = Result<T, E>> + ?Sized>(
    mut first: std::pin::Pin<Box<F>>,
    mut second: std::pin::Pin<Box<F>>,
    window: Duration,
    received: impl Fn() -> Vec<u64>,
    choose: impl FnOnce(usize, RaceChoice),
) -> (Result<T, E>, usize) {
    use futures_util::future::{select, Either};
    let mut timer = Box::pin(tokio::time::sleep(window));
    let early = match select(select(&mut first, &mut second), &mut timer).await {
        Either::Left((Either::Left((result, _)), _)) => Some((0, result)),
        Either::Left((Either::Right((result, _)), _)) => Some((1, result)),
        Either::Right(_) => None,
    };
    let (index, why) = match early {
        Some((index, Ok(value))) => {
            choose(index, RaceChoice::FinishedEarly);
            return (Ok(value), index);
        }
        Some((failed, Err(error))) => (1 - failed, RaceChoice::FailedOver(update_error_text(&error))),
        None => {
            let got = received();
            (race_leader(&got), RaceChoice::Leader(got))
        }
    };
    choose(index, why);
    // 落后或失败的那个直接丢掉；选中的接着跑，不用从头再来
    let remaining = if index == 0 { drop(second); first } else { drop(first); second };
    (remaining.await, index)
}

async fn download_with_retry(app: &AppHandle, update: &tauri_plugin_updater::Update) -> Result<Vec<u8>, String> {
    use tauri::Emitter;
    let sources = download_sources(&update.download_url, &update.raw_json);
    let mut attempt = 1;
    loop {
        let started = std::time::Instant::now();
        let _ = app.emit(PROGRESS_EVENT, UpdateProgress { downloaded: 0, total: None, attempt });
        // 每次尝试都重新赛跑：上一次用的来源中途断了，这次可能换成另一个
        match download_racing(app, update, &sources, attempt).await {
            Ok((bytes, index)) => {
                diag("info", "update.downloaded", &format!(
                    "{} {} 字节，来自 {}，第 {attempt} 次，{:.1} 秒",
                    update.version, bytes.len(), sources[index].host_str().unwrap_or("?"), started.elapsed().as_secs_f64()
                ));
                return Ok(bytes);
            }
            Err((error, downloaded)) => {
                let text = update_error_text(&error);
                let again = attempt < DOWNLOAD_ATTEMPTS && retryable(&error);
                diag(if again { "warn" } else { "error" }, "update.download_failed", &format!(
                    "第 {attempt} 次，已收到 {downloaded} 字节，{:.1} 秒：{text}", started.elapsed().as_secs_f64()
                ));
                if !again {
                    let (kind, message) = if retryable(&error) {
                        ("update.download_failed", format!("下载更新包失败（试了 {attempt} 次）：{text}"))
                    } else {
                        ("update.rejected", format!("更新包没通过校验，已拒绝安装：{text}"))
                    };
                    telemetry::report_error(kind, &message, &update.version);
                    return Err(message);
                }
                tokio::time::sleep(retry_delay(attempt)).await;
                attempt += 1;
            }
        }
    }
}

/// 连不上常驻电脑、原因是 macOS「本地网络」拦截时，提示条上的「重新打开 VortoOne」。
#[tauri::command]
pub(crate) fn relaunch_app(app: AppHandle) {
    relaunch(app);
}

/// macOS 上不像 Tauri 默认那样直接拉起新的可执行文件，而是等本进程退出后用 open 交给 LaunchServices
/// 重新打开整个 App，和从程序坞点开一样。真机 2026-10-28：App 内更新后直接拉起的新版，系统的「本地网络」
/// 放行记录还停在旧版的可执行文件指纹上，连常驻电脑被静默拦下；从程序坞重新打开后记录换成新版，立即恢复。
/// 拿不到 .app 路径（开发构建）或脚本起不来时退回默认重启。
fn relaunch(app: AppHandle) {
    #[cfg(target_os = "macos")]
    if let Some(bundle) = std::env::current_exe().ok().as_deref().and_then(app_bundle_of) {
        match Command::new("/bin/sh")
            .args(["-c", RELAUNCH_SCRIPT])
            .env("VC_RELAUNCH_PID", std::process::id().to_string())
            .env("VC_RELAUNCH_BUNDLE", &bundle)
            .stdin(Stdio::null())
            .spawn()
        {
            Ok(_) => return app.exit(0),
            Err(error) => diag("warn", "update.relaunch_failed", &format!("改用默认重启：{error}")),
        }
    }
    app.request_restart();
}

/// 最多等 30 秒旧进程退出；超时也照样 open，App 还开着时 open 只会把它叫到前台。
pub(crate) const RELAUNCH_SCRIPT: &str = r#"i=0; while kill -0 "$VC_RELAUNCH_PID" 2>/dev/null && [ $i -lt 150 ]; do sleep 0.2; i=$((i+1)); done; exec /usr/bin/open "$VC_RELAUNCH_BUNDLE""#;

/// …/VortoCode.app/Contents/MacOS/vortocode-desktop → …/VortoCode.app；不在 .app 里（开发构建）就是 None。
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub(crate) fn app_bundle_of(exe: &Path) -> Option<PathBuf> {
    let macos = exe.parent().filter(|dir| dir.file_name() == Some("MacOS".as_ref()))?;
    let contents = macos.parent().filter(|dir| dir.file_name() == Some("Contents".as_ref()))?;
    let bundle = contents.parent().filter(|dir| dir.extension() == Some("app".as_ref()))?;
    Some(bundle.to_path_buf())
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
