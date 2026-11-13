//! The local resident is an independent server, never a Desktop-owned child.
//! Files, launchd destinations and commands are generated here, not by the webview.

use super::*;
use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::sync::Arc;

const NOT_INSTALLED: &str = "not-installed";
const CONFIG_LIMIT: u64 = 64 * 1024;
// Registry descriptor only; this feature never opens the reserved TCP port.
const VIRTUAL_ORIGIN: &str = "http://127.0.0.1:18731";
const VIRTUAL_PORT: u16 = 18731;

#[derive(Default)]
pub(crate) struct LocalResidentManager(Arc<Mutex<()>>);

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct LocalResidentStatus {
    supported: bool,
    installed: bool,
    state: &'static str,
    version: Option<String>,
    available_version: String,
    cron_enabled: bool,
    project_id: Option<String>,
    launch_id: Option<String>,
    revision: String,
    model_revision: Option<String>,
    message: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ServiceConfig {
    schema_version: u32,
    revision: String,
    runtime_version: String,
    workspace_root: String,
    port: u16,
    cron_enabled: bool,
    model_revision: String,
    token: String,
    model_env: BTreeMap<String, Option<String>>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Installation {
    schema_version: u32,
    revision: String,
    release_id: String,
    version: String,
    project_id: Option<String>,
}

#[derive(Clone)]
struct ResidentPaths {
    root: PathBuf,
    agent: PathBuf,
    label: String,
    domain: String,
}

impl ResidentPaths {
    fn from_app(app: &AppHandle) -> Result<Self, String> {
        let suffix = if cfg!(debug_assertions) { ".dev" } else { "" };
        let label = format!("com.vortoone.desktop.local-resident{suffix}");
        Ok(Self {
            root: app
                .path()
                .app_config_dir()
                .map_err(|_| "无法定位本机常驻资料目录")?
                .join("local-resident"),
            agent: app
                .path()
                .home_dir()
                .map_err(|_| "无法定位登录项目录")?
                .join("Library/LaunchAgents")
                .join(format!("{label}.plist")),
            domain: format!("gui/{}", current_uid()),
            label,
        })
    }

    fn record(&self) -> PathBuf {
        self.root.join("service.json")
    }
    fn workspace(&self) -> PathBuf {
        self.root.join("assistant")
    }
    fn config(&self, record: &Installation) -> PathBuf {
        self.root
            .join("generations")
            .join(&record.revision)
            .join("service-config.json")
    }
    fn runtime(&self, record: &Installation) -> PathBuf {
        self.root.join("releases").join(&record.release_id)
    }
    fn target(&self) -> String {
        format!("{}/{}", self.domain, self.label)
    }
}

#[cfg(unix)]
fn current_uid() -> u32 {
    unsafe { libc::geteuid() }
}
#[cfg(not(unix))]
fn current_uid() -> u32 {
    0
}

fn private_dir(path: &Path) -> Result<(), String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                if metadata.uid() != current_uid() {
                    return Err("本机常驻目录不属于当前用户".into());
                }
            }
        }
        Ok(_) => return Err("本机常驻目录包含不安全的链接或文件".into()),
        Err(error) if error.kind() == ErrorKind::NotFound => {
            fs::create_dir_all(path).map_err(|_| "无法创建本机常驻目录")?;
        }
        Err(_) => return Err("无法核对本机常驻目录".into()),
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))
            .map_err(|_| "无法保护本机常驻目录")?;
    }
    Ok(())
}

fn read_private<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T, String> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options
        .open(path)
        .map_err(|_| "无法读取本机常驻配置；请核对安装状态")?;
    let metadata = file.metadata().map_err(|_| "无法核对本机常驻配置")?;
    if !metadata.is_file() || metadata.len() > CONFIG_LIMIT {
        return Err("本机常驻配置无效".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.uid() != current_uid() || metadata.mode() & 0o777 != 0o600 {
            return Err("本机常驻配置必须仅由当前用户读写".into());
        }
    }
    serde_json::from_reader(file.take(CONFIG_LIMIT + 1)).map_err(|_| "本机常驻配置格式无效".into())
}

fn identity(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn load_installation(
    paths: &ResidentPaths,
) -> Result<Option<(Installation, ServiceConfig)>, String> {
    match fs::symlink_metadata(paths.record()) {
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("无法核对本机常驻安装记录".into()),
        Ok(_) => {}
    }
    let record: Installation = read_private(&paths.record())?;
    if record.schema_version != 1
        || !identity(&record.revision)
        || record.release_id.len() != 64
        || !record.release_id.bytes().all(|v| v.is_ascii_hexdigit())
    {
        return Err("本机常驻安装记录无效".into());
    }
    // Never follow a generation-directory link to another user's configuration.
    for directory in [
        paths.root.clone(),
        paths.root.join("generations"),
        paths.config(&record).parent().unwrap().to_path_buf(),
        paths.workspace(),
    ] {
        let metadata = fs::symlink_metadata(&directory).map_err(|_| "本机常驻资料目录不可用")?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return Err("本机常驻资料目录无效".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if metadata.uid() != current_uid() || metadata.mode() & 0o777 != 0o700 {
                return Err("本机常驻资料目录必须仅由当前用户访问".into());
            }
        }
    }
    let config: ServiceConfig = read_private(&paths.config(&record))?;
    let workspace = paths
        .workspace()
        .canonicalize()
        .map_err(|_| "无法核对本机常驻资料范围")?;
    if config.schema_version != 1
        || config.revision != record.revision
        || config.runtime_version != record.version
        || Path::new(&config.workspace_root) != workspace
        || config.port < 1024
        || !identity(&config.model_revision)
        || !(32..=256).contains(&config.token.len())
        || !config
            .token
            .bytes()
            .all(|byte| (0x21..=0x7e).contains(&byte))
        || !valid_model_env(&config.model_env)
    {
        return Err("本机常驻配置与登记范围不符".into());
    }
    Ok(Some((record, config)))
}

fn require_installation(
    paths: &ResidentPaths,
    expected: &str,
) -> Result<(Installation, ServiceConfig), String> {
    let pair = load_installation(paths)?.ok_or("尚未安装本机常驻服务")?;
    if pair.0.revision != expected {
        return Err("本机常驻配置已变化，请刷新后重试".into());
    }
    Ok(pair)
}

fn model_env_key(name: &str) -> bool {
    if matches!(
        name,
        "OPENAI_API_BASE"
            | "OPENAI_API_KEY"
            | "DEFAULT_MODEL"
            | "LLM_MODEL_BALANCED"
            | "LLM_MODEL_CHEAP"
            | "LLM_MODEL_POWERFUL"
            | "VORTOONE_MODEL_ROLES"
            | "VORTOONE_EXTERNAL_WORKERS"
            | "VORTOONE_MODEL_CHOICES"
            | "VORTOONE_MODEL_CONTEXT_WINDOW"
            | "VORTOONE_MODEL_CONTEXT_WINDOW_SOURCE"
    ) {
        return true;
    }
    name.strip_prefix("VORTOONE_PROVIDER_")
        .and_then(|name| {
            name.strip_suffix("_BASE")
                .or_else(|| name.strip_suffix("_KEY"))
        })
        .is_some_and(|id| {
            id.as_bytes().first().is_some_and(u8::is_ascii_uppercase)
                && id.len() <= 24
                && id
                    .bytes()
                    .all(|v| v.is_ascii_uppercase() || v.is_ascii_digit() || v == b'_')
        })
}

fn valid_model_env(env: &BTreeMap<String, Option<String>>) -> bool {
    env.len() <= 64
        && env.keys().all(|key| model_env_key(key))
        && env
            .values()
            .flatten()
            .all(|value| value.len() <= 8192 && !value.contains(['\0', '\r', '\n']))
        && ["OPENAI_API_BASE", "DEFAULT_MODEL"].iter().all(|name| {
            env.get(*name)
                .and_then(|value| value.as_deref())
                .is_some_and(|value| !value.trim().is_empty())
        })
}

#[derive(Clone)]
struct ModelSnapshot {
    revision: String,
    env: BTreeMap<String, Option<String>>,
}

#[derive(Clone, Deserialize)]
struct ActivityReport {
    version: u32,
    ready: bool,
    launch_id: String,
    scope: String,
    workspace_root: String,
    counts: BTreeMap<String, i64>,
}

#[derive(Default)]
struct LaunchOutput {
    code: i32,
    stdout: String,
}

trait ResidentHost {
    fn capabilities(&mut self, executable: &Path) -> Result<(), String>;
    fn launchctl(&mut self, arguments: &[String]) -> Result<LaunchOutput, String>;
    fn report(&mut self, config: &ServiceConfig) -> Result<ActivityReport, String>;
    fn confirm(&mut self, text: &str) -> Result<bool, String>;
    fn model(&mut self) -> Result<ModelSnapshot, String>;
    fn register(&mut self, config: &ServiceConfig) -> Result<DesktopProjectProfile, String>;
    fn select(&mut self, config: &ServiceConfig) -> Result<(), String>;
    fn remove_agent(&mut self, path: &Path) -> Result<(), String> {
        match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
            Err(_) => Err("无法移除常驻登录项，未声明停止完成".into()),
        }
    }
}

fn loaded(paths: &ResidentPaths, host: &mut impl ResidentHost) -> Result<bool, String> {
    let output = host.launchctl(&["print".into(), paths.target()])?;
    match output.code {
        0 => Ok(true),
        3 | 113 => Ok(false),
        _ => Err("无法核对本机常驻登录项状态".into()),
    }
}

fn valid_report(config: &ServiceConfig, report: &ActivityReport) -> Result<(), String> {
    if report.version != 1
        || !report.ready
        || !identity(&report.launch_id)
        || report.scope != "general"
        || report.workspace_root != config.workspace_root
        || ACTIVITY_COUNTS
            .iter()
            .any(|key| !report.counts.get(*key).is_some_and(|count| *count >= 0))
    {
        return Err("无法核对本机常驻实例，请刷新后重试".into());
    }
    Ok(())
}

const ACTIVITY_COUNTS: [&str; 7] = [
    "turns",
    "queued_inputs",
    "confirmations",
    "tasks",
    "commands",
    "terminals",
    "writes",
];

