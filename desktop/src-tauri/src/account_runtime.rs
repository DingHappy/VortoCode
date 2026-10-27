//! Explicit account association for a saved resident host. No token enters the webview.
use super::*;

const PATH: &str = "/api/mobile/account/registration";

#[derive(Clone, PartialEq)]
struct Target {
    id: String,
    name: String,
    base: String,
}

fn target(path: &Path, id: &str) -> Result<Target, String> {
    let base = resolve_remote_base(path, id)?;
    // Account proofs must not travel over unencrypted LAN HTTP.
    if account::account_base_url(Some(&base))
        .map_err(|_| "账号关联需要 HTTPS 或已登记的本机回环隧道，不能使用普通局域网 HTTP")?
        != base
    {
        return Err("常驻电脑地址未规范化".into());
    }
    let registry = load_project_registry(path)?;
    let project = registry
        .projects
        .iter()
        .find(|item| {
            item.id == id && item.kind == PROJECT_KIND_REMOTE && item.repo_root == "@assistant"
        })
        .ok_or("只能关联已登记的常驻助手，不能使用项目运行端")?;
    Ok(Target {
        id: id.into(),
        name: project.name.clone(),
        base,
    })
}

#[derive(Deserialize)]
struct Owner {
    issuer: String,
    subject: String,
}
#[derive(Deserialize)]
struct Registration {
    owner: Owner,
    name: String,
}
#[derive(Deserialize)]
struct Record {
    schema_version: u8,
    instance_id: Option<String>,
    revision: u64,
    registration: Option<Registration>,
    account_issuer: String,
    mobile_entry_url: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct View {
    project_id: String,
    name: String,
    base_url: String,
    instance_id: Option<String>,
    revision: u64,
    registered: bool,
    belongs_to_current: Option<bool>,
    mobile_url: Option<String>,
}

fn valid_id(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn parse_record(body: &[u8]) -> Result<Record, String> {
    let value: Record =
        serde_json::from_slice(body).map_err(|_| "常驻服务尚不支持账号关联，请更新服务后重试")?;
    if value.schema_version != 1
        || value.instance_id.as_ref().is_some_and(|id| !valid_id(id))
        || (value.registration.is_some() && (value.instance_id.is_none() || value.revision == 0))
        || account::account_base_url(Some(&value.account_issuer))? != value.account_issuer
    {
        return Err("常驻助手身份响应无法核验".into());
    }
    Ok(value)
}

fn mobile_url(raw: Option<&str>) -> Option<String> {
    let raw = raw?;
    let url = Url::parse(raw).ok()?;
    if url.path() != "/mobile" || url.query().is_some() || url.fragment().is_some() {
        return None;
    }
    let origin = url.origin().ascii_serialization();
    account::account_base_url(Some(&origin)).ok()?;
    if !url.username().is_empty() || url.password().is_some() {
        return None;
    }
    Some(format!("{origin}/mobile"))
}

fn view(target: &Target, record: Record, proof: Option<&account::RuntimeProof>) -> View {
    let belongs = proof.and_then(|proof| {
        record
            .registration
            .as_ref()
            .map(|item| item.owner.issuer == proof.issuer && item.owner.subject == proof.subject)
    });
    View {
        project_id: target.id.clone(),
        name: record
            .registration
            .as_ref()
            .map(|item| item.name.clone())
            .unwrap_or_else(|| target.name.clone()),
        base_url: target.base.clone(),
        instance_id: record.instance_id,
        revision: record.revision,
        registered: record.registration.is_some(),
        belongs_to_current: belongs,
        mobile_url: mobile_url(record.mobile_entry_url.as_deref()),
    }
}

fn http() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .build()
        .map_err(|_| "无法创建助手连接".to_string())
}

async fn request(
    client: &reqwest::Client,
    target: &Target,
    token: &str,
    method: reqwest::Method,
    body: Option<serde_json::Value>,
) -> Result<Vec<u8>, String> {
    let mut request = client
        .request(method, format!("{}{PATH}", target.base))
        .bearer_auth(token);
    if let Some(body) = body {
        request = request.json(&body);
    }
    let mut response = request
        .send()
        .await
        .map_err(|_| "无法确认账号关联回执，请刷新状态后再操作")?;
    let message = match response.status().as_u16() {
        200 => None,
        401 | 403 => Some("账号或助手授权不匹配，请检查登录与常驻电脑连接"),
        404 | 405 => Some("常驻服务尚不支持账号关联，请更新服务"),
        409 => Some("助手身份或归属已变化，请刷新后重新确认"),
        _ => Some("账号关联服务暂不可用，请稍后刷新"),
    };
    if let Some(message) = message {
        return Err(message.into());
    }
    if !response
        .headers()
        .get("content-type")
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.starts_with("application/json"))
    {
        return Err("助手响应不是账号关联数据".into());
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "无法读取账号关联回执")? {
        if bytes.len() + chunk.len() > 16_384 {
            return Err("助手响应超出限制".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

fn credential(id: &str) -> Result<String, String> {
    remote_token_keychain::read(id)?
        .filter(|value| !value.trim().is_empty())
        .ok_or("未找到常驻助手的访问身份，请重新连接常驻电脑".into())
}

fn same_target(path: &Path, original: &Target) -> Result<(), String> {
    if target(path, &original.id)? != *original {
        return Err("常驻电脑登记已变化，请重新确认".into());
    }
    Ok(())
}

#[tauri::command]
pub(crate) async fn resident_account_status(
    app: AppHandle,
    state: State<'_, account::AccountState>,
    project_id: String,
) -> Result<View, String> {
    let path = project_registry_path(&app)?;
    let selected = target(&path, &project_id)?;
    let record = parse_record(
        &request(
            &http()?,
            &selected,
            &credential(&project_id)?,
            reqwest::Method::GET,
            None,
        )
        .await?,
    )?;
    let proof = account::runtime_proof(&app, &state).await.ok();
    same_target(&path, &selected)?;
    Ok(view(&selected, record, proof.as_ref()))
}

async fn confirm(app: &AppHandle, selected: &Target, bind: bool) -> Result<bool, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    let action = if bind {
        "关联当前 VortoCode 账号"
    } else {
        "解除账号关联"
    };
    let detail = if bind {
        "短期账号证明将由原生端发送到这个地址。该账号登录的手机可读取此助手概览。"
    } else {
        "该助手的账号手机读取授权会被撤销；电脑登录与手动配对授权保留。"
    };
    let message = format!(
        "{action}？\n\n{}\n{}\n\n{detail}",
        selected.name, selected.base
    );
    let app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&app, app.dialog().message(message))
            .title(action)
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancel)
            .blocking_show()
    })
    .await
    .map_err(|_| "无法显示账号关联确认".to_string())
}

