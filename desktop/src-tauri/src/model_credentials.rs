//! Model secrets stay native. The existing profile file owns references and
//! bounded cleanup receipts; it remains the only configuration transaction.
use super::*;

const SERVICE: &str = "com.vortocode.desktop.models";
const MAX_PENDING: usize = 32;
const MAX_PROFILE_BYTES: u64 = 8 * 1024 * 1024;
const STORE_ERROR: &str = "系统凭证存储不可用，请解锁钥匙串后重试；原配置未被新的凭证替换";

pub(crate) trait SecretStore {
    fn read(&self, reference: &str) -> Result<Option<String>, String>;
    fn write(&self, reference: &str, value: &str) -> Result<(), String>;
    fn delete(&self, reference: &str) -> Result<(), String>;
}

struct SystemSecrets;

#[cfg(not(test))]
impl SecretStore for SystemSecrets {
    fn read(&self, reference: &str) -> Result<Option<String>, String> {
        platform_keychain::read(keychain_service(SERVICE).as_bytes(), reference.as_bytes())
            .map_err(|_| STORE_ERROR.to_string())
    }
    fn write(&self, reference: &str, value: &str) -> Result<(), String> {
        platform_keychain::write(
            keychain_service(SERVICE).as_bytes(),
            reference.as_bytes(),
            value,
        )
        .map_err(|_| STORE_ERROR.to_string())
    }
    fn delete(&self, reference: &str) -> Result<(), String> {
        platform_keychain::delete(keychain_service(SERVICE).as_bytes(), reference.as_bytes())
            .map_err(|_| STORE_ERROR.to_string())
    }
}

// All native unit tests, including old profile tests, use synthetic memory only.
// They must never create/read an item in the user's login Keychain.
#[cfg(test)]
impl SecretStore for SystemSecrets {
    fn read(&self, reference: &str) -> Result<Option<String>, String> {
        Ok(test_secrets().lock().unwrap().get(reference).cloned())
    }
    fn write(&self, reference: &str, value: &str) -> Result<(), String> {
        test_secrets()
            .lock()
            .unwrap()
            .insert(reference.into(), value.into());
        Ok(())
    }
    fn delete(&self, reference: &str) -> Result<(), String> {
        test_secrets().lock().unwrap().remove(reference);
        Ok(())
    }
}

#[cfg(test)]
fn test_secrets() -> &'static Mutex<HashMap<String, String>> {
    static ITEMS: std::sync::OnceLock<Mutex<HashMap<String, String>>> = std::sync::OnceLock::new();
    ITEMS.get_or_init(Default::default)
}

#[derive(Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum StorageMode {
    Legacy,
    Keychain,
    Empty,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Storage {
    version: u8,
    mode: StorageMode,
    reference: Option<String>,
    pending_cleanup: Vec<String>,
}

#[derive(Deserialize)]
struct StorageProbe {
    #[serde(rename = "credentialStorage")]
    storage: Option<Storage>,
}

#[derive(Clone)]
struct Document {
    profile: Option<DesktopLlmProfile>,
    storage: Storage,
}

#[derive(PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BoundKey {
    base_url: String,
    key: String,
}

#[derive(PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Secrets {
    version: u8,
    default: Option<BoundKey>,
    providers: HashMap<String, BoundKey>,
}

impl Secrets {
    fn from_profile(profile: &DesktopLlmProfile) -> Self {
        Self {
            version: 1,
            default: (!profile.api_key.is_empty()).then(|| BoundKey {
                base_url: profile.base_url.clone(),
                key: profile.api_key.clone(),
            }),
            providers: profile
                .providers
                .iter()
                .filter(|provider| !provider.api_key.is_empty())
                .map(|provider| {
                    (
                        provider.id.clone(),
                        BoundKey {
                            base_url: provider.base_url.clone(),
                            key: provider.api_key.clone(),
                        },
                    )
                })
                .collect(),
        }
    }
    fn empty(&self) -> bool {
        self.default.is_none() && self.providers.is_empty()
    }
}

fn scope(path: &Path) -> Result<String, String> {
    let parent = path.parent().ok_or("模型配置路径无效")?;
    let parent = parent.canonicalize().map_err(|_| "模型配置目录不可读")?;
    let target = parent.join(path.file_name().ok_or("模型配置路径无效")?);
    Ok(format!(
        "profile-{:x}-",
        Sha256::digest(target.as_os_str().as_encoded_bytes())
    ))
}

