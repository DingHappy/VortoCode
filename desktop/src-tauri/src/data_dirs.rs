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

#[cfg(test)]
mod tests {
    use super::*;

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