#[tauri::command]
pub(crate) async fn resident_account_update(
    app: AppHandle,
    state: State<'_, account::AccountState>,
    project_id: String,
    instance_id: Option<String>,
    revision: u64,
    bind: bool,
) -> Result<View, String> {
    let path = project_registry_path(&app)?;
    let selected = target(&path, &project_id)?;
    if !confirm(&app, &selected, bind).await? {
        return Err("已取消账号关联修改".into());
    }
    same_target(&path, &selected)?;
    let client = http()?;
    let token = credential(&project_id)?;
    let before =
        parse_record(&request(&client, &selected, &token, reqwest::Method::GET, None).await?)?;
    if before.instance_id != instance_id || before.revision != revision {
        return Err("助手身份或归属已变化，请刷新后重新确认".into());
    }
    let proof = if bind {
        Some(account::runtime_proof(&app, &state).await?)
    } else {
        None
    };
    let mut body = serde_json::json!({"instance_id":instance_id,"revision":revision});
    let method = if let Some(proof) = &proof {
        if before.account_issuer != proof.issuer {
            return Err("助手使用的账号服务与本机登录来源不同，未发送账号证明".into());
        }
        body["issuer"] = proof.issuer.clone().into();
        body["access_token"] = proof.access.clone().into();
        body["name"] = selected
            .name
            .chars()
            .filter(|c| !c.is_control())
            .take(80)
            .collect::<String>()
            .into();
        reqwest::Method::PUT
    } else {
        reqwest::Method::DELETE
    };
    same_target(&path, &selected)?;
    request(&client, &selected, &token, method, Some(body)).await?;
    same_target(&path, &selected)?;
    let after =
        parse_record(&request(&client, &selected, &token, reqwest::Method::GET, None).await?)?;
    same_target(&path, &selected)?;
    if before.instance_id.is_some() && after.instance_id != before.instance_id {
        return Err("运行端身份已变化，请刷新核对".into());
    }
    if bind
        && (after.registration.as_ref().is_none_or(|item| {
            let proof = proof.as_ref().unwrap();
            item.owner.issuer != proof.issuer || item.owner.subject != proof.subject
        }))
        || (!bind && after.registration.is_some())
    {
        return Err("归属回执已变化，请刷新核对".into());
    }
    Ok(view(&selected, after, proof.as_ref()))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_saved_resident_and_secure_transport_are_accepted() {
        let dir = std::env::temp_dir().join(format!("vorto-account-target-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("projects.json");
        let base = "https://resident.test";
        let id = remote_project_id(base, "@assistant");
        remember_remote_at(&path, base, "@assistant", "助手", &id).unwrap();
        assert!(target(&path, &id).is_ok());
        assert!(target(&path, "unknown").is_err());
        let insecure = "http://192.168.1.97:8080";
        let bad = remote_project_id(insecure, "@assistant");
        remember_remote_at(&path, insecure, "@assistant", "助手", &bad).unwrap();
        assert!(target(&path, &bad).is_err());
        let project = remote_project_id(base, "/repo");
        remember_remote_at(&path, base, "/repo", "项目", &project).unwrap();
        assert!(target(&path, &project).is_err());
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn native_view_does_not_serialize_account_proof_or_owner() {
        let record = parse_record(br#"{"schema_version":1,"instance_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","revision":1,"registration":{"owner":{"issuer":"https://account.test","subject":"12"},"name":"assistant"},"account_issuer":"https://account.test","mobile_entry_url":"https://phone.test/mobile"}"#).unwrap();
        let selected = Target {
            id: "id".into(),
            name: "assistant".into(),
            base: "https://resident.test".into(),
        };
        let proof = account::RuntimeProof {
            issuer: "https://account.test".into(),
            subject: "12".into(),
            access: "private-proof".into(),
        };
        let public = view(&selected, record, Some(&proof));
        assert_eq!(public.belongs_to_current, Some(true));
        let body = serde_json::to_string(&public).unwrap();
        assert!(!body.contains("private-proof"));
        assert!(!body.contains("subject"));
        assert!(mobile_url(Some("https://user:password@phone.test/mobile")).is_none());
        assert!(mobile_url(Some("http://phone.test/mobile")).is_none());
    }
    #[test]
    fn transport_uses_only_fixed_path_and_never_follows_redirects_or_echoes_body() {
        use std::io::{Read, Write};
        for status in [200, 302, 409] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let base = format!("http://{}", listener.local_addr().unwrap());
            let server = std::thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut bytes = Vec::new();
                let mut buffer = [0; 1024];
                while !bytes.windows(4).any(|item| item == b"\r\n\r\n") {
                    let n = stream.read(&mut buffer).unwrap();
                    if n == 0 {
                        break;
                    }
                    bytes.extend_from_slice(&buffer[..n]);
                }
                let head = String::from_utf8(bytes).unwrap();
                assert!(head.starts_with("GET /api/mobile/account/registration HTTP/1.1\r\n"));
                assert!(head
                    .to_ascii_lowercase()
                    .contains("authorization: bearer fixture-admin"));
                write!(stream,"HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: 14\r\nLocation: https://never.test/\r\nConnection: close\r\n\r\nprivate-secret").unwrap();
            });
            let selected = Target {
                id: "test".into(),
                name: "test".into(),
                base,
            };
            let outcome = tauri::async_runtime::block_on(request(
                &http().unwrap(),
                &selected,
                "fixture-admin",
                reqwest::Method::GET,
                None,
            ));
            if status == 200 {
                assert_eq!(outcome.unwrap(), b"private-secret");
            } else {
                let error = outcome.unwrap_err();
                assert!(!error.contains("private-secret"));
            }
            server.join().unwrap();
        }
    }
}
