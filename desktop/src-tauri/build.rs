use sha2::{Digest, Sha256};
use std::os::unix::ffi::OsStrExt;
use std::path::Path;

fn main() {
    // 开发回落（打开的正好是构建这个 App 的源码树时直接跑源码）要精确比对那棵源码树，
    // 但二进制里不该留下构建机的绝对路径（含用户名）：只编进它的 sha256。
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let digest = root
        .canonicalize()
        .map(|path| format!("{:x}", Sha256::digest(path.as_os_str().as_bytes())))
        .unwrap_or_default();
    println!("cargo:rustc-env=VORTOCODE_SOURCE_ROOT_SHA256={digest}");
    tauri_build::build()
}
