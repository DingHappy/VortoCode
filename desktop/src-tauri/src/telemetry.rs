// 错误报告：App 崩溃、内置引擎起不来或意外退出、更新失败、界面没接住的报错，发给账号服务（默认开，设置里可关）。
// 只发错误类别、简短说明、版本、系统和随机安装编号；不发对话、文件内容和账号。说明先过 diagnostics 的脱敏。
// 先写进本机队列再发：断网、账号服务不通时留到下次；同一个错误每次运行只发一次，每天最多 MAX_REPORTS_PER_DAY 条。
// 崩溃处理里不能联网，只入队，下次启动再发。隐私页（account.vortotech.com/privacy）与这里同口径。
use super::*;
use std::collections::HashSet;
use std::sync::OnceLock;

const MAX_REPORTS_PER_DAY: u32 = 20;
const MAX_QUEUE: usize = 50;
const MAX_MESSAGE: usize = 600;
const MAX_DETAIL: usize = 4000;
const NOTICE_EVENT: &str = "telemetry-notice";

static LOCK: Mutex<()> = Mutex::new(());
static SEEN: Mutex<Option<HashSet<String>>> = Mutex::new(None);
static APP: OnceLock<AppHandle> = OnceLock::new();

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub(crate) struct TelemetryState {
    pub(crate) install_id: String,
    pub(crate) errors_enabled: bool,
    pub(crate) usage_enabled: bool,
    pub(crate) notice_shown: bool,
    pub(crate) day: String,
    pub(crate) sent_today: u32,
}

