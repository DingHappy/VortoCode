// 本机诊断日志：连接、切换、重连、远程请求失败、界面报错这些关键事件，按行追加到
// ~/Library/Logs/VortoCode/desktop.log，超过 1MB 轮转成 desktop.log.1（只留一份）。
// 2026-10-27：App 被 macOS 静默拦了网络，界面上的报错又被回落提示盖掉，排查只能翻系统日志——
// 这份日志就是为了下次直接看。
// 不记 token、不记对话内容：写入前统一脱敏、截断；前端只传事件名和简短说明。
use super::*;
use std::io::Write;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_LOG_BYTES: u64 = 1024 * 1024;
const MAX_DETAIL_CHARS: usize = 600;
const MAX_EVENT_CHARS: usize = 60;

static WRITE_LOCK: Mutex<()> = Mutex::new(());

pub(crate) fn diagnostics_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(|home| PathBuf::from(home).join("Library/Logs/VortoCode"))
}

pub(crate) fn diagnostics_log_path() -> Option<PathBuf> {
    // 开发构建（dev:app）另写一份，别和装好的 App 混在一起（同 keychain_service 的 .dev 后缀）
    let name = if cfg!(debug_assertions) { "desktop.dev.log" } else { "desktop.log" };
    diagnostics_dir().map(|dir| dir.join(name))
}

/// 像密钥的一律隐去：Bearer 后面的值、sk- 开头的、32 个字符以上的连续 token 样子的串、
/// URL 里 token/key/secret/password 参数的值。宁可多隐去，不能漏。
pub(crate) fn redact_secrets(text: &str) -> String {
    const HIDDEN: &str = "[已隐去]";
    let mut out = Vec::new();
    let mut after_bearer = false;
    for word in text.split(' ') {
        let bare = word.trim_matches(|c: char| "\"'(),;[]{}<>".contains(c));
        let tokenish = bare.len() >= 32
            && bare.chars().all(|c| c.is_ascii_alphanumeric() || "-_+/=.".contains(c));
        if after_bearer || bare.starts_with("sk-") || tokenish {
            out.push(HIDDEN.to_string());
        } else if let Some(index) = ["token=", "key=", "secret=", "password="]
            .iter()
            .filter_map(|marker| word.to_ascii_lowercase().find(marker).map(|at| at + marker.len()))
            .min()
        {
            out.push(format!("{}{HIDDEN}", &word[..index]));
        } else {
            out.push(word.to_string());
        }
        after_bearer = bare.eq_ignore_ascii_case("bearer");
    }
    out.join(" ")
}

/// 截断到 limit 个字符（按字符，不按字节，免得切坏中文）。
fn clip(text: &str, limit: usize) -> String {
    let single_line = text.replace(['\n', '\r'], " ");
    if single_line.chars().count() <= limit {
        single_line
    } else {
        format!("{}…", single_line.chars().take(limit).collect::<String>())
    }
}

/// UTC 时间戳（不为一个时间格式多引一个依赖）。
fn utc_timestamp(now: SystemTime) -> String {
    let secs = now.duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let (days, rem) = (secs / 86_400, secs % 86_400);
    // civil-from-days（Howard Hinnant）
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + if month <= 2 { 1 } else { 0 };
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

pub(crate) fn format_line(now: SystemTime, level: &str, event: &str, detail: &str) -> String {
    let level = match level {
        "error" | "warn" => level,
        _ => "info",
    };
    let event: String = event
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || "._-:".contains(*c))
        .take(MAX_EVENT_CHARS)
        .collect();
    let detail = clip(&redact_secrets(detail), MAX_DETAIL_CHARS);
    format!("{} {level:<5} {} {detail}\n", utc_timestamp(now), if event.is_empty() { "event" } else { &event })
}

fn append_to(path: &Path, line: &str) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    if std::fs::metadata(path).map(|meta| meta.len()).unwrap_or(0) >= MAX_LOG_BYTES {
        let _ = std::fs::rename(path, path.with_extension("log.1"));
    }
    let mut file = std::fs::OpenOptions::new().create(true).append(true).open(path)?;
    file.write_all(line.as_bytes())
}

/// 写一条诊断日志。写不了就算了：诊断日志绝不能影响 App 本身。
pub(crate) fn diag(level: &str, event: &str, detail: &str) {
    let Some(path) = diagnostics_log_path() else { return };
    let line = format_line(SystemTime::now(), level, event, detail);
    if let Ok(_guard) = WRITE_LOCK.lock() {
        let _ = append_to(&path, &line);
    }
}

#[tauri::command]
pub(crate) fn diag_log(level: String, event: String, detail: String) {
    diag(&level, &event, &detail);
}

/// 在 Finder 里打开诊断日志所在位置（没有文件就先写一行，保证能定位到）。
#[tauri::command]
pub(crate) fn reveal_diagnostics_log(app: AppHandle) -> Result<String, String> {
    use tauri_plugin_opener::OpenerExt;
    let path = diagnostics_log_path().ok_or("找不到用户目录")?;
    if !path.exists() {
        diag("info", "diagnostics.opened", "用户打开诊断日志");
    }
    app.opener()
        .reveal_item_in_dir(&path)
        .map_err(|error| format!("没能在 Finder 里打开：{error}"))?;
    Ok(path.display().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn redacts_anything_that_looks_like_a_secret() {
        let text = "Authorization: Bearer abc123 sk-live-xyz https://h/x?token=t0p&ok=1 \
                    0123456789abcdef0123456789abcdef 常驻电脑连不上";
        let clean = redact_secrets(text);
        for leaked in ["abc123", "sk-live-xyz", "t0p", "0123456789abcdef0123456789abcdef"] {
            assert!(!clean.contains(leaked), "{leaked} 漏出来了：{clean}");
        }
        assert!(clean.contains("常驻电脑连不上") && clean.contains("token=[已隐去]"));
    }

    #[test]
    fn formats_one_bounded_line() {
        let now = UNIX_EPOCH + Duration::from_secs(1_793_111_683);
        let line = format_line(now, "weird", "resident unreachable!\n", &format!("原因\n{}", "长".repeat(800)));
        assert!(line.starts_with("2026-10-27T") && line.ends_with('\n'));
        assert_eq!(line.matches('\n').count(), 1);
        assert!(line.contains(" info  residentunreachable "));
        assert!(line.chars().count() < 700);
        assert_eq!(utc_timestamp(UNIX_EPOCH), "1970-01-01T00:00:00Z");
        assert_eq!(utc_timestamp(UNIX_EPOCH + Duration::from_secs(951_782_400)), "2000-02-29T00:00:00Z");
    }

    #[test]
    fn rotates_when_the_log_grows_past_the_limit() {
        let dir = std::env::temp_dir().join(format!("vortocode-diag-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join("desktop.log");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&path, vec![b'x'; MAX_LOG_BYTES as usize]).unwrap();
        append_to(&path, "new\n").unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "new\n");
        assert!(dir.join("desktop.log.1").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
