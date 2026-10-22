// 工作区文件：路径围栏（只能读工作区里的文件）、列文件、读文件、用外部编辑器打开。
// 从 lib.rs 原样搬出。
use super::*;

pub(crate) fn normalize_workspace_relative(relative_path: &str) -> Result<PathBuf, String> {
    let raw = Path::new(relative_path.trim());
    if raw.as_os_str().is_empty() || raw.is_absolute() {
        return Err("文件路径必须是项目内的相对路径".into());
    }

    let mut normalized = PathBuf::new();
    for component in raw.components() {
        match component {
            Component::Normal(part) => normalized.push(part),
            Component::CurDir => {}
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err("文件路径不能离开项目目录".into());
            }
        }
    }
    if normalized.as_os_str().is_empty() {
        return Err("文件路径不能为空".into());
    }
    Ok(normalized)
}

pub(crate) fn resolve_workspace_file(root: &Path, relative_path: &str) -> Result<(PathBuf, PathBuf), String> {
    let relative = normalize_workspace_relative(relative_path)?;
    let resolved = root
        .join(&relative)
        .canonicalize()
        .map_err(|error| format!("文件不可用：{error}"))?;
    if !resolved.starts_with(root) {
        return Err("拒绝读取项目目录之外的文件".into());
    }
    if !resolved.is_file() {
        return Err("选择的路径不是文件".into());
    }
    Ok((relative, resolved))
}

// ---- 工作区命令的路径围栏 ----
// repo_root 由 webview 传入，不可信：只放行「用户显式登记过 / Desktop 自己创建过」的根
// 及其子目录——注册项目集（projects.json）∪ Desktop 自管工作区子树（general/scratch）∪
// 在管 runtime 根 ∪ 恢复记录根。子目录只会缩小可读集，故允许；比对在 canonicalize 之后
// 按路径组件进行，symlink 会先被解析再判定。

pub(crate) fn workspace_fence_roots(
    registry: &DesktopProjectRegistry,
    recoveries: &GatewayRecoveryRegistry,
    managed_base: Option<PathBuf>,
    supervisor: &GatewaySupervisorInner,
) -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();
    roots.extend(
        registry
            .projects
            .iter()
            // 只并入 local 条目：remote 的 repo_root 是**服务器侧**路径（webview 传入、未经本地
            // 校验），绝不能当本机受信根喂进本地文件读取围栏——否则 repo_root="/" 之类会把本地读
            // 面撑到整个文件系统（远程文件走 R3a-2 的 server API，不经本地围栏）。
            .filter(|project| project.kind != PROJECT_KIND_REMOTE)
            .map(|project| PathBuf::from(&project.repo_root)),
    );
    roots.extend(managed_base);
    for record in &recoveries.runtimes {
        for root in [&record.workspace_root, &record.repo_root] {
            if !root.is_empty() {
                roots.push(PathBuf::from(root));
            }
        }
    }
    for runtime in supervisor.runtimes.values() {
        roots.extend(runtime.workspace_root.clone());
        roots.extend(runtime.repo_root.clone());
    }
    roots
}

pub(crate) fn fence_workspace_root(trusted: &[PathBuf], repo_root: &str) -> Result<PathBuf, String> {
    let requested = canonical_repo_root(repo_root)?;
    let authorized = trusted.iter().any(|root| {
        root.canonicalize()
            .is_ok_and(|canonical| requested.starts_with(&canonical))
    });
    if authorized {
        Ok(requested)
    } else {
        Err("该目录不属于 Desktop 已登记的项目或托管工作区，已拒绝访问".into())
    }
}

pub(crate) fn desktop_workspace_fence(app: &AppHandle, state: &GatewayProcess) -> Result<Vec<PathBuf>, String> {
    // 注册表/恢复记录读取失败时按「收窄」处理（fail-closed）：宁可拒绝合法预览，不放宽围栏。
    let registry = project_registry_path(app)
        .and_then(|path| load_project_registry(&path))
        .unwrap_or_default();
    let recoveries = gateway_recovery_path(app)
        .and_then(|path| load_gateway_recoveries(&path))
        .unwrap_or_default();
    let managed_base = app
        .path()
        .app_data_dir()
        .ok()
        .map(|directory| directory.join("workspaces"));
    let supervisor = state
        .0
        .lock()
        .map_err(|_| "runtime 状态锁已损坏".to_string())?;
    Ok(workspace_fence_roots(
        &registry,
        &recoveries,
        managed_base,
        &supervisor,
    ))
}

#[tauri::command]
pub(crate) fn list_workspace_files(
    app: AppHandle,
    state: State<'_, GatewayProcess>,
    repo_root: String,
) -> Result<WorkspaceFileList, String> {
    list_workspace_files_within(&desktop_workspace_fence(&app, state.inner())?, &repo_root)
}