impl Default for TelemetryState {
    fn default() -> Self {
        Self { install_id: String::new(), errors_enabled: true, usage_enabled: false, notice_shown: false,
               day: String::new(), sent_today: 0 }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub(crate) struct QueuedReport {
    pub(crate) kind: String,
    pub(crate) message: String,
    pub(crate) detail: String,
    pub(crate) version: String,
    pub(crate) os: String,
    pub(crate) at: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct TelemetrySettings {
    pub(crate) errors_enabled: bool,
    pub(crate) usage_enabled: bool,
}

fn state_dir() -> Option<PathBuf> {
    let id = if cfg!(debug_assertions) { format!("{}.dev", data_dirs::BUNDLE_ID) } else { data_dirs::BUNDLE_ID.to_string() };
    std::env::var_os("HOME").map(|home| PathBuf::from(home).join("Library/Application Support").join(id))
}

fn random_id() -> String {
    use std::io::Read;
    let mut bytes = [0u8; 16];
    if std::fs::File::open("/dev/urandom").and_then(|mut file| file.read_exact(&mut bytes)).is_err() {
        let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
        bytes.copy_from_slice(&nanos.to_le_bytes());
    }
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub(crate) fn load_state_from(dir: &Path) -> TelemetryState {
    let mut state: TelemetryState = read_to_string(dir.join("telemetry.json")).ok()
        .and_then(|raw| serde_json::from_str(&raw).ok()).unwrap_or_default();
    if state.install_id.len() < 16 {
        state.install_id = random_id();
        let _ = save_state_to(dir, &state);
    }
    state
}

pub(crate) fn save_state_to(dir: &Path, state: &TelemetryState) -> std::io::Result<()> {
    create_dir_all(dir)?;
    let tmp = dir.join("telemetry.json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(state).unwrap_or_default())?;
    rename(tmp, dir.join("telemetry.json"))
}

fn today(now: SystemTime) -> String {
    // UTC 日期就够：只用来数「今天发了几条」
    let days = now.duration_since(UNIX_EPOCH).map(|d| d.as_secs() / 86_400).unwrap_or(0) as i64;
    let (y, m, d) = civil_from_days(days);
    format!("{y:04}-{m:02}-{d:02}")
}

fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (yoe as i64 + era * 400 + i64::from(m <= 2), m, d)
}

fn os_label() -> String {
    let version = read_to_string("/System/Library/CoreServices/SystemVersion.plist").ok().and_then(|plist| {
        let key = plist.find("<key>ProductVersion</key>")?;
        let rest = &plist[key..];
        let start = rest.find("<string>")? + 8;
        let end = rest[start..].find("</string>")? + start;
        Some(rest[start..end].to_string())
    });
    format!("macOS {} {}", version.unwrap_or_default(), std::env::consts::ARCH).trim().to_string()
}

/// 合并同一个错误时去掉会变的部分（数字、地址），否则端口号、pid 不同就当成不同错误。
pub(crate) fn fingerprint(kind: &str, message: &str) -> String {
    let normalized: String = message.chars().map(|c| if c.is_ascii_digit() { '#' } else { c }).collect();
    format!("{kind}\n{normalized}")
}

fn clip(text: &str, limit: usize) -> String {
    let clean = redact_secrets(text);
    clean.chars().take(limit).collect()
}

/// 决定一条错误要不要入队：开着错误报告、这次运行没发过同一个、今天还没到上限。纯逻辑，单独测。
pub(crate) fn admit(state: &mut TelemetryState, seen: &mut HashSet<String>, kind: &str, message: &str,
                    now: SystemTime) -> bool {
    if !state.errors_enabled {
        return false;
    }
    let day = today(now);
    if state.day != day {
        state.day = day;
        state.sent_today = 0;
    }
    // 先看上限再记「发过了」：到上限被拦下的错误第二天还要能发
    if state.sent_today >= MAX_REPORTS_PER_DAY || !seen.insert(fingerprint(kind, message)) {
        return false;
    }
    state.sent_today += 1;
    true
}

pub(crate) fn enqueue_to(dir: &Path, report: &QueuedReport) -> std::io::Result<()> {
    create_dir_all(dir)?;
    let path = dir.join("telemetry-queue.jsonl");
    let mut lines: Vec<String> = read_to_string(&path).unwrap_or_default().lines().map(str::to_owned).collect();
    lines.push(serde_json::to_string(report).unwrap_or_default());
    let keep = lines.len().saturating_sub(MAX_QUEUE);
    std::fs::write(&path, lines[keep..].join("\n") + "\n")
}

/// 记一条错误：写本机诊断日志（不论开没开错误报告），开着就入队并尽快发出去。任何失败都吞掉，绝不影响 App。
pub(crate) fn report_error(kind: &str, message: &str, detail: &str) {
    if cfg!(test) {
        return;
    }
    let Some(dir) = state_dir() else { return };
    let Ok(_guard) = LOCK.lock() else { return };
    let mut state = load_state_from(&dir);
    let admitted = {
        let Ok(mut seen) = SEEN.lock() else { return };
        admit(&mut state, seen.get_or_insert_with(HashSet::new), kind, message, SystemTime::now())
    };
    if !admitted {
        return;
    }
    let report = QueuedReport {
        kind: kind.chars().take(40).collect(),
        message: clip(message, MAX_MESSAGE),
        detail: clip(detail, MAX_DETAIL),
        version: env!("CARGO_PKG_VERSION").to_string(),
        os: os_label(),
        at: format!("{:?}", SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)),
    };
    let _ = save_state_to(&dir, &state);
    if enqueue_to(&dir, &report).is_ok() {
        if let Some(app) = APP.get() {
            let app = app.clone();
            tauri::async_runtime::spawn(async move { flush(app).await });
        }
    }
}

fn http_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .user_agent(format!("VortoOne-Desktop/{}", env!("CARGO_PKG_VERSION")))
        .build()
        .map_err(|error| error.to_string())
}

/// 发出队列里的报告：成功或被服务端判为不合格就丢掉；其它情况留到下次（队列最多 MAX_QUEUE 条）。
pub(crate) async fn flush(app: AppHandle) {
    let Some(dir) = state_dir() else { return };
    let (queue, install_id, enabled) = {
        let Ok(_guard) = LOCK.lock() else { return };
        let state = load_state_from(&dir);
        (read_to_string(dir.join("telemetry-queue.jsonl")).unwrap_or_default(), state.install_id, state.errors_enabled)
    };
    let reports: Vec<QueuedReport> = queue.lines().filter_map(|line| serde_json::from_str(line).ok()).collect();
    if reports.is_empty() {
        return;
    }
    if !enabled {                        // 用户关掉了：队列里还没发的也不发了
        let _ = std::fs::remove_file(dir.join("telemetry-queue.jsonl"));
        return;
    }
    let (Ok(base), Ok(client)) = (account::default_base_url(), http_client()) else { return };
    let mut sent = 0usize;
    for report in &reports {
        let body = serde_json::json!({"install_id": install_id, "version": report.version, "os": report.os,
                                      "kind": report.kind, "message": report.message, "detail": report.detail});
        match client.post(format!("{base}/api/telemetry/error")).json(&body).send().await {
            // 成功，或服务端明确说这条不合格（400/413/422）就丢掉；404（账号服务还没上这个接口）、429、5xx、断网都留着下次再发
            Ok(response) if response.status().is_success() || matches!(response.status().as_u16(), 400 | 413 | 422) => sent += 1,
            _ => break,
        }
    }
    if sent == 0 {
        return;
    }
    let Ok(_guard) = LOCK.lock() else { return };
    // 发送期间可能又入队了新的：只去掉已经发出的那几条
    let now: Vec<String> = read_to_string(dir.join("telemetry-queue.jsonl")).unwrap_or_default()
        .lines().map(str::to_owned).collect();
    let rest = now.get(sent..).map(|lines| lines.join("\n")).unwrap_or_default();
    let _ = if rest.trim().is_empty() {
        std::fs::remove_file(dir.join("telemetry-queue.jsonl"))
    } else {
        std::fs::write(dir.join("telemetry-queue.jsonl"), rest + "\n")
    };
    diag("info", "telemetry.sent", &format!("{sent} 条错误报告"));
    drop(app);
}

/// 启动时：记住 AppHandle、装崩溃记录、补发上次没发出去的；第一次（或升级到带错误报告的版本后第一次）告诉用户。
pub(crate) fn start(app: &AppHandle) {
    let _ = APP.set(app.clone());
    install_panic_hook();
    let handle = app.clone();
    tauri::async_runtime::spawn(async move { flush(handle).await });
    let Some(dir) = state_dir() else { return };
    let show = {
        let Ok(_guard) = LOCK.lock() else { return };
        let mut state = load_state_from(&dir);
        let show = state.errors_enabled && !state.notice_shown;
        if show {
            state.notice_shown = true;
            let _ = save_state_to(&dir, &state);
        }
        show
    };
    if show {
        let handle = app.clone();
        tauri::async_runtime::spawn(async move {
            tokio::time::sleep(Duration::from_secs(4)).await;    // 等主窗口出来再说
            show_notice(handle);
        });
    }
}

fn show_notice(app: AppHandle) {
    use tauri::Emitter;
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    let prompt = "VortoOne 出错时（崩溃、更新失败、内置引擎起不来）会把错误报告发给开发者，帮助尽快修好。\n\n\
                  报告只有错误说明、App 和系统版本，不含对话、文件内容和账号信息。随时可以在「设置 → 高级」关闭。";
    let dialog_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let open_settings = dialog_app.dialog().message(prompt).title("错误报告").kind(MessageDialogKind::Info)
            .buttons(MessageDialogButtons::OkCancelCustom("知道了".into(), "去设置".into())).blocking_show();
        if !open_settings {
            let _ = dialog_app.emit(NOTICE_EVENT, "open-settings");
        }
    });
}

