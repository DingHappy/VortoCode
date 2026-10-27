// 模型服务：连接测试、模型列表、多服务商、会员服务、作者中转登录与额度查询。
// 从 lib.rs 原样搬出。
use super::*;

// ── 设置页补齐：测试连接 / 额度查询 / 全局指令 / 配置文件定位 ─────────────────────────────
// 出网请求与 discover_model_context_window_with 同口径：只打配置声明的 base_url 本身，限时、
// 不跟随重定向；Key 不外借——留空时只有与已保存配置同一地址才借用已保存的 Key。

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct LlmConnectionTest {
    pub(crate) ok: bool,
    pub(crate) status: Option<u16>,
    pub(crate) model_count: Option<usize>,
    pub(crate) model_available: Option<bool>,
    /// 服务返回的模型 id（最多 MAX_LISTED_MODELS 个），供设置页给模型名做候选。
    pub(crate) models: Vec<String>,
    pub(crate) message: String,
}

pub(crate) const MAX_LISTED_MODELS: usize = 200;

pub(crate) fn model_ids(payload: &serde_json::Value) -> Vec<String> {
    payload
        .get("data")
        .and_then(serde_json::Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.get("id").or_else(|| item.get("model")).and_then(serde_json::Value::as_str))
                .take(MAX_LISTED_MODELS)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// 模型描述里的短标签（类别 / 能力 / 档位）：小写、限长、不含控制字符，否则当没给。
pub(crate) fn model_label(value: Option<&serde_json::Value>) -> Option<String> {
    let label = value?.as_str()?.trim().to_ascii_lowercase();
    (!label.is_empty() && label.len() <= 40 && !label.chars().any(char::is_control)).then_some(label)
}

/// 解析 /models 的完整描述。只要服务给任何一个模型标了 category，就按它的分类走：
/// 不能跑 Agent 的（专用模型、非对话模型、没有 tools 的）**先**剔掉再截断，免得被挤出上限。
pub(crate) fn model_infos(payload: &serde_json::Value) -> Vec<ModelInfo> {
    let Some(items) = payload.get("data").and_then(serde_json::Value::as_array) else {
        return Vec::new();
    };
    let infos = items.iter().filter_map(|item| {
        let id = item.get("id").or_else(|| item.get("model")).and_then(serde_json::Value::as_str)?;
        Some(ModelInfo {
            id: id.to_string(),
            category: model_label(item.get("category")),
            capabilities: item
                .get("capabilities")
                .and_then(serde_json::Value::as_array)
                .map(|values| values.iter().filter_map(|value| model_label(Some(value))).take(16).collect())
                .unwrap_or_default(),
            tier: model_label(item.get("tier")),
            snapshot_of: item
                .get("snapshot_of")
                .and_then(serde_json::Value::as_str)
                .map(str::trim)
                .filter(|base| !base.is_empty() && base.len() <= 160 && !base.chars().any(char::is_control))
                .map(str::to_string),
            context_window: item
                .get("context_window")
                .and_then(serde_json::Value::as_u64)
                .filter(|window| (MIN_MODEL_CONTEXT_WINDOW..=MAX_MODEL_CONTEXT_WINDOW).contains(window)),
        })
    });
    let infos: Vec<ModelInfo> = infos.collect();
    let classified = infos.iter().any(|info| info.category.is_some());
    clean_model_infos(infos.into_iter().filter(|info| !classified || info.agent_ready()).collect())
}

/// 同 clean_model_list 的口径清洗 id 并去重。
pub(crate) fn clean_model_infos(infos: Vec<ModelInfo>) -> Vec<ModelInfo> {
    let mut seen = std::collections::HashSet::new();
    infos
        .into_iter()
        .map(|mut info| {
            info.id = info.id.trim().to_string();
            info
        })
        .filter(|info| {
            !info.id.is_empty()
                && info.id.len() <= 160
                && !info.id.contains(',')
                && !info.id.chars().any(char::is_control)
        })
        .filter(|info| seen.insert(info.id.clone()))
        .take(MAX_LISTED_MODELS)
        .collect()
}

/// runtime 允许点名的模型：服务给了分类就只放能跑 Agent 的；没给分类（别家服务、旧配置）沿用整份清单。
pub(crate) fn model_choice_ids(profile: &DesktopLlmProfile) -> Vec<String> {
    if profile.model_info.iter().any(|info| info.category.is_some()) {
        return profile
            .model_info
            .iter()
            .filter(|info| info.agent_ready())
            .map(|info| info.id.clone())
            .collect();
    }
    profile.models.clone()
}

pub(crate) fn set_model_list(profile: &mut DesktopLlmProfile, infos: Vec<ModelInfo>) {
    profile.models = infos.iter().map(|info| info.id.clone()).collect();
    profile.model_info = infos;
}

pub(crate) fn summarize_models_payload(payload: &serde_json::Value, model: &str) -> (usize, Option<bool>) {
    let Some(models) = payload.get("data").and_then(serde_json::Value::as_array) else {
        return (0, None);
    };
    let wanted = model.trim();
    let available = (!wanted.is_empty()).then(|| {
        models.iter().any(|item| {
            item.get("id")
                .or_else(|| item.get("model"))
                .and_then(serde_json::Value::as_str)
                .is_some_and(|id| id.eq_ignore_ascii_case(wanted))
        })
    });
    (models.len(), available)
}

pub(crate) fn probe_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(LLM_PROBE_TIMEOUT)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|error| format!("无法创建网络客户端：{error}"))
}