fn valid_reference(path: &Path, reference: &str) -> Result<(), String> {
    let prefix = scope(path)?;
    let tail = reference
        .strip_prefix(&prefix)
        .ok_or("模型凭证引用不属于此配置")?;
    if tail.is_empty()
        || tail.len() > 96
        || !tail.bytes().all(|c| c.is_ascii_hexdigit() || c == b'-')
    {
        return Err("模型凭证引用无效".into());
    }
    Ok(())
}

fn read_document(path: &Path) -> Result<Document, String> {
    let info = match std::fs::symlink_metadata(path) {
        Ok(info) => info,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            return Ok(Document {
                profile: None,
                storage: Storage {
                    version: 1,
                    mode: StorageMode::Empty,
                    reference: None,
                    pending_cleanup: vec![],
                },
            })
        }
        Err(_) => return Err("模型配置不可读".into()),
    };
    if !info.is_file() || info.len() > MAX_PROFILE_BYTES {
        return Err("模型配置文件身份或容量无效".into());
    }
    let payload = read_to_string(path).map_err(|_| "模型配置不可读")?;
    let probe: StorageProbe = serde_json::from_str(&payload)
        .map_err(|_| format!("模型配置文件格式有误：{}", path.display()))?;
    let raw: serde_json::Value = serde_json::from_str(&payload).map_err(|_| "模型配置格式无效")?;
    if probe.storage.is_none() && raw.get("credentialStorage").is_some() {
        return Err("模型凭证存储元数据无效".into());
    }
    let storage = probe.storage.unwrap_or(Storage {
        version: 1,
        mode: StorageMode::Legacy,
        reference: None,
        pending_cleanup: vec![],
    });
    if storage.version != 1 || storage.pending_cleanup.len() > MAX_PENDING {
        return Err("模型凭证存储版本或清理容量无效".into());
    }
    let mut seen = std::collections::HashSet::new();
    for reference in &storage.pending_cleanup {
        valid_reference(path, reference)?;
        if !seen.insert(reference) || storage.reference.as_ref() == Some(reference) {
            return Err("模型凭证清理引用冲突".into());
        }
    }
    if let Some(reference) = &storage.reference {
        valid_reference(path, reference)?;
    }
    if storage.mode != StorageMode::Keychain && storage.reference.is_some() {
        return Err("模型凭证存储模式与引用不一致".into());
    }
    let profile = if storage.mode == StorageMode::Empty {
        if raw.as_object().map_or(true, |object| object.len() != 1) {
            return Err("空配置不能同时包含模型字段".into());
        }
        None
    } else {
        let profile: DesktopLlmProfile = serde_json::from_str(&payload)
            .map_err(|_| format!("模型配置文件格式有误：{}", path.display()))?;
        if storage.mode == StorageMode::Keychain && !Secrets::from_profile(&profile).empty() {
            return Err("系统存储配置不应包含明文 Key".into());
        }
        Some(profile)
    };
    Ok(Document { profile, storage })
}

fn write_document(path: &Path, doc: &Document) -> Result<(), String> {
    #[cfg(test)]
    tests::fail_write_if_requested(path)?;
    let mut value = match &doc.profile {
        Some(profile) => serde_json::to_value(profile).map_err(|_| "无法序列化模型配置")?,
        None => serde_json::json!({}),
    };
    value.as_object_mut().ok_or("模型配置格式无效")?.insert(
        "credentialStorage".into(),
        serde_json::to_value(&doc.storage).map_err(|_| "无法序列化凭证存储状态")?,
    );
    save_json_atomic_with_mode(path, "llm-profile", &value, Some(0o600))
}

