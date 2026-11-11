//! The webview supplies structured staff identifiers, never an arbitrary signing payload.
use serde::Serialize;
use sha2::{Digest, Sha256};

#[derive(Serialize, serde::Deserialize)]
pub(crate) struct StaffKeyProof {
    public_key: String,
    signature: Option<String>,
}

fn hex(value: &str, length: usize) -> Result<(), String> {
    if value.len() != length || !value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        return Err("批准请求标识无效".into());
    }
    Ok(())
}

fn approval_message(instance: &str, public: &str, proposal: Option<&str>, digest: Option<&str>, nonce: Option<&str>) -> Result<String, String> {
    hex(instance, 32)?;
    hex(public, 130)?;
    if !public.starts_with("04") { return Err("批准钥匙公钥无效".into()); }
    match (proposal, digest, nonce) {
        (None, None, None) => Ok(format!("vortoone-staff-key-v1\n{instance}\n{public}")),
        (Some(id), Some(digest), Some(nonce)) => {
            let suffix = id.strip_prefix("staff-proposal-").ok_or("提议标识无效")?;
            hex(suffix, 32)?; hex(digest, 64)?; hex(nonce, 32)?;
            let key_id = format!("{:x}", Sha256::digest(serde_json::to_vec(public).map_err(|_| "公钥编码无效")?));
            Ok(format!("vortoone-staff-approval-v1\n{instance}\n{key_id}\n{id}\n{digest}\n{nonce}"))
        }
        _ => Err("批准请求字段不完整".into()),
    }
}

#[cfg(target_os = "macos")]
fn native(tag: &str, message: &str) -> Result<StaffKeyProof, String> {
    use std::ffi::{CStr, CString};
    unsafe extern "C" {
        fn vorto_staff_approval(tag: *const std::ffi::c_char, message: *const std::ffi::c_char) -> *mut std::ffi::c_char;
    }
    let tag = CString::new(tag).map_err(|_| "钥匙标识无效")?;
    let message = CString::new(message).map_err(|_| "签名请求无效")?;
    let ptr = unsafe { vorto_staff_approval(tag.as_ptr(), message.as_ptr()) };
    if ptr.is_null() { return Err("批准钥匙不可用".into()); }
    let text = unsafe { CStr::from_ptr(ptr).to_string_lossy().into_owned() };
    unsafe { libc::free(ptr.cast()); }
    let value: serde_json::Value = serde_json::from_str(&text).map_err(|_| "批准钥匙回执无效")?;
    if let Some(error) = value.get("error").and_then(|v| v.as_str()) { return Err(error.to_owned()); }
    serde_json::from_value(value).map_err(|_| "批准钥匙回执缺失".into())
}

#[cfg(not(target_os = "macos"))]
fn native(_tag: &str, _message: &str) -> Result<StaffKeyProof, String> {
    Err("此平台尚未接入 Desktop 批准钥匙，提议保持待批".into())
}

#[tauri::command]
pub(crate) async fn staff_approval_sign(instance: String, proposal_id: Option<String>, digest: Option<String>, nonce: Option<String>) -> Result<StaffKeyProof, String> {
    hex(&instance, 32)?;
    // Validate the full structure before touching Keychain or asking for biometry.
    approval_message(&instance, &format!("04{}", "0".repeat(128)), proposal_id.as_deref(), digest.as_deref(), nonce.as_deref())?;
    tauri::async_runtime::spawn_blocking(move || {
        let tag = format!("{}:{instance}", crate::keychain_service("com.vortoone.desktop.staff-approval"));
        let key = native(&tag, "")?;
        let message = approval_message(&instance, &key.public_key, proposal_id.as_deref(), digest.as_deref(), nonce.as_deref())?;
        let proof = native(&tag, &message)?;
        if proof.public_key != key.public_key || proof.signature.is_none() { return Err("批准钥匙已变化，未批准".into()); }
        Ok(proof)
    }).await.map_err(|_| "批准钥匙操作未完成".to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn namespace_and_full_request_are_bound() {
        let instance = "a".repeat(32); let public = format!("04{}", "b".repeat(128));
        let id = format!("staff-proposal-{}", "c".repeat(32)); let digest = "d".repeat(64); let nonce = "e".repeat(32);
        let enrollment = approval_message(&instance, &public, None, None, None).unwrap();
        let approve = approval_message(&instance, &public, Some(&id), Some(&digest), Some(&nonce)).unwrap();
        assert_ne!(enrollment, approve);
        assert!(approve.contains(&id) && approve.contains(&digest) && approve.ends_with(&nonce));
        assert_ne!(approve, approval_message(&"f".repeat(32), &public, Some(&id), Some(&digest), Some(&nonce)).unwrap());
    }
    #[test]
    fn malformed_or_incomplete_requests_are_refused_before_native_calls() {
        let instance = "a".repeat(32); let public = format!("04{}", "b".repeat(128));
        assert!(approval_message(&instance, &public, Some("other-action"), None, None).is_err());
        assert!(approval_message("bad\ninstance", &public, None, None, None).is_err());
        assert!(approval_message(&instance, "private key", None, None, None).is_err());
    }
}
