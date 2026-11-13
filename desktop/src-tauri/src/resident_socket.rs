//! Stable short IPC address shared with the bundled resident launcher.
//! User data remains in Application Support; only the socket lives in /tmp.
#[cfg(unix)]
use sha2::{Digest, Sha256};
#[cfg(unix)]
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};

#[cfg(unix)]
fn directory_name(root: &Path, uid: u32) -> String {
    let mut digest = Sha256::new();
    digest.update(b"vortoone-local-resident-socket-v1\0");
    digest.update(uid.to_string().as_bytes());
    digest.update(b"\0");
    digest.update(root.as_os_str().as_bytes());
    let identity = format!("{:x}", digest.finalize());
    format!("vortoone-resident-{uid}-{}", &identity[..16])
}

#[cfg(unix)]
pub(crate) fn socket_path(root: &Path, uid: u32) -> Result<PathBuf, String> {
    if !root.is_absolute() {
        return Err("本机常驻资料目录必须是绝对路径".into());
    }
    let root = root
        .canonicalize()
        .map_err(|_| "无法核对本机常驻资料目录")?;
    // Do not inherit an arbitrary TMPDIR: the launcher must derive this address
    // again after login or reboot, independently of Desktop's environment.
    let temporary = Path::new("/tmp")
        .canonicalize()
        .map_err(|_| "无法定位本机私有连接目录")?;
    let path = temporary
        .join(directory_name(&root, uid))
        .join("service.sock");
    if path.as_os_str().as_bytes().len() > 103 {
        return Err("本机常驻私有连接路径超过系统限制".into());
    }
    Ok(path)
}

#[cfg(not(unix))]
pub(crate) fn socket_path(_root: &Path, _uid: u32) -> Result<PathBuf, String> {
    Err("当前平台不支持私有常驻连接".into())
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn resident_socket_identity_matches_launcher_protocol() {
        assert_eq!(
            directory_name(Path::new("/fixture/application-data/local-resident"), 501),
            "vortoone-resident-501-e8689798d38e6f28"
        );
        assert_ne!(
            directory_name(Path::new("/fixture/a"), 501),
            directory_name(Path::new("/fixture/b"), 501)
        );
        assert_ne!(
            directory_name(Path::new("/fixture/a"), 501),
            directory_name(Path::new("/fixture/a"), 502)
        );
    }

    #[test]
    fn resident_socket_stays_short_for_long_data_paths() {
        let base = std::env::temp_dir().join(format!("vorto-short-socket-{}", std::process::id()));
        let root = base.join("long-data-directory-".repeat(8));
        std::fs::create_dir_all(&root).unwrap();
        let path = socket_path(&root, 501).unwrap();
        assert!(root.join("service.sock").as_os_str().as_bytes().len() > 103);
        assert!(path.as_os_str().as_bytes().len() <= 103);
        assert!(!path.starts_with(&root));
        assert_eq!(socket_path(&root, 501).unwrap(), path);
        std::fs::remove_dir_all(base).unwrap();
    }
}