fn hydrate(doc: &Document, store: &dyn SecretStore) -> Result<Option<DesktopLlmProfile>, String> {
    let Some(mut profile) = doc.profile.clone() else {
        return Ok(None);
    };
    if let Some(reference) = &doc.storage.reference {
        let raw = store
            .read(reference)
            .map_err(|_| STORE_ERROR)?
            .ok_or("模型凭证缺失，请恢复对应钥匙串条目；不会回退到其他 Key")?;
        let secrets: Secrets = serde_json::from_str(&raw).map_err(|_| "模型凭证内容损坏")?;
        if secrets.version != 1 || secrets.providers.len() > MAX_CUSTOM_PROVIDERS {
            return Err("模型凭证版本或容量无效".into());
        }
        if let Some(key) = secrets.default {
            if key.base_url != profile.base_url || key.key.is_empty() {
                return Err("默认模型凭证与端点不匹配".into());
            }
            profile.api_key = key.key;
        }
        for (id, key) in secrets.providers {
            let provider = profile
                .providers
                .iter_mut()
                .find(|p| p.id == id)
                .ok_or("模型凭证与供应商身份不匹配")?;
            if provider.base_url != key.base_url || key.key.is_empty() {
                return Err("供应商凭证与端点不匹配".into());
            }
            provider.api_key = key.key;
        }
    }
    normalize_llm_profile(profile).map(Some)
}

fn cleanup(path: &Path, doc: &mut Document, store: &dyn SecretStore) {
    let before = doc.storage.pending_cleanup.clone();
    doc.storage
        .pending_cleanup
        .retain(|reference| store.delete(reference).is_err());
    if doc.storage.pending_cleanup != before && write_document(path, doc).is_err() {
        // The durable receipt still includes already-deleted IDs. Deletion is
        // idempotent; a later explicit maintenance operation can retry it.
        doc.storage.pending_cleanup = before;
    }
}

fn rollback(path: &Path, staged: &mut Document, store: &dyn SecretStore, reference: &str) {
    if store.delete(reference).is_ok() {
        staged
            .storage
            .pending_cleanup
            .retain(|item| item != reference);
        let _ = write_document(path, staged); // Receipt is retained if this write fails.
    }
}

fn save_with(
    path: &Path,
    profile: &DesktopLlmProfile,
    store: &dyn SecretStore,
    migrate: bool,
) -> Result<(), String> {
    let mut old = read_document(path)?;
    // Existing plaintext is changed only by its original save operations until
    // the owner explicitly selects migration. Reads/automatic refresh never migrate.
    if old.storage.mode == StorageMode::Legacy && !migrate {
        old.profile = Some(profile.clone());
        return write_document(path, &old);
    }
    create_dir_all(path.parent().ok_or("模型配置路径无效")?).map_err(|_| "无法创建模型配置目录")?;
    let secrets = Secrets::from_profile(profile);
    let previous = hydrate(&old, store)?;
    let reuse = old.storage.mode == StorageMode::Keychain
        && previous
            .as_ref()
            .is_some_and(|saved| Secrets::from_profile(saved) == secrets);
    let mut fresh = None;
    if !secrets.empty() && !reuse {
        if old.storage.pending_cleanup.len() >= MAX_PENDING {
            return Err("旧凭证清理记录已满，请先重试清理".into());
        }
        let reference = format!("{}{}", scope(path)?, new_profile_revision());
        old.storage.pending_cleanup.push(reference.clone());
        write_document(path, &old)?; // Write-ahead cleanup receipt, before any new secret.
        let raw = serde_json::to_string(&secrets).map_err(|_| "无法保存模型凭证")?;
        let verified = store.write(&reference, &raw).is_ok()
            && store
                .read(&reference)
                .ok()
                .flatten()
                .and_then(|value| serde_json::from_str::<Secrets>(&value).ok())
                .is_some_and(|saved| saved == secrets);
        if !verified {
            rollback(path, &mut old, store, &reference);
            return Err(STORE_ERROR.into());
        }
        fresh = Some(reference);
    }
    let reference = if secrets.empty() {
        None
    } else if reuse {
        old.storage.reference.clone()
    } else {
        fresh.clone()
    };
    let mut clean = profile.clone();
    clean.api_key.clear();
    for provider in &mut clean.providers {
        provider.api_key.clear();
    }
    let mut next = Document {
        profile: Some(clean),
        storage: Storage {
            version: 1,
            mode: StorageMode::Keychain,
            reference,
            pending_cleanup: old.storage.pending_cleanup.clone(),
        },
    };
    if let Some(reference) = &fresh {
        next.storage
            .pending_cleanup
            .retain(|item| item != reference);
    }
    if let Some(reference) = &old.storage.reference {
        if next.storage.reference.as_ref() != Some(reference) {
            next.storage.pending_cleanup.push(reference.clone());
        }
    }
    if next.storage.pending_cleanup.len() > MAX_PENDING {
        if let Some(reference) = &fresh {
            rollback(path, &mut old, store, reference);
        }
        return Err("旧凭证清理记录已满，请先重试清理".into());
    }
    if let Err(error) = write_document(path, &next) {
        if let Some(reference) = &fresh {
            rollback(path, &mut old, store, reference);
        }
        return Err(error);
    }
    cleanup(path, &mut next, store);
    Ok(())
}

