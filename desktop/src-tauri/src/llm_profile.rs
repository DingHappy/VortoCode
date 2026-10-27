// 模型配置：配置结构与归一化、上下文窗口探测、配置文件读写、设置页保存/清除命令，
// 以及把配置注入 runtime 进程环境。从 lib.rs 原样搬出。
use super::*;

/// 没配置时表单预填的地址：通用的 OpenAI 官方接口（与 OpenAI SDK 的默认一致），不预设任何自营中转。
pub(crate) const DEFAULT_LLM_BASE_URL: &str = "https://api.openai.com/v1";
pub(crate) const DEFAULT_LLM_MODEL: &str = "gpt-4o-mini";
/// 作者自用的中转：不在预设里；只用来识别「当前配置就是它」，以便显示它的账号/用量页。
pub(crate) const RELAY_BASE_URL: &str = "https://token.vortotech.com/v1";
pub(crate) const RELAY_DEFAULT_MODEL: &str = "mimo-v2.6-pro";

#[derive(Clone, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopLlmProfile {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) revision: Option<String>,
    pub(crate) base_url: String,
    pub(crate) api_key: String,
    pub(crate) model: String,
    #[serde(default)]
    pub(crate) context_window: Option<u64>,
    #[serde(default)]
    pub(crate) context_window_source: Option<String>,
    /// 模型调度的快速档 / 强力档（可选）。没配时「自动」只在主模型上运行。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) fast_model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) strong_model: Option<String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub(crate) role_models: HashMap<String, String>,
    /// 用户自己添加的其他供应商（各带各的 Key）；聊天里以 `id:模型` 选用。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub(crate) providers: Vec<DesktopLlmProvider>,
    /// 经账号登录得到的中转站 Key 时，记下是谁、Key 从哪来（不含密码/会话）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) account: Option<DesktopAccount>,
    /// 已退出登录：默认服务没有 Key、不注入 runtime，但保留自定义供应商。
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub(crate) signed_out: bool,
    /// 默认服务 /models 返回的模型清单：输入框据此列出可选模型，runtime 据此放行点名。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub(crate) models: Vec<String>,
    /// 同一份清单带上服务端给的分类 / 能力 / 推荐档位（relay 2026-10-07 起提供，别家服务只有 id）。
    /// 旧配置没有这个字段：界面在启动引擎前补拉一次。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub(crate) model_info: Vec<ModelInfo>,
}

/// /models 里一个模型的描述。除 id 外都是可选的：OpenAI 兼容的别家服务不返回这些字段。
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ModelInfo {
    pub(crate) id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) category: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub(crate) capabilities: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) tier: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) snapshot_of: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) context_window: Option<u64>,
}

/// 能对话的三类：纯文本 / 图片视频输入 / 全模态。其余（OCR、翻译、生图、语音……）不能跑 Agent。
pub(crate) const CHAT_MODEL_CATEGORIES: [&str; 3] = ["chat", "multimodal", "omni"];

impl ModelInfo {
    /// 能拿来跑 Agent：能对话，且支持工具调用。
    pub(crate) fn agent_ready(&self) -> bool {
        self.category.as_deref().is_some_and(|category| CHAT_MODEL_CATEGORIES.contains(&category))
            && self.capabilities.iter().any(|capability| capability == "tools")
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopAccount {
    pub(crate) username: String,
    pub(crate) display_name: String,
    /// "token_plan" = 账号的 Token Plan Key；"pay_as_you_go" = 桌面端建的按量 Key。
    pub(crate) key_source: String,
    #[serde(default)]
    pub(crate) plan_name: Option<String>,
    #[serde(default)]
    pub(crate) plan_expiry: Option<i64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopLlmProvider {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) base_url: String,
    pub(crate) api_key: String,
    #[serde(default)]
    pub(crate) models: Vec<String>,
}

/// 给网页层看的供应商信息：**不含 Key**。
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopLlmProviderStatus {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) base_url: String,
    pub(crate) models: Vec<String>,
    pub(crate) has_key: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopLlmProfileStatus {
    pub(crate) revision: Option<String>,
    pub(crate) configured: bool,
    pub(crate) base_url: String,
    pub(crate) model: String,
    pub(crate) provider: String,
    pub(crate) requires_key: bool,
    pub(crate) context_window: Option<u64>,
    pub(crate) context_window_source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) fast_model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) strong_model: Option<String>,
    pub(crate) role_models: HashMap<String, String>,
    pub(crate) providers: Vec<DesktopLlmProviderStatus>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) account: Option<DesktopAccount>,
    pub(crate) models: Vec<String>,
    pub(crate) model_info: Vec<ModelInfo>,
    /// 本机配置文件位置；设置页显示它，方便用户直接编辑。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) config_path: Option<String>,
}

