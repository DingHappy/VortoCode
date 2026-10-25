// 内置引擎进程：状态、端口选择、启动与停止（含进程组回收）、开发态与打包后的启动命令。
// 从 lib.rs 原样搬出。
use super::*;

pub(crate) fn process_status(inner: &mut GatewayProcessInner) -> GatewayProcessStatus {
    let checked = inner
        .child
        .as_mut()
        .map(|child| (child.id(), child.try_wait()));

    match checked {
        Some((pid, Ok(None))) => GatewayProcessStatus {
            running: true,
            pid: Some(pid),
            runtime_id: Some(inner.runtime_id.clone()),
            project_id: inner.project_id.clone(),
            workspace_id: inner.workspace_id.clone(),
            command: inner.command.clone(),
            scope: inner.scope.clone(),
            workspace_root: inner
                .workspace_root
                .as_ref()
                .map(|path| path.to_string_lossy().to_string()),
            repo_root: inner
                .repo_root
                .as_ref()
                .map(|path| path.to_string_lossy().to_string()),
            base_url: inner.base_url.clone(),
            message: "Desktop 启动的本机 runtime 正在运行".into(),
        },
        Some((_pid, Ok(Some(exit)))) => {
            inner.child = None;
            GatewayProcessStatus {
                running: false,
                pid: None,
                runtime_id: Some(inner.runtime_id.clone()),
                project_id: inner.project_id.clone(),
                workspace_id: inner.workspace_id.clone(),
                command: inner.command.clone(),
                scope: inner.scope.clone(),
                workspace_root: inner
                    .workspace_root
                    .as_ref()
                    .map(|path| path.to_string_lossy().to_string()),
                repo_root: inner
                    .repo_root
                    .as_ref()
                    .map(|path| path.to_string_lossy().to_string()),
                base_url: inner.base_url.clone(),
                message: format!("runtime 已退出（{exit}）"),
            }
        }
        Some((_pid, Err(error))) => GatewayProcessStatus {
            running: false,
            pid: None,
            runtime_id: Some(inner.runtime_id.clone()),
            project_id: inner.project_id.clone(),
            workspace_id: inner.workspace_id.clone(),
            command: inner.command.clone(),
            scope: inner.scope.clone(),
            workspace_root: inner
                .workspace_root
                .as_ref()
                .map(|path| path.to_string_lossy().to_string()),
            repo_root: inner
                .repo_root
                .as_ref()
                .map(|path| path.to_string_lossy().to_string()),
            base_url: inner.base_url.clone(),
            message: format!("无法读取 runtime 状态：{error}"),
        },
        None => GatewayProcessStatus {
            running: false,
            pid: None,
            runtime_id: None,
            project_id: None,
            workspace_id: None,
            command: None,
            scope: None,
            workspace_root: None,
            repo_root: None,
            base_url: None,
            message: "Desktop 尚未启动本机 runtime".into(),
        },
    }
}

pub(crate) fn empty_process_status(message: impl Into<String>) -> GatewayProcessStatus {
    GatewayProcessStatus {
        running: false,
        pid: None,
        runtime_id: None,
        project_id: None,
        workspace_id: None,
        command: None,
        scope: None,
        workspace_root: None,
        repo_root: None,
        base_url: None,
        message: message.into(),
    }
}

pub(crate) fn select_gateway_port_with<F, G>(
    preferred: u16,
    is_available: F,
    allocate: G,
) -> Result<u16, String>
where
    F: FnOnce(u16) -> bool,
    G: FnOnce() -> Result<u16, String>,
{
    if preferred >= 1024 && is_available(preferred) {
        return Ok(preferred);
    }

    let port = allocate()?;
    if port < 1024 {
        return Err("系统分配的本地引擎端口无效".into());
    }
    Ok(port)
}

pub(crate) fn select_gateway_port(preferred: u16) -> Result<u16, String> {
    select_gateway_port_with(
        preferred,
        |port| TcpListener::bind((Ipv4Addr::LOCALHOST, port)).is_ok(),
        || {
            let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
                .map_err(|error| format!("无法为本地引擎分配端口：{error}"))?;
            listener
                .local_addr()
                .map(|address| address.port())
                .map_err(|error| format!("无法读取本地引擎端口：{error}"))
        },
    )
}