pub(crate) fn list_workspace_files_within(
    trusted: &[PathBuf],
    repo_root: &str,
) -> Result<WorkspaceFileList, String> {
    let root = fence_workspace_root(trusted, repo_root)?;
    let output = Command::new("git")
        .args([
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ])
        .current_dir(&root)
        .output()
        .map_err(|error| format!("无法运行 git ls-files：{error}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "源码工作区需要 Git 仓库：{}",
            detail.trim().chars().take(300).collect::<String>()
        ));
    }

    let mut files = output
        .stdout
        .split(|byte| *byte == 0)
        .filter_map(|raw| std::str::from_utf8(raw).ok())
        .filter(|path| !path.is_empty())
        .filter_map(|path| {
            normalize_workspace_relative(path)
                .ok()
                .map(|relative| relative.to_string_lossy().replace('\\', "/"))
        })
        .collect::<Vec<_>>();
    files.sort_unstable();
    files.dedup();
    let truncated = files.len() > MAX_WORKSPACE_FILES;
    files.truncate(MAX_WORKSPACE_FILES);
    Ok(WorkspaceFileList {
        root: root.to_string_lossy().to_string(),
        files,
        truncated,
    })
}

#[tauri::command]
pub(crate) fn read_workspace_file(
    app: AppHandle,
    state: State<'_, GatewayProcess>,
    repo_root: String,
    relative_path: String,
) -> Result<WorkspaceFileContent, String> {
    read_workspace_file_within(
        &desktop_workspace_fence(&app, state.inner())?,
        &repo_root,
        &relative_path,
    )
}

pub(crate) fn read_workspace_file_within(
    trusted: &[PathBuf],
    repo_root: &str,
    relative_path: &str,
) -> Result<WorkspaceFileContent, String> {
    let root = fence_workspace_root(trusted, repo_root)?;
    let (relative, resolved) = resolve_workspace_file(&root, relative_path)?;
    let size = resolved
        .metadata()
        .map_err(|error| format!("无法读取文件信息：{error}"))?
        .len();
    if size > MAX_PREVIEW_BYTES {
        return Err(format!(
            "文件超过源码预览上限（{} KiB）",
            MAX_PREVIEW_BYTES / 1024
        ));
    }

    let bytes = read(&resolved).map_err(|error| format!("读取文件失败：{error}"))?;
    if bytes.contains(&0) {
        return Err("二进制文件不能在源码预览中打开".into());
    }
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    let content = String::from_utf8(bytes).map_err(|_| "文件不是有效 UTF-8 文本".to_string())?;
    Ok(WorkspaceFileContent {
        path: relative.to_string_lossy().replace('\\', "/"),
        content,
        size,
        sha256,
    })
}

pub(crate) fn editor_candidates(path: &Path, line: u32) -> Vec<(String, Vec<OsString>, bool)> {
    let goto = OsString::from(format!("{}:{line}", path.to_string_lossy()));
    let direct = path.as_os_str().to_os_string();
    let mut candidates = vec![
        (
            "code".into(),
            vec![OsString::from("--goto"), goto.clone()],
            true,
        ),
        ("cursor".into(), vec![OsString::from("--goto"), goto], true),
    ];

    #[cfg(target_os = "macos")]
    candidates.push(("open".into(), vec![direct], false));
    #[cfg(target_os = "linux")]
    candidates.push(("xdg-open".into(), vec![direct], false));
    #[cfg(target_os = "windows")]
    candidates.push(("explorer".into(), vec![direct], false));

    candidates
}

#[tauri::command]
pub(crate) fn open_workspace_file(
    app: AppHandle,
    state: State<'_, GatewayProcess>,
    repo_root: String,
    relative_path: String,
    line: Option<u32>,
) -> Result<OpenWorkspaceFileResult, String> {
    open_workspace_file_within(
        &desktop_workspace_fence(&app, state.inner())?,
        &repo_root,
        &relative_path,
        line,
    )
}

pub(crate) fn open_workspace_file_within(
    trusted: &[PathBuf],
    repo_root: &str,
    relative_path: &str,
    line: Option<u32>,
) -> Result<OpenWorkspaceFileResult, String> {
    let root = fence_workspace_root(trusted, repo_root)?;
    let (_relative, resolved) = resolve_workspace_file(&root, relative_path)?;
    let line = line.unwrap_or(1);
    if line == 0 {
        return Err("源码行号必须从 1 开始".into());
    }

    let mut failures = Vec::new();
    for (launcher, args, line_aware) in editor_candidates(&resolved, line) {
        let mut command = Command::new(&launcher);
        command
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        match command.spawn() {
            Ok(mut child) => {
                std::thread::spawn(move || {
                    let _ = child.wait();
                });
                let message = if line_aware {
                    format!("已用 {launcher} 打开第 {line} 行")
                } else {
                    format!(
                        "未找到 VS Code/Cursor CLI，已用系统默认应用打开（请定位到第 {line} 行）"
                    )
                };
                return Ok(OpenWorkspaceFileResult {
                    launcher,
                    line_aware,
                    message,
                });
            }
            Err(error) if error.kind() == ErrorKind::NotFound => continue,
            Err(error) => failures.push(format!("{launcher}: {error}")),
        }
    }

    let detail = if failures.is_empty() {
        "系统中未找到可用启动器".into()
    } else {
        failures.join("；")
    };
    Err(format!("无法打开外部编辑器：{detail}"))
}