pub(crate) const MAX_CUSTOM_PROVIDERS: usize = 12;
pub(crate) const RESERVED_PROVIDER_IDS: [&str; 3] = ["default", "anthropic", "cli"];

/// 校验一个自定义供应商：ID 只能是小写字母开头的 `[a-z0-9_]`（会拼进环境变量名），
/// 地址与 Key 沿用主模型服务的同一套规则（远程必须 HTTPS 且带 Key）。
pub(crate) fn normalize_llm_provider(mut provider: DesktopLlmProvider) -> Result<DesktopLlmProvider, String> {
    provider.id = provider.id.trim().to_lowercase();
    let id_ok = provider.id.len() <= 24
        && provider.id.chars().next().is_some_and(|c| c.is_ascii_lowercase())
        && provider
            .id
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_');
    if !id_ok {
        return Err("供应商 ID 只能用小写字母、数字和下划线，并以字母开头（最长 24 位）".into());
    }
    if RESERVED_PROVIDER_IDS.contains(&provider.id.as_str()) {
        return Err(format!("供应商 ID 不能用 {}", provider.id));
    }
    provider.name = provider.name.trim().to_string();
    if provider.name.is_empty() {
        provider.name = provider.id.clone();
    }
    if provider.name.chars().count() > 40 || provider.name.chars().any(char::is_control) {
        return Err("供应商名称无效".into());
    }
    let checked = normalize_llm_profile(DesktopLlmProfile {
        revision: None,
        base_url: provider.base_url.clone(),
        api_key: provider.api_key.clone(),
        model: "-".into(),
        context_window: None,
        context_window_source: None,
        fast_model: None,
        strong_model: None,
        role_models: HashMap::new(),
        providers: Vec::new(),
        account: None,
        signed_out: false,
        model_info: Vec::new(),
        models: Vec::new(),
    })?;
    provider.base_url = checked.base_url;
    provider.api_key = checked.api_key;
    provider.models = provider
        .models
        .into_iter()
        .map(|model| model.trim().to_string())
        .filter(|model| !model.is_empty() && model.len() <= 160 && !model.chars().any(char::is_control))
        .take(MAX_LISTED_MODELS)
        .collect();
    Ok(provider)
}

pub(crate) async fn fetch_provider_models(provider: &DesktopLlmProvider) -> Option<Vec<ModelInfo>> {
    let mut request = probe_client().ok()?.get(format!("{}/models", provider.base_url));
    if !provider.api_key.is_empty() {
        request = request.bearer_auth(&provider.api_key);
    }
    let response = request.send().await.ok()?;
    if !response.status().is_success() {
        return None;
    }
    let payload = response.json::<serde_json::Value>().await.ok()?;
    Some(model_infos(&payload))
}

/// 配置里还没有模型清单（或只有旧版的纯模型名）时，用它自己的地址和 Key 拉一份（只访问这个默认服务）。
pub(crate) async fn with_model_list(mut profile: DesktopLlmProfile) -> DesktopLlmProfile {
    if profile.model_info.is_empty() && !profile.signed_out && !profile.api_key.is_empty() {
        let main = DesktopLlmProvider {
            id: "default".into(),
            name: String::new(),
            base_url: profile.base_url.clone(),
            api_key: profile.api_key.clone(),
            models: Vec::new(),
        };
        if let Some(models) = fetch_provider_models(&main).await {
            set_model_list(&mut profile, models);
        }
    }
    profile
}

pub(crate) fn clean_model_list(models: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    models
        .into_iter()
        .map(|model| model.trim().to_string())
        .filter(|model| {
            !model.is_empty()
                && model.len() <= 160
                && !model.contains(',')
                && !model.chars().any(char::is_control)
        })
        .filter(|model| seen.insert(model.clone()))
        .take(MAX_LISTED_MODELS)
        .collect()
}