fn assert_idle(
    root: &Path,
    expected_launch: &str,
    launch: &str,
    scope: &str,
    workspace: &str,
    counts: Option<&BTreeMap<String, i64>>,
) -> Result<(), String> {
    if expected_launch.is_empty()
        || launch != expected_launch
        || scope != "general"
        || Path::new(workspace) != root
    {
        return Err("本机常驻实例已变化，未执行停止；请刷新后重试".into());
    }
    let counts = counts.ok_or("无法核对本机常驻活动，未执行停止")?;
    if ACTIVITY_COUNTS
        .iter()
        .any(|key| !counts.get(*key).is_some_and(|count| *count == 0))
    {
        return Err("本机常驻仍有活动或活动记录不全；请先处理会话、确认、后台任务和终端".into());
    }
    Ok(())
}

fn copy_runtime_tree(source: &Path, target: &Path) -> Result<String, String> {
    if !source.join("vortocode-runtime").is_file() {
        return Err("当前 App 未包含可安装的完整 runtime".into());
    }
    let source = source.canonicalize().map_err(|_| "无法核对内置 runtime")?;
    let expected = tree_digest(&source)?;
    private_dir(target)?;
    copy_children(&source, &source, target)?;
    let copied = tree_digest(target)?;
    if copied != expected || tree_digest(&source)? != expected {
        return Err("复制期间 runtime 已变化；未发布安装".into());
    }
    Ok(copied)
}

fn sorted_entries(path: &Path) -> Result<Vec<fs::DirEntry>, String> {
    let mut entries = fs::read_dir(path)
        .map_err(|_| "无法读取 runtime 目录")?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| "无法读取 runtime 文件")?;
    entries.sort_by_key(|entry| entry.file_name());
    Ok(entries)
}

fn tree_digest(root: &Path) -> Result<String, String> {
    fn visit(root: &Path, path: &Path, hash: &mut Sha256) -> Result<(), String> {
        for entry in sorted_entries(path)? {
            let file = entry.path();
            let metadata = fs::symlink_metadata(&file).map_err(|_| "无法核对 runtime 文件")?;
            hash.update(
                file.strip_prefix(root)
                    .unwrap()
                    .to_string_lossy()
                    .as_bytes(),
            );
            hash.update([0]);
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                hash.update(metadata.permissions().mode().to_le_bytes());
            }
            if metadata.file_type().is_symlink() {
                let link = fs::read_link(&file).map_err(|_| "无法核对 runtime 链接")?;
                if link.is_absolute()
                    || !file
                        .canonicalize()
                        .map_err(|_| "runtime 包含失效链接")?
                        .starts_with(root)
                {
                    return Err("runtime 包含越界链接，未安装".into());
                }
                hash.update(b"link");
                hash.update(link.to_string_lossy().as_bytes());
            } else if metadata.is_dir() {
                hash.update(b"dir");
                visit(root, &file, hash)?;
            } else if metadata.is_file() {
                hash.update(b"file");
                let mut source = fs::File::open(file).map_err(|_| "无法读取 runtime 文件")?;
                let mut bytes = [0_u8; 65536];
                loop {
                    let count = source
                        .read(&mut bytes)
                        .map_err(|_| "无法读取 runtime 文件")?;
                    if count == 0 {
                        break;
                    }
                    hash.update(&bytes[..count]);
                }
            } else {
                return Err("runtime 包含不支持的文件类型".into());
            }
            hash.update([0]);
        }
        Ok(())
    }
    let root = root.canonicalize().map_err(|_| "无法核对 runtime 目录")?;
    let mut hash = Sha256::new();
    visit(&root, &root, &mut hash)?;
    Ok(format!("{:x}", hash.finalize()))
}

fn copy_children(source_root: &Path, source: &Path, target: &Path) -> Result<(), String> {
    for entry in sorted_entries(source)? {
        let source = entry.path();
        let target = target.join(entry.file_name());
        let metadata = fs::symlink_metadata(&source).map_err(|_| "无法读取内置 runtime")?;
        if metadata.file_type().is_symlink() {
            let link = fs::read_link(&source).map_err(|_| "无法读取 runtime 链接")?;
            if link.is_absolute()
                || !source
                    .canonicalize()
                    .map_err(|_| "runtime 链接无效")?
                    .starts_with(source_root)
            {
                return Err("runtime 包含越界链接，未安装".into());
            }
            #[cfg(unix)]
            std::os::unix::fs::symlink(link, target).map_err(|_| "无法复制 runtime 链接")?;
            #[cfg(not(unix))]
            return Err("当前平台不支持本机常驻安装".into());
        } else if metadata.is_dir() {
            fs::create_dir(&target).map_err(|_| "无法复制 runtime 目录")?;
            copy_children(source_root, &source, &target)?;
            fs::set_permissions(target, metadata.permissions())
                .map_err(|_| "无法保留 runtime 目录权限")?;
        } else if metadata.is_file() {
            fs::copy(&source, &target).map_err(|_| "无法复制完整 runtime")?;
            fs::set_permissions(target, metadata.permissions())
                .map_err(|_| "无法保留 runtime 文件权限")?;
        } else {
            return Err("runtime 包含不支持的文件类型".into());
        }
    }
    Ok(())
}

fn stage_runtime(
    paths: &ResidentPaths,
    source: &Path,
    host: &mut impl ResidentHost,
) -> Result<String, String> {
    private_dir(&paths.root)?;
    private_dir(&paths.root.join("releases"))?;
    let stage = paths
        .root
        .join("releases")
        .join(format!("stage-{}", new_profile_revision()));
    let result = (|| {
        let digest = copy_runtime_tree(source, &stage)?;
        host.capabilities(&stage.join("vortocode-runtime"))?;
        let release = paths.root.join("releases").join(&digest);
        if release.exists() {
            if fs::symlink_metadata(&release)
                .map_err(|_| "无法核对已安装 runtime")?
                .file_type()
                .is_symlink()
                || tree_digest(&release)? != digest
            {
                return Err("已安装 runtime 与发布记录不符".into());
            }
        } else {
            fs::rename(&stage, &release).map_err(|_| "无法发布本机常驻 runtime")?;
        }
        Ok(digest)
    })();
    if stage.exists() {
        let _ = fs::remove_dir_all(stage);
    }
    result
}

fn stage_config(paths: &ResidentPaths, config: &ServiceConfig) -> Result<(), String> {
    if !valid_model_env(&config.model_env) {
        return Err("独立常驻模型配置不完整或含不支持的字段，未保存".into());
    }
    if serde_json::to_vec_pretty(config)
        .map_err(|_| "无法序列化常驻配置")?
        .len() as u64
        > CONFIG_LIMIT
    {
        return Err("独立常驻模型配置超过大小上限，未保存".into());
    }
    private_dir(&paths.root)?;
    private_dir(&paths.root.join("generations"))?;
    let folder = paths.root.join("generations").join(&config.revision);
    // A generation is immutable once published, including when an update fails.
    fs::create_dir(&folder).map_err(|_| "无法创建本机常驻配置代次")?;
    private_dir(&folder)?;
    save_json_atomic_with_mode(
        &folder.join("service-config.json"),
        "resident-config",
        config,
        Some(0o600),
    )
}

fn save_installation(paths: &ResidentPaths, record: &Installation) -> Result<(), String> {
    save_json_atomic_with_mode(
        &paths.record(),
        "resident-installation",
        record,
        Some(0o600),
    )
}

fn xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn launch_agent(paths: &ResidentPaths, record: &Installation) -> String {
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{}</string>
<key>ProgramArguments</key><array><string>{}</string><string>resident-server</string><string>--config</string><string>{}</string></array>
<key>WorkingDirectory</key><string>{}</string>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
<key>ThrottleInterval</key><integer>10</integer>
<key>StandardOutPath</key><string>{}</string><key>StandardErrorPath</key><string>{}</string>
</dict></plist>
"#,
        xml(&paths.label),
        xml(&paths
            .runtime(record)
            .join("vortocode-runtime")
            .to_string_lossy()),
        xml(&paths.config(record).to_string_lossy()),
        xml(&paths.workspace().to_string_lossy()),
        xml(&paths.root.join("logs/service.log").to_string_lossy()),
        xml(&paths.root.join("logs/service.log").to_string_lossy())
    )
}

fn check_agent(paths: &ResidentPaths, record: &Installation) -> Result<(), String> {
    if fs::symlink_metadata(&paths.agent).is_err_and(|e| e.kind() == ErrorKind::NotFound) {
        return Ok(());
    }
    let metadata = fs::symlink_metadata(&paths.agent).map_err(|_| "无法核对本机常驻登录项")?;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.len() > 16384
        || fs::read_to_string(&paths.agent).ok().as_deref()
            != Some(launch_agent(paths, record).as_str())
    {
        return Err("登录项与本机常驻记录不符，未修改".into());
    }
    Ok(())
}

fn write_agent(paths: &ResidentPaths, record: &Installation) -> Result<(), String> {
    check_agent(paths, record)?;
    fs::create_dir_all(paths.agent.parent().unwrap()).map_err(|_| "无法创建登录项目录")?;
    private_dir(&paths.root.join("logs"))?;
    let temporary = paths
        .agent
        .with_extension(format!("{}.tmp", new_profile_revision()));
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(&temporary)
        .map_err(|_| "无法写入本机常驻登录项")?;
    file.write_all(launch_agent(paths, record).as_bytes())
        .and_then(|_| file.sync_all())
        .map_err(|_| "无法写入本机常驻登录项")?;
    fs::rename(&temporary, &paths.agent).map_err(|_| "无法保存本机常驻登录项".into())
}

fn launch(
    paths: &ResidentPaths,
    record: &Installation,
    host: &mut impl ResidentHost,
) -> Result<(), String> {
    if tree_digest(&paths.runtime(record))? != record.release_id {
        return Err("本机常驻运行文件已变化，未启动".into());
    }
    write_agent(paths, record)?;
    let result = host.launchctl(&[
        "bootstrap".into(),
        paths.domain.clone(),
        paths.agent.to_string_lossy().into_owned(),
    ]);
    let error = match result {
        Ok(output) if output.code == 0 => return Ok(()),
        Ok(_) => "本机常驻启动未完成；资料与配置已保留".into(),
        Err(error) => error,
    };
    if loaded(paths, host).is_ok_and(|value| !value) {
        let _ = remove_agent(paths, record, host);
    }
    Err(error)
}