pub(crate) fn prepare_gateway_process(command: &mut Command) {
    // runtime 可能再派生子进程（旧版 PyInstaller one-file 的 bootloader、server 起的工具进程）。
    // 只杀直接 Child 会留下仍在监听的孙进程；独立进程组让 Desktop 能把整个托管 runtime 作为一个生命周期单元回收。
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
}

#[cfg(unix)]
pub(crate) fn process_group_alive(pid: u32) -> bool {
    let result = unsafe { libc::kill(-(pid as i32), 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

pub(crate) fn terminate_gateway_child(child: &mut Child) -> Result<(), String> {
    let pid = child.id();
    #[cfg(unix)]
    {
        let terminated = unsafe { libc::kill(-(pid as i32), libc::SIGTERM) };
        if terminated != 0 {
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(format!("无法停止 runtime 进程组：{error}"));
            }
        }
        for _ in 0..20 {
            let _ = child.try_wait();
            if !process_group_alive(pid) {
                let _ = child.wait();
                return Ok(());
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        let killed = unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
        if killed != 0 && std::io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH) {
            return Err(format!(
                "runtime 未在宽限期内退出，且无法终止进程组：{}",
                std::io::Error::last_os_error()
            ));
        }
        let _ = child.wait();
        return Ok(());
    }
    #[cfg(not(unix))]
    {
        child
            .kill()
            .map_err(|error| format!("停止 runtime 失败：{error}"))?;
        let _ = child.wait();
        Ok(())
    }
}

pub(crate) fn build_command(
    executable: &str,
    module: bool,
    repo_root: &Path,
    port: u16,
    token: &str,
    scope: &str,
) -> Command {
    configure_gateway_command(
        Command::new(executable),
        module,
        repo_root,
        port,
        token,
        scope,
    )
}

pub(crate) fn configure_gateway_command(
    mut command: Command,
    module: bool,
    repo_root: &Path,
    port: u16,
    token: &str,
    scope: &str,
) -> Command {
    if module {
        command.args(["-m", "src.cli"]);
    }
    command.args(["server", "--host", "127.0.0.1", "--port"]);
    command.arg(port.to_string());
    command.current_dir(repo_root);
    command.env("VORTOCODE_WORKSPACE_SCOPE", scope);
    // Channels belong to an explicitly started resident service, not each Desktop tab.
    // Pin values: removing them allows Python's load_dotenv to re-enable them.
    command.env("VORTOCODE_IM", "");
    command.env("VORTOCODE_CRON", "0");
    command.env("VORTOCODE_HEARTBEAT", "0");
    // 监护进程 pid：runtime 据此在我们被强杀/崩溃时自己退出。正常退出走 RunEvent::Exit 的
    // terminate_gateway_child；强杀时那条回调根本不会执行，于是 runtime 会继续占着端口、
    // 持着一个能调模型的 agent，而界面上再无入口（2026-09-17 真机诊断留下两对孤儿进程）。
    command.env("VORTOCODE_SUPERVISOR_PID", std::process::id().to_string());
    if token.is_empty() {
        command.env_remove("VORTOCODE_API_TOKEN");
        command.env_remove("AUTODEV_API_TOKEN");
    } else {
        command.env("VORTOCODE_API_TOKEN", token);
    }
    command
}

/// 内置 runtime 是 PyInstaller onedir 目录（可执行文件 + _internal/），随 App 放在 resources/runtime/。
/// 不用 one-file sidecar：它每次启动都要把整个 Python 环境解压到临时目录，多花 3–4 秒。
pub(crate) fn bundled_runtime_path(app: &AppHandle) -> Result<PathBuf, String> {
    let resources = app
        .path()
        .resource_dir()
        .map_err(|error| format!("无法定位 Desktop 内置 runtime：{error}"))?;
    let path = bundled_runtime_in(&resources);
    if path.is_file() {
        Ok(path)
    } else {
        Err(format!("无法定位 Desktop 内置 runtime：{} 不存在", path.display()))
    }
}

pub(crate) fn bundled_runtime_in(resources: &Path) -> PathBuf {
    let name = if cfg!(windows) { "vortocode-runtime.exe" } else { "vortocode-runtime" };
    resources.join("runtime").join(name)
}

pub(crate) fn build_bundled_runtime_command(
    app: &AppHandle,
    repo_root: &Path,
    port: u16,
    token: &str,
    scope: &str,
) -> Result<Command, String> {
    Ok(configure_gateway_command(
        Command::new(bundled_runtime_path(app)?),
        false,
        repo_root,
        port,
        token,
        scope,
    ))
}

/// 只认构建这个 App 的那棵源码树（按 build.rs 编进来的路径哈希精确比对）：长得像 VortoCode 仓库
/// 的别处目录一律不算，否则打开一个仿冒仓库就能让 Desktop 跑它的源码。
pub(crate) fn is_vortocode_development_root(root: &Path) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let expected = env!("VORTOCODE_SOURCE_ROOT_SHA256");
    !expected.is_empty()
        && root.canonicalize().is_ok_and(|path| {
            format!("{:x}", Sha256::digest(path.as_os_str().as_bytes())) == expected
        })
        && root.join("src/cli.py").is_file()
}

#[tauri::command]
pub(crate) fn gateway_process_status(
    app: AppHandle,
    runtime_id: Option<String>,
    state: State<'_, GatewayProcess>,
) -> Result<GatewayProcessStatus, String> {
    let mut supervisor = state
        .0
        .lock()
        .map_err(|_| "runtime 状态锁已损坏".to_string())?;
    let target = runtime_id.or_else(|| supervisor.active_runtime_id.clone());
    let Some(target) = target else {
        return Ok(empty_process_status("Desktop 尚未启动本机 runtime"));
    };
    let Some(inner) = supervisor.runtimes.get_mut(&target) else {
        if supervisor.active_runtime_id.as_deref() == Some(target.as_str()) {
            supervisor.active_runtime_id = None;
        }
        return Ok(empty_process_status("该 runtime 当前未由 Desktop 托管"));
    };
    let had_child = inner.child.is_some();
    let status = process_status(inner);
    let crashed = had_child && !status.running;
    if crashed {
        supervisor.runtimes.remove(&target);
        if supervisor.active_runtime_id.as_deref() == Some(target.as_str()) {
            supervisor.active_runtime_id = None;
        }
        if let Ok(path) = gateway_recovery_path(&app) {
            let previous = load_gateway_recovery(&path, Some(&target)).ok().flatten();
            if let Some(record) = crashed_recovery_record(previous, &status) {
                let _ = save_gateway_recovery(&path, &record);
            }
        }
    }
    Ok(status)
}

#[tauri::command]
pub(crate) fn list_gateway_processes(
    app: AppHandle,
    state: State<'_, GatewayProcess>,
) -> Result<Vec<GatewayProcessStatus>, String> {
    let mut supervisor = state
        .0
        .lock()
        .map_err(|_| "runtime 状态锁已损坏".to_string())?;
    let runtime_ids = supervisor.runtimes.keys().cloned().collect::<Vec<_>>();
    let mut statuses = Vec::new();
    let mut crashed = Vec::new();
    for runtime_id in runtime_ids {
        let Some(inner) = supervisor.runtimes.get_mut(&runtime_id) else {
            continue;
        };
        let had_child = inner.child.is_some();
        let status = process_status(inner);
        if status.running {
            statuses.push(status);
        } else if had_child {
            crashed.push((runtime_id, status));
        }
    }
    for (runtime_id, status) in crashed {
        supervisor.runtimes.remove(&runtime_id);
        if supervisor.active_runtime_id.as_deref() == Some(runtime_id.as_str()) {
            supervisor.active_runtime_id = None;
        }
        if let Ok(path) = gateway_recovery_path(&app) {
            let previous = load_gateway_recovery(&path, Some(&runtime_id))
                .ok()
                .flatten();
            if let Some(record) = crashed_recovery_record(previous, &status) {
                let _ = save_gateway_recovery(&path, &record);
            }
        }
    }
    statuses.sort_by(|left, right| left.runtime_id.cmp(&right.runtime_id));
    Ok(statuses)
}

// 可能阻塞：移出主线程，原因见 get_llm_profile。
#[tauri::command(async)]
pub(crate) fn start_gateway(
    app: AppHandle,
    repo_root: Option<String>,
    scope: String,
    workspace_id: Option<String>,
    port: u16,
    token: Option<String>,
    state: State<'_, GatewayProcess>,
    llm_profile_store: State<'_, DesktopLlmProfileStore>,
) -> Result<GatewayProcessStatus, String> {
    let scope = normalize_runtime_scope(&scope)?;
    let runtime_workspace_id = if scope == SCRATCH_SCOPE {
        Some(normalize_workspace_id(
            workspace_id.as_deref().unwrap_or_default(),
        )?)
    } else {
        None
    };
    let (root, project_root) = if scope == PROJECT_SCOPE {
        let selected = canonical_repo_root(repo_root.as_deref().unwrap_or_default())?;
        let root = git_workspace_root(&selected)?;
        (root.clone(), Some(root))
    } else {
        let root = managed_workspace_root(&app, scope, runtime_workspace_id.as_deref())?;
        create_dir_all(&root).map_err(|error| format!("无法创建 {scope} 工作区：{error}"))?;
        if scope == SCRATCH_SCOPE && !root.join(".git").is_dir() {
            let output = Command::new("git")
                .args(["init", "-q"])
                .current_dir(&root)
                .output()
                .map_err(|error| format!("无法初始化 Scratch Git 工作区：{error}"))?;
            if !output.status.success() {
                return Err(format!(
                    "无法初始化 Scratch Git 工作区：{}",
                    String::from_utf8_lossy(&output.stderr).trim()
                ));
            }
        }
        (root, None)
    };

    let runtime_id = runtime_id_for(scope, &root);
    let runtime_project_id = project_root.as_ref().map(|root| project_id(root));

    // 先读模型配置、再拿 runtime 锁：读配置是 I/O，持锁期间做会让 gateway_process_status
    // 等命令一起排队。结果先留着，失败到原位置再报，保持"已在运行就直接返回"的语义不变。
    let llm_snapshot = cached_profile_snapshot(&app, &llm_profile_store);
    let preferences = desktop_preferences_path(&app)
        .map(|path| read_desktop_preferences(&path))
        .unwrap_or_default();

    let mut supervisor = state
        .0
        .lock()
        .map_err(|_| "runtime 状态锁已损坏".to_string())?;
    if let Some(existing) = supervisor.runtimes.get_mut(&runtime_id) {
        let status = process_status(existing);
        if status.running {
            supervisor.active_runtime_id = Some(runtime_id);
            return Ok(status);
        }
        supervisor.runtimes.remove(&runtime_id);
    }
    if supervisor.runtimes.len() >= MAX_DESKTOP_RUNTIMES {
        return Err(format!(
            "Desktop 已托管 {MAX_DESKTOP_RUNTIMES} 个后台 runtime；请先停止不再需要的工作区"
        ));
    }
    let port = select_gateway_port(port)?;
    let base_url = format!("http://127.0.0.1:{port}");

    let log_dir = app
        .path()
        .app_config_dir()
        .map_err(|error| format!("无法定位 Desktop 日志目录：{error}"))?
        .join("logs");
    create_dir_all(&log_dir).map_err(|error| format!("无法创建 runtime 日志目录：{error}"))?;
    let log_path = log_dir.join(format!("runtime-{runtime_id}.log"));
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|error| format!("无法打开 runtime 日志：{error}"))?;
    let token = token.unwrap_or_default().trim().to_string();
    let llm_snapshot = llm_snapshot?;
    let llm_profile = llm_snapshot.profile;
    let launch_id = new_profile_revision();

    let mut candidates = Vec::new();
    let mut sidecar_setup_error = None;
    match build_bundled_runtime_command(&app, &root, port, &token, scope) {
        Ok(command) => candidates.push((command, "Desktop 内置 runtime".to_string())),
        Err(error) => sidecar_setup_error = Some(error),
    }
    candidates.push((
        build_command("vc", false, &root, port, &token, scope),
        "系统 vc server".to_string(),
    ));
    if is_vortocode_development_root(&root) {
        candidates.extend([("python3", "python3"), ("python", "python")].map(
            |(executable, label)| {
                (
                    build_command(executable, true, &root, port, &token, scope),
                    format!("{label} -m src.cli server"),
                )
            },
        ));
    }
    let mut last_not_found = None;
    for (mut command, label) in candidates {
        command.env("VORTOCODE_MODEL_CONFIGURATION_REVISION", &llm_snapshot.revision);
        command.env("VORTOCODE_CONFIGURATION_LAUNCH_ID", &launch_id);
        configure_llm_profile(&mut command, llm_profile.as_ref());
        configure_browser_control(&mut command, &preferences);
        configure_experimental_workers(&mut command, &preferences);
        configure_desktop_control(&mut command, &preferences);
        configure_codex_watch(&mut command, &app, scope, &preferences);
        prepare_gateway_process(&mut command);
        command
            .stdout(Stdio::from(log.try_clone().map_err(|error| {
                format!("无法复制 runtime 日志句柄：{error}")
            })?));
        command
            .stderr(Stdio::from(log.try_clone().map_err(|error| {
                format!("无法复制 runtime 日志句柄：{error}")
            })?));

        match command.spawn() {
            Ok(mut child) => {
                let now = match now_epoch_seconds() {
                    Ok(now) => now,
                    Err(error) => {
                        let _ = terminate_gateway_child(&mut child);
                        return Err(format!("runtime 已启动，但无法建立安全恢复记录：{error}"));
                    }
                };
                let recovery = GatewayRecoveryRecord {
                    runtime_id: runtime_id.clone(),
                    project_id: runtime_project_id.clone(),
                    workspace_id: runtime_workspace_id.clone(),
                    scope: scope.into(),
                    workspace_root: root.to_string_lossy().to_string(),
                    repo_root: root.to_string_lossy().to_string(),
                    base_url: base_url.clone(),
                    pid: Some(child.id()),
                    started_at: now,
                    updated_at: now,
                    status: "running".into(),
                    message: "Desktop 启动的本机 runtime 正在运行".into(),
                };
                if let Err(error) = gateway_recovery_path(&app)
                    .and_then(|path| save_gateway_recovery(&path, &recovery))
                {
                    let _ = terminate_gateway_child(&mut child);
                    return Err(format!("runtime 已启动，但无法建立安全恢复记录：{error}"));
                }
                let mut inner = GatewayProcessInner {
                    configuration_revision: Some(llm_snapshot.revision.clone()),
                    configuration_launch_id: Some(launch_id.clone()),
                    child: Some(child),
                    runtime_id: runtime_id.clone(),
                    project_id: runtime_project_id.clone(),
                    workspace_id: runtime_workspace_id.clone(),
                    scope: Some(scope.into()),
                    workspace_root: Some(root.clone()),
                    repo_root: project_root.clone(),
                    command: Some(label),
                    base_url: Some(base_url.clone()),
                };
                let status = process_status(&mut inner);
                supervisor.runtimes.insert(runtime_id.clone(), inner);
                supervisor.active_runtime_id = Some(runtime_id);
                return Ok(status);
            }
            Err(error) if error.kind() == ErrorKind::NotFound => {
                last_not_found = Some(error);
            }
            Err(error) => return Err(format!("启动 {label} 失败：{error}")),
        }
    }

    Err(format!(
        "找不到 Desktop 内置 runtime 或已安装的 vc 命令，无法为该项目启动 runtime：{}{}",
        last_not_found
            .map(|error| error.to_string())
            .unwrap_or_else(|| "unknown error".into()),
        sidecar_setup_error
            .map(|error| format!("；{error}"))
            .unwrap_or_default(),
    ))
}

#[tauri::command]
pub(crate) fn stop_gateway(
    app: AppHandle,
    runtime_id: Option<String>,
    state: State<'_, GatewayProcess>,
) -> Result<GatewayProcessStatus, String> {
    let mut supervisor = state
        .0
        .lock()
        .map_err(|_| "runtime 状态锁已损坏".to_string())?;
    let target = runtime_id.or_else(|| supervisor.active_runtime_id.clone());
    let Some(target) = target else {
        return Ok(empty_process_status("Desktop 尚未启动本机 runtime"));
    };
    if !supervisor.runtimes.contains_key(&target) {
        if supervisor.active_runtime_id.as_deref() == Some(target.as_str()) {
            supervisor.active_runtime_id = None;
        }
        clear_gateway_recovery_at(&gateway_recovery_path(&app)?, Some(&target))?;
        return Ok(empty_process_status("该 runtime 已停止"));
    }
    stop_supervised_runtime(&mut supervisor, &target)?;
    clear_gateway_recovery_at(&gateway_recovery_path(&app)?, Some(&target))?;
    Ok(empty_process_status("runtime 已停止"))
}

pub(crate) fn stop_supervised_runtime(
    supervisor: &mut GatewaySupervisorInner,
    runtime_id: &str,
) -> Result<bool, String> {
    let Some(mut inner) = supervisor.runtimes.remove(runtime_id) else {
        return Ok(false);
    };
    if let Some(mut child) = inner.child.take() {
        if let Err(error) = terminate_gateway_child(&mut child) {
            inner.child = Some(child);
            supervisor.runtimes.insert(runtime_id.into(), inner);
            return Err(error);
        }
        let _ = child.wait();
    }
    if supervisor.active_runtime_id.as_deref() == Some(runtime_id) {
        supervisor.active_runtime_id = None;
    }
    Ok(true)
}