pub(crate) const MIN_MODEL_CONTEXT_WINDOW: u64 = 1_024;
pub(crate) const MAX_MODEL_CONTEXT_WINDOW: u64 = 20_000_000;

pub(crate) fn official_model_context_window(model: &str) -> Option<u64> {
    let name = model.trim().to_ascii_lowercase();
    if name.contains("mimo-v2.5-base") {
        Some(256_000)
    } else if name.contains("mimo-v2.5") {
        Some(1_000_000)
    } else {
        None
    }
}

pub(crate) fn default_llm_profile() -> DesktopLlmProfile {
    DesktopLlmProfile {
        revision: None,
        base_url: DEFAULT_LLM_BASE_URL.into(),
        api_key: String::new(),
        model: DEFAULT_LLM_MODEL.into(),
        // 默认模型的窗口只在官方资料有把握时填；没有就留空，保存时向服务探测。
        context_window: official_model_context_window(DEFAULT_LLM_MODEL),
        context_window_source: official_model_context_window(DEFAULT_LLM_MODEL).map(|_| "catalog".into()),
        fast_model: None,
        strong_model: None,
        role_models: HashMap::new(),
        providers: Vec::new(),
        account: None,
        signed_out: false,
        model_info: Vec::new(),
        models: Vec::new(),
    }
}

pub(crate) fn normalize_llm_profile(mut profile: DesktopLlmProfile) -> Result<DesktopLlmProfile, String> {
    profile.base_url = profile.base_url.trim().trim_end_matches('/').to_string();
    profile.api_key = profile.api_key.trim().to_string();
    profile.model = profile.model.trim().to_string();
    for tier in [&mut profile.fast_model, &mut profile.strong_model] {
        *tier = tier
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .map(str::to_string);
        if tier
            .as_deref()
            .is_some_and(|name| name.len() > 160 || name.chars().any(char::is_control))
        {
            return Err("调度模型名无效".into());
        }
    }
    if profile.base_url.len() > 512 || profile.model.is_empty() || profile.model.len() > 160 {
        return Err("模型服务地址或模型名无效".into());
    }
    if profile.model.chars().any(char::is_control) || profile.api_key.chars().any(char::is_control)
    {
        return Err("模型配置不能包含控制字符".into());
    }
    if profile.api_key.len() > 2_048 {
        return Err("模型服务 Key 过长".into());
    }
    if profile.context_window.is_some_and(|window| {
        !(MIN_MODEL_CONTEXT_WINDOW..=MAX_MODEL_CONTEXT_WINDOW).contains(&window)
    }) {
        return Err("模型上下文窗口必须在 1K–20M tokens 之间".into());
    }
    if profile.context_window.is_none() {
        profile.context_window = official_model_context_window(&profile.model);
        if profile.context_window.is_some() {
            profile.context_window_source = Some("catalog".into());
        }
    }
    profile.context_window_source = Some(match profile.context_window_source.as_deref() {
        Some("service" | "catalog" | "configured") => profile
            .context_window_source
            .clone()
            .unwrap_or_else(|| "unknown".into()),
        _ if profile.context_window.is_some() => "configured".into(),
        _ => "unknown".into(),
    });
    let parsed =
        Url::parse(&profile.base_url).map_err(|_| "模型服务地址不是有效 URL".to_string())?;
    if !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err("模型服务地址不能包含用户名、密码、查询参数或片段".into());
    }
    let host = parsed.host_str().unwrap_or_default();
    let local = matches!(host, "127.0.0.1" | "localhost");
    if parsed.scheme() != "https" && !(parsed.scheme() == "http" && local) {
        return Err("远程模型服务必须使用 HTTPS；HTTP 仅允许 127.0.0.1 / localhost".into());
    }
    if !local && profile.api_key.is_empty() && !profile.signed_out {
        return Err("远程模型服务需要 API Key".into());
    }
    // 配置文件可以手改：清单会拼进环境变量，读回来也要清洗一遍。
    profile.models = clean_model_list(std::mem::take(&mut profile.models));
    profile.model_info = clean_model_infos(std::mem::take(&mut profile.model_info));
    profile.role_models = normalize_role_models(&profile)?;
    Ok(profile)
}