fn install_panic_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let message = info.payload().downcast_ref::<&str>().map(|text| text.to_string())
            .or_else(|| info.payload().downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "（无说明）".into());
        let location = info.location().map(|place| format!("{}:{}", place.file(), place.line())).unwrap_or_default();
        diag("error", "app.panic", &format!("{message} @ {location}"));
        // 崩溃处理里不联网：report_error 只会入队并尝试起一个发送任务，起不来就留到下次启动
        report_error("app.panic", &message, &location);
        previous(info);
    }));
}

/// 内置引擎意外退出时，从它的日志尾部挑出错误行（Traceback、Error、Exception）作说明，最多 2KB；
/// 不带普通输出行，免得把任务内容带出去。
pub(crate) fn engine_error_lines(log_tail: &str) -> String {
    let lines: Vec<&str> = log_tail.lines().filter(|line| {
        line.contains("Traceback") || line.contains("Error") || line.contains("Exception") || line.trim_start().starts_with("File \"")
    }).collect();
    let start = lines.len().saturating_sub(20);
    let joined = lines[start..].join("\n");
    let skip = joined.len().saturating_sub(2048);
    joined.char_indices().find(|(index, _)| *index >= skip).map(|(index, _)| joined[index..].to_string()).unwrap_or_default()
}

pub(crate) fn report_engine_exit(runtime_id: &str, exit: &str) {
    let tail = state_dir().and_then(|_| {
        let app = APP.get()?;
        let path = app.path().app_config_dir().ok()?.join("logs").join(format!("runtime-{runtime_id}.log"));
        let bytes = read(path).ok()?;
        let start = bytes.len().saturating_sub(16 * 1024);
        Some(String::from_utf8_lossy(&bytes[start..]).to_string())
    }).unwrap_or_default();
    diag("error", "engine.exited", &format!("runtime 已退出（{exit}）"));
    report_error("engine.exited", &format!("内置引擎意外退出（{exit}）"), &engine_error_lines(&tail));
}