fn remove_agent(
    paths: &ResidentPaths,
    record: &Installation,
    host: &mut impl ResidentHost,
) -> Result<(), String> {
    check_agent(paths, record)?;
    host.remove_agent(&paths.agent)
}

fn unload(
    paths: &ResidentPaths,
    record: &Installation,
    host: &mut impl ResidentHost,
) -> Result<(), String> {
    check_agent(paths, record)?;
    let output = host.launchctl(&["bootout".into(), paths.target()])?;
    if output.code != 0 {
        return Err("本机常驻停止未完成，未更新配置".into());
    }
    remove_agent(paths, record, host)
        .map_err(|_| "本机常驻已停止，但登录项未移除，请核对后重试".into())
}

fn preflight(
    paths: &ResidentPaths,
    config: &ServiceConfig,
    host: &mut impl ResidentHost,
    launch: Option<&str>,
) -> Result<bool, String> {
    if !loaded(paths, host)? {
        if launch.is_some() {
            return Err("本机常驻实例已停止或变化，请刷新后重试".into());
        }
        return Ok(false);
    }
    let expected = launch.ok_or("请刷新并核对本机常驻运行实例后重试")?;
    let report = host.report(config)?;
    valid_report(config, &report)?;
    assert_idle(
        Path::new(&config.workspace_root),
        expected,
        &report.launch_id,
        &report.scope,
        &report.workspace_root,
        Some(&report.counts),
    )?;
    Ok(true)
}

fn status(
    paths: &ResidentPaths,
    available: &str,
    host: &mut impl ResidentHost,
) -> Result<LocalResidentStatus, String> {
    let Some((record, config)) = load_installation(paths)? else {
        return Ok(LocalResidentStatus {
            supported: true,
            installed: false,
            state: "not_installed",
            version: None,
            available_version: available.into(),
            cron_enabled: false,
            project_id: None,
            launch_id: None,
            revision: NOT_INSTALLED.into(),
            model_revision: None,
            message: "尚未安装；本机临时引擎照常可用".into(),
        });
    };
    let (state, launch_id, message) = match host.launchctl(&["print".into(), paths.target()]) {
        Ok(output) if matches!(output.code, 3 | 113) => {
            match fs::symlink_metadata(&paths.agent) {
                Err(error) if error.kind() == ErrorKind::NotFound => (
                    "stopped",
                    None,
                    "本机常驻服务当前未加载；资料与配置已保留".into(),
                ),
                Err(_) => return Err("无法核对本机常驻登录项".into()),
                Ok(_) => {
                    check_agent(paths, &record)?;
                    (
                        "cleanup_required",
                        None,
                        "服务当前未加载，但登录项待清理；请重试移除，否则下次登录仍可能启动".into(),
                    )
                }
            }
        }
        Ok(output) if output.code == 0 => match host.report(&config).and_then(|report| {
            valid_report(&config, &report)?;
            Ok(report)
        }) {
            Ok(report) => (
                "running",
                Some(report.launch_id),
                "本机常驻运行中；退出 Desktop 后继续，Mac 休眠时暂停".into(),
            ),
            Err(_) => {
                // Parse only fixed launchd keys. Never forward the raw print
                // output (which may include environment, paths or descriptions).
                let exit = output.stdout.lines().find_map(|line| {
                    line.trim()
                        .strip_prefix("last exit code = ")
                        .and_then(|value| value.parse::<i32>().ok())
                });
                let waiting = output
                    .stdout
                    .lines()
                    .any(|line| matches!(line.trim(), "state = waiting" | "state = exited"));
                let detail = exit
                    .filter(|code| *code != 0)
                    .map(|code| format!("上次退出码 {code}；"))
                    .unwrap_or_default();
                (
                    if waiting || exit.is_some_and(|code| code != 0) {
                        "unavailable"
                    } else {
                        "starting"
                    },
                    None,
                    format!(
                        "服务尚未就绪；{detail}请核对日志：{}。未放宽未知实例的停止检查。",
                        paths.root.join("logs/service.log").display()
                    ),
                )
            }
        },
        _ => (
            "unavailable",
            None,
            "无法核对登录项，未执行启动、停止或资料迁移".into(),
        ),
    };
    Ok(LocalResidentStatus {
        supported: true,
        installed: true,
        state,
        version: Some(record.version),
        available_version: available.into(),
        cron_enabled: config.cron_enabled,
        project_id: record.project_id,
        launch_id,
        revision: record.revision,
        model_revision: Some(config.model_revision),
        message,
    })
}

fn new_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    fs::File::open("/dev/urandom")
        .and_then(|mut source| source.read_exact(&mut bytes))
        .map_err(|_| "无法生成本机常驻访问凭据")?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn install(
    paths: &ResidentPaths,
    source: &Path,
    version: &str,
    host: &mut impl ResidentHost,
    cron_enabled: bool,
    copy_model: bool,
    expected_revision: &str,
) -> Result<LocalResidentStatus, String> {
    if expected_revision != NOT_INSTALLED || load_installation(paths)?.is_some() {
        return Err("安装状态已变化，请刷新；已有资料与模型未覆盖".into());
    }
    if fs::symlink_metadata(&paths.agent).is_ok() {
        return Err("已存在同名登录项；未覆盖，请先核对".into());
    }
    if !copy_model {
        return Err("安装需要主人明确同意复制当前模型配置为独立副本".into());
    }
    let snapshot = host.model()?;
    let cron_notice = if cron_enabled {
        "定时调度：开启；启动服务后会执行已启用的排期，可能产生模型费用。"
    } else {
        "定时调度：关闭；本次不会启用自动排期执行。"
    };
    if !host.confirm(&format!("安装这台 Mac 的独立常驻服务？\n将复制当前模型配置和凭据为独立副本，之后修改或退出 Desktop 的模型账号不会同步撤销该副本。\n{cron_notice}\n本次不启动服务、不改变已选助手、不迁移任何对话或资料。服务只使用本机私有连接；启用后随用户登录启动，退出 Desktop 继续，休眠与注销会停止工作。"))? {
        return Err("已取消安装；未创建常驻配置".into());
    }
    if load_installation(paths)?.is_some() || host.model()?.revision != snapshot.revision {
        return Err("安装状态或模型配置已变化，未安装；请刷新后重试".into());
    }
    private_dir(&paths.root)?;
    private_dir(&paths.workspace())?;
    let workspace = paths
        .workspace()
        .canonicalize()
        .map_err(|_| "无法建立独立常驻资料目录")?;
    // Preserve the existing registered-origin descriptor, without opening TCP.
    // Actual local-resident HTTP and WebSocket traffic uses the private socket.
    let port = VIRTUAL_PORT;
    let release_id = stage_runtime(paths, source, host)?;
    let revision = new_profile_revision();
    let config = ServiceConfig {
        schema_version: 1,
        revision: revision.clone(),
        runtime_version: version.into(),
        workspace_root: workspace.to_string_lossy().into_owned(),
        port,
        cron_enabled,
        model_revision: snapshot.revision,
        token: new_token()?,
        model_env: snapshot.env,
    };
    stage_config(paths, &config)?;
    let project_id = remote_project_id(&format!("http://127.0.0.1:{port}"), "@assistant");
    let record = Installation {
        schema_version: 1,
        revision,
        release_id,
        version: version.into(),
        project_id: Some(project_id),
    };
    save_installation(paths, &record)?;
    status(paths, version, host)
}

fn start(
    paths: &ResidentPaths,
    version: &str,
    host: &mut impl ResidentHost,
    expected: &str,
) -> Result<LocalResidentStatus, String> {
    let (record, _) = require_installation(paths, expected)?;
    if !loaded(paths, host)? {
        check_agent(paths, &record)?;
        if paths.agent.exists() {
            return Err("登录项待清理，请先重试停止本机常驻服务".into());
        }
        launch(paths, &record, host)?;
    }
    status(paths, version, host)
}

fn stop(
    paths: &ResidentPaths,
    version: &str,
    host: &mut impl ResidentHost,
    expected: &str,
    launch_id: Option<&str>,
) -> Result<LocalResidentStatus, String> {
    let (record, config) = require_installation(paths, expected)?;
    let running = preflight(paths, &config, host, launch_id)?;
    if !host.confirm("停止这台 Mac 的常驻服务？\n会停止本机常驻的调度和任务接收，并移除其登录自动启动项。资料和独立模型配置保留；不会操作其他电脑的服务。")? {
        return Err("已取消停止本机常驻服务".into());
    }
    let (_, config) = require_installation(paths, expected)?;
    if preflight(paths, &config, host, launch_id)? != running {
        return Err("本机常驻运行状态已变化，未执行停止".into());
    }
    if running {
        unload(paths, &record, host)?;
    } else {
        remove_agent(paths, &record, host)?;
    }
    status(paths, version, host)
}

enum Change<'a> {
    Program(&'a Path),
    Model,
}