pub(crate) fn normalize_role_models(profile: &DesktopLlmProfile) -> Result<HashMap<String, String>, String> {
    let mut roles = HashMap::new();
    for (role, value) in &profile.role_models {
        if !matches!(role.as_str(), "research" | "implement" | "review" | "assess") {
            return Err("不支持的子 Agent 模型角色".into());
        }
        let value = value.trim();
        if value.is_empty() { continue; }
        if value.len() > 160 || value.chars().any(|c| c.is_control() || c == ',' || c == '=') {
            return Err("子 Agent 模型名无效".into());
        }
        // `cli:codex` / `cli:claude`：交给本机已登录的外部 CLI（只开放实现与审查两个角色）。
        if let Some(worker) = value.strip_prefix("cli:") {
            if !matches!(role.as_str(), "implement" | "review") {
                return Err("只有实现与审查角色可以交给外部 CLI".into());
            }
            if !EXTERNAL_WORKERS.contains(&worker) {
                return Err("不支持的外部 CLI（可选 codex、claude）".into());
            }
            roles.insert(role.clone(), value.to_string());
            continue;
        }
        let target = if !value.contains(':') || model_choice_ids(profile).iter().any(|id| id == value) {
            format!("default:{value}")
        } else {
            let (provider, model) = value.split_once(':').ok_or("子 Agent 模型配置无效")?;
            if model.is_empty() { return Err("子 Agent 模型名不能为空".into()); }
            if provider != "default" && !profile.providers.iter().any(|item| item.id == provider
                && (!item.api_key.is_empty() || llm_provider(&item.base_url) == "local")) {
                return Err("子 Agent 使用的供应商未配置或缺少凭据".into());
            }
            value.to_string()
        };
        roles.insert(role.clone(), target);
    }
    Ok(roles)
}

pub(crate) const EXTERNAL_WORKERS: [&str; 2] = ["codex", "claude"];

pub(crate) fn external_worker_label(worker: &str) -> &'static str {
    match worker {
        "codex" => "Codex CLI（本机）",
        "claude" => "Claude Code CLI（本机）",
        _ => "外部 CLI",
    }
}

/// 角色表里的 `cli:*` 分流成 VORTOCODE_EXTERNAL_WORKERS；其余照旧进 VORTOCODE_MODEL_ROLES。
/// 找 CLI、补 PATH、查登录都在引擎内核里做（同一份实现服务本机与常驻电脑），这里只传角色表。
pub(crate) fn configure_external_workers(command: &mut Command, profile: &DesktopLlmProfile) {
    let mut roles: Vec<_> = profile.role_models.iter().collect();
    roles.sort_by_key(|(role, _)| *role);
    let (external, models): (Vec<_>, Vec<_>) = roles.into_iter().partition(|(_, value)| value.starts_with("cli:"));
    command.env("VORTOCODE_MODEL_ROLES", models.iter().map(|(role, model)| format!("{role}={model}")).collect::<Vec<_>>().join(","));
    command.env("VORTOCODE_EXTERNAL_WORKERS", external.iter()
        .map(|(role, value)| format!("{role}={}", value.trim_start_matches("cli:"))).collect::<Vec<_>>().join(","));
    // 实验开关不随模型配置走，只由引擎启动处按 Desktop 偏好打开（configure_experimental_workers）。
    command.env_remove("VORTOCODE_EXPERIMENTAL_EXTERNAL_WORKERS");
}