#[tauri::command]
pub(crate) fn telemetry_settings() -> TelemetrySettings {
    let state = state_dir().map(|dir| load_state_from(&dir)).unwrap_or_default();
    TelemetrySettings { errors_enabled: state.errors_enabled, usage_enabled: state.usage_enabled }
}

#[tauri::command]
pub(crate) fn set_telemetry_settings(errors_enabled: bool, usage_enabled: bool) -> Result<TelemetrySettings, String> {
    let dir = state_dir().ok_or("找不到 App 数据目录")?;
    let _guard = LOCK.lock().map_err(|_| "设置正忙，请重试".to_string())?;
    let mut state = load_state_from(&dir);
    state.errors_enabled = errors_enabled;
    state.usage_enabled = usage_enabled;
    state.notice_shown = true;
    save_state_to(&dir, &state).map_err(|error| format!("保存失败：{error}"))?;
    if !errors_enabled {
        let _ = std::fs::remove_file(dir.join("telemetry-queue.jsonl"));
    }
    diag("info", "telemetry.settings", &format!("错误报告 {}，使用统计 {}", if errors_enabled { "开" } else { "关" },
                                                 if usage_enabled { "开" } else { "关" }));
    Ok(TelemetrySettings { errors_enabled, usage_enabled })
}

/// 界面里没人接住的报错：前端 diagnostics.ts 调过来，和原生错误走同一条路。
#[tauri::command]
pub(crate) fn report_ui_error(kind: String, message: String) {
    let kind = if kind == "ui.unhandled_rejection" { "ui.unhandled_rejection" } else { "ui.error" };
    report_error(kind, &message, "");
}

/// 反馈附带的诊断日志：取文件最后 MAX_FEEDBACK_LOG 字节，从整行开始，再脱敏一遍（日志本来就脱敏过）。
pub(crate) fn feedback_log_excerpt(bytes: &[u8]) -> String {
    let start = bytes.len().saturating_sub(MAX_FEEDBACK_LOG);
    let text = String::from_utf8_lossy(&bytes[start..]).to_string();
    let text = if start > 0 { text.split_once('\n').map(|(_, rest)| rest.to_string()).unwrap_or(text) } else { text };
    text.lines().map(redact_secrets).collect::<Vec<_>>().join("\n")
}

const MAX_FEEDBACK_TEXT: usize = 4000;
const MAX_FEEDBACK_LOG: usize = 60 * 1024;