fn change(
    paths: &ResidentPaths,
    version: &str,
    host: &mut impl ResidentHost,
    expected: &str,
    launch_id: Option<&str>,
    change: Change<'_>,
) -> Result<LocalResidentStatus, String> {
    let (old, mut config) = require_installation(paths, expected)?;
    let running = preflight(paths, &config, host, launch_id)?;
    let snapshot = if matches!(change, Change::Model) {
        Some(host.model()?)
    } else {
        None
    };
    let message = if snapshot.is_some() {
        "把当前 Desktop 模型配置和凭据重新复制给本机常驻服务？\n这是一份独立副本；之后修改或退出 Desktop 模型账号不自动撤销它。运行中的常驻服务仅在核对空闲后安全重启；资料与其他电脑不变。"
    } else {
        "更新这台 Mac 的常驻运行程序？\n只更新程序，不替换独立模型配置，不迁移资料。运行中的服务仅在再次核对空闲和实例后重启；中断任务不会自动重跑。"
    };
    if !host.confirm(message)? {
        return Err("已取消本机常驻变更".into());
    }
    let mut next = old.clone();
    next.revision = new_profile_revision();
    config.revision = next.revision.clone();
    match change {
        Change::Program(source) => {
            next.release_id = stage_runtime(paths, source, host)?;
            next.version = version.into();
            config.runtime_version = version.into();
        }
        Change::Model => {
            let snapshot = snapshot.unwrap();
            if host.model()?.revision != snapshot.revision {
                return Err("当前模型配置已变化，未复制，请重新核对".into());
            }
            config.model_revision = snapshot.revision;
            config.model_env = snapshot.env;
        }
    }
    stage_config(paths, &config)?;
    // Copying the onedir may take time. Re-read both identity and all activity
    // after that work and the native confirmation, immediately before stopping.
    let (_, current) = require_installation(paths, expected)?;
    if preflight(paths, &current, host, launch_id)? != running {
        return Err("常驻运行状态已变化，未执行更新或重启".into());
    }
    if running {
        unload(paths, &old, host)?;
    } else {
        check_agent(paths, &old)?;
        if paths.agent.exists() {
            return Err("登录项待清理，请先重试停止本机常驻服务".into());
        }
    }
    if let Err(error) = save_installation(paths, &next) {
        if running {
            let _ = launch(paths, &old, host);
        }
        return Err(error);
    }
    if running {
        if let Err(error) = launch(paths, &next, host) {
            // Never roll back over a possibly started new process. An ambiguous
            // launch remains visible for the owner; no blind kill or extra start.
            if loaded(paths, host).is_ok_and(|value| !value) {
                // A failed/timeout bootstrap can leave the new login item behind.
                // Keep its matching pointer unless exact-owned cleanup succeeds.
                remove_agent(paths, &next, host)?;
                save_installation(paths, &old)?;
                if launch(paths, &old, host).is_ok() {
                    return Err("变更启动失败，已恢复旧运行版本与配置；请核对服务状态".into());
                }
            }
            return Err(format!("{error}；启动结果待核对，未自动重试任务"));
        }
    }
    status(paths, version, host)
}

fn connect(
    paths: &ResidentPaths,
    host: &mut impl ResidentHost,
    expected: &str,
) -> Result<DesktopProjectProfile, String> {
    let (_, config) = require_installation(paths, expected)?;
    if !loaded(paths, host)? {
        return Err("请先启动本机常驻服务，再连接".into());
    }
    let first = host.report(&config)?;
    valid_report(&config, &first)?;
    if !host.confirm("连接这台 Mac 的本机常驻助手？\n将登记这份独立助手，之后由界面切换到它。不会迁移或合并原本机、远端的对话、任务与资料，也不会开启远程访问权限。")? {
        return Err("已取消连接本机常驻助手".into());
    }
    let (mut record, config) = require_installation(paths, expected)?;
    if !loaded(paths, host)? {
        return Err("本机常驻服务已停止，未登记连接".into());
    }
    let latest = host.report(&config)?;
    valid_report(&config, &latest)?;
    if first.launch_id != latest.launch_id {
        return Err("确认期间本机常驻实例已变化，未登记连接".into());
    }
    let profile = host.register(&config)?;
    record.project_id = Some(profile.id.clone());
    save_installation(paths, &record)
        .map_err(|_| "常驻连接已登记，但本机安装记录未保存，请刷新后核对")?;
    Ok(profile)
}

fn select(
    paths: &ResidentPaths,
    version: &str,
    host: &mut impl ResidentHost,
    expected: &str,
    expected_launch: &str,
) -> Result<LocalResidentStatus, String> {
    let (_, config) = require_installation(paths, expected)?;
    if !loaded(paths, host)? {
        return Err("常驻服务已停止，未切换默认助手".into());
    }
    let report = host.report(&config)?;
    valid_report(&config, &report)?;
    if report.launch_id != expected_launch {
        return Err("连接期间常驻实例已变化，未切换默认助手".into());
    }
    // The webview calls this only after its actual WebSocket attachment succeeds.
    // It changes preference only: no process, task, permission or model mutation.
    host.select(&config)?;
    status(paths, version, host)
}

fn register_preserving_order(
    registry_path: &Path,
    base: &str,
    id: &str,
) -> Result<DesktopProjectProfile, String> {
    let mut registry = load_project_registry(registry_path)?;
    if let Some(known) = registry.projects.iter().find(|profile| profile.id == id) {
        if known.kind != PROJECT_KIND_REMOTE
            || known.repo_root != "@assistant"
            || known.base_url != base
        {
            return Err("已有常驻登记与本机实例不符；未修改默认助手".into());
        }
        return Ok(known.clone());
    }
    if registry.projects.len() >= MAX_DESKTOP_PROJECTS {
        return Err("工作区登记已满；未移除已有助手或项目".into());
    }
    let profile = DesktopProjectProfile {
        id: id.into(),
        name: "这台 Mac · 常驻助手".into(),
        repo_root: "@assistant".into(),
        base_url: base.into(),
        last_opened_at: 0,
        kind: PROJECT_KIND_REMOTE.into(),
        assistant_pending: true,
    };
    registry.projects.push(profile.clone());
    save_project_registry(registry_path, &registry)?;
    Ok(profile)
}

fn select_registered(registry_path: &Path, base: &str, id: &str) -> Result<(), String> {
    let mut registry = load_project_registry(registry_path)?;
    let index = registry
        .projects
        .iter()
        .position(|profile| {
            profile.id == id
                && profile.kind == PROJECT_KIND_REMOTE
                && profile.repo_root == "@assistant"
                && profile.base_url == base
        })
        .ok_or("本机常驻尚未由主人登记，未切换默认助手")?;
    let mut profile = registry.projects.remove(index);
    profile.assistant_pending = false;
    profile.last_opened_at = now_epoch_seconds()?.max(
        registry
            .projects
            .iter()
            .map(|profile| profile.last_opened_at)
            .max()
            .unwrap_or(0)
            .saturating_add(1),
    );
    registry.projects.insert(0, profile);
    save_project_registry(registry_path, &registry)
}

struct NativeHost {
    app: AppHandle,
}

impl ResidentHost for NativeHost {
    fn capabilities(&mut self, executable: &Path) -> Result<(), String> {
        let mut child = Command::new(executable)
            .arg("resident-capabilities")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| "无法检查本次内置 runtime 的常驻能力；未安装")?;
        let limit = std::time::Instant::now() + Duration::from_secs(10);
        loop {
            match child.try_wait() {
                Ok(Some(status)) if status.success() => break,
                Ok(None) if std::time::Instant::now() < limit => {
                    std::thread::sleep(Duration::from_millis(25))
                }
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("内置 runtime 未通过常驻能力检查；请使用包含本功能的新构建".into());
                }
            }
        }
        let output = child
            .wait_with_output()
            .map_err(|_| "无法读取常驻能力检查")?;
        if output.stdout.len() > 4096 {
            return Err("runtime 能力响应超过上限；未安装".into());
        }
        let value: serde_json::Value = serde_json::from_slice(&output.stdout)
            .map_err(|_| "runtime 未声明常驻配置合同；未安装")?;
        if value.get("schema_version").and_then(|v| v.as_u64()) != Some(1)
            || value.get("configuration_schema").and_then(|v| v.as_u64()) != Some(1)
        {
            return Err("runtime 常驻配置合同不兼容；未安装".into());
        }
        Ok(())
    }
    fn launchctl(&mut self, arguments: &[String]) -> Result<LaunchOutput, String> {
        let mut child = Command::new("/bin/launchctl")
            .args(arguments)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| "无法调用本机服务管理器")?;
        let limit = std::time::Instant::now() + Duration::from_secs(10);
        loop {
            match child.try_wait() {
                Ok(Some(status)) => {
                    let output = child
                        .wait_with_output()
                        .map_err(|_| "无法读取本机服务管理器结果")?;
                    if output.stdout.len() > 65536 {
                        return Err("本机服务状态超过检查上限".into());
                    }
                    return Ok(LaunchOutput {
                        code: status.code().unwrap_or(-1),
                        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
                    });
                }
                Ok(None) if std::time::Instant::now() < limit => {
                    std::thread::sleep(Duration::from_millis(25))
                }
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("本机服务管理器未在时限内完成；请刷新核对状态".into());
                }
            }
        }
    }

    fn report(&mut self, config: &ServiceConfig) -> Result<ActivityReport, String> {
        let report: ActivityReport = local_json(config, "/api/runtime-activity")?;
        #[derive(Deserialize)]
        struct Receipt {
            schema_version: u32,
            revision: String,
            launch_id: String,
        }
        #[derive(Deserialize)]
        struct Health {
            status: String,
            model_configuration: Option<Receipt>,
        }
        let health: Health = local_json(config, "/api/health/quick")?;
        let receipt = health
            .model_configuration
            .ok_or("尚未收到本机常驻配置回执")?;
        if health.status != "healthy"
            || receipt.schema_version != 1
            || receipt.revision != config.model_revision
            || receipt.launch_id != report.launch_id
        {
            return Err("常驻进程的配置或启动实例尚未核对".into());
        }
        Ok(report)
    }

    fn confirm(&mut self, text: &str) -> Result<bool, String> {
        use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
        Ok(
            with_main_window_parent(&self.app, self.app.dialog().message(text))
                .title("本机常驻服务")
                .kind(MessageDialogKind::Warning)
                .buttons(MessageDialogButtons::OkCancelCustom(
                    "确认".into(),
                    "取消".into(),
                ))
                .blocking_show(),
        )
    }

    fn model(&mut self) -> Result<ModelSnapshot, String> {
        let store = self.app.state::<DesktopLlmProfileStore>();
        let snapshot = cached_profile_snapshot(&self.app, &store)?;
        let profile = snapshot
            .profile
            .ok_or("请先配置可用的主模型，再明确复制给常驻服务")?;
        if profile.signed_out
            || !desktop_llm_profile_status(Some(&profile)).configured
            || (profile
                .model_info
                .iter()
                .any(|info| info.category.is_some())
                && !profile
                    .model_info
                    .iter()
                    .any(|info| info.id == profile.model && info.agent_ready()))
        {
            return Err("当前主模型未就绪或不能使用工具；未复制给常驻服务".into());
        }
        let mut command = Command::new("resident-model-snapshot");
        configure_llm_profile(&mut command, Some(&profile));
        let env = command
            .get_envs()
            .filter_map(|(name, value)| {
                let name = name.to_str()?;
                model_env_key(name).then(|| {
                    (
                        name.to_string(),
                        value.map(|v| v.to_string_lossy().into_owned()),
                    )
                })
            })
            .collect();
        Ok(ModelSnapshot {
            revision: snapshot.revision,
            env,
        })
    }

    fn register(&mut self, config: &ServiceConfig) -> Result<DesktopProjectProfile, String> {
        let base = validate_remote_server_url(&format!("http://127.0.0.1:{}", config.port))?;
        let id = remote_project_id(&base, "@assistant");
        let registry = project_registry_path(&self.app)?;
        let old = remote_token_keychain::read(&id)?;
        remote_token_keychain::write(&id, &config.token)?;
        let saved = register_preserving_order(&registry, &base, &id);
        if saved.is_err() {
            match old {
                Some(token) => {
                    let _ = remote_token_keychain::write(&id, &token);
                }
                None => {
                    let _ = remote_token_keychain::delete(&id);
                }
            }
        }
        saved
    }

    fn select(&mut self, config: &ServiceConfig) -> Result<(), String> {
        let base = validate_remote_server_url(&format!("http://127.0.0.1:{}", config.port))?;
        let id = remote_project_id(&base, "@assistant");
        select_registered(&project_registry_path(&self.app)?, &base, &id)
    }
}

