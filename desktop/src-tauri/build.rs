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
    println!("cargo:rustc-env=VORTOONE_SOURCE_ROOT_SHA256={digest}");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        use std::process::Command;
        println!("cargo:rerun-if-changed=native/staff_approval.m");
        let out = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("OUT_DIR"));
        let object = out.join("staff_approval.o");
        let arch = match std::env::var("CARGO_CFG_TARGET_ARCH").as_deref() {
            Ok("aarch64") => "arm64", Ok("x86_64") => "x86_64", _ => panic!("unsupported macOS architecture"),
        };
        let built = Command::new("xcrun").args(["--sdk", "macosx", "clang", "-fobjc-arc", "-arch", arch,
            "-mmacosx-version-min=11.0", "-c", "native/staff_approval.m", "-o"])
            .arg(&object).status().expect("compile Desktop approval key bridge");
        assert!(built.success(), "Desktop approval key bridge did not compile");
        println!("cargo:rustc-link-arg={}", object.display());
        for framework in ["Foundation", "Security", "LocalAuthentication"] {
            println!("cargo:rustc-link-lib=framework={framework}");
        }
    }
    tauri_build::build()
}