pub(crate) fn saved_profile_for_providers(
    app: &AppHandle,
    store: &State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfile, String> {
    cached_desktop_llm_profile(app, store)?
        .ok_or_else(|| "请先在上方配置并保存默认模型服务，再添加其他供应商".to_string())
}

pub(crate) fn persist_profile(
    app: &AppHandle,
    store: &State<'_, DesktopLlmProfileStore>,
    profile: DesktopLlmProfile,
) -> Result<DesktopLlmProfileStatus, String> {
    let expected = profile.revision.clone().ok_or("模型配置缺少版本，请重新读取后再保存")?;
    let snapshot = store.commit(&llm_profile_path(app)?, &expected, Some(profile))?;
    Ok(profile_snapshot_status(&snapshot, app))
}

/// 会员模型作为「其他供应商」加进模型列表时用的 ID：默认服务（比如本机模型）不动，两边在输入框里切换。
pub(crate) const MEMBER_PROVIDER_ID: &str = "vortocode_member";

/// 已登录会员、默认服务是别的（本机模型、自己的 Key）时：把会员模型加成一个供应商，而不是替换默认服务。
/// 地址固定为中转站（调用方已校验），所以不弹原生确认框；Key 有变化时同一函数也负责更新。
pub(crate) async fn upsert_member_provider(
    app: &AppHandle,
    store: &State<'_, DesktopLlmProfileStore>,
    key: &str,
) -> Result<DesktopLlmProfileStatus, String> {
    let profile = saved_profile_for_providers(app, store)?;
    let replacing = profile.providers.iter().any(|existing| existing.id == MEMBER_PROVIDER_ID);
    if !replacing && profile.providers.len() >= MAX_CUSTOM_PROVIDERS {
        return Err(format!("最多添加 {MAX_CUSTOM_PROVIDERS} 个供应商，先删掉一个再加会员模型"));
    }
    let provider = normalize_llm_provider(DesktopLlmProvider {
        id: MEMBER_PROVIDER_ID.into(),
        name: "VortoCode 会员".into(),
        base_url: format!("{RELAY_ORIGIN}/v1"),
        api_key: with_sk_prefix(key),
        models: Vec::new(),
    })?;
    let models = fetch_provider_models(&provider).await;
    let profile = refreshed_member_profile(profile, provider, models);
    persist_profile(app, store, profile)
}

pub(crate) fn refreshed_member_profile(
    mut profile: DesktopLlmProfile,
    mut provider: DesktopLlmProvider,
    models: Option<Vec<ModelInfo>>,
) -> DesktopLlmProfile {
    provider.models = match models {
        Some(models) => member_menu_models(models),
        None => profile.providers.iter().find(|existing| existing.id == MEMBER_PROVIDER_ID
            && existing.base_url == provider.base_url && existing.api_key == provider.api_key)
            .map(|existing| existing.models.clone()).unwrap_or_else(|| member_menu_models(Vec::new())),
    };
    if let Some(existing) = profile.providers.iter_mut().find(|existing| existing.id == MEMBER_PROVIDER_ID) {
        *existing = provider;
    } else {
        profile.providers.push(provider);
    }
    profile
}

/// 会员模型菜单只放能跑 Agent 的对话模型（不要语音、图片、带日期的快照），默认模型排第一。
/// 服务端没给分类信息时原样保留；什么都没有时至少有默认模型。
pub(crate) fn member_menu_models(infos: Vec<ModelInfo>) -> Vec<String> {
    let classified = infos.iter().any(|info| info.category.is_some());
    let mut models: Vec<String> = infos
        .into_iter()
        .filter(|info| !classified || (info.agent_ready() && info.snapshot_of.is_none()))
        .map(|info| info.id)
        .collect();
    if let Some(index) = models.iter().position(|model| model == RELAY_DEFAULT_MODEL) {
        let first = models.remove(index);
        models.insert(0, first);
    }
    if models.is_empty() {
        models.push(RELAY_DEFAULT_MODEL.into());
    }
    models
}

/// 「设为默认」换下来的默认服务变成列表里的一个供应商：地址、Key、模型清单都保留，主模型排第一。
/// 已退出登录或没配好的默认服务没有可保留的东西，返回 None。
pub(crate) fn default_as_provider(profile: &DesktopLlmProfile, taken: &[DesktopLlmProvider]) -> Option<DesktopLlmProvider> {
    if profile.signed_out || profile.base_url.trim().is_empty() || profile.model.trim().is_empty() {
        return None;
    }
    let member = profile
        .account
        .as_ref()
        .is_some_and(|account| account.key_source == account::MEMBER_KEY_SOURCE);
    let host = Url::parse(&profile.base_url)
        .ok()
        .and_then(|url| url.host_str().map(str::to_string))
        .unwrap_or_default();
    let (base_id, name) = if member {
        (MEMBER_PROVIDER_ID.to_string(), "VortoCode 会员".to_string())
    } else if llm_provider(&profile.base_url) == "local" {
        ("local".to_string(), "本机模型".to_string())
    } else {
        (provider_id_from_host(&host), host.clone())
    };
    let mut id = base_id.clone();
    let mut n = 2;
    while taken.iter().any(|provider| provider.id == id) {
        let suffix = format!("_{n}");
        id = format!("{}{suffix}", &base_id[..base_id.len().min(24 - suffix.len())]);
        n += 1;
    }
    // 有分类信息时只留能跑 Agent 的对话模型（和会员模型菜单同一规则），主模型排第一
    let mut models = if profile.model_info.iter().any(|info| info.category.is_some()) {
        member_menu_models(profile.model_info.clone())
    } else {
        profile.models.clone()
    };
    models.retain(|model| model != &profile.model);
    models.insert(0, profile.model.clone());
    Some(DesktopLlmProvider {
        id,
        name,
        base_url: profile.base_url.clone(),
        api_key: profile.api_key.clone(),
        models,
    })
}

/// 用地址生成一个合法的供应商 ID：api.deepseek.com → deepseek。
pub(crate) fn provider_id_from_host(host: &str) -> String {
    let trimmed = host.trim_start_matches("api.").trim_start_matches("www.");
    let mut id: String = trimmed
        .split('.')
        .next()
        .unwrap_or_default()
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
        .collect();
    if !id.chars().next().is_some_and(|c| c.is_ascii_lowercase()) {
        id = format!("p_{id}");
    }
    id.truncate(20);
    if RESERVED_PROVIDER_IDS.contains(&id.as_str()) {
        id.push_str("_svc");
    }
    id
}

/// 把列表里的一个供应商设为默认服务，原来的默认服务挪进列表。会员模型走账号命令（要带上账号信息）。
pub(crate) fn promote_provider(mut profile: DesktopLlmProfile, id: &str) -> Result<DesktopLlmProfile, String> {
    if id == MEMBER_PROVIDER_ID {
        return Err("会员模型请在账号里设为默认".into());
    }
    let index = profile
        .providers
        .iter()
        .position(|provider| provider.id == id)
        .ok_or("没有找到这个模型服务")?;
    let chosen = profile.providers.remove(index);
    let model = chosen
        .models
        .first()
        .cloned()
        .ok_or("这个服务还没有模型列表：先点刷新，或检查地址和 Key")?;
    if let Some(old) = default_as_provider(&profile, &profile.providers) {
        profile.providers.push(old);
    }
    profile.base_url = chosen.base_url;
    profile.api_key = chosen.api_key;
    profile.model = model;
    profile.models = chosen.models;
    profile.model_info = Vec::new();
    profile.context_window = None;
    profile.context_window_source = None;
    // 快速 / 强力档和角色模型是按原来那个服务的模型名配的，换了服务就不再成立
    profile.fast_model = None;
    profile.strong_model = None;
    profile.role_models = HashMap::new();
    profile.account = None;
    profile.signed_out = false;
    Ok(profile)
}

/// 「设为默认」：列表里的某个服务换成默认服务（原来的默认服务留在列表里）。
#[tauri::command]
pub(crate) async fn promote_llm_provider(
    app: AppHandle,
    id: String,
    expected_revision: Option<String>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, ModelProfileMutationError> {
    let profile = saved_profile_for_providers(&app, &store)?;
    check_profile_revision(&profile, expected_revision.as_deref())?;
    let profile = promote_provider(profile, id.trim())?;
    let profile = with_model_list(profile).await;
    let profile = save_llm_profile_flow(profile, true, LLM_PROBE_TIMEOUT, |_| Ok(())).await?;
    persist_profile(&app, &store, profile).map_err(Into::into)
}

/// 会员模型设为默认之前：把现在的默认服务挪进列表，并去掉列表里那份会员模型（它要成为默认）。
pub(crate) fn demote_default_for_member(
    app: &AppHandle,
    store: &State<'_, DesktopLlmProfileStore>,
) -> Result<(), String> {
    let Some(mut profile) = cached_desktop_llm_profile(app, store)? else {
        return Ok(());
    };
    profile.providers.retain(|provider| provider.id != MEMBER_PROVIDER_ID);
    if let Some(old) = default_as_provider(&profile, &profile.providers) {
        if old.id != MEMBER_PROVIDER_ID {
            profile.providers.push(old);
        }
    }
    persist_profile(app, store, profile).map(|_| ())
}

/// 会员 Key 已作为供应商加在列表里时返回它（用于静默更新 Key、退出账号时移除）。
pub(crate) fn member_provider_key(app: &AppHandle, store: &State<'_, DesktopLlmProfileStore>) -> Option<String> {
    cached_desktop_llm_profile(app, store)
        .ok()
        .flatten()?
        .providers
        .into_iter()
        .find(|provider| provider.id == MEMBER_PROVIDER_ID)
        .map(|provider| provider.api_key)
}

/// 退出账号时把会员供应商从列表里拿掉（Key 不该留在本机）。
pub(crate) fn remove_member_provider(
    app: &AppHandle,
    store: &State<'_, DesktopLlmProfileStore>,
) -> Result<(), String> {
    let Some(mut profile) = cached_desktop_llm_profile(app, store)? else {
        return Ok(());
    };
    let before = profile.providers.len();
    profile.providers.retain(|existing| existing.id != MEMBER_PROVIDER_ID);
    if profile.providers.len() != before {
        persist_profile(app, store, profile)?;
    }
    Ok(())
}

/// 添加或更新一个自定义供应商。Key 留空 = 沿用同一 ID、同一地址下已保存的 Key。
#[tauri::command]
pub(crate) async fn save_llm_provider(
    app: AppHandle,
    id: String,
    name: String,
    base_url: String,
    api_key: String,
    expected_revision: Option<String>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, ModelProfileMutationError> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    let mut profile = saved_profile_for_providers(&app, &store)?;
    check_profile_revision(&profile, expected_revision.as_deref())?;
    let wanted_id = id.trim().to_lowercase();
    let wanted_base = base_url.trim().trim_end_matches('/').to_string();
    let mut api_key = api_key.trim().to_string();
    if api_key.is_empty() {
        if let Some(existing) = profile
            .providers
            .iter()
            .find(|provider| provider.id == wanted_id && provider.base_url == wanted_base)
        {
            api_key = existing.api_key.clone();
        }
    }
    let mut provider = normalize_llm_provider(DesktopLlmProvider {
        id,
        name,
        base_url,
        api_key,
        models: Vec::new(),
    })?;
    let replacing = profile.providers.iter().any(|existing| existing.id == provider.id);
    if !replacing && profile.providers.len() >= MAX_CUSTOM_PROVIDERS {
        return Err(format!("最多添加 {MAX_CUSTOM_PROVIDERS} 个供应商").into());
    }
    // 原生确认在 webview 之外：页面脚本不能悄悄把请求和 Key 指向别的地址。
    let message = format!(
        "{}模型供应商：\n\n名称：{}\nID：{}\n接口地址：{}\n\n在聊天里选用它的模型时，请求会带着你填写的 Key 发到这个地址。",
        if replacing { "更新" } else { "添加" },
        provider.name,
        provider.id,
        provider.base_url
    );
    let dialog_app = app.clone();
    let confirmed = tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&dialog_app, dialog_app.dialog().message(message))
            .title("确认模型供应商")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom("确认".into(), "取消".into()))
            .blocking_show()
    })
    .await
    .map_err(|error| format!("供应商确认对话框失败：{error}"))?;
    if !confirmed {
        return Err("未确认，供应商没有保存".into());
    }
    provider.models = fetch_provider_models(&provider)
        .await
        .map(|infos| infos.into_iter().map(|info| info.id).collect())
        .unwrap_or_default();
    profile.providers.retain(|existing| existing.id != provider.id);
    profile.providers.push(provider);
    persist_profile(&app, &store, profile).map_err(Into::into)
}