fn private_socket(root: &Path) -> Result<PathBuf, String> {
    let data_directory = fs::symlink_metadata(root).map_err(|_| "本机常驻私有目录不可用")?;
    if !data_directory.is_dir() || data_directory.file_type().is_symlink() {
        return Err("本机常驻私有目录无效".into());
    }
    let socket = crate::resident_socket::socket_path(root, current_uid())?;
    let directory = fs::symlink_metadata(socket.parent().unwrap())
        .map_err(|_| "本机常驻私有连接目录尚未就绪")?;
    if !directory.is_dir() || directory.file_type().is_symlink() {
        return Err("本机常驻私有连接目录无效".into());
    }
    let metadata =
        fs::symlink_metadata(&socket).map_err(|_| "本机常驻私有连接尚未就绪；未尝试网络连接")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::{FileTypeExt, MetadataExt};
        if data_directory.uid() != current_uid()
            || data_directory.mode() & 0o777 != 0o700
            || directory.uid() != current_uid()
            || directory.mode() & 0o777 != 0o700
            || !metadata.file_type().is_socket()
            || metadata.file_type().is_symlink()
            || metadata.uid() != current_uid()
            || metadata.mode() & 0o777 != 0o600
        {
            return Err("本机常驻私有连接权限无效；未尝试网络连接".into());
        }
    }
    #[cfg(not(unix))]
    {
        let _ = metadata;
        return Err("当前平台不支持私有常驻连接".into());
    }
    Ok(socket)
}

/// Exact managed identity selects a private socket. A missing socket is an error,
/// never a reason to send the service bearer to the registered loopback URL.
pub(crate) fn local_resident_socket_for_project(
    app: &AppHandle,
    project_id: &str,
) -> Result<Option<PathBuf>, String> {
    socket_for_project(&ResidentPaths::from_app(app)?, project_id)
}

pub(crate) fn is_local_resident_project(project_id: &str) -> bool {
    project_id == remote_project_id(VIRTUAL_ORIGIN, "@assistant")
}

fn socket_for_project(paths: &ResidentPaths, project_id: &str) -> Result<Option<PathBuf>, String> {
    if !is_local_resident_project(project_id) {
        return Ok(None);
    }
    let (record, config) =
        load_installation(paths)?.ok_or("本机常驻安装记录缺失；未尝试网络连接")?;
    if record.project_id.as_deref() != Some(project_id) || config.port != VIRTUAL_PORT {
        return Err("本机常驻身份记录不符；未尝试网络连接".into());
    }
    private_socket(&paths.root).map(Some)
}

#[cfg(unix)]
fn local_json<T: serde::de::DeserializeOwned>(
    config: &ServiceConfig,
    path: &'static str,
) -> Result<T, String> {
    let root = Path::new(&config.workspace_root)
        .parent()
        .ok_or("本机常驻资料范围无效")?;
    let socket = private_socket(root)?;
    // Production calls originate in spawn_blocking. Reuse the standard HTTP
    // client over a private UDS; no proxy, redirect, TCP fallback or raw parser.
    tauri::async_runtime::block_on(async {
        let client = reqwest::Client::builder()
            .unix_socket(socket)
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(2))
            .connect_timeout(Duration::from_secs(2))
            .build()
            .map_err(|_| "无法创建本机常驻私有连接")?;
        let mut response = client
            .get(format!("{VIRTUAL_ORIGIN}{path}"))
            .bearer_auth(&config.token)
            .send()
            .await
            .map_err(|_| "本机常驻私有连接未在时限内就绪")?;
        if !response.status().is_success() {
            return Err("本机常驻尚未返回可核对的状态".into());
        }
        if response
            .content_length()
            .is_some_and(|length| length > 65536)
        {
            return Err("本机常驻状态超过读取上限".into());
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| "本机常驻状态读取未完成")?
        {
            if bytes.len() + chunk.len() > 65536 {
                return Err("本机常驻状态超过读取上限".into());
            }
            bytes.extend_from_slice(&chunk);
        }
        serde_json::from_slice(&bytes).map_err(|_| "本机常驻状态格式无效".into())
    })
}

#[cfg(not(unix))]
fn local_json<T: serde::de::DeserializeOwned>(
    _config: &ServiceConfig,
    _path: &'static str,
) -> Result<T, String> {
    Err("当前平台不支持私有常驻连接".into())
}

async fn native_work<T: Send + 'static>(
    app: AppHandle,
    manager: &LocalResidentManager,
    work: impl FnOnce(&ResidentPaths, &str, &mut NativeHost) -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    if !cfg!(target_os = "macos") {
        return Err("本机常驻服务目前仅支持 macOS".into());
    }
    let lock = manager.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = lock.lock().map_err(|_| "本机常驻操作锁不可用")?;
        let paths = ResidentPaths::from_app(&app)?;
        let version = app.package_info().version.to_string();
        work(&paths, &version, &mut NativeHost { app })
    })
    .await
    .map_err(|_| "本机常驻操作未完成；请刷新核对状态")?
}

#[tauri::command]
pub(crate) async fn local_resident_status(
    app: AppHandle,
    manager: State<'_, LocalResidentManager>,
) -> Result<LocalResidentStatus, String> {
    if !cfg!(target_os = "macos") {
        return Ok(LocalResidentStatus {
            supported: false,
            installed: false,
            state: "unavailable",
            version: None,
            available_version: app.package_info().version.to_string(),
            cron_enabled: false,
            project_id: None,
            launch_id: None,
            revision: NOT_INSTALLED.into(),
            model_revision: None,
            message: "本机常驻服务目前仅支持 macOS".into(),
        });
    }
    native_work(app, &manager, |paths, version, host| {
        status(paths, version, host)
    })
    .await
}

#[tauri::command]
pub(crate) async fn local_resident_install(
    app: AppHandle,
    manager: State<'_, LocalResidentManager>,
    cron_enabled: bool,
    copy_model_profile: bool,
    expected_revision: String,
) -> Result<LocalResidentStatus, String> {
    let source = app
        .path()
        .resource_dir()
        .map_err(|_| "无法定位内置 runtime")?
        .join("runtime");
    native_work(app, &manager, move |paths, version, host| {
        install(
            paths,
            &source,
            version,
            host,
            cron_enabled,
            copy_model_profile,
            &expected_revision,
        )
    })
    .await
}

#[tauri::command]
pub(crate) async fn local_resident_start(
    app: AppHandle,
    manager: State<'_, LocalResidentManager>,
    expected_revision: String,
) -> Result<LocalResidentStatus, String> {
    native_work(app, &manager, move |paths, version, host| {
        start(paths, version, host, &expected_revision)
    })
    .await
}

#[tauri::command]
pub(crate) async fn local_resident_stop(
    app: AppHandle,
    manager: State<'_, LocalResidentManager>,
    expected_revision: String,
    expected_launch_id: Option<String>,
) -> Result<LocalResidentStatus, String> {
    native_work(app, &manager, move |paths, version, host| {
        stop(
            paths,
            version,
            host,
            &expected_revision,
            expected_launch_id.as_deref(),
        )
    })
    .await
}

#[tauri::command]
pub(crate) async fn local_resident_update(
    app: AppHandle,
    manager: State<'_, LocalResidentManager>,
    expected_revision: String,
    expected_launch_id: Option<String>,
) -> Result<LocalResidentStatus, String> {
    let source = app
        .path()
        .resource_dir()
        .map_err(|_| "无法定位内置 runtime")?
        .join("runtime");
    native_work(app, &manager, move |paths, version, host| {
        change(
            paths,
            version,
            host,
            &expected_revision,
            expected_launch_id.as_deref(),
            Change::Program(&source),
        )
    })
    .await
}

#[tauri::command]
pub(crate) async fn local_resident_apply_model(
    app: AppHandle,
    manager: State<'_, LocalResidentManager>,
    expected_revision: String,
    expected_launch_id: Option<String>,
) -> Result<LocalResidentStatus, String> {
    native_work(app, &manager, move |paths, version, host| {
        change(
            paths,
            version,
            host,
            &expected_revision,
            expected_launch_id.as_deref(),
            Change::Model,
        )
    })
    .await
}

#[tauri::command]
pub(crate) async fn local_resident_connect(
    app: AppHandle,
    manager: State<'_, LocalResidentManager>,
    expected_revision: String,
) -> Result<DesktopProjectProfile, String> {
    native_work(app, &manager, move |paths, _, host| {
        connect(paths, host, &expected_revision)
    })
    .await
}