pub(crate) fn context_window_number(value: &serde_json::Value) -> Option<u64> {
    let number = value.as_u64().or_else(|| {
        value
            .as_str()
            .and_then(|raw| raw.trim().parse::<u64>().ok())
    })?;
    (MIN_MODEL_CONTEXT_WINDOW..=MAX_MODEL_CONTEXT_WINDOW)
        .contains(&number)
        .then_some(number)
}

pub(crate) fn context_window_from_metadata(value: &serde_json::Value) -> Option<u64> {
    let object = value.as_object()?;
    pub(crate) const DIRECT_KEYS: [&str; 8] = [
        "context_window",
        "context_length",
        "max_context_length",
        "max_model_len",
        "max_sequence_length",
        "input_token_limit",
        "max_input_tokens",
        "n_ctx",
    ];
    for key in DIRECT_KEYS {
        if let Some(window) = object.get(key).and_then(context_window_number) {
            return Some(window);
        }
    }
    for key in ["capabilities", "limits", "metadata", "model_info"] {
        if let Some(window) = object.get(key).and_then(context_window_from_metadata) {
            return Some(window);
        }
    }
    None
}

pub(crate) fn context_window_from_models_payload(payload: &serde_json::Value, model: &str) -> Option<u64> {
    let models = payload.get("data")?.as_array()?;
    let wanted = model.trim();
    models.iter().find_map(|item| {
        let id = item
            .get("id")
            .or_else(|| item.get("model"))
            .or_else(|| item.get("name"))
            .and_then(serde_json::Value::as_str)?;
        id.eq_ignore_ascii_case(wanted)
            .then(|| context_window_from_metadata(item))
            .flatten()
    })
}

pub(crate) const LLM_PROBE_TIMEOUT: Duration = Duration::from_secs(8);

pub(crate) async fn discover_model_context_window_with(
    profile: &DesktopLlmProfile,
    timeout: Duration,
) -> Option<u64> {
    // 探测收敛：只允许打 profile 声明的 base_url 本身——限时，且不跟随重定向，
    // 避免探测阶段被当跳板打别的 host、或 Bearer Key 随跳转外流。
    let client = reqwest::Client::builder()
        .timeout(timeout)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .ok()?;
    let mut request = client.get(format!("{}/models", profile.base_url));
    if !profile.api_key.is_empty() {
        request = request.bearer_auth(&profile.api_key);
    }
    let response = request.send().await.ok()?;
    if !response.status().is_success() {
        return None;
    }
    let payload = response.json::<serde_json::Value>().await.ok()?;
    context_window_from_models_payload(&payload, &profile.model)
}

pub(crate) fn llm_provider(base_url: &str) -> &'static str {
    if base_url == RELAY_BASE_URL {
        "vortocode"
    } else if Url::parse(base_url)
        .ok()
        .and_then(|url| url.host_str().map(str::to_string))
        .is_some_and(|host| matches!(host.as_str(), "127.0.0.1" | "localhost"))
    {
        "local"
    } else {
        "custom"
    }
}

// 模型服务配置存在 Desktop 配置目录下的 JSON 文件里（与 projects.json 同目录，权限 600），
// 不再放 macOS Keychain：自签名/开发构建每换一次二进制，Keychain 就要求重新授权一次。
// 文件可直接手动编辑，下次启动生效；格式与设置页保存的一致（camelCase）。
pub(crate) const LLM_PROFILE_FILE: &str = "llm-profile.json";

pub(crate) fn llm_profile_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join(LLM_PROFILE_FILE))
        .map_err(|error| format!("无法定位 Desktop 配置目录：{error}"))
}

pub(crate) fn read_llm_profile_file(path: &Path) -> Result<Option<DesktopLlmProfile>, String> {
    let payload = match std::fs::read_to_string(path) {
        Ok(payload) => payload,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("无法读取模型配置文件 {}：{error}", path.display())),
    };
    let profile: DesktopLlmProfile = serde_json::from_str(&payload).map_err(|_| {
        format!("模型配置文件格式有误：{}；请在 Desktop 设置中重新保存", path.display())
    })?;
    normalize_llm_profile(profile).map(Some)
}

