//! Read back receipts from Desktop-owned processes only. Never probe a caller URL.
use super::*;

#[derive(Clone, Deserialize)]
struct ModelReceipt {
    schema_version: u32,
    revision: String,
    launch_id: String,
}

#[derive(Deserialize)]
struct QuickHealth {
    status: String,
    model_configuration: Option<ModelReceipt>,
}

fn runtime_is_running(inner: &mut GatewayProcessInner) -> bool {
    // Do not consume the supervisor's crash transition by clearing `child` here.
    // Its normal observation path must still persist the recovery record.
    inner
        .child
        .as_mut()
        .is_some_and(|child| child.try_wait().is_ok_and(|exit| exit.is_none()))
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ModelRuntimeApplication {
    runtime_id: String,
    scope: Option<String>,
    status: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ModelConfigurationState {
    schema_version: u32,
    saved_revision: String,
    runtimes: Vec<ModelRuntimeApplication>,
}

fn receipt_state(
    healthy: bool,
    receipt: Option<&ModelReceipt>,
    launch: Option<&str>,
    launched_revision: Option<&str>,
    saved: &str,
) -> &'static str {
    let Some(receipt) = receipt else {
        return "unknown";
    };
    if !healthy
        || receipt.schema_version != 1
        || Some(receipt.launch_id.as_str()) != launch
        || Some(receipt.revision.as_str()) != launched_revision
    {
        return "unknown";
    }
    if receipt.revision == saved {
        "applied"
    } else {
        "pending"
    }
}

#[tauri::command]
pub(crate) async fn get_model_configuration_state(
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
    state: State<'_, GatewayProcess>,
) -> Result<ModelConfigurationState, String> {
    // No mutex is held across network I/O.
    let targets = {
        let mut supervisor = state.0.lock().map_err(|_| "runtime 状态锁已损坏")?;
        supervisor
            .runtimes
            .iter_mut()
            .filter_map(|(id, inner)| {
                if !runtime_is_running(inner) {
                    return None;
                }
                Some((id.clone(), inner.base_url.clone()))
            })
            .collect::<Vec<_>>()
    };
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(2))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "无法核对本机引擎配置")?;
    let reads = targets.into_iter().map(|(id, base)| {
        let client = client.clone();
        async move {
            let health = match base {
                Some(base) => match client.get(format!("{base}/api/health/quick")).send().await {
                    Ok(response) if response.status().is_success() => {
                        response.json::<QuickHealth>().await.ok()
                    }
                    _ => None,
                },
                None => None,
            };
            (id, health)
        }
    });
    let reads = futures_util::future::join_all(reads)
        .await
        .into_iter()
        .collect::<HashMap<_, _>>();
    // Read the saved revision after probes, then recheck launch identity so a late
    // reply from an exited/replaced process cannot certify its replacement.
    let saved = cached_profile_snapshot(&app, &store)?.revision;
    let mut supervisor = state.0.lock().map_err(|_| "runtime 状态锁已损坏")?;
    let mut runtimes = Vec::new();
    for (id, current) in supervisor.runtimes.iter_mut() {
        if !runtime_is_running(current) {
            continue;
        }
        let health = reads.get(id).and_then(|health| health.as_ref());
        let status = receipt_state(
            health.is_some_and(|h| h.status == "healthy"),
            health.and_then(|h| h.model_configuration.as_ref()),
            current.configuration_launch_id.as_deref(),
            current.configuration_revision.as_deref(),
            &saved,
        );
        runtimes.push(ModelRuntimeApplication {
            runtime_id: id.clone(),
            scope: current.scope.clone(),
            status,
        });
    }
    runtimes.sort_by(|a, b| a.runtime_id.cmp(&b.runtime_id));
    Ok(ModelConfigurationState {
        schema_version: 1,
        saved_revision: saved,
        runtimes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn receipt_check_preserves_the_supervisors_crash_transition() {
        let mut child = Command::new("/bin/sh")
            .args(["-c", "exit 0"])
            .spawn()
            .unwrap();
        child.wait().unwrap();
        let mut inner = GatewayProcessInner {
            child: Some(child),
            ..Default::default()
        };
        assert!(!runtime_is_running(&mut inner));
        assert!(inner.child.is_some());
        assert!(!process_status(&mut inner).running);
        assert!(inner.child.is_none());
    }

    #[test]
    fn receipt_requires_health_launch_identity_and_exact_revision() {
        let mut receipt = ModelReceipt {
            schema_version: 1,
            revision: "old".into(),
            launch_id: "launch".into(),
        };
        let check = |healthy, r: Option<&ModelReceipt>, launch, revision, saved| {
            receipt_state(healthy, r, launch, revision, saved)
        };
        assert_eq!(
            check(true, Some(&receipt), Some("launch"), Some("old"), "old"),
            "applied"
        );
        assert_eq!(
            check(true, Some(&receipt), Some("launch"), Some("old"), "new"),
            "pending"
        );
        assert_eq!(
            check(false, Some(&receipt), Some("launch"), Some("old"), "old"),
            "unknown"
        );
        assert_eq!(
            check(true, None, Some("launch"), Some("old"), "old"),
            "unknown"
        );
        assert_eq!(
            check(
                true,
                Some(&receipt),
                Some("replacement"),
                Some("old"),
                "old"
            ),
            "unknown"
        );
        assert_eq!(
            check(
                true,
                Some(&receipt),
                Some("launch"),
                Some("different"),
                "old"
            ),
            "unknown"
        );
        receipt.schema_version = 2;
        assert_eq!(
            check(true, Some(&receipt), Some("launch"), Some("old"), "old"),
            "unknown"
        );
    }
}