#[tauri::command(async)]
pub(crate) fn remove_llm_provider(
    app: AppHandle,
    id: String,
    expected_revision: Option<String>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, ModelProfileMutationError> {
    let mut profile = saved_profile_for_providers(&app, &store)?;
    check_profile_revision(&profile, expected_revision.as_deref())?;
    let before = profile.providers.len();
    profile.providers.retain(|provider| provider.id != id);
    if profile.providers.len() == before {
        return Err("没有这个供应商".into());
    }
    persist_profile(&app, &store, profile).map_err(Into::into)
}

/// 重新拉取默认服务和各供应商的模型列表；拉取失败的保留原列表。
#[tauri::command]
pub(crate) async fn refresh_llm_providers(
    app: AppHandle,
    expected_revision: Option<String>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, ModelProfileMutationError> {
    let mut profile = saved_profile_for_providers(&app, &store)?;
    check_profile_revision(&profile, expected_revision.as_deref())?;
    if !profile.signed_out && !profile.api_key.is_empty() {
        let main = DesktopLlmProvider {
            id: "default".into(),
            name: String::new(),
            base_url: profile.base_url.clone(),
            api_key: profile.api_key.clone(),
            models: Vec::new(),
        };
        if let Some(models) = fetch_provider_models(&main).await {
            set_model_list(&mut profile, models);
        }
    }
    for provider in profile.providers.iter_mut() {
        if let Some(models) = fetch_provider_models(provider).await {
            provider.models = models.into_iter().map(|info| info.id).collect();
        }
    }
    persist_profile(&app, &store, profile).map_err(Into::into)
}

pub(crate) const RELAY_ORIGIN: &str = "https://token.vortotech.com";
pub(crate) const RELAY_PLAN_URL: &str = "https://token.vortotech.com/panel/plan";
pub(crate) const DESKTOP_TOKEN_NAME: &str = "VortoCode Desktop";

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RelayLoginOutcome {
    /// 登录并保存成功后的模型配置；需要先选套餐时为 None。
    pub(crate) status: Option<DesktopLlmProfileStatus>,
    /// 账号还没有 Token Plan：界面让用户去购买，或明确选择改用按量 Key。
    pub(crate) needs_plan: bool,
    pub(crate) plan_url: String,
    pub(crate) message: String,
}

/// 只认 One API 的 `{success, message, data}` 外壳；success=false 时把服务端原话带回去。
pub(crate) fn relay_data(payload: serde_json::Value, failure: &str) -> Result<serde_json::Value, String> {
    if payload.get("success").and_then(serde_json::Value::as_bool) == Some(true) {
        return Ok(payload.get("data").cloned().unwrap_or(serde_json::Value::Null));
    }
    let message = payload
        .get("message")
        .and_then(serde_json::Value::as_str)
        .filter(|message| !message.trim().is_empty())
        .unwrap_or(failure);
    Err(message.chars().take(200).collect())
}

/// 从 Token Plan Key 列表里挑一把启用中的（托管的那把排在最前）。
pub(crate) fn pick_plan_key(data: &serde_json::Value) -> Option<String> {
    data.as_array()?
        .iter()
        .find(|item| item.get("status").and_then(serde_json::Value::as_i64) == Some(1))
        .and_then(|item| item.get("key").and_then(serde_json::Value::as_str))
        .filter(|key| !key.trim().is_empty())
        .map(str::to_string)
}

/// 在令牌搜索结果里找桌面端自己建的那把按量 Key（同名、启用、非套餐）。
pub(crate) fn pick_desktop_token(data: &serde_json::Value) -> Option<String> {
    data.as_array()?
        .iter()
        .find(|item| {
            item.get("name").and_then(serde_json::Value::as_str) == Some(DESKTOP_TOKEN_NAME)
                && item.get("status").and_then(serde_json::Value::as_i64) == Some(1)
                && item.get("billing_type").and_then(serde_json::Value::as_str) != Some("token_plan")
        })
        .and_then(|item| item.get("key").and_then(serde_json::Value::as_str))
        .filter(|key| !key.trim().is_empty())
        .map(str::to_string)
}

pub(crate) fn with_sk_prefix(key: &str) -> String {
    if key.starts_with("sk-") {
        key.to_string()
    } else {
        format!("sk-{key}")
    }
}

/// 把中转站的一把 Key 存成默认模型服务（中转站登录与 VortoCode 会员共用）：同一地址时沿用已选模型、
/// 调度档和模型清单，换地址则回到默认模型；自定义供应商一律保留。
pub(crate) async fn save_relay_plan_profile(
    app: &AppHandle,
    store: &State<'_, DesktopLlmProfileStore>,
    key: &str,
    account: DesktopAccount,
) -> Result<DesktopLlmProfile, String> {
    let snapshot = cached_profile_snapshot(app, store)?;
    let saved = snapshot.profile;
    let base_url = format!("{RELAY_ORIGIN}/v1");
    let same_base = saved.as_ref().is_some_and(|saved| saved.base_url == base_url);
    let profile = normalize_llm_profile(DesktopLlmProfile {
        revision: Some(snapshot.revision),
        base_url,
        api_key: with_sk_prefix(key),
        model: saved
            .as_ref()
            .filter(|_| same_base)
            .map(|saved| saved.model.clone())
            .unwrap_or_else(|| RELAY_DEFAULT_MODEL.into()),
        context_window: None,
        context_window_source: None,
        fast_model: saved.as_ref().filter(|_| same_base).and_then(|saved| saved.fast_model.clone()),
        strong_model: saved.as_ref().filter(|_| same_base).and_then(|saved| saved.strong_model.clone()),
        role_models: saved.as_ref().map(|profile| profile.role_models.clone()).unwrap_or_default(),
        models: saved.as_ref().filter(|_| same_base).map(|saved| saved.models.clone()).unwrap_or_default(),
        model_info: saved.as_ref().filter(|_| same_base).map(|saved| saved.model_info.clone()).unwrap_or_default(),
        providers: saved.map(|saved| saved.providers).unwrap_or_default(),
        account: Some(account),
        signed_out: false,
    })?;
    let profile = with_model_list(profile).await;
    let profile = save_llm_profile_flow(profile, true, LLM_PROBE_TIMEOUT, |_| Ok(())).await?;
    let expected = profile.revision.clone().ok_or("模型配置缺少版本")?;
    store.commit(&llm_profile_path(app)?, &expected, Some(profile))?.profile.ok_or("模型配置保存失败".into())
}

/// 用 VortoCode 中转站账号登录，取用账号的 Token Plan Key（Desktop 套餐即 Token Plan）。
/// 没有套餐时默认不建 Key，交给用户决定；`allow_pay_as_you_go` 为真才建/复用一把按量 Key。
/// 只连固定的中转站地址；密码和登录会话只在本次调用里用，结束即登出，不落盘。
#[tauri::command]
pub(crate) async fn relay_login(
    app: AppHandle,
    username: String,
    password: String,
    allow_pay_as_you_go: bool,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<RelayLoginOutcome, String> {
    let username = username.trim().to_string();
    if username.is_empty() || password.is_empty() || username.len() > 64 || password.len() > 256 {
        return Err("请输入用户名和密码".into());
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(20))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|error| format!("无法创建网络客户端：{error}"))?;
    let unreachable = |error: reqwest::Error| {
        if error.is_timeout() { "连接中转站超时".to_string() } else { "无法连接中转站".to_string() }
    };
    let login = client
        .post(format!("{RELAY_ORIGIN}/api/user/login"))
        .json(&serde_json::json!({ "username": username, "password": password }))
        .send()
        .await
        .map_err(unreachable)?;
    let cookie = login
        .headers()
        .get_all(reqwest::header::SET_COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .filter_map(|value| value.split(';').next())
        .map(str::trim)
        .filter(|pair| pair.contains('='))
        .collect::<Vec<_>>()
        .join("; ");
    let user = relay_data(login.json().await.unwrap_or_default(), "登录失败")?;
    if cookie.is_empty() {
        return Err("中转站没有返回登录会话".into());
    }
    let get = |path: &str| {
        client
            .get(format!("{RELAY_ORIGIN}{path}"))
            .header(reqwest::header::COOKIE, cookie.clone())
    };

    let plan_keys = relay_data(
        get("/api/subscription/key").send().await.map_err(unreachable)?.json().await.unwrap_or_default(),
        "读取套餐 Key 失败",
    )?;
    let (key, key_source) = match pick_plan_key(&plan_keys) {
        Some(key) => (key, "token_plan"),
        None if !allow_pay_as_you_go => {
            let _ = get("/api/user/logout").send().await;
            return Ok(RelayLoginOutcome {
                status: None,
                needs_plan: true,
                plan_url: RELAY_PLAN_URL.into(),
                message: "这个账号还没有 Token Plan（Desktop 套餐）".into(),
            });
        }
        None => {
            let found = relay_data(
                get(&format!("/api/token/search?keyword={}", DESKTOP_TOKEN_NAME.replace(' ', "%20")))
                    .send()
                    .await
                    .map_err(unreachable)?
                    .json()
                    .await
                    .unwrap_or_default(),
                "读取令牌失败",
            )?;
            let key = match pick_desktop_token(&found) {
                Some(key) => key,
                None => {
                    let created = relay_data(
                        client
                            .post(format!("{RELAY_ORIGIN}/api/token/"))
                            .header(reqwest::header::COOKIE, cookie.clone())
                            .json(&serde_json::json!({
                                "name": DESKTOP_TOKEN_NAME,
                                "expired_time": -1,
                                "unlimited_quota": true,
                                "remain_quota": 0,
                            }))
                            .send()
                            .await
                            .map_err(unreachable)?
                            .json()
                            .await
                            .unwrap_or_default(),
                        "创建按量 Key 失败",
                    )?;
                    created
                        .get("key")
                        .and_then(serde_json::Value::as_str)
                        .map(str::to_string)
                        .ok_or_else(|| "中转站没有返回新 Key".to_string())?
                }
            };
            (key, "pay_as_you_go")
        }
    };

    // 套餐名称和到期时间只用于展示；读不到不影响登录。
    let subscription = match get("/api/subscription/self").send().await {
        Ok(response) => response.json::<serde_json::Value>().await.unwrap_or_default(),
        Err(_) => serde_json::Value::Null,
    };
    let active = subscription.get("data").and_then(|data| data.get("active")).cloned().unwrap_or_default();
    let _ = get("/api/user/logout").send().await;

    let account = DesktopAccount {
        username: user
            .get("username")
            .and_then(serde_json::Value::as_str)
            .unwrap_or(&username)
            .to_string(),
        display_name: user
            .get("display_name")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_string(),
        key_source: key_source.into(),
        plan_name: active.get("plan_name").and_then(serde_json::Value::as_str).map(str::to_string),
        plan_expiry: active.get("expiry_time").and_then(serde_json::Value::as_i64),
    };
    let profile = save_relay_plan_profile(&app, &store, &key, account).await?;
    Ok(RelayLoginOutcome {
        status: Some(with_config_path(desktop_llm_profile_status(Some(&profile)), &app)),
        needs_plan: false,
        plan_url: RELAY_PLAN_URL.into(),
        message: if key_source == "token_plan" {
            "已登录，正在使用 Token Plan".into()
        } else {
            "已登录，正在使用按量 Key".into()
        },
    })
}

/// 退出登录：删掉本机保存的中转站 Key 和账号信息，保留自定义供应商。
#[tauri::command(async)]
pub(crate) fn relay_logout(
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    sign_out_default_service(&app, &store)
}

pub(crate) fn sign_out_default_service(
    app: &AppHandle,
    store: &State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    let snapshot = cached_profile_snapshot(app, store)?;
    let Some(mut profile) = snapshot.profile.clone() else {
        return Ok(profile_snapshot_status(&snapshot, app));
    };
    if profile.providers.is_empty() {
        let expected = profile.revision.as_deref().ok_or("模型配置缺少版本")?;
        let cleared = store.commit(&llm_profile_path(app)?, expected, None)?;
        return Ok(profile_snapshot_status(&cleared, app));
    }
    profile.api_key.clear();
    profile.account = None;
    profile.signed_out = true;
    persist_profile(app, store, profile)
}

/// A probe can use a saved key only for the explicitly selected service and exact saved endpoint.
/// Missing provider_id retains the default-service behavior of older clients.
pub(crate) fn saved_connection_probe_key<'a>(
    saved: Option<&'a DesktopLlmProfile>,
    provider_id: Option<&str>,
    base_url: &str,
) -> Option<&'a str> {
    let saved = saved?;
    let wanted = base_url.trim().trim_end_matches('/');
    match provider_id {
        Some(id) => saved.providers.iter()
            .find(|provider| provider.id == id.trim().to_lowercase() && provider.base_url == wanted)
            .map(|provider| provider.api_key.as_str()),
        None if saved.base_url == wanted => Some(saved.api_key.as_str()),
        None => None,
    }
}

#[tauri::command]
pub(crate) async fn test_llm_connection(
    base_url: String,
    api_key: String,
    model: String,
    provider_id: Option<String>,
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<LlmConnectionTest, String> {
    // Resolve credentials in native code; never return keys to the webview or borrow from another ID/base.
    let mut api_key = api_key.trim().to_string();
    if api_key.is_empty() {
        let saved = cached_desktop_llm_profile(&app, &store)?;
        api_key = saved_connection_probe_key(saved.as_ref(), provider_id.as_deref(), &base_url)
            .unwrap_or_default().to_string();
    }
    let profile = normalize_llm_profile(DesktopLlmProfile {
        revision: None,
        base_url,
        api_key,
        model: if model.trim().is_empty() { "-".into() } else { model },
        context_window: None,
        context_window_source: None,
        fast_model: None,
        strong_model: None,
        role_models: HashMap::new(),
        providers: Vec::new(),
        account: None,
        signed_out: false,
        model_info: Vec::new(),
        models: Vec::new(),
    })?;
    let mut request = probe_client()?.get(format!("{}/models", profile.base_url));
    if !profile.api_key.is_empty() {
        request = request.bearer_auth(&profile.api_key);
    }
    let response = match request.send().await {
        Ok(response) => response,
        Err(error) => {
            return Ok(LlmConnectionTest {
                ok: false,
                status: None,
                model_count: None,
                model_available: None,
                models: Vec::new(),
                message: if error.is_timeout() { "连接超时".into() } else { "无法连接到该地址".into() },
            })
        }
    };
    let status = response.status();
    if !status.is_success() {
        let message = match status.as_u16() {
            401 | 403 => "API Key 无效或没有权限".to_string(),
            404 => "该地址不是 OpenAI 兼容接口（/models 不存在）".to_string(),
            code => format!("服务返回 HTTP {code}"),
        };
        return Ok(LlmConnectionTest {
            ok: false,
            status: Some(status.as_u16()),
            model_count: None,
            model_available: None,
            models: Vec::new(),
            message,
        });
    }
    let payload = response.json::<serde_json::Value>().await.unwrap_or_default();
    let wanted = if profile.model == "-" { "" } else { profile.model.as_str() };
    let (count, available) = summarize_models_payload(&payload, wanted);
    let message = match available {
        Some(false) => format!("连接成功，但服务的 {count} 个模型里没有 {wanted}"),
        _ => format!("连接成功，服务提供 {count} 个模型"),
    };
    Ok(LlmConnectionTest {
        ok: available != Some(false),
        status: Some(status.as_u16()),
        model_count: Some(count),
        model_available: available,
        models: model_ids(&payload),
        message,
    })
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct LlmUsageSummary {
    pub(crate) available: bool,
    pub(crate) unlimited: bool,
    pub(crate) hard_limit_usd: Option<f64>,
    pub(crate) used_usd: Option<f64>,
    pub(crate) period_days: u32,
    pub(crate) message: String,
}

pub(crate) const USAGE_PERIOD_DAYS: i64 = 7;
// One API 对"不限额度"的令牌返回一个极大的上限（实测 1e8 美元）。
pub(crate) const UNLIMITED_QUOTA_USD: f64 = 10_000_000.0;

/// 自 1970-01-01 起的天数 → (年, 月, 日)。公历，Howard Hinnant 的 civil_from_days。
pub(crate) fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

pub(crate) fn iso_date(days: i64) -> String {
    let (year, month, day) = civil_from_days(days);
    format!("{year:04}-{month:02}-{day:02}")
}

pub(crate) fn usage_from_billing(subscription: &serde_json::Value, usage: Option<&serde_json::Value>) -> LlmUsageSummary {
    let hard_limit = subscription
        .get("hard_limit_usd")
        .or_else(|| subscription.get("system_hard_limit_usd"))
        .and_then(serde_json::Value::as_f64);
    // OpenAI 兼容的 total_usage 单位是美分。
    let used = usage
        .and_then(|value| value.get("total_usage"))
        .and_then(serde_json::Value::as_f64)
        .map(|cents| cents / 100.0);
    let unlimited = hard_limit.is_some_and(|limit| limit >= UNLIMITED_QUOTA_USD);
    LlmUsageSummary {
        available: hard_limit.is_some() || used.is_some(),
        unlimited,
        hard_limit_usd: hard_limit.filter(|_| !unlimited),
        used_usd: used,
        period_days: USAGE_PERIOD_DAYS as u32,
        message: String::new(),
    }
}

#[tauri::command]
pub(crate) async fn get_llm_usage(
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<LlmUsageSummary, String> {
    let unavailable = |message: &str| LlmUsageSummary {
        available: false,
        unlimited: false,
        hard_limit_usd: None,
        used_usd: None,
        period_days: USAGE_PERIOD_DAYS as u32,
        message: message.into(),
    };
    let Some(profile) = cached_desktop_llm_profile(&app, &store)? else {
        return Ok(unavailable("还没有配置模型服务"));
    };
    if profile.api_key.is_empty() {
        return Ok(unavailable("当前模型服务未使用 API Key，没有额度信息"));
    }
    let client = probe_client()?;
    let fetch = |path: String| {
        client
            .get(format!("{}{}", profile.base_url, path))
            .bearer_auth(&profile.api_key)
            .send()
    };
    let subscription = match fetch("/dashboard/billing/subscription".into()).await {
        Ok(response) if response.status().is_success() => {
            response.json::<serde_json::Value>().await.unwrap_or_default()
        }
        Ok(_) => return Ok(unavailable("该服务不提供额度查询")),
        Err(_) => return Ok(unavailable("暂时无法连接模型服务")),
    };
    let today = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("系统时间不可用：{error}"))?
        .as_secs() as i64
        / 86_400;
    let usage = match fetch(format!(
        "/dashboard/billing/usage?start_date={}&end_date={}",
        iso_date(today - USAGE_PERIOD_DAYS + 1),
        iso_date(today + 1)
    ))
    .await
    {
        Ok(response) if response.status().is_success() => response.json::<serde_json::Value>().await.ok(),
        _ => None,
    };
    let mut summary = usage_from_billing(&subscription, usage.as_ref());
    if !summary.available {
        summary.message = "该服务没有返回额度信息".into();
    }
    Ok(summary)
}