pub(crate) fn save_llm_profile_file(path: &Path, profile: &DesktopLlmProfile) -> Result<(), String> {
    // 含 API Key：只允许当前账户读写。
    save_json_atomic_with_mode(path, "llm-profile", profile, Some(0o600))
}

pub(crate) fn delete_llm_profile_file(path: &Path) -> Result<(), String> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("无法删除模型配置文件 {}：{error}", path.display())),
    }
}

pub(crate) fn cached_desktop_llm_profile(
    app: &AppHandle,
    store: &DesktopLlmProfileStore,
) -> Result<Option<DesktopLlmProfile>, String> {
    let path = llm_profile_path(app)?;
    store.get_or_try_init(|| read_llm_profile_file(&path))
}

pub(crate) fn with_config_path(mut status: DesktopLlmProfileStatus, app: &AppHandle) -> DesktopLlmProfileStatus {
    status.config_path = llm_profile_path(app).ok().map(|path| path.display().to_string());
    status
}

pub(crate) fn desktop_llm_profile_status(profile: Option<&DesktopLlmProfile>) -> DesktopLlmProfileStatus {
    let fallback = default_llm_profile();
    let profile = profile.unwrap_or(&fallback);
    DesktopLlmProfileStatus {
        revision: profile.revision.clone(),
        configured: !profile.signed_out
            && (profile.api_key.len() > 0 || llm_provider(&profile.base_url) == "local"),
        base_url: profile.base_url.clone(),
        model: profile.model.clone(),
        provider: llm_provider(&profile.base_url).into(),
        requires_key: llm_provider(&profile.base_url) != "local",
        context_window: profile.context_window,
        context_window_source: profile
            .context_window_source
            .clone()
            .unwrap_or_else(|| "unknown".into()),
        fast_model: profile.fast_model.clone(),
        strong_model: profile.strong_model.clone(),
        role_models: profile.role_models.clone(),
        providers: profile
            .providers
            .iter()
            .map(|provider| DesktopLlmProviderStatus {
                id: provider.id.clone(),
                name: provider.name.clone(),
                base_url: provider.base_url.clone(),
                models: provider.models.clone(),
                has_key: !provider.api_key.is_empty(),
            })
            .collect(),
        account: profile.account.clone(),
        models: profile.models.clone(),
        model_info: profile.model_info.clone(),
        config_path: None,
    }
}