#[derive(Debug, Deserialize)]
struct FeedbackReply { #[serde(default)] id: Option<u64> }

/// 「问题反馈」：用户写的话 + 可选联系方式 + 可选诊断日志，发到账号服务；登录了就带上令牌关联账号。
#[tauri::command]
pub(crate) async fn send_feedback(app: AppHandle, account_state: State<'_, account::AccountState>, text: String,
                                  contact: String, attach_log: bool) -> Result<u64, String> {
    let text: String = text.trim().chars().take(MAX_FEEDBACK_TEXT).collect();
    if text.is_empty() {
        return Err("先写一句遇到了什么问题".into());
    }
    let log = if attach_log {
        diagnostics_log_path().and_then(|path| read(path).ok()).map(|bytes| feedback_log_excerpt(&bytes)).unwrap_or_default()
    } else {
        String::new()
    };
    let install_id = state_dir().map(|dir| load_state_from(&dir).install_id).unwrap_or_default();
    let body = serde_json::json!({"install_id": install_id, "version": env!("CARGO_PKG_VERSION"), "os": os_label(),
                                  "text": text, "contact": contact.trim().chars().take(200).collect::<String>(), "log": log});
    let base = account::default_base_url()?;
    let client = http_client()?;
    let mut request = client.post(format!("{base}/api/telemetry/feedback")).json(&body);
    if let Some(token) = account::access_token_if_signed_in(&app, &account_state).await {
        request = request.bearer_auth(token);
    }
    let response = request.send().await.map_err(|error| {
        diag("warn", "feedback.send_failed", &error.to_string());
        "发送失败：连不上反馈服务，请检查网络后重试，或改用邮件发送".to_string()
    })?;
    let status = response.status();
    diag(if status.is_success() { "info" } else { "warn" }, "feedback.sent", &format!("HTTP {}，{}", status.as_u16(),
                                                                                       if attach_log { "附日志" } else { "不附日志" }));
    match status.as_u16() {
        200..=299 => Ok(response.json::<FeedbackReply>().await.ok().and_then(|reply| reply.id).unwrap_or(0)),
        404 => Err("反馈服务还没开通，请先改用邮件发送".into()),
        429 => Err("发得有点频繁，请过一会儿再发".into()),
        400 | 413 | 422 => Err("内容太长或格式不对，删减一些再试".into()),
        _ => Err(format!("发送失败（HTTP {}），请稍后重试或改用邮件发送", status.as_u16())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("vc-telemetry-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn defaults_send_errors_but_not_usage_and_keep_one_install_id() {
        let dir = temp_dir("defaults");
        let first = load_state_from(&dir);
        assert!(first.errors_enabled && !first.usage_enabled && !first.notice_shown);
        assert_eq!(first.install_id.len(), 32);
        assert_eq!(load_state_from(&dir).install_id, first.install_id);     // 存下来了，下次还是同一个
    }

    #[test]
    fn admits_each_error_once_per_run_and_at_most_twenty_a_day() {
        let mut state = TelemetryState::default();
        let mut seen = HashSet::new();
        let monday = UNIX_EPOCH + Duration::from_secs(20_000 * 86_400);
        assert!(admit(&mut state, &mut seen, "engine.exited", "内置引擎意外退出（exit 1，端口 54926）", monday));
        // 只差数字的同一个错误不再发
        assert!(!admit(&mut state, &mut seen, "engine.exited", "内置引擎意外退出（exit 1，端口 61001）", monday));
        for index in 0..30 {
            admit(&mut state, &mut seen, "ui.error", &format!("错误 {}", "x".repeat(index + 1)), monday);
        }
        assert_eq!(state.sent_today, MAX_REPORTS_PER_DAY);
        assert!(!admit(&mut state, &mut seen, "app.panic", "又一个", monday));
        // 第二天重新计数
        assert!(admit(&mut state, &mut seen, "app.panic", "又一个", monday + Duration::from_secs(86_400)));
        assert_eq!(state.sent_today, 1);
        // 用户关掉后一条都不收
        state.errors_enabled = false;
        assert!(!admit(&mut state, &mut seen, "app.panic", "关掉以后", monday + Duration::from_secs(86_400)));
    }

    #[test]
    fn queue_keeps_only_the_newest_reports() {
        let dir = temp_dir("queue");
        for index in 0..(MAX_QUEUE + 5) {
            let report = QueuedReport { kind: "ui.error".into(), message: format!("第 {index} 条"), detail: String::new(),
                                        version: "0.2.58".into(), os: "macOS".into(), at: String::new() };
            enqueue_to(&dir, &report).unwrap();
        }
        let lines: Vec<String> = read_to_string(dir.join("telemetry-queue.jsonl")).unwrap().lines().map(str::to_owned).collect();
        assert_eq!(lines.len(), MAX_QUEUE);
        assert!(lines[0].contains("第 5 条") && lines.last().unwrap().contains(&format!("第 {} 条", MAX_QUEUE + 4)));
    }

    #[test]
    fn engine_exit_detail_keeps_error_lines_only() {
        let log = "  ⏰ cron 已启动\n正在处理：帮我写周报（这是任务内容）\nTraceback (most recent call last):\n  File \"src/web/server.py\", line 25, in _lifespan\nzlib.error: Error -3 while decompressing data\n";
        let detail = engine_error_lines(log);
        assert!(detail.contains("Traceback") && detail.contains("zlib.error") && detail.contains("server.py"));
        assert!(!detail.contains("周报") && !detail.contains("cron"));
        let long = "ValueError: boom\n".repeat(400);
        assert!(engine_error_lines(&long).len() <= 2048);
    }

    #[test]
    fn feedback_log_starts_on_a_whole_line_and_hides_secrets_again() {
        let mut log = String::new();
        for index in 0..5000 { log.push_str(&format!("2026-11-09T00:00:00Z info  connect.ok 第 {index} 行\n")); }
        log.push_str("2026-11-09T00:00:01Z warn  remote.http_failed Bearer abcdefghijklmnop\n");
        let excerpt = feedback_log_excerpt(log.as_bytes());
        assert!(excerpt.len() <= MAX_FEEDBACK_LOG);
        assert!(excerpt.starts_with("2026-11-09T00:00:00Z info"));        // 不从半行开始
        assert!(excerpt.contains("第 4999 行") && !excerpt.contains("abcdefghijklmnop"));
        assert_eq!(feedback_log_excerpt(b"short\nlog"), "short\nlog");
    }

    #[test]
    fn utc_day_is_a_calendar_date() {
        assert_eq!(today(UNIX_EPOCH), "1970-01-01");
        assert_eq!(today(UNIX_EPOCH + Duration::from_secs(1_794_206_639)), "2026-11-09");
    }
}
