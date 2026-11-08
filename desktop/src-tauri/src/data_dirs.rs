//! 状态目录 `.vortocode` → `.vortoone`（ADR-018 批次 C）。
//!
//! 搬家只由引擎做（`src/utils/state_dir.py`：整目录改名、原处留链接、git 仓库里有提交的配置就不搬）。
//! Desktop 这边只读位置：新目录在就用新的；还没搬、旧目录是真实目录就用旧的，免得 Desktop 先建出一个空的
//! `.vortoone`、让引擎以为两个都在而不搬。

use std::path::{Path, PathBuf};

pub(crate) fn state_dir(root: &Path) -> PathBuf {
    let new = root.join(".vortoone");
    if new.exists() {
        return new;
    }
    let old = root.join(".vortocode");
    match std::fs::symlink_metadata(&old) {
        Ok(meta) if meta.is_dir() => old,
        _ => new,
    }
}

// ── bundle id `com.vorto.vortocode` → `com.vorto.vortoone`（ADR-018 批次 D）──
// 系统按 bundle id 给 App 分目录：设置、项目登记、模型配置在 Application Support/<id>，
// 网页里的本机偏好（会话 id、主题等 localStorage）在 WebKit/<id>。新版第一次启动、窗口和插件
// 还没碰这些目录前整个改名，旧位置留一个指向新目录的链接（降级回旧版也能读到）。缓存不搬，丢了会重建。

pub(crate) const BUNDLE_ID: &str = "com.vorto.vortoone";
pub(crate) const LEGACY_BUNDLE_ID: &str = "com.vorto.vortocode";
const BUNDLE_DIR_BASES: [&str; 2] = ["Library/Application Support", "Library/WebKit"];

/// 搬一次；返回要写进诊断日志的 (级别, 说明)。旧目录不在、或已经是链接就什么都不做。
pub(crate) fn migrate_bundle_dirs(home: &Path, legacy: &str, current: &str) -> Vec<(&'static str, String)> {
    let mut events = Vec::new();
    for base in BUNDLE_DIR_BASES {
        let old = home.join(base).join(legacy);
        let new = home.join(base).join(current);
        match std::fs::symlink_metadata(&old) {
            Ok(meta) if meta.is_dir() => {}
            _ => continue,
        }
        if std::fs::symlink_metadata(&new).is_ok() {
            events.push(("warn", format!("{base}：{legacy} 和 {current} 都在，用新的，旧的不动")));
            continue;
        }
        if let Err(error) = std::fs::rename(&old, &new) {
            events.push(("error", format!("{base}/{legacy} 搬不过去：{error}")));
            continue;
        }
        #[cfg(unix)]
        if let Err(error) = std::os::unix::fs::symlink(current, &old) {
            events.push(("warn", format!("{base}/{legacy} 已搬，旧位置的链接没建上：{error}")));
            continue;
        }
        events.push(("info", format!("{base}/{legacy} → {current}")));
    }
    events
}

/// App 启动最早调用。调试构建（dev:app、cargo test）的 id 带 `.dev`，只搬开发版自己的目录。
pub(crate) fn migrate_bundle_dirs_on_start() {
    let Some(home) = std::env::var_os("HOME") else { return };
    let suffix = if cfg!(debug_assertions) { ".dev" } else { "" };
    let legacy = format!("{LEGACY_BUNDLE_ID}{suffix}");
    let current = format!("{BUNDLE_ID}{suffix}");
    for (level, detail) in migrate_bundle_dirs(Path::new(&home), &legacy, &current) {
        crate::diag(level, "data.bundle_dirs", &detail);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundle_ids_match_the_tauri_configs() {
        let config = |raw: &str| serde_json::from_str::<serde_json::Value>(raw).unwrap()["identifier"].as_str().unwrap().to_string();
        assert_eq!(config(include_str!("../tauri.conf.json")), BUNDLE_ID);
        assert_eq!(config(include_str!("../tauri.dev.conf.json")), format!("{BUNDLE_ID}.dev"));
    }

    #[test]
    fn bundle_dirs_move_once_and_leave_a_link() {
        let home = scratch("bundle");
        for base in BUNDLE_DIR_BASES {
            std::fs::create_dir_all(home.join(base).join("old.id")).unwrap();
            std::fs::write(home.join(base).join("old.id/projects.json"), "{}").unwrap();
        }
        let events = migrate_bundle_dirs(&home, "old.id", "new.id");
        assert_eq!(events.iter().filter(|(level, _)| *level == "info").count(), 2, "{events:?}");
        for base in BUNDLE_DIR_BASES {
            assert_eq!(std::fs::read_to_string(home.join(base).join("new.id/projects.json")).unwrap(), "{}");
            assert!(std::fs::symlink_metadata(home.join(base).join("old.id")).unwrap().file_type().is_symlink());
            assert_eq!(std::fs::read_to_string(home.join(base).join("old.id/projects.json")).unwrap(), "{}"); // 旧路径照样能读
        }
        assert!(migrate_bundle_dirs(&home, "old.id", "new.id").is_empty());          // 第二次：旧的已是链接
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn bundle_dirs_keep_the_new_one_when_both_exist_and_skip_when_absent() {
        let home = scratch("bundle-both");
        assert!(migrate_bundle_dirs(&home, "old.id", "new.id").is_empty());          // 新装：没有旧目录
        let base = home.join(BUNDLE_DIR_BASES[0]);
        std::fs::create_dir_all(base.join("old.id")).unwrap();
        std::fs::create_dir_all(base.join("new.id")).unwrap();
        std::fs::write(base.join("old.id/keep"), "x").unwrap();
        let events = migrate_bundle_dirs(&home, "old.id", "new.id");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].0, "warn");
        assert!(base.join("old.id/keep").exists() && base.join("new.id").is_dir());
        let _ = std::fs::remove_dir_all(&home);
    }

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("vortoone-data-dirs-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn prefers_the_new_directory_and_reads_an_unmoved_legacy_one() {
        let root = scratch("pick");
        assert_eq!(state_dir(&root), root.join(".vortoone"));          // 都没有：新名字
        std::fs::create_dir(root.join(".vortocode")).unwrap();
        assert_eq!(state_dir(&root), root.join(".vortocode"));         // 引擎还没搬：读旧的
        std::fs::rename(root.join(".vortocode"), root.join(".vortoone")).unwrap();
        std::os::unix::fs::symlink(".vortoone", root.join(".vortocode")).unwrap();
        assert_eq!(state_dir(&root), root.join(".vortoone"));          // 搬过了：新的
        let _ = std::fs::remove_dir_all(&root);
    }
}