// 会做磁盘 / 进程 / Keychain 等可能阻塞操作的命令不能跑在主线程：阻塞期间整个窗口白屏
// （2026-10-06 真机复现：Keychain 授权弹窗等待期间白屏）。`async` 让 Tauri 在后台线程执行。
#[tauri::command(async)]
pub(crate) fn get_llm_profile(
    app: AppHandle,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, String> {
    let snapshot = cached_profile_snapshot(&app, &store)?;
    Ok(profile_snapshot_status(&snapshot, &app))
}

/// 原生确认框必须挂到主窗口上（弹成 sheet）。rfd 在 macOS 上的无父窗口消息框会建出窗口却
/// 从不显示，`blocking_show` 因此永远挂起——2026-10-06 真机复现：确认框不出现、命令不返回。
pub(crate) fn with_main_window_parent(
    app: &AppHandle,
    dialog: tauri_plugin_dialog::MessageDialogBuilder<tauri::Wry>,
) -> tauri_plugin_dialog::MessageDialogBuilder<tauri::Wry> {
    match app.get_webview_window("main") {
        Some(window) => dialog.parent(&window),
        None => dialog,
    }
}

pub(crate) async fn confirm_llm_profile_change(
    app: &AppHandle,
    profile: &DesktopLlmProfile,
) -> Result<bool, String> {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    // 改档是敏感动作：base_url+Key 会落 Keychain 并注入后续 runtime 环境。原生对话框在
    // webview 进程之外，被注入的页面脚本无法替用户点「确认」，静默改档因此不生效。
    let tiers = match (&profile.fast_model, &profile.strong_model) {
        (None, None) => String::new(),
        (fast, strong) => format!(
            "\n快速模型：{}\n强力模型：{}",
            fast.as_deref().unwrap_or("（同主模型）"),
            strong.as_deref().unwrap_or("（同主模型）")
        ),
    };
    let mut roles: Vec<_> = profile.role_models.iter().collect();
    roles.sort_by_key(|(role, _)| *role);
    let roles = roles.iter().map(|(role, model)| format!("\n{}：{}", match role.as_str() {
        "research" => "研究子 Agent", "implement" => "实现子 Agent", "review" => "审查子 Agent", "assess" => "模型评判器（按需额外调用）", _ => role,
    }, model.strip_prefix("cli:").map(external_worker_label).unwrap_or(model.as_str()))).collect::<String>();
    let message = format!(
        "网页层请求把模型服务改为：\n\n服务地址：{}\n主模型：{}{}\n子 Agent 分工：{}\n\n仅当这是你刚在设置页保存的配置时才确认。",
        profile.base_url, profile.model, tiers, if roles.is_empty() { "未单独配置" } else { &roles }
    );
    let app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        with_main_window_parent(&app, app.dialog().message(message))
            .title("确认修改模型服务")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom(
                "确认修改".into(),
                "取消".into(),
            ))
            .blocking_show()
    })
    .await
    .map_err(|error| format!("模型服务确认对话框失败：{error}"))
}

pub(crate) async fn save_llm_profile_flow(
    profile: DesktopLlmProfile,
    confirmed: bool,
    probe_timeout: Duration,
    persist: impl FnOnce(&DesktopLlmProfile) -> Result<(), String>,
) -> Result<DesktopLlmProfile, String> {
    let mut profile = normalize_llm_profile(profile)?;
    // 确认门在探测之前：未获用户确认时连一次出网探测都不发生。
    if !confirmed {
        return Err("模型服务修改未获用户确认，已取消".into());
    }
    if let Some(window) = discover_model_context_window_with(&profile, probe_timeout).await {
        profile.context_window = Some(window);
        profile.context_window_source = Some("service".into());
    }
    persist(&profile)?;
    Ok(profile)
}