#[tauri::command]
pub(crate) async fn local_resident_select(
    app: AppHandle,
    manager: State<'_, LocalResidentManager>,
    expected_revision: String,
    expected_launch_id: String,
) -> Result<LocalResidentStatus, String> {
    native_work(app, &manager, move |paths, version, host| {
        select(
            paths,
            version,
            host,
            &expected_revision,
            &expected_launch_id,
        )
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    use std::path::PathBuf;

    fn scratch(label: &str) -> PathBuf {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        // macOS's per-process temp root is too long for sockaddr_un.sun_path.
        let base = if cfg!(target_os = "macos") {
            PathBuf::from("/private/tmp")
        } else {
            std::env::temp_dir()
        };
        let path = base.join(format!(
            "vorto-resident-{label}-{}-{nonce}",
            std::process::id()
        ));
        std::fs::create_dir_all(&path).unwrap();
        path
    }

    fn idle_counts() -> BTreeMap<String, i64> {
        ACTIVITY_COUNTS
            .iter()
            .map(|name| (name.to_string(), 0))
            .collect()
    }

    struct FakeHost {
        loaded: bool,
        confirmed: bool,
        supports_resident: bool,
        fail_bootstrap: usize,
        error_bootstrap: usize,
        fail_agent_removal: usize,
        bootstrap_print_errors: usize,
        bootstrap_agent_removal_failures: usize,
        print_errors: usize,
        ambiguous_bootstrap: bool,
        launch_stdout: String,
        report_error: bool,
        calls: Vec<String>,
        reports: VecDeque<ActivityReport>,
        model: ModelSnapshot,
        registrations: usize,
    }

    impl Default for FakeHost {
        fn default() -> Self {
            Self {
                loaded: false,
                confirmed: true,
                supports_resident: true,
                fail_bootstrap: 0,
                error_bootstrap: 0,
                fail_agent_removal: 0,
                bootstrap_print_errors: 0,
                bootstrap_agent_removal_failures: 0,
                print_errors: 0,
                ambiguous_bootstrap: false,
                launch_stdout: "state = running".into(),
                report_error: false,
                calls: Vec::new(),
                reports: VecDeque::new(),
                registrations: 0,
                model: ModelSnapshot {
                    revision: "model-first".into(),
                    env: BTreeMap::from([
                        (
                            "OPENAI_API_BASE".into(),
                            Some("http://127.0.0.1:1/v1".into()),
                        ),
                        ("OPENAI_API_KEY".into(), Some("fixture-model-key".into())),
                        ("DEFAULT_MODEL".into(), Some("fixture-model".into())),
                    ]),
                },
            }
        }
    }

    impl ResidentHost for FakeHost {
        fn capabilities(&mut self, executable: &Path) -> Result<(), String> {
            assert!(executable.is_file());
            if self.supports_resident {
                Ok(())
            } else {
                Err("fixture runtime not supported".into())
            }
        }
        fn launchctl(&mut self, arguments: &[String]) -> Result<LaunchOutput, String> {
            self.calls.push(arguments.join(" "));
            let code = match arguments[0].as_str() {
                "print" if self.print_errors > 0 => {
                    self.print_errors -= 1;
                    return Err("fixture launchctl print unavailable".into());
                }
                "print" => {
                    if self.loaded {
                        0
                    } else {
                        113
                    }
                }
                "bootstrap" if self.fail_bootstrap > 0 => {
                    self.fail_bootstrap -= 1;
                    self.loaded = self.ambiguous_bootstrap;
                    5
                }
                "bootstrap" if self.error_bootstrap > 0 => {
                    self.error_bootstrap -= 1;
                    self.loaded = self.ambiguous_bootstrap;
                    self.print_errors = self.bootstrap_print_errors;
                    self.fail_agent_removal = self.bootstrap_agent_removal_failures;
                    return Err("fixture launchctl timeout".into());
                }
                "bootstrap" => {
                    self.loaded = true;
                    0
                }
                "bootout" => {
                    self.loaded = false;
                    0
                }
                _ => panic!("unexpected launchctl operation"),
            };
            Ok(LaunchOutput {
                code,
                stdout: self.launch_stdout.clone(),
            })
        }
        fn report(&mut self, config: &ServiceConfig) -> Result<ActivityReport, String> {
            if self.report_error {
                return Err("fixture unavailable".into());
            }
            Ok(self
                .reports
                .pop_front()
                .unwrap_or_else(|| report(config, "launch-first")))
        }
        fn confirm(&mut self, _text: &str) -> Result<bool, String> {
            Ok(self.confirmed)
        }
        fn model(&mut self) -> Result<ModelSnapshot, String> {
            Ok(self.model.clone())
        }
        fn register(&mut self, config: &ServiceConfig) -> Result<DesktopProjectProfile, String> {
            self.registrations += 1;
            let base = format!("http://127.0.0.1:{}", config.port);
            Ok(DesktopProjectProfile {
                id: remote_project_id(&base, "@assistant"),
                name: "fixture resident".into(),
                repo_root: "@assistant".into(),
                base_url: base,
                last_opened_at: 0,
                kind: "remote".into(),
                assistant_pending: false,
            })
        }
        fn select(&mut self, _config: &ServiceConfig) -> Result<(), String> {
            Ok(())
        }
        fn remove_agent(&mut self, path: &Path) -> Result<(), String> {
            if self.fail_agent_removal > 0 {
                self.fail_agent_removal -= 1;
                return Err("fixture login item removal failed".into());
            }
            match fs::remove_file(path) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
                Err(error) => Err(error.to_string()),
            }
        }
    }

    fn fixture(label: &str) -> (ResidentPaths, PathBuf, FakeHost) {
        let root = scratch(label);
        let paths = ResidentPaths {
            root: root.join("resident"),
            agent: root.join("agents/fixture.plist"),
            label: "fixture-resident".into(),
            domain: "gui/fixture".into(),
        };
        let source = root.join("bundled");
        fs::create_dir_all(source.join("_internal")).unwrap();
        fs::write(source.join("vortocode-runtime"), b"fixture executable").unwrap();
        fs::write(source.join("_internal/data"), b"fixture runtime dependency").unwrap();
        (paths, source, FakeHost::default())
    }

    fn report(config: &ServiceConfig, launch: &str) -> ActivityReport {
        ActivityReport {
            version: 1,
            ready: true,
            launch_id: launch.into(),
            scope: "general".into(),
            workspace_root: config.workspace_root.clone(),
            counts: idle_counts(),
        }
    }

    #[test]
    fn resident_install_is_stopped_unregistered_and_keeps_independent_model_after_update() {
        let (paths, source, mut host) = fixture("independent");
        let installed = install(
            &paths,
            &source,
            "first",
            &mut host,
            false,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        assert_eq!(installed.state, "stopped");
        assert!(!paths.agent.exists());
        assert_eq!(host.registrations, 0);
        assert!(host.calls.iter().all(|call| call.starts_with("print ")));
        let (old, old_config) = load_installation(&paths).unwrap().unwrap();
        fs::write(source.join("_internal/data"), b"updated program").unwrap();
        host.model.revision = "model-second".into();
        host.model
            .env
            .insert("OPENAI_API_KEY".into(), Some("new-key-not-copied".into()));
        change(
            &paths,
            "second",
            &mut host,
            &old.revision,
            None,
            Change::Program(&source),
        )
        .unwrap();
        let (new, new_config) = load_installation(&paths).unwrap().unwrap();
        assert_ne!(new.release_id, old.release_id);
        assert_ne!(new.revision, old.revision);
        assert_eq!(new_config.model_revision, old_config.model_revision);
        assert_eq!(new_config.model_env, old_config.model_env);
        assert_eq!(new_config.workspace_root, old_config.workspace_root);
        assert_eq!(new_config.token, old_config.token);
        assert_eq!(new_config.port, old_config.port);
        assert!(paths.config(&old).is_file());
        assert_eq!(
            read_private::<ServiceConfig>(&paths.config(&old))
                .unwrap()
                .runtime_version,
            "first"
        );
        assert_eq!(host.registrations, 0);
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_install_rejects_a_bundled_runtime_without_resident_support() {
        let (paths, source, mut host) = fixture("capability");
        host.supports_resident = false;
        assert!(install(
            &paths,
            &source,
            "first",
            &mut host,
            false,
            true,
            NOT_INSTALLED
        )
        .is_err());
        assert!(!paths.record().exists());
        assert!(!paths.agent.exists());
        assert_eq!(host.registrations, 0);
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_stop_rechecks_activity_and_launch_after_confirmation() {
        for changed_launch in [false, true] {
            let (paths, source, mut host) = fixture("recheck");
            let installed = install(
                &paths,
                &source,
                "first",
                &mut host,
                true,
                true,
                NOT_INSTALLED,
            )
            .unwrap();
            start(&paths, "first", &mut host, &installed.revision).unwrap();
            let (_, config) = load_installation(&paths).unwrap().unwrap();
            let mut changed = report(
                &config,
                if changed_launch {
                    "replacement"
                } else {
                    "launch-first"
                },
            );
            if !changed_launch {
                changed.counts.insert("confirmations".into(), 1);
            }
            host.reports = VecDeque::from([report(&config, "launch-first"), changed]);
            host.calls.clear();
            assert!(stop(
                &paths,
                "first",
                &mut host,
                &installed.revision,
                Some("launch-first")
            )
            .is_err());
            assert!(host.loaded);
            assert!(paths.agent.is_file());
            assert!(host.calls.iter().all(|call| !call.starts_with("bootout ")));
            fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
        }
    }

    #[test]
    fn resident_declined_install_does_not_save_credentials_or_launch_anything() {
        let (paths, source, mut host) = fixture("declined");
        host.confirmed = false;
        assert!(install(
            &paths,
            &source,
            "first",
            &mut host,
            false,
            true,
            NOT_INSTALLED
        )
        .is_err());
        assert!(!paths.root.exists());
        assert!(host.calls.is_empty());
        assert_eq!(host.registrations, 0);
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_connect_is_the_only_registration_and_preserves_generation() {
        let (paths, source, mut host) = fixture("connect");
        let installed = install(
            &paths,
            &source,
            "first",
            &mut host,
            false,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        assert!(installed.project_id.is_some());
        assert_eq!(host.registrations, 0);
        start(&paths, "first", &mut host, &installed.revision).unwrap();
        let profile = connect(&paths, &mut host, &installed.revision).unwrap();
        assert_eq!(host.registrations, 1);
        let current = status(&paths, "first", &mut host).unwrap();
        assert_eq!(current.project_id, Some(profile.id));
        assert_eq!(current.revision, installed.revision);
        assert_eq!(current.launch_id.as_deref(), Some("launch-first"));
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_prepared_registration_keeps_old_default_until_explicit_select() {
        let root = scratch("registry-order");
        let registry_path = root.join("projects.json");
        let first = DesktopProjectProfile {
            id: "old-resident".into(),
            name: "old".into(),
            repo_root: "@assistant".into(),
            base_url: "http://127.0.0.1:18555".into(),
            last_opened_at: 10,
            kind: "remote".into(),
            assistant_pending: false,
        };
        let project = DesktopProjectProfile {
            id: "local-project".into(),
            name: "project".into(),
            repo_root: "/fixture/project".into(),
            base_url: "http://127.0.0.1:18556".into(),
            last_opened_at: 9,
            kind: "local".into(),
            assistant_pending: false,
        };
        save_project_registry(
            &registry_path,
            &DesktopProjectRegistry {
                projects: vec![first.clone(), project.clone()],
            },
        )
        .unwrap();
        let base = "http://127.0.0.1:18731";
        let id = remote_project_id(base, "@assistant");
        let added = register_preserving_order(&registry_path, base, &id).unwrap();
        assert_eq!(
            serde_json::to_value(&added)
                .unwrap()
                .get("assistantPending"),
            Some(&serde_json::Value::Bool(true))
        );
        let prepared = load_project_registry(&registry_path).unwrap();
        assert_eq!(
            prepared.projects,
            vec![first.clone(), project.clone(), added.clone()]
        );
        assert_eq!(
            register_preserving_order(&registry_path, base, &id).unwrap(),
            added
        );
        assert_eq!(
            load_project_registry(&registry_path).unwrap().projects,
            prepared.projects
        );
        select_registered(&registry_path, base, &id).unwrap();
        let selected = load_project_registry(&registry_path).unwrap();
        assert_eq!(selected.projects[0].id, id);
        assert!(serde_json::to_value(&selected.projects[0])
            .unwrap()
            .get("assistantPending")
            .is_none());
        assert_eq!(selected.projects[1..], [first, project]);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn resident_prepared_empty_registry_stays_pending_until_successful_select() {
        let root = scratch("empty-pending");
        let path = root.join("projects.json");
        let base = "http://127.0.0.1:18731";
        let id = remote_project_id(base, "@assistant");
        register_preserving_order(&path, base, &id).unwrap();
        let restored = load_project_registry(&path).unwrap();
        let pending = serde_json::to_value(&restored.projects[0]).unwrap();
        assert_eq!(
            pending.get("assistantPending"),
            Some(&serde_json::Value::Bool(true))
        );
        let again = register_preserving_order(&path, base, &id).unwrap();
        assert_eq!(
            serde_json::to_value(again).unwrap().get("assistantPending"),
            Some(&serde_json::Value::Bool(true))
        );
        select_registered(&path, base, &id).unwrap();
        assert!(
            serde_json::to_value(&load_project_registry(&path).unwrap().projects[0])
                .unwrap()
                .get("assistantPending")
                .is_none()
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    #[cfg(unix)]
    fn resident_managed_identity_without_private_socket_never_selects_tcp_fallback() {
        let (paths, source, mut host) = fixture("no-socket-fallback");
        let installed = install(
            &paths,
            &source,
            "first",
            &mut host,
            false,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        assert!(socket_for_project(&paths, installed.project_id.as_deref().unwrap()).is_err());
        assert!(socket_for_project(&paths, "another-remote-instance")
            .unwrap()
            .is_none());
        let endpoint = crate::resident_socket::socket_path(&paths.root, current_uid()).unwrap();
        private_dir(endpoint.parent().unwrap()).unwrap();
        fs::write(&endpoint, b"ordinary file is not a socket").unwrap();
        assert!(socket_for_project(&paths, installed.project_id.as_deref().unwrap()).is_err());
        fs::remove_file(&endpoint).unwrap();
        fs::remove_dir(endpoint.parent().unwrap()).unwrap();
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_deleted_install_record_still_reserves_managed_identity_against_tcp_fallback() {
        let (paths, source, mut host) = fixture("lost-record");
        let managed_id = remote_project_id(VIRTUAL_ORIGIN, "@assistant");
        assert!(socket_for_project(&paths, &managed_id).is_err());
        assert!(socket_for_project(&paths, "other-remote")
            .unwrap()
            .is_none());
        install(
            &paths,
            &source,
            "first",
            &mut host,
            false,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        fs::remove_file(paths.record()).unwrap();
        assert!(socket_for_project(&paths, &managed_id).is_err());
        assert!(socket_for_project(&paths, "other-remote")
            .unwrap()
            .is_none());
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn resident_http_bearer_uses_private_unix_socket_only() {
        use std::os::unix::fs::PermissionsExt;
        use std::os::unix::net::UnixListener;
        let (paths, source, mut host) = fixture("uds-http");
        install(
            &paths,
            &source,
            "first",
            &mut host,
            false,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        let (_, config) = load_installation(&paths).unwrap().unwrap();
        let socket = crate::resident_socket::socket_path(&paths.root, current_uid()).unwrap();
        private_dir(socket.parent().unwrap()).unwrap();
        let listener = UnixListener::bind(&socket).unwrap();
        fs::set_permissions(&socket, fs::Permissions::from_mode(0o600)).unwrap();
        let expected_token = config.token.clone();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut buffer = [0_u8; 1024];
            while !request.windows(4).any(|part| part == b"\r\n\r\n") {
                let count = stream.read(&mut buffer).unwrap();
                assert!(count > 0);
                request.extend_from_slice(&buffer[..count]);
                assert!(request.len() <= 8192);
            }
            let request = String::from_utf8(request).unwrap();
            assert!(request.starts_with("GET /api/runtime-activity HTTP/1.1\r\n"));
            assert!(request.lines().any(|line| line
                .split_once(':')
                .is_some_and(|(name, value)| name.eq_ignore_ascii_case("authorization")
                    && value.trim() == format!("Bearer {expected_token}"))));
            let body = b"{\"private_transport\":true}";
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    )
                    .as_bytes(),
                )
                .unwrap();
            stream.write_all(body).unwrap();
        });
        let value: serde_json::Value = local_json(&config, "/api/runtime-activity").unwrap();
        assert_eq!(
            value.get("private_transport"),
            Some(&serde_json::Value::Bool(true))
        );
        server.join().unwrap();
        fs::set_permissions(&socket, fs::Permissions::from_mode(0o666)).unwrap();
        assert!(private_socket(&paths.root).is_err());
        fs::remove_file(&socket).unwrap();
        fs::remove_dir(socket.parent().unwrap()).unwrap();
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_select_rejects_replacement_launch_before_preference_change() {
        let (paths, source, mut host) = fixture("select-identity");
        let installed = install(
            &paths,
            &source,
            "first",
            &mut host,
            false,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        start(&paths, "first", &mut host, &installed.revision).unwrap();
        assert!(select(
            &paths,
            "first",
            &mut host,
            &installed.revision,
            "old-launch"
        )
        .is_err());
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_bootstrap_error_restores_a_startable_old_installation() {
        let (paths, source, mut host) = fixture("bootstrap-error-rollback");
        let installed = install(
            &paths, &source, "first", &mut host, false, true, NOT_INSTALLED,
        )
        .unwrap();
        start(&paths, "first", &mut host, &installed.revision).unwrap();
        let (old, _) = load_installation(&paths).unwrap().unwrap();
        fs::write(source.join("_internal/data"), b"replacement program").unwrap();
        host.error_bootstrap = 1;
        assert!(change(
            &paths, "second", &mut host, &old.revision, Some("launch-first"),
            Change::Program(&source),
        )
        .is_err());
        let (current, _) = load_installation(&paths).unwrap().unwrap();
        assert_eq!(current.revision, old.revision);
        assert!(check_agent(&paths, &current).is_ok());
        assert!(host.loaded);
        start(&paths, "second", &mut host, &current.revision).unwrap();
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_bootstrap_error_keeps_matching_new_files_when_rollback_is_not_safe() {
        for reason in ["loaded", "unknown", "cleanup-failed"] {
            let (paths, source, mut host) = fixture(reason);
            let installed = install(
                &paths, &source, "first", &mut host, false, true, NOT_INSTALLED,
            )
            .unwrap();
            start(&paths, "first", &mut host, &installed.revision).unwrap();
            fs::write(source.join("_internal/data"), b"replacement program").unwrap();
            host.error_bootstrap = 1;
            host.ambiguous_bootstrap = reason == "loaded";
            host.bootstrap_print_errors = if reason == "unknown" { 2 } else { 0 };
            host.bootstrap_agent_removal_failures = if reason == "cleanup-failed" { 2 } else { 0 };
            host.calls.clear();
            assert!(change(
                &paths, "second", &mut host, &installed.revision, Some("launch-first"),
                Change::Program(&source),
            )
            .is_err());
            let (current, _) = load_installation(&paths).unwrap().unwrap();
            assert_ne!(current.revision, installed.revision, "{reason}");
            assert!(paths.agent.is_file(), "{reason}");
            assert!(check_agent(&paths, &current).is_ok(), "{reason}");
            assert_eq!(host.calls.iter().filter(|call| call.starts_with("bootstrap ")).count(), 1);
            assert_eq!(host.calls.iter().filter(|call| call.starts_with("bootout ")).count(), 1);
            fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
        }
    }

    #[test]
    fn resident_stop_removal_failure_stays_explicit_and_can_be_retried_without_a_launch() {
        let (paths, source, mut host) = fixture("stop-cleanup-retry");
        let installed = install(
            &paths, &source, "first", &mut host, true, true, NOT_INSTALLED,
        )
        .unwrap();
        start(&paths, "first", &mut host, &installed.revision).unwrap();
        host.fail_agent_removal = 1;
        assert!(stop(
            &paths, "first", &mut host, &installed.revision, Some("launch-first"),
        )
        .is_err());
        let pending = status(&paths, "first", &mut host).unwrap();
        assert_eq!(pending.state, "cleanup_required");
        assert!(pending.launch_id.is_none());
        assert!(paths.agent.is_file());
        assert!(start(&paths, "first", &mut host, &installed.revision).is_err());
        assert!(change(
            &paths, "first", &mut host, &installed.revision, None, Change::Model,
        )
        .is_err());
        assert_eq!(load_installation(&paths).unwrap().unwrap().0.revision, installed.revision);
        host.calls.clear();
        let stopped = stop(&paths, "first", &mut host, &installed.revision, None).unwrap();
        assert_eq!(stopped.state, "stopped");
        assert!(!paths.agent.exists());
        assert!(host.calls.iter().all(|call| call.starts_with("print ")));
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_cleanup_retry_never_removes_a_foreign_item_or_stops_a_live_service() {
        for foreign in [false, true] {
            let (paths, source, mut host) = fixture("cleanup-recheck");
            let installed = install(
                &paths, &source, "first", &mut host, false, true, NOT_INSTALLED,
            )
            .unwrap();
            start(&paths, "first", &mut host, &installed.revision).unwrap();
            host.fail_agent_removal = 1;
            assert!(stop(
                &paths, "first", &mut host, &installed.revision, Some("launch-first"),
            )
            .is_err());
            if foreign {
                fs::write(&paths.agent, b"foreign login item").unwrap();
            } else {
                host.loaded = true;
            }
            let before = fs::read(&paths.agent).unwrap();
            host.calls.clear();
            assert!(stop(&paths, "first", &mut host, &installed.revision, None).is_err());
            assert_eq!(fs::read(&paths.agent).unwrap(), before);
            assert!(host.calls.iter().all(|call| call.starts_with("print ")));
            fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
        }
    }

    #[test]
    fn resident_running_update_restores_old_pointer_only_when_failed_start_is_unloaded() {
        let (paths, source, mut host) = fixture("rollback");
        let installed = install(
            &paths,
            &source,
            "first",
            &mut host,
            true,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        start(&paths, "first", &mut host, &installed.revision).unwrap();
        let (old, old_config) = load_installation(&paths).unwrap().unwrap();
        fs::write(source.join("_internal/data"), b"replacement program").unwrap();
        host.fail_bootstrap = 1;
        host.calls.clear();
        let error = change(
            &paths,
            "second",
            &mut host,
            &old.revision,
            Some("launch-first"),
            Change::Program(&source),
        )
        .err()
        .unwrap();
        assert!(error.contains("已恢复"));
        let (current, config) = load_installation(&paths).unwrap().unwrap();
        assert_eq!(current.release_id, old.release_id);
        assert_eq!(current.revision, old.revision);
        assert_eq!(config.model_env, old_config.model_env);
        assert_eq!(config.workspace_root, old_config.workspace_root);
        assert!(host.loaded);
        assert_eq!(
            host.calls
                .iter()
                .filter(|call| call.starts_with("bootstrap "))
                .count(),
            2
        );
        assert!(fs::read_to_string(&paths.agent)
            .unwrap()
            .contains(&old.release_id));
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_ambiguous_new_start_keeps_new_pointer_and_does_not_start_again() {
        let (paths, source, mut host) = fixture("ambiguous");
        let installed = install(
            &paths,
            &source,
            "first",
            &mut host,
            true,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        start(&paths, "first", &mut host, &installed.revision).unwrap();
        let (old, _) = load_installation(&paths).unwrap().unwrap();
        fs::write(source.join("_internal/data"), b"replacement program").unwrap();
        host.fail_bootstrap = 1;
        host.ambiguous_bootstrap = true;
        host.calls.clear();
        assert!(change(
            &paths,
            "second",
            &mut host,
            &old.revision,
            Some("launch-first"),
            Change::Program(&source)
        )
        .is_err());
        let (current, _) = load_installation(&paths).unwrap().unwrap();
        assert_ne!(current.release_id, old.release_id);
        assert_ne!(current.revision, old.revision);
        assert!(host.loaded);
        assert_eq!(
            host.calls
                .iter()
                .filter(|call| call.starts_with("bootstrap "))
                .count(),
            1
        );
        assert_eq!(
            host.calls
                .iter()
                .filter(|call| call.starts_with("bootout "))
                .count(),
            1
        );
        assert!(fs::read_to_string(&paths.agent)
            .unwrap()
            .contains(&current.release_id));
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_update_probe_failure_preserves_running_old_program_and_config() {
        let (paths, source, mut host) = fixture("update-probe");
        let installed = install(
            &paths,
            &source,
            "first",
            &mut host,
            true,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        start(&paths, "first", &mut host, &installed.revision).unwrap();
        let (old, _) = load_installation(&paths).unwrap().unwrap();
        host.supports_resident = false;
        host.calls.clear();
        assert!(change(
            &paths,
            "second",
            &mut host,
            &old.revision,
            Some("launch-first"),
            Change::Program(&source)
        )
        .is_err());
        assert_eq!(
            load_installation(&paths).unwrap().unwrap().0.revision,
            old.revision
        );
        assert!(host.loaded);
        assert!(host
            .calls
            .iter()
            .all(|call| !call.starts_with("bootout ") && !call.starts_with("bootstrap ")));
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn resident_generation_is_private_and_rejects_shared_or_linked_config() {
        use std::os::unix::fs::PermissionsExt;
        let (paths, source, mut host) = fixture("private-generation");
        install(
            &paths,
            &source,
            "first",
            &mut host,
            false,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        let (record, config) = load_installation(&paths).unwrap().unwrap();
        assert_eq!(
            Path::new(&config.workspace_root),
            paths.workspace().canonicalize().unwrap()
        );
        assert_eq!(
            paths.config(&record),
            paths
                .root
                .join("generations")
                .join(&record.revision)
                .join("service-config.json")
        );
        assert_eq!(
            fs::metadata(paths.config(&record))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(paths.config(&record).parent().unwrap())
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        fs::set_permissions(paths.config(&record), fs::Permissions::from_mode(0o644)).unwrap();
        assert!(load_installation(&paths).is_err());
        fs::set_permissions(paths.config(&record), fs::Permissions::from_mode(0o600)).unwrap();
        let saved = paths.root.join("saved-config.json");
        fs::rename(paths.config(&record), &saved).unwrap();
        std::os::unix::fs::symlink(saved, paths.config(&record)).unwrap();
        assert!(load_installation(&paths).is_err());
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_failed_login_item_reports_fixed_diagnostic_and_log_without_secret() {
        let (paths, source, mut host) = fixture("startup-diagnostic");
        install(
            &paths,
            &source,
            "first",
            &mut host,
            false,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        host.loaded = true;
        host.report_error = true;
        host.launch_stdout =
            "state = waiting\nlast exit code = 42\nenv = fixture-private-secret".into();
        let current = status(&paths, "first", &mut host).unwrap();
        assert_eq!(current.state, "unavailable");
        assert!(current.message.contains("42"));
        assert!(current.message.contains(
            &paths
                .root
                .join("logs/service.log")
                .to_string_lossy()
                .to_string()
        ));
        assert!(!current.message.contains("fixture-private-secret"));
        assert!(current.launch_id.is_none());
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_start_stop_restart_uses_only_launchd_and_preserves_config() {
        let (paths, source, mut host) = fixture("lifecycle");
        let installed = install(
            &paths,
            &source,
            "first",
            &mut host,
            true,
            true,
            NOT_INSTALLED,
        )
        .unwrap();
        let running = start(&paths, "first", &mut host, &installed.revision).unwrap();
        assert_eq!(running.state, "running");
        let plist = fs::read_to_string(&paths.agent).unwrap();
        let (record, config) = load_installation(&paths).unwrap().unwrap();
        assert!(plist.contains(
            &paths
                .runtime(&record)
                .join("vortocode-runtime")
                .to_string_lossy()
                .to_string()
        ));
        assert!(!plist.contains(&config.token));
        assert!(!plist.contains("fixture-model-key"));
        assert!(!plist.contains("VORTOONE_SUPERVISOR_PID"));
        stop(
            &paths,
            "first",
            &mut host,
            &installed.revision,
            Some("launch-first"),
        )
        .unwrap();
        assert!(!paths.agent.exists());
        assert!(!host.loaded);
        assert!(paths.workspace().is_dir());
        assert!(paths.config(&record).is_file());
        start(&paths, "first", &mut host, &installed.revision).unwrap();
        assert!(host.loaded);
        assert_eq!(
            host.calls
                .iter()
                .filter(|call| call.starts_with("bootstrap "))
                .count(),
            2
        );
        fs::remove_dir_all(paths.root.parent().unwrap()).unwrap();
    }

    #[test]
    fn resident_preflight_rejects_changed_launch_and_workspace() {
        let counts = idle_counts();
        assert!(assert_idle(
            Path::new("/assistant"),
            "first",
            "second",
            "general",
            "/assistant",
            Some(&counts)
        )
        .is_err());
        assert!(assert_idle(
            Path::new("/assistant"),
            "first",
            "first",
            "general",
            "/another",
            Some(&counts)
        )
        .is_err());
        assert!(assert_idle(
            Path::new("/assistant"),
            "first",
            "first",
            "project",
            "/assistant",
            Some(&counts)
        )
        .is_err());
    }

    #[test]
    fn resident_preflight_requires_all_seven_idle_counters() {
        let counts = idle_counts();
        assert!(assert_idle(
            Path::new("/assistant"),
            "first",
            "first",
            "general",
            "/assistant",
            Some(&counts)
        )
        .is_ok());
        for key in ACTIVITY_COUNTS {
            let mut active = counts.clone();
            active.insert(key.into(), 1);
            assert!(
                assert_idle(
                    Path::new("/assistant"),
                    "first",
                    "first",
                    "general",
                    "/assistant",
                    Some(&active)
                )
                .is_err(),
                "{key}"
            );
            active.remove(key);
            assert!(
                assert_idle(
                    Path::new("/assistant"),
                    "first",
                    "first",
                    "general",
                    "/assistant",
                    Some(&active)
                )
                .is_err(),
                "missing {key}"
            );
        }
    }

    #[test]
    fn resident_stable_copy_keeps_the_complete_onedir_tree() {
        let root = scratch("copy");
        let source = root.join("bundled");
        let target = root.join("stable");
        std::fs::create_dir_all(source.join("_internal/config")).unwrap();
        std::fs::write(source.join("vortocode-runtime"), b"executable").unwrap();
        std::fs::write(
            source.join("_internal/config/default.json"),
            b"runtime data",
        )
        .unwrap();
        copy_runtime_tree(&source, &target).unwrap();
        std::fs::remove_dir_all(&source).unwrap();
        assert_eq!(
            std::fs::read(target.join("_internal/config/default.json")).unwrap(),
            b"runtime data"
        );
        std::fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn resident_stable_copy_rejects_links_outside_the_runtime() {
        let root = scratch("outside-link");
        let source = root.join("bundled");
        std::fs::create_dir_all(&source).unwrap();
        std::fs::write(source.join("vortocode-runtime"), b"executable").unwrap();
        std::fs::write(root.join("outside"), b"outside").unwrap();
        std::os::unix::fs::symlink("../outside", source.join("linked")).unwrap();
        assert!(copy_runtime_tree(&source, &root.join("stable")).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
}