fn clear_with(path: &Path, store: &dyn SecretStore) -> Result<(), String> {
    let mut doc = read_document(path)?;
    if !path.exists() {
        return Ok(());
    }
    if let Some(reference) = doc.storage.reference.take() {
        doc.storage.pending_cleanup.push(reference);
    }
    if doc.storage.pending_cleanup.len() > MAX_PENDING {
        return Err("旧凭证清理记录已满，请先重试清理".into());
    }
    doc.profile = None;
    doc.storage.mode = StorageMode::Empty;
    write_document(path, &doc)?; // Clear ownership first; failed cleanup stays retryable.
    cleanup(path, &mut doc, store);
    if doc.storage.pending_cleanup.is_empty() {
        let _ = remove_file(path);
    }
    Ok(())
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CredentialStorageStatus {
    version: u8,
    pub(crate) mode: &'static str,
    pending_cleanup: usize,
}

pub(crate) fn status(path: &Path) -> Result<CredentialStorageStatus, String> {
    let doc = read_document(path)?;
    Ok(CredentialStorageStatus {
        version: 1,
        mode: match doc.storage.mode {
            StorageMode::Legacy => "legacy",
            StorageMode::Keychain => "keychain",
            StorageMode::Empty => "none",
        },
        pending_cleanup: doc.storage.pending_cleanup.len(),
    })
}

pub(crate) fn load(path: &Path) -> Result<Option<DesktopLlmProfile>, String> {
    hydrate(&read_document(path)?, &SystemSecrets)
}
pub(crate) fn save(path: &Path, profile: &DesktopLlmProfile, migrate: bool) -> Result<(), String> {
    save_with(path, profile, &SystemSecrets, migrate)
}
pub(crate) fn clear(path: &Path) -> Result<(), String> {
    clear_with(path, &SystemSecrets)
}
pub(crate) fn retry_cleanup(path: &Path) -> Result<(), String> {
    let mut doc = read_document(path)?;
    cleanup(path, &mut doc, &SystemSecrets);
    Ok(())
}

#[tauri::command(async)]
pub(crate) fn get_model_credential_storage(
    app: AppHandle,
) -> Result<CredentialStorageStatus, String> {
    status(&llm_profile_path(&app)?) // Metadata only; never reads or writes the Keychain.
}

#[tauri::command]
pub(crate) async fn update_model_credential_storage(
    app: AppHandle,
    action: String,
    expected_revision: Option<String>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<CredentialStorageStatus, ModelProfileMutationError> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    let path = llm_profile_path(&app)?;
    let snapshot = cached_profile_snapshot(&app, &store)?;
    snapshot.check_expected(expected_revision.as_deref())?;
    let current = status(&path)?;
    let (migrate, message) = match action.as_str() {
        "migrate" if current.mode == "legacy" => (true,
            "将本机模型配置中的 API Key 迁入系统钥匙串。先保存并核验凭证，再用引用替换配置中的明文；模型、地址和引擎版本保持不变。不会同步到云端。旧备份中的明文不在此次迁移范围。"),
        "cleanup" if current.pending_cleanup > 0 => (false,
            "重试删除本机钥匙串里已不被当前配置使用的旧凭证副本。不会删除当前引用，也不会吊销供应商的 API Key。"),
        _ => return Err("凭证存储状态已变化，请刷新后重试".into()),
    };
    let dialog_app = app.clone();
    let confirmed = tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&dialog_app, dialog_app.dialog().message(message))
            .title("确认更改模型凭证存储")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancel)
            .blocking_show()
    })
    .await
    .map_err(|_| "无法完成原生确认")?;
    if !confirmed {
        return Err("已取消，未更改凭证存储".into());
    }
    store.maintain_credentials(&path, &snapshot.revision, migrate)?;
    status(&path).map_err(Into::into)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    thread_local! { static WRITE_FAILURE: RefCell<Option<(PathBuf, usize, usize)>> = const { RefCell::new(None) }; }
    pub(super) fn fail_write_if_requested(path: &Path) -> Result<(), String> {
        WRITE_FAILURE.with(|state| {
            if let Some((target, fail_at, count)) = state.borrow_mut().as_mut() {
                if target == path {
                    *count += 1;
                    if count == fail_at {
                        return Err("synthetic atomic write failure".into());
                    }
                }
            }
            Ok(())
        })
    }
    struct WriteFailure;
    impl Drop for WriteFailure {
        fn drop(&mut self) {
            WRITE_FAILURE.with(|state| *state.borrow_mut() = None);
        }
    }

    #[derive(Default)]
    struct Memory {
        items: RefCell<HashMap<String, String>>,
        writes: Cell<usize>,
        reads: Cell<usize>,
        deletes: Cell<usize>,
        fail_write: Cell<bool>,
        fail_read: Cell<bool>,
        fail_delete: Cell<bool>,
        corrupt_read: Cell<bool>,
    }
    impl SecretStore for Memory {
        fn read(&self, reference: &str) -> Result<Option<String>, String> {
            self.reads.set(self.reads.get() + 1);
            if self.fail_read.get() {
                return Err("synthetic-private-secret-error".into());
            }
            if self.corrupt_read.get() {
                return Ok(Some("{}".into()));
            }
            Ok(self.items.borrow().get(reference).cloned())
        }
        fn write(&self, reference: &str, value: &str) -> Result<(), String> {
            self.writes.set(self.writes.get() + 1);
            self.items
                .borrow_mut()
                .insert(reference.into(), value.into());
            if self.fail_write.get() {
                return Err("synthetic-private-secret-error".into());
            }
            Ok(())
        }
        fn delete(&self, reference: &str) -> Result<(), String> {
            self.deletes.set(self.deletes.get() + 1);
            if self.fail_delete.get() {
                return Err("synthetic-private-secret-error".into());
            }
            self.items.borrow_mut().remove(reference);
            Ok(())
        }
    }
    struct Fixture {
        root: PathBuf,
        path: PathBuf,
        profile: DesktopLlmProfile,
        store: Memory,
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }
    fn fixture() -> Fixture {
        let root =
            std::env::temp_dir().join(format!("vorto-model-secrets-{}", new_profile_revision()));
        create_dir_all(&root).unwrap();
        let mut profile = default_llm_profile();
        profile.base_url = "https://default.example.test/v1".into();
        profile.api_key = "synthetic-main-private".into();
        profile.providers.push(DesktopLlmProvider {
            id: "reviewer".into(),
            name: "Reviewer".into(),
            base_url: "https://review.example.test/v1".into(),
            api_key: "synthetic-provider-private".into(),
            models: vec![],
        });
        Fixture {
            path: root.join("llm-profile.json"),
            root,
            profile: normalize_llm_profile(profile).unwrap(),
            store: Memory::default(),
        }
    }
    fn seed_legacy(f: &Fixture) {
        std::fs::write(&f.path, serde_json::to_vec(&f.profile).unwrap()).unwrap();
    }
    fn loaded(f: &Fixture) -> DesktopLlmProfile {
        hydrate(&read_document(&f.path).unwrap(), &f.store)
            .unwrap()
            .unwrap()
    }

    #[test]
    fn legacy_reads_and_metadata_never_access_or_migrate_secrets() {
        let f = fixture();
        seed_legacy(&f);
        let before = read(&f.path).unwrap();
        assert!(loaded(&f) == f.profile);
        assert_eq!(status(&f.path).unwrap().mode, "legacy");
        assert_eq!(
            f.store.reads.get() + f.store.writes.get() + f.store.deletes.get(),
            0
        );
        assert_eq!(read(&f.path).unwrap(), before);
    }

    #[test]
    fn legacy_automatic_save_preserves_format_until_explicit_migration() {
        let f = fixture();
        seed_legacy(&f);
        let mut next = f.profile.clone();
        next.model = "another-model".into();
        save_with(&f.path, &next, &f.store, false).unwrap();
        assert_eq!(f.store.writes.get(), 0);
        assert!(read_to_string(&f.path)
            .unwrap()
            .contains("synthetic-main-private"));
        save_with(&f.path, &next, &f.store, true).unwrap();
        assert_eq!(status(&f.path).unwrap().mode, "keychain");
        assert!(!read_to_string(&f.path).unwrap().contains("synthetic-"));
        assert!(loaded(&f) == next);
    }

    #[test]
    fn new_profiles_round_trip_multiple_bound_keys_without_plaintext_on_disk() {
        let f = fixture();
        save_with(&f.path, &f.profile, &f.store, false).unwrap();
        assert!(loaded(&f) == f.profile);
        let raw = read_to_string(&f.path).unwrap();
        assert!(
            !raw.contains("synthetic-main-private") && !raw.contains("synthetic-provider-private")
        );
        assert_eq!(f.store.items.borrow().len(), 1);
        let before = raw;
        save_with(&f.path, &f.profile, &f.store, false).unwrap();
        assert_eq!(read_to_string(&f.path).unwrap(), before);
        assert_eq!(f.store.writes.get(), 1); // Same credentials reuse the existing reference.
        assert!(keychain_service_for(SERVICE, true) != keychain_service_for(SERVICE, false));
        assert_ne!(profile_file_for(true), profile_file_for(false));
    }

    #[test]
    fn partial_secret_write_failure_keeps_legacy_and_masks_backend_errors() {
        let f = fixture();
        seed_legacy(&f);
        f.store.fail_write.set(true);
        let error = save_with(&f.path, &f.profile, &f.store, true).unwrap_err();
        assert!(!error.contains("synthetic-private-secret-error"));
        assert!(loaded(&f) == f.profile && f.store.items.borrow().is_empty());
        assert_eq!(status(&f.path).unwrap().mode, "legacy");
    }

    #[test]
    fn failed_verification_retains_cleanup_receipt_until_explicit_retry() {
        let f = fixture();
        seed_legacy(&f);
        f.store.corrupt_read.set(true);
        f.store.fail_delete.set(true);
        assert!(save_with(&f.path, &f.profile, &f.store, true).is_err());
        assert_eq!(status(&f.path).unwrap().pending_cleanup, 1);
        assert_eq!(status(&f.path).unwrap().mode, "legacy");
        f.store.corrupt_read.set(false);
        f.store.fail_delete.set(false);
        let mut reopened = read_document(&f.path).unwrap();
        cleanup(&f.path, &mut reopened, &f.store);
        assert_eq!(status(&f.path).unwrap().pending_cleanup, 0);
        assert!(loaded(&f) == f.profile && f.store.items.borrow().is_empty());
    }

    #[test]
    fn failed_reference_switch_does_not_remove_the_active_old_secret() {
        let f = fixture();
        save_with(&f.path, &f.profile, &f.store, false).unwrap();
        let old = read_document(&f.path).unwrap().storage.reference.unwrap();
        let mut changed = f.profile.clone();
        changed.api_key = "synthetic-rotated-private".into();
        WRITE_FAILURE.with(|state| *state.borrow_mut() = Some((f.path.clone(), 2, 0)));
        let _failure = WriteFailure;
        assert!(save_with(&f.path, &changed, &f.store, false).is_err());
        assert!(loaded(&f) == f.profile);
        assert!(f.store.items.borrow().contains_key(&old));
        assert_eq!(f.store.items.borrow().len(), 1);
    }

    #[test]
    fn cleanup_failure_after_commit_preserves_new_key_and_retryable_old_reference() {
        let f = fixture();
        save_with(&f.path, &f.profile, &f.store, false).unwrap();
        let old = read_document(&f.path).unwrap().storage.reference.unwrap();
        let mut changed = f.profile.clone();
        changed.api_key = "synthetic-rotated-private".into();
        f.store.fail_delete.set(true);
        save_with(&f.path, &changed, &f.store, false).unwrap();
        assert!(loaded(&f) == changed);
        assert_eq!(status(&f.path).unwrap().pending_cleanup, 1);
        let mut reopened = read_document(&f.path).unwrap();
        let active = reopened.storage.reference.clone().unwrap();
        f.store.fail_delete.set(false);
        cleanup(&f.path, &mut reopened, &f.store);
        assert!(!f.store.items.borrow().contains_key(&old));
        assert!(f.store.items.borrow().contains_key(&active));
        assert_eq!(status(&f.path).unwrap().pending_cleanup, 0);
    }

    #[test]
    fn clear_stops_using_credentials_even_when_keychain_is_locked_and_cleanup_can_retry() {
        let f = fixture();
        save_with(&f.path, &f.profile, &f.store, false).unwrap();
        f.store.fail_read.set(true);
        f.store.fail_delete.set(true);
        clear_with(&f.path, &f.store).unwrap();
        let mut reopened = read_document(&f.path).unwrap();
        assert!(hydrate(&reopened, &f.store).unwrap().is_none());
        assert_eq!(status(&f.path).unwrap().pending_cleanup, 1);
        f.store.fail_delete.set(false);
        cleanup(&f.path, &mut reopened, &f.store);
        assert_eq!(status(&f.path).unwrap().pending_cleanup, 0);
        assert!(f.store.items.borrow().is_empty());
    }

    #[test]
    fn keyless_local_profile_does_not_need_a_working_keychain() {
        let f = fixture();
        let mut profile = f.profile.clone();
        profile.base_url = "http://127.0.0.1:11500/v1".into();
        profile.api_key.clear();
        profile.providers.clear();
        f.store.fail_read.set(true);
        f.store.fail_write.set(true);
        f.store.fail_delete.set(true);
        save_with(&f.path, &profile, &f.store, false).unwrap();
        assert!(loaded(&f) == profile);
        assert_eq!(
            f.store.reads.get() + f.store.writes.get() + f.store.deletes.get(),
            0
        );
    }

    #[test]
    fn changing_default_or_provider_endpoint_never_reuses_the_bound_key() {
        for provider in [false, true] {
            let f = fixture();
            save_with(&f.path, &f.profile, &f.store, false).unwrap();
            let mut doc = read_document(&f.path).unwrap();
            if provider {
                doc.profile.as_mut().unwrap().providers[0].base_url =
                    "https://other.example.test/v1".into();
            } else {
                doc.profile.as_mut().unwrap().base_url = "https://other.example.test/v1".into();
            }
            write_document(&f.path, &doc).unwrap();
            assert!(hydrate(&read_document(&f.path).unwrap(), &f.store).is_err());
        }
    }

    #[test]
    fn copied_references_wrong_versions_and_active_cleanup_conflicts_fail_closed() {
        let f = fixture();
        save_with(&f.path, &f.profile, &f.store, false).unwrap();
        let copy = f.root.join("another-profile.json");
        std::fs::copy(&f.path, &copy).unwrap();
        assert!(read_document(&copy).is_err());
        let original = read_to_string(&f.path).unwrap();
        for bad in ["version", "null", "active", "empty", "plaintext"] {
            let mut raw: serde_json::Value = serde_json::from_str(&original).unwrap();
            match bad {
                "version" => raw["credentialStorage"]["version"] = 2.into(),
                "null" => raw["credentialStorage"] = serde_json::Value::Null,
                "active" => {
                    raw["credentialStorage"]["pendingCleanup"] =
                        serde_json::json!([raw["credentialStorage"]["reference"]])
                }
                "empty" => {
                    raw["credentialStorage"]["mode"] = "empty".into();
                    raw["credentialStorage"]["reference"] = serde_json::Value::Null;
                }
                _ => raw["apiKey"] = "synthetic-inline-private".into(),
            }
            std::fs::write(&f.path, serde_json::to_vec(&raw).unwrap()).unwrap();
            assert!(read_document(&f.path).is_err(), "{bad}");
        }
    }

    #[test]
    fn full_cleanup_receipts_are_not_evicted_to_admit_a_new_secret() {
        let f = fixture();
        seed_legacy(&f);
        let mut doc = read_document(&f.path).unwrap();
        doc.storage.pending_cleanup = (0..MAX_PENDING)
            .map(|n| format!("{}a-{n:x}", scope(&f.path).unwrap()))
            .collect();
        write_document(&f.path, &doc).unwrap();
        let before = read(&f.path).unwrap();
        assert!(save_with(&f.path, &f.profile, &f.store, true).is_err());
        assert_eq!(read(&f.path).unwrap(), before);
        assert_eq!(f.store.writes.get(), 0);
    }
}