#[tauri::command]
pub(crate) async fn set_llm_profile(
    app: AppHandle,
    base_url: String,
    api_key: String,
    model: String,
    fast_model: Option<String>,
    strong_model: Option<String>,
    role_models: Option<HashMap<String, String>>,
    expected_revision: Option<String>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, ModelProfileMutationError> {
    // 只改模型名 / 调度档时不必重输 Key：留空则沿用已保存的 Key，规则同 test_llm_connection
    // （仅限同一地址，Key 绝不借给别的服务）。
    let snapshot = cached_profile_snapshot(&app, &store)?;
    snapshot.check_expected(expected_revision.as_deref())?;
    let saved = snapshot.profile;
    let mut api_key = api_key.trim().to_string();
    if api_key.is_empty() {
        let wanted = base_url.trim().trim_end_matches('/');
        if let Some(saved) = saved.as_ref() {
            if saved.base_url == wanted {
                api_key = saved.api_key.clone();
            }
        }
    }
    // 地址和 Key 都没变（只改了模型名 / 调度档）时保留登录信息；换了 Key 或地址就不再是账号给的那把了。
    let wanted_base = base_url.trim().trim_end_matches('/').to_string();
    let account = saved
        .as_ref()
        .filter(|saved| saved.base_url == wanted_base && saved.api_key == api_key)
        .and_then(|saved| saved.account.clone());
    // 同一个服务地址：模型清单沿用（换地址后由界面重新拉取）。
    let (models, model_info) = saved
        .as_ref()
        .filter(|saved| saved.base_url == wanted_base)
        .map(|saved| (saved.models.clone(), saved.model_info.clone()))
        .unwrap_or_default();
    let profile = normalize_llm_profile(DesktopLlmProfile {
        revision: Some(snapshot.revision),
        base_url,
        api_key,
        model,
        context_window: None,
        context_window_source: None,
        fast_model,
        strong_model,
        role_models: role_models.or_else(|| saved.as_ref().map(|profile| profile.role_models.clone())).unwrap_or_default(),
        // 改默认模型服务时保留已添加的其他供应商（它们各带各的 Key，与默认服务无关）。
        providers: saved.map(|saved| saved.providers).unwrap_or_default(),
        account,
        signed_out: false,
        models,
        model_info,
    })?;
    let confirmed = confirm_llm_profile_change(&app, &profile).await?;
    let profile = if confirmed { with_model_list(profile).await } else { profile };
    let profile = save_llm_profile_flow(profile, confirmed, LLM_PROBE_TIMEOUT, |_| Ok(())).await?;
    persist_profile(&app, &store, profile).map_err(Into::into)
}

// 可能阻塞：移出主线程，原因见 get_llm_profile。
#[tauri::command(async)]
pub(crate) fn clear_llm_profile(
    app: AppHandle,
    expected_revision: Option<String>,
    store: State<'_, DesktopLlmProfileStore>,
) -> Result<DesktopLlmProfileStatus, ModelProfileMutationError> {
    let snapshot = cached_profile_snapshot(&app, &store)?;
    snapshot.check_expected(expected_revision.as_deref())?;
    let cleared = store.commit(&llm_profile_path(&app)?, &snapshot.revision, None)?;
    Ok(profile_snapshot_status(&cleared, &app))
}

pub(crate) fn configure_llm_profile(command: &mut Command, profile: Option<&DesktopLlmProfile>) {
    let Some(profile) = profile else {
        return;
    };
    configure_external_workers(command, profile);
    if profile.signed_out {
        // 退出登录后默认服务不可用；自定义供应商仍各用各的 Key。
        for provider in &profile.providers {
            let id = provider.id.to_uppercase();
            command.env(format!("VORTOCODE_PROVIDER_{id}_BASE"), &provider.base_url);
            command.env(format!("VORTOCODE_PROVIDER_{id}_KEY"), &provider.api_key);
        }
        return;
    }
    command.env("OPENAI_API_BASE", &profile.base_url);
    command.env(
        "OPENAI_API_KEY",
        if profile.api_key.is_empty() {
            "not-required"
        } else {
            &profile.api_key
        },
    );
    command.env("DEFAULT_MODEL", &profile.model);
    // 默认服务上可点名的模型清单（同一端点、同一把 Key）。
    let choices = model_choice_ids(profile);
    if choices.is_empty() {
        command.env_remove("VORTOCODE_MODEL_CHOICES");
    } else {
        command.env("VORTOCODE_MODEL_CHOICES", choices.join(","));
    }
    // 调度三档：均衡档就是主模型；快速 / 强力没配时显式清掉，免得继承到父进程环境里的旧值。
    command.env("LLM_MODEL_BALANCED", &profile.model);
    match &profile.fast_model {
        Some(model) => command.env("LLM_MODEL_CHEAP", model),
        None => command.env_remove("LLM_MODEL_CHEAP"),
    };
    match &profile.strong_model {
        Some(model) => command.env("LLM_MODEL_POWERFUL", model),
        None => command.env_remove("LLM_MODEL_POWERFUL"),
    };
    // 自定义供应商：runtime 的 src/llm/providers.py 按这对变量登记端点，key 只随自己的端点走。
    for provider in &profile.providers {
        let id = provider.id.to_uppercase();
        command.env(format!("VORTOCODE_PROVIDER_{id}_BASE"), &provider.base_url);
        command.env(format!("VORTOCODE_PROVIDER_{id}_KEY"), &provider.api_key);
    }
    if let Some(window) = profile.context_window {
        command.env("VORTOCODE_MODEL_CONTEXT_WINDOW", window.to_string());
        command.env(
            "VORTOCODE_MODEL_CONTEXT_WINDOW_SOURCE",
            profile
                .context_window_source
                .as_deref()
                .unwrap_or("configured"),
        );
    }
}
