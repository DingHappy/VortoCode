//! Model configuration transactions. Dialogs/network probes run without the lock;
//! only a still-current snapshot may persist after they finish.
use super::*;
use std::sync::atomic::{AtomicU64, Ordering};

const CONFLICT: &str =
    "模型配置已被其他操作更新；本次没有写入。请重新读取最新配置后再保存，当前草稿仍保留";

/// Stable command-boundary errors; internal persistence flows retain their existing String errors.
#[derive(Serialize)]
pub(crate) struct ModelProfileMutationError {
    code: &'static str,
    message: String,
}

impl From<String> for ModelProfileMutationError {
    fn from(message: String) -> Self {
        Self {
            code: if message == CONFLICT {
                "model_configuration_conflict"
            } else {
                "model_configuration_error"
            },
            message,
        }
    }
}

impl From<&str> for ModelProfileMutationError {
    fn from(message: &str) -> Self {
        message.to_string().into()
    }
}

pub(crate) fn new_profile_revision() -> String {
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    // An opaque identity, never a hash of credentials or a security token.
    format!(
        "{nanos:x}-{:x}-{:x}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    )
}

#[derive(Clone)]
pub(crate) struct ProfileSnapshot {
    pub revision: String,
    pub profile: Option<DesktopLlmProfile>,
}

impl ProfileSnapshot {
    pub fn check_expected(&self, expected: Option<&str>) -> Result<(), String> {
        if expected.is_some_and(|value| value != self.revision) {
            return Err(CONFLICT.into());
        }
        Ok(())
    }
}

#[derive(Default)]
struct CachedDesktopLlmProfile {
    loaded: bool,
    revision: String,
    profile: Option<DesktopLlmProfile>,
    // Exact normalized disk baseline, including credentials, stays native-only.
    disk_profile: Option<DesktopLlmProfile>,
}

#[derive(Default)]
pub(crate) struct DesktopLlmProfileStore(Mutex<CachedDesktopLlmProfile>);

impl DesktopLlmProfileStore {
    pub fn snapshot(
        &self,
        load: impl FnOnce() -> Result<Option<DesktopLlmProfile>, String>,
    ) -> Result<ProfileSnapshot, String> {
        let mut cached = self.0.lock().map_err(|_| "模型配置缓存锁已损坏")?;
        if !cached.loaded {
            let profile = load()?;
            cached.revision = profile
                .as_ref()
                .and_then(|p| p.revision.clone())
                .unwrap_or_else(new_profile_revision);
            cached.disk_profile = profile.clone();
            cached.profile = profile.map(|mut p| {
                p.revision = Some(cached.revision.clone());
                p
            });
            cached.loaded = true;
        }
        Ok(ProfileSnapshot {
            revision: cached.revision.clone(),
            profile: cached.profile.clone(),
        })
    }

    pub fn get_or_try_init(
        &self,
        load: impl FnOnce() -> Result<Option<DesktopLlmProfile>, String>,
    ) -> Result<Option<DesktopLlmProfile>, String> {
        Ok(self.snapshot(load)?.profile)
    }

    pub fn commit(
        &self,
        path: &Path,
        expected: &str,
        mut next: Option<DesktopLlmProfile>,
    ) -> Result<ProfileSnapshot, String> {
        let mut cached = self.0.lock().map_err(|_| "模型配置缓存锁已损坏")?;
        if !cached.loaded || cached.revision != expected {
            return Err(CONFLICT.into());
        }
        // Detect edits made outside this process while confirmation/probing was pending.
        // This is not a cross-process filesystem lock; Desktop is single-instance.
        if read_llm_profile_file(path)? != cached.disk_profile {
            cached.loaded = false;
            return Err(CONFLICT.into());
        }
        next = next.map(normalize_llm_profile).transpose()?;
        if let Some(profile) = next.as_mut() {
            profile.revision = Some(cached.revision.clone());
        }
        // Check CAS and the disk baseline before recognizing an unchanged write.
        // Credentials remain part of the native-only equality comparison.
        if next == cached.profile {
            // Migrate a legacy file once, retaining the revision already supplied
            // to running engines. Reading alone never rewrites that file.
            if next != cached.disk_profile {
                if let Some(profile) = next.as_ref() {
                    save_llm_profile_file(path, profile)?;
                }
                cached.disk_profile = next;
            }
            return Ok(ProfileSnapshot {
                revision: cached.revision.clone(),
                profile: cached.profile.clone(),
            });
        }
        let revision = new_profile_revision();
        if let Some(profile) = next.as_mut() {
            profile.revision = Some(revision.clone());
            save_llm_profile_file(path, profile)?;
        } else {
            delete_llm_profile_file(path)?;
        }
        // Disk failure leaves the cache and revision unchanged; no false save receipt.
        cached.revision = revision.clone();
        cached.disk_profile = next.clone();
        cached.profile = next.clone();
        Ok(ProfileSnapshot {
            revision,
            profile: next,
        })
    }

    #[cfg(test)]
    pub fn replace(&self, profile: Option<DesktopLlmProfile>) -> Result<(), String> {
        let mut cached = self.0.lock().map_err(|_| "模型配置缓存锁已损坏")?;
        cached.loaded = false;
        drop(cached);
        self.snapshot(|| Ok(profile)).map(|_| ())
    }
}

pub(crate) fn cached_profile_snapshot(
    app: &AppHandle,
    store: &DesktopLlmProfileStore,
) -> Result<ProfileSnapshot, String> {
    let path = llm_profile_path(app)?;
    store.snapshot(|| read_llm_profile_file(&path))
}

pub(crate) fn check_profile_revision(
    profile: &DesktopLlmProfile,
    expected: Option<&str>,
) -> Result<(), String> {
    if expected.is_some_and(|value| Some(value) != profile.revision.as_deref()) {
        return Err(CONFLICT.into());
    }
    Ok(())
}

pub(crate) fn profile_snapshot_status(
    snapshot: &ProfileSnapshot,
    app: &AppHandle,
) -> DesktopLlmProfileStatus {
    let mut status = with_config_path(desktop_llm_profile_status(snapshot.profile.as_ref()), app);
    status.revision = Some(snapshot.revision.clone());
    status
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_errors_have_a_stable_code_and_preserve_other_failures() {
        let conflict = serde_json::to_value(ModelProfileMutationError::from(CONFLICT)).unwrap();
        assert_eq!(conflict["code"], "model_configuration_conflict");
        assert_eq!(conflict["message"], CONFLICT);
        let failed = serde_json::to_value(ModelProfileMutationError::from("磁盘不可写")).unwrap();
        assert_eq!(failed["code"], "model_configuration_error");
        assert_eq!(failed["message"], "磁盘不可写");
    }

    fn fixture(label: &str) -> (PathBuf, DesktopLlmProfileStore, ProfileSnapshot) {
        let path = std::env::temp_dir()
            .join(format!("vorto-profile-{label}-{}", new_profile_revision()))
            .join("profile.json");
        let store = DesktopLlmProfileStore::default();
        let snapshot = store.snapshot(|| read_llm_profile_file(&path)).unwrap();
        (path, store, snapshot)
    }

    #[test]
    fn stale_snapshot_cannot_overwrite_new_credentials_or_clear() {
        let (path, store, initial) = fixture("concurrent");
        let mut first = default_llm_profile();
        first.api_key = "test-key-one".into();
        let saved = store
            .commit(&path, &initial.revision, Some(first.clone()))
            .unwrap();
        let mut next = saved.profile.clone().unwrap();
        next.api_key = "test-key-two".into();
        let changed = store.commit(&path, &saved.revision, Some(next)).unwrap();
        assert_ne!(saved.revision, changed.revision);
        assert!(store
            .commit(&path, &saved.revision, Some(first))
            .err()
            .unwrap()
            .contains("本次没有写入"));
        assert!(store.commit(&path, &saved.revision, None).is_err());
        assert_eq!(
            read_llm_profile_file(&path).unwrap().unwrap().api_key,
            "test-key-two"
        );
        let cleared = store.commit(&path, &changed.revision, None).unwrap();
        assert!(cleared.profile.is_none());
        assert!(!path.exists());
        assert_ne!(cleared.revision, initial.revision);
        assert!(store
            .commit(&path, &initial.revision, Some(default_llm_profile()))
            .is_err());
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn failed_disk_write_does_not_publish_a_new_revision() {
        let (path, store, initial) = fixture("failed-write");
        // A regular file in place of the parent directory makes the write fail.
        std::fs::write(path.parent().unwrap(), "block directory").unwrap();
        assert!(store
            .commit(&path, &initial.revision, Some(default_llm_profile()))
            .is_err());
        let after = store
            .snapshot(|| panic!("cache should remain valid"))
            .unwrap();
        assert_eq!(after.revision, initial.revision);
        assert!(after.profile.is_none());
        std::fs::remove_file(path.parent().unwrap()).unwrap();
    }

    #[test]
    fn unchanged_save_and_repeated_clear_preserve_revision_and_disk_bytes() {
        let (path, store, initial) = fixture("unchanged");
        let empty = store.commit(&path, &initial.revision, None).unwrap();
        assert_eq!(empty.revision, initial.revision);
        assert!(!path.exists());
        let mut profile = default_llm_profile();
        profile.api_key = "test-idempotent-key".into();
        profile
            .role_models
            .insert("research".into(), "default:research-model".into());
        let saved = store
            .commit(&path, &initial.revision, Some(profile))
            .unwrap();
        // Formatting is outside the semantic profile. It must survive a no-op,
        // which also proves that we did not atomically rewrite identical data.
        let payload = format!("{}\n\n", std::fs::read_to_string(&path).unwrap());
        std::fs::write(&path, &payload).unwrap();
        let mut equivalent = saved.profile.clone().unwrap();
        equivalent.base_url.push('/');
        equivalent
            .role_models
            .insert("research".into(), "research-model".into());
        let repeated = store
            .commit(&path, &saved.revision, Some(equivalent))
            .unwrap();
        assert_eq!(repeated.revision, saved.revision);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), payload);
        assert!(store
            .commit(&path, &initial.revision, saved.profile.clone())
            .is_err());
        let cleared = store.commit(&path, &saved.revision, None).unwrap();
        assert_ne!(cleared.revision, saved.revision);
        assert_eq!(
            store
                .commit(&path, &cleared.revision, None)
                .unwrap()
                .revision,
            cleared.revision
        );
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn no_op_does_not_hide_external_edits_or_credential_and_routing_changes() {
        let (path, store, initial) = fixture("meaningful");
        let mut profile = default_llm_profile();
        profile.api_key = "test-original-key".into();
        let mut current = store
            .commit(&path, &initial.revision, Some(profile))
            .unwrap();
        for change in 0..3 {
            let mut next = current.profile.clone().unwrap();
            match change {
                0 => next.api_key = "test-new-key".into(),
                1 => next.base_url = "https://other.example.test/v1".into(),
                _ => {
                    next.role_models
                        .insert("research".into(), "default:other-model".into());
                }
            }
            let changed = store.commit(&path, &current.revision, Some(next)).unwrap();
            assert_ne!(changed.revision, current.revision);
            current = changed;
        }
        let mut external = current.profile.clone().unwrap();
        external.api_key = "test-external-key".into();
        save_llm_profile_file(&path, &external).unwrap();
        assert!(store
            .commit(&path, &current.revision, current.profile.clone())
            .is_err());
        assert_eq!(
            read_llm_profile_file(&path).unwrap().unwrap().api_key,
            "test-external-key"
        );
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn external_edit_invalidates_cache_without_overwriting_and_legacy_migrates_on_save() {
        let (path, store, initial) = fixture("external");
        let mut external = default_llm_profile();
        external.api_key = "external-test-key".into();
        save_llm_profile_file(&path, &external).unwrap();
        assert!(store
            .commit(&path, &initial.revision, Some(default_llm_profile()))
            .is_err());
        let fresh = store.snapshot(|| read_llm_profile_file(&path)).unwrap();
        assert_eq!(fresh.profile.as_ref().unwrap().api_key, "external-test-key");
        assert!(read_llm_profile_file(&path)
            .unwrap()
            .unwrap()
            .revision
            .is_none());
        let saved = store
            .commit(&path, &fresh.revision, fresh.profile.clone())
            .unwrap();
        assert_eq!(saved.revision, fresh.revision);
        let reloaded_store = DesktopLlmProfileStore::default();
        let reloaded = reloaded_store
            .snapshot(|| read_llm_profile_file(&path))
            .unwrap();
        assert_eq!(reloaded.revision, saved.revision);
        // Key-free status exposes only the opaque revision.
        let status =
            serde_json::to_string(&desktop_llm_profile_status(saved.profile.as_ref())).unwrap();
        assert!(!status.contains("external-test-key"));
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn simultaneous_writers_have_exactly_one_winner() {
        let (path, store, initial) = fixture("writers");
        let store = std::sync::Arc::new(store);
        let barrier = std::sync::Arc::new(std::sync::Barrier::new(3));
        let writers = (0..2)
            .map(|_| {
                let (store, barrier, path, revision) = (
                    store.clone(),
                    barrier.clone(),
                    path.clone(),
                    initial.revision.clone(),
                );
                std::thread::spawn(move || {
                    barrier.wait();
                    let mut profile = default_llm_profile();
                    profile.api_key = "test-concurrent-key".into();
                    store.commit(&path, &revision, Some(profile)).is_ok()
                })
            })
            .collect::<Vec<_>>();
        barrier.wait();
        assert_eq!(
            writers
                .into_iter()
                .map(|t| usize::from(t.join().unwrap()))
                .sum::<usize>(),
            1
        );
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }
}
