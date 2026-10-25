//! Manual viewer transport. It never grants Agent permissions or stops remote services.
use serde::{Deserialize, Serialize};
use std::io::Read;
use std::net::{Ipv4Addr, TcpListener};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{
    atomic::{AtomicU64, Ordering},
    mpsc, Mutex,
};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

const EVENT: &str = "resident-desktop-changed";
const WINDOW_PREFIX: &str = "resident-desktop-";
static NEXT: AtomicU64 = AtomicU64::new(1);

#[derive(Default, Deserialize, Serialize)]
struct Config {
    target: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Computer {
    pub ready: bool,
    pub mode: String,
    pub viewers: u32,
    pub password_store: String,
    pub observer_ready: bool,
    pub observers: Option<u32>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Status {
    pub target: String,
    pub ssh_connected: bool,
    pub viewer_url: Option<String>,
    pub viewer: Option<Viewer>,
    pub computer: Option<Computer>,
    pub checked_at: Option<u64>,
    pub error: Option<String>,
    #[serde(skip)]
    id: Option<u64>,
}

#[derive(Clone, Debug, Serialize)]
pub(crate) struct Viewer {
    pub id: u64,
    pub surface: &'static str,
}

struct OwnedChild(Child);
impl Drop for OwnedChild {
    fn drop(&mut self) {
        if matches!(self.0.try_wait(), Ok(Some(_))) {
            return;
        }
        #[cfg(unix)]
        unsafe {
            libc::kill(-(self.0.id() as i32), libc::SIGKILL);
        }
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn command() -> Command {
    let mut command = Command::new("/usr/bin/ssh");
    command.env_clear();
    for key in ["HOME", "USER", "LOGNAME", "SSH_AUTH_SOCK", "LANG", "TMPDIR"] {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    command.env(
        "PATH",
        "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
    );
    command.stdin(Stdio::null()).stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    command
}

fn output(mut command: Command, timeout: Duration) -> Result<Vec<u8>, String> {
    command.stdout(Stdio::piped());
    let mut child = OwnedChild(
        command
            .spawn()
            .map_err(|_| "无法启动系统 SSH".to_string())?,
    );
    let stdout = child.0.stdout.take().ok_or("无法读取 SSH 响应")?;
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = stdout.take(65_537).read_to_end(&mut bytes).map(|_| bytes);
        let _ = sender.send(result);
    });
    let deadline = Instant::now() + timeout;
    let bytes = receiver
        .recv_timeout(timeout)
        .map_err(|_| "SSH 查询超时，已停止本次请求")?
        .map_err(|_| "无法读取 SSH 响应")?;
    if bytes.len() > 65_536 {
        return Err("SSH 响应过大".into());
    }
    loop {
        match child.0.try_wait().map_err(|_| "无法确认 SSH 结果")? {
            Some(status) if status.success() => return Ok(bytes),
            Some(_) => return Err("SSH 请求失败，请检查免密连接及远端桌面安装".into()),
            None if Instant::now() >= deadline => return Err("SSH 查询超时，已停止本次请求".into()),
            None => std::thread::sleep(Duration::from_millis(20)),
        }
    }
}

fn normalize(raw: &str) -> Result<String, String> {
    let raw = raw.trim();
    if raw.is_empty() || raw.len() > 200 {
        return Err("请填写 SSH 别名或 用户@主机".into());
    }
    let target = if raw.starts_with("ssh:") {
        raw.into()
    } else {
        format!("ssh:{raw}")
    };
    super::normalize_desktop_target(&target)
}

fn mux(socket: &Path) -> Command {
    let mut command = command();
    command.args(["-F", "/dev/null", "-S"]).arg(socket).args([
        "-o",
        "ControlMaster=no",
        "-o",
        "BatchMode=yes",
        "-o",
        "ProxyCommand=/usr/bin/false",
    ]);
    command
}

fn read_computer(socket: &Path, host: &str, timeout: Duration) -> Result<Computer, String> {
    let mut cmd = mux(socket);
    cmd.arg(host)
        .arg("python3 \"$HOME/.local/share/vortocode/computer/resident-desktop.py\" status");
    parse(&output(cmd, timeout)?)
}

#[derive(Deserialize)]
struct Password {
    backend: String,
    state: String,
}
#[derive(Deserialize)]
struct Observation {
    available: bool,
    viewers: u32,
}
#[derive(Deserialize)]
struct Wire {
    version: u32,
    ready: bool,
    mode: String,
    viewers: u32,
    heartbeat_age_seconds: f64,
    password_store: Option<Password>,
    observation: Option<Observation>,
}

fn parse(bytes: &[u8]) -> Result<Computer, String> {
    if bytes.len() > 16384 {
        return Err("桌面状态响应过大".into());
    }
    let wire: Wire =
        serde_json::from_slice(bytes).map_err(|_| "远端桌面状态不兼容，请更新桌面组件")?;
    if wire.version != 1
        || !wire.heartbeat_age_seconds.is_finite()
        || wire.heartbeat_age_seconds < 0.0
        || wire.viewers > 32
        || wire
            .observation
            .as_ref()
            .is_some_and(|value| value.viewers > 32)
    {
        return Err("远端桌面状态无效".into());
    }
    let mode = if matches!(wire.mode.as_str(), "human" | "agent") {
        wire.mode
    } else {
        "unknown".into()
    };
    let password_store = wire
        .password_store
        .filter(|value| value.backend == "gnome-libsecret")
        .map(|value| value.state)
        .filter(|value| {
            matches!(
                value.as_str(),
                "unlocked" | "locked" | "needs_setup" | "unavailable"
            )
        })
        .unwrap_or_else(|| "unknown".into());
    let ready = wire.ready && wire.heartbeat_age_seconds <= 8.0;
    Ok(Computer {
        ready,
        mode,
        viewers: wire.viewers,
        password_store,
        observer_ready: ready
            && wire
                .observation
                .as_ref()
                .is_some_and(|value| value.available),
        observers: wire.observation.map(|value| value.viewers),
    })
}

struct Session {
    id: u64,
    host: String,
    directory: PathBuf,
    socket: PathBuf,
    process: OwnedChild,
    port: Option<u16>,
    surface: Option<&'static str>,
}
impl Drop for Session {
    fn drop(&mut self) {
        // Drop/kill the owned master before removing its private control socket.
        if matches!(self.process.0.try_wait(), Ok(None)) {
            #[cfg(unix)]
            unsafe {
                libc::kill(-(self.process.0.id() as i32), libc::SIGKILL);
            }
            let _ = self.process.0.kill();
            let _ = self.process.0.wait();
        }
        let _ = std::fs::remove_file(&self.socket);
        let _ = std::fs::remove_dir(&self.directory);
    }
}

impl Session {
    fn mux(&self) -> Command {
        // Mux requests use ONLY our private socket, never user-configured
        // forwards. If that master is gone, fail instead of making a new login.
        mux(&self.socket)
    }

    fn alive(&mut self) -> bool {
        matches!(self.process.0.try_wait(), Ok(None))
    }

    #[cfg(unix)]
    fn start(target: &str) -> Result<Self, String> {
        use std::os::unix::fs::DirBuilderExt;
        let host = target
            .strip_prefix("ssh:")
            .ok_or("需要 SSH 目标")?
            .to_string();
        let id = NEXT.fetch_add(1, Ordering::Relaxed);
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        // Short enough for macOS's Unix socket path limit; mkdir is exclusive.
        let directory = PathBuf::from(format!(
            "/tmp/vc-desk-{:x}-{nonce:x}-{id:x}",
            std::process::id()
        ));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&directory)
            .map_err(|_| "无法创建私有 SSH 连接目录")?;
        let socket = directory.join("m");
        let mut cmd = command();
        cmd.args(["-M", "-N", "-T", "-S"])
            .arg(&socket)
            .args([
                "-o",
                "ControlMaster=yes",
                "-o",
                "ControlPersist=no",
                "-o",
                "ClearAllForwardings=yes",
                "-o",
                "ForwardAgent=no",
                "-o",
                "ForwardX11=no",
                "-o",
                "PermitLocalCommand=no",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                "-o",
                "ServerAliveInterval=10",
                "-o",
                "ServerAliveCountMax=2",
                "-o",
                "ExitOnForwardFailure=yes",
            ])
            .arg(&host)
            .stdout(Stdio::null());
        let process = match cmd.spawn() {
            Ok(value) => OwnedChild(value),
            Err(_) => {
                let _ = std::fs::remove_dir(&directory);
                return Err("无法启动系统 SSH".into());
            }
        };
        let mut session = Self {
            id,
            host,
            directory,
            socket,
            process,
            port: None,
            surface: None,
        };
        let deadline = Instant::now() + Duration::from_secs(12);
        loop {
            if !session.alive() {
                return Err("SSH 连接失败，请检查别名、密钥或已信任的主机记录".into());
            }
            if session.socket.exists() {
                let mut check = session.mux();
                check.args(["-O", "check"]).arg(&session.host);
                if output(check, Duration::from_secs(2)).is_ok() {
                    return Ok(session);
                }
            }
            if Instant::now() >= deadline {
                return Err("SSH 连接超时".into());
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }

    #[cfg(not(unix))]
    fn start(_target: &str) -> Result<Self, String> {
        Err("此版本的桌面连接管理支持 macOS / Linux".into())
    }

    fn computer(&self) -> Result<Computer, String> {
        read_computer(&self.socket, &self.host, Duration::from_secs(12))
    }

    fn viewer(&mut self) -> Result<String, String> {
        if let Some(port) = self.port {
            return Ok(format!("http://127.0.0.1:{port}/"));
        }
        for _ in 0..3 {
            let listener =
                TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).map_err(|_| "无法分配本地查看端口")?;
            let port = listener
                .local_addr()
                .map_err(|_| "无法读取查看端口")?
                .port();
            drop(listener);
            let mut forward = self.mux();
            forward
                .args(["-O", "forward", "-L"])
                .arg(format!("127.0.0.1:{port}:127.0.0.1:6088"))
                .arg(&self.host);
            // OpenSSH's mux acknowledgement proves its bind succeeded. A
            // competing local listener causes failure, never a wrong URL.
            if output(forward, Duration::from_secs(3)).is_ok() {
                self.port = Some(port);
                return Ok(format!("http://127.0.0.1:{port}/"));
            }
            if !self.alive() {
                break;
            }
        }
        Err("无法建立本地远程画面转发".into())
    }
}

#[derive(Default)]
struct Inner {
    loaded: bool,
    target: String,
    session: Option<Session>,
    computer: Option<Computer>,
    checked_at: Option<u64>,
    error: Option<String>,
    revision: u64,
    pending_probe: Option<u64>,
}

struct Probe {
    token: u64,
    revision: u64,
    session_id: u64,
    socket: PathBuf,
    host: String,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

impl Inner {
    fn load(&mut self, path: &Path) {
        if self.loaded {
            return;
        }
        self.loaded = true;
        let read = || -> Option<String> {
            let info = std::fs::symlink_metadata(path).ok()?;
            if !info.is_file() || info.len() > 16384 {
                return None;
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                if info.uid() != unsafe { libc::geteuid() } || info.mode() & 0o077 != 0 {
                    return None;
                }
            }
            let config: Config = serde_json::from_slice(&std::fs::read(path).ok()?).ok()?;
            normalize(&config.target).ok()
        };
        self.target = read().unwrap_or_default();
    }

    fn snapshot(&mut self) -> Status {
        if self
            .session
            .as_mut()
            .is_some_and(|session| !session.alive())
        {
            self.session = None;
            self.error = Some("SSH 连接已断开，请手动重新连接".into());
        }
        Status {
            target: self.target.clone(),
            viewer: self
                .session
                .as_ref()
                .and_then(|s| s.surface.map(|surface| Viewer { id: s.id, surface })),
            ssh_connected: self.session.is_some(),
            viewer_url: self
                .session
                .as_ref()
                .and_then(|s| s.port.map(|port| format!("http://127.0.0.1:{port}/"))),
            computer: self.computer.clone(),
            checked_at: self.checked_at,
            error: self.error.clone(),
            id: self.session.as_ref().map(|s| s.id),
        }
    }

    fn check(&mut self, open: bool) -> Result<Status, String> {
        self.revision = self.revision.wrapping_add(1);
        self.snapshot();
        let result: Result<Status, String> = (|| {
            let target = normalize(&self.target)?;
            if self.session.is_none() {
                self.session = Some(Session::start(&target)?);
            }
            let session = self.session.as_mut().unwrap();
            let computer = session.computer()?;
            if open && (!computer.ready || !computer.observer_ready) {
                return Err("远端观察/接管入口未就绪，请先检查远端桌面服务".into());
            }
            self.computer = Some(computer);
            self.checked_at = Some(now_ms());
            if open {
                session.viewer()?;
            }
            self.error = None;
            Ok(self.snapshot())
        })();
        if let Err(error) = &result {
            self.error = Some(error.clone());
        }
        result
    }

    fn begin_probe(&mut self) -> Option<Probe> {
        self.snapshot();
        if self.pending_probe.is_some() {
            return None;
        }
        let session = self.session.as_ref()?;
        let token = NEXT.fetch_add(1, Ordering::Relaxed);
        self.pending_probe = Some(token);
        Some(Probe {
            token,
            revision: self.revision,
            session_id: session.id,
            socket: session.socket.clone(),
            host: session.host.clone(),
        })
    }

    fn finish_probe(&mut self, probe: &Probe, result: Result<Computer, String>) -> Status {
        self.snapshot();
        if self.pending_probe == Some(probe.token) {
            self.pending_probe = None;
            // A close, target save, or explicit check wins over an older read.
            if self.revision == probe.revision
                && self
                    .session
                    .as_ref()
                    .is_some_and(|s| s.id == probe.session_id)
            {
                match result {
                    Ok(computer) => {
                        self.computer = Some(computer);
                        self.checked_at = Some(now_ms());
                        self.error = None;
                    }
                    Err(error) => {
                        self.error = Some(error);
                    }
                }
            }
        }
        self.snapshot()
    }
}

#[derive(Default)]
pub(crate) struct ResidentDesktopManager(Mutex<Inner>);

impl ResidentDesktopManager {
    fn surface(&self, id: u64, surface: &'static str) -> Result<Status, String> {
        let mut state = self.0.lock().map_err(|_| "桌面连接状态不可用")?;
        let session = state
            .session
            .as_mut()
            .filter(|s| s.id == id)
            .ok_or("连接已结束，请重新打开")?;
        session.surface = Some(surface);
        Ok(state.snapshot())
    }
    fn release_workspace(&self, id: u64) {
        if let Ok(mut state) = self.0.lock() {
            if state
                .session
                .as_ref()
                .is_some_and(|s| s.id == id && s.surface == Some("workspace"))
            {
                state.session = None;
                state.revision = state.revision.wrapping_add(1);
                state.error = None;
            }
        }
    }
    fn get(&self, path: &Path) -> Result<Status, String> {
        let mut state = self.0.lock().map_err(|_| "桌面连接状态不可用")?;
        state.load(path);
        Ok(state.snapshot())
    }
    fn save(&self, path: &Path, target: &str) -> Result<Status, String> {
        let target = normalize(target)?;
        let mut state = self.0.lock().map_err(|_| "桌面连接状态不可用")?;
        state.load(path);
        super::save_json_atomic_with_mode(
            path,
            "resident-desktop",
            &Config {
                target: target.clone(),
            },
            Some(0o600),
        )?;
        if state.target != target {
            state.session = None;
            state.computer = None;
            state.checked_at = None;
        }
        state.target = target;
        state.revision = state.revision.wrapping_add(1);
        state.error = None;
        Ok(state.snapshot())
    }
    fn check(&self, path: &Path, open: bool) -> Result<Status, String> {
        let mut state = self.0.lock().map_err(|_| "桌面连接状态不可用")?;
        state.load(path);
        state.check(open)
    }
    pub(crate) fn close(&self, id: Option<u64>) {
        if let Ok(mut state) = self.0.lock() {
            if id.is_none() || state.session.as_ref().is_some_and(|s| Some(s.id) == id) {
                state.session = None;
                state.revision = state.revision.wrapping_add(1);
                state.error = None;
            }
        }
    }

    fn refresh(&self, path: &Path) -> Result<Status, String> {
        self.refresh_with(path, |probe| {
            read_computer(&probe.socket, &probe.host, Duration::from_secs(6))
        })
    }

    fn refresh_with(
        &self,
        path: &Path,
        read: impl FnOnce(&Probe) -> Result<Computer, String>,
    ) -> Result<Status, String> {
        let probe = {
            let mut state = self.0.lock().map_err(|_| "桌面连接状态不可用")?;
            state.load(path);
            match state.begin_probe() {
                Some(probe) => probe,
                None => return Ok(state.snapshot()),
            }
        };
        // Do not hold the state lock while waiting on SSH. Disconnect and save
        // can proceed immediately; the result is bound to this session/revision.
        let result = read(&probe);
        let mut state = self.0.lock().map_err(|_| "桌面连接状态不可用")?;
        Ok(state.finish_probe(&probe, result))
    }
}

fn config_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|path| path.join("resident-desktop.json"))
        .map_err(|_| "无法定位桌面连接配置".into())
}

fn close_windows(app: &AppHandle, except: Option<&str>) {
    for (label, window) in app.webview_windows() {
        if label.starts_with(WINDOW_PREFIX) && Some(label.as_str()) != except {
            let _ = window.close();
        }
    }
}

#[tauri::command(async)]
pub(crate) fn get_resident_desktop(app: AppHandle) -> Result<Status, String> {
    app.state::<ResidentDesktopManager>()
        .get(&config_path(&app)?)
}

#[tauri::command]
pub(crate) async fn refresh_resident_desktop(app: AppHandle) -> Result<Status, String> {
    let path = config_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        app.state::<ResidentDesktopManager>().refresh(&path)
    })
    .await
    .map_err(|_| "桌面状态核对未完成".to_string())?
}

#[tauri::command(async)]
pub(crate) fn save_resident_desktop_target(
    app: AppHandle,
    target: String,
) -> Result<Status, String> {
    let result = app
        .state::<ResidentDesktopManager>()
        .save(&config_path(&app)?, &target)?;
    if !result.ssh_connected {
        close_windows(&app, None);
    }
    let _ = app.emit(EVENT, ());
    Ok(result)
}

async fn check(app: &AppHandle, open: bool) -> Result<Status, String> {
    let path = config_path(app)?;
    let work = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        work.state::<ResidentDesktopManager>().check(&path, open)
    })
    .await
    .map_err(|_| "桌面连接检查未完成".to_string())?;
    let _ = app.emit(EVENT, ());
    result
}

#[tauri::command]
pub(crate) async fn check_resident_desktop(app: AppHandle) -> Result<Status, String> {
    check(&app, false).await
}

fn allowed_navigation(url: &url::Url, port: u16) -> bool {
    url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port() == Some(port)
        && url.username().is_empty()
        && url.password().is_none()
}

fn present_viewer(window: &tauri::WebviewWindow) -> Result<(), String> {
    window.show().map_err(|_| "无法显示远程桌面窗口")?;
    if window.is_minimized().unwrap_or(false) {
        window.unminimize().map_err(|_| "无法还原远程桌面窗口")?;
    }
    window
        .set_focus()
        .map_err(|_| "无法切换到远程桌面窗口".into())
}

#[tauri::command]
pub(crate) async fn open_resident_desktop(app: AppHandle) -> Result<Status, String> {
    let current = app
        .state::<ResidentDesktopManager>()
        .get(&config_path(&app)?)?;
    if let Some(id) = current.id.filter(|_| current.viewer_url.is_some()) {
        if let Some(window) = app.get_webview_window(&format!("{WINDOW_PREFIX}{id}")) {
            // Showing an existing viewer must work even if the remote status
            // helper is temporarily unavailable. Do not reload or change mode.
            present_viewer(&window)?;
            return Ok(current);
        }
        // A destroyed window's cleanup event may still be queued. Give the
        // replacement a new connection id so that old callback cannot kill it.
        app.state::<ResidentDesktopManager>().close(Some(id));
    }
    let status = check(&app, true).await?;
    let id = status.id.ok_or("桌面连接已结束")?;
    let status = app
        .state::<ResidentDesktopManager>()
        .surface(id, "window")?;
    let _ = app.emit(EVENT, ());
    let label = format!("{WINDOW_PREFIX}{id}");
    if let Some(window) = app.get_webview_window(&label) {
        present_viewer(&window)?;
        return Ok(status);
    }
    close_windows(&app, Some(&label));
    let url = url::Url::parse(status.viewer_url.as_deref().ok_or("缺少画面连接地址")?)
        .map_err(|_| "画面地址无效")?;
    let port = url.port().ok_or("画面端口无效")?;
    let window = WebviewWindowBuilder::new(&app, &label, WebviewUrl::External(url))
        .title("远程桌面 · VortoCode")
        .inner_size(1200.0, 850.0)
        .min_inner_size(640.0, 480.0)
        .on_navigation(move |url| allowed_navigation(url, port))
        .build();
    let window = match window {
        Ok(window) => window,
        Err(_) => {
            if let Some(window) = app.get_webview_window(&label) {
                present_viewer(&window)?;
                return Ok(status);
            }
            app.state::<ResidentDesktopManager>().close(Some(id));
            let _ = app.emit(EVENT, ());
            return Err("无法打开远程桌面窗口".into());
        }
    };
    let handle = app.clone();
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::Destroyed) {
            let handle = handle.clone();
            tauri::async_runtime::spawn_blocking(move || {
                handle.state::<ResidentDesktopManager>().close(Some(id));
                let _ = handle.emit(EVENT, ());
            });
        }
    });
    Ok(status)
}

#[tauri::command]
pub(crate) async fn open_resident_desktop_workspace(app: AppHandle) -> Result<Status, String> {
    let current = app
        .state::<ResidentDesktopManager>()
        .get(&config_path(&app)?)?;
    if let Some(id) = current.id.filter(|_| current.viewer_url.is_some()) {
        app.state::<ResidentDesktopManager>().close(Some(id));
        close_windows(&app, None);
    }
    let status = check(&app, true).await?;
    let status = app
        .state::<ResidentDesktopManager>()
        .surface(status.id.ok_or("连接已结束")?, "workspace")?;
    let _ = app.emit(EVENT, ());
    Ok(status)
}

#[tauri::command(async)]
pub(crate) fn release_resident_desktop_workspace(app: AppHandle, connection_id: u64) {
    app.state::<ResidentDesktopManager>()
        .release_workspace(connection_id);
    let _ = app.emit(EVENT, ());
}

#[tauri::command(async)]
pub(crate) fn close_resident_desktop(app: AppHandle) -> Result<Status, String> {
    let manager = app.state::<ResidentDesktopManager>();
    manager.close(None);
    close_windows(&app, None);
    let _ = app.emit(EVENT, ());
    manager.get(&config_path(&app)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    fn fixture_session() -> Session {
        use std::os::unix::process::CommandExt;
        let id = NEXT.fetch_add(1, Ordering::Relaxed);
        let directory =
            std::env::temp_dir().join(format!("vorto-probe-{}-{id}", std::process::id()));
        std::fs::create_dir(&directory).unwrap();
        let process = Command::new("/bin/sleep")
            .arg("60")
            .process_group(0)
            .spawn()
            .unwrap();
        Session {
            id,
            host: "fixture".into(),
            socket: directory.join("m"),
            directory,
            process: OwnedChild(process),
            port: None,
            surface: None,
        }
    }

    fn computer() -> Computer {
        Computer {
            ready: true,
            mode: "human".into(),
            viewers: 1,
            password_store: "unlocked".into(),
            observer_ready: true,
            observers: Some(0),
        }
    }

    #[test]
    fn background_refresh_does_not_connect_an_unopened_target() {
        let mut state = Inner {
            target: "ssh:vm".into(),
            ..Default::default()
        };
        assert!(state.begin_probe().is_none());
        assert!(!state.snapshot().ssh_connected);
    }

    #[test]
    #[cfg(unix)]
    fn workspace_cleanup_cannot_close_a_window_or_newer_connection() {
        let session = fixture_session();
        let old = session.id;
        let manager = ResidentDesktopManager(Mutex::new(Inner {
            loaded: true,
            session: Some(session),
            ..Default::default()
        }));
        assert_eq!(
            manager
                .surface(old, "workspace")
                .unwrap()
                .viewer
                .unwrap()
                .surface,
            "workspace"
        );
        manager.surface(old, "window").unwrap();
        manager.release_workspace(old);
        assert!(manager.get(Path::new("unused")).unwrap().ssh_connected);
        let next = fixture_session();
        let next_id = next.id;
        manager.0.lock().unwrap().session = Some(next);
        manager.surface(next_id, "workspace").unwrap();
        manager.release_workspace(old);
        manager.close(Some(old));
        assert_eq!(
            manager.get(Path::new("unused")).unwrap().viewer.unwrap().id,
            next_id
        );
        manager.release_workspace(next_id);
        assert!(!manager.get(Path::new("unused")).unwrap().ssh_connected);
    }

    #[test]
    #[cfg(unix)]
    fn late_probe_cannot_revive_closed_or_replaced_session_or_overwrite_explicit_check() {
        let mut state = Inner {
            session: Some(fixture_session()),
            ..Default::default()
        };
        let probe = state.begin_probe().unwrap();
        assert!(state.begin_probe().is_none()); // one passive SSH read at a time
        state.session = None;
        let closed = state.finish_probe(&probe, Ok(computer()));
        assert!(!closed.ssh_connected && closed.computer.is_none());
        state.session = Some(fixture_session());
        let probe = state.begin_probe().unwrap();
        state.session = Some(fixture_session());
        assert!(state
            .finish_probe(&probe, Err("old host".into()))
            .error
            .is_none());
        let probe = state.begin_probe().unwrap();
        state.revision += 1;
        state.error = Some("new explicit check".into());
        assert_eq!(
            state.finish_probe(&probe, Ok(computer())).error.as_deref(),
            Some("new explicit check")
        );
    }

    #[test]
    #[cfg(unix)]
    fn passive_failure_retains_transport_and_can_recover_without_reconnecting() {
        let mut state = Inner {
            session: Some(fixture_session()),
            ..Default::default()
        };
        let probe = state.begin_probe().unwrap();
        let failed = state.finish_probe(&probe, Err("state query failed".into()));
        assert!(failed.ssh_connected);
        assert!(failed.error.is_some());
        let probe = state.begin_probe().unwrap();
        let recovered = state.finish_probe(&probe, Ok(computer()));
        assert!(
            recovered.ssh_connected && recovered.error.is_none() && recovered.checked_at.is_some()
        );
        let id = recovered.id;
        state.session.as_mut().unwrap().process.0.kill().unwrap();
        state.session.as_mut().unwrap().process.0.wait().unwrap();
        let gone = state.snapshot();
        assert!(!gone.ssh_connected && gone.error.is_some());
        assert!(state.begin_probe().is_none());
        assert!(id.is_some());
    }

    #[test]
    #[cfg(unix)]
    fn disconnect_can_finish_while_background_reader_is_still_waiting() {
        let manager = ResidentDesktopManager(Mutex::new(Inner {
            loaded: true,
            session: Some(fixture_session()),
            ..Default::default()
        }));
        let (started, wait_started) = mpsc::channel();
        let (finish, wait_finish) = mpsc::channel();
        std::thread::scope(|scope| {
            let reader = &manager;
            let read = scope.spawn(move || {
                reader.refresh_with(Path::new("unused"), |_| {
                    started.send(()).unwrap();
                    wait_finish.recv_timeout(Duration::from_secs(3)).unwrap();
                    Ok(computer())
                })
            });
            wait_started.recv_timeout(Duration::from_secs(3)).unwrap();
            manager.close(None);
            finish.send(()).unwrap();
            let status = read.join().unwrap().unwrap();
            assert!(!status.ssh_connected && status.computer.is_none());
        });
    }
    #[test]
    fn target_is_data_not_shell_and_loopback_navigation_is_pinned() {
        assert_eq!(normalize(" vorto-code ").unwrap(), "ssh:vorto-code");
        for bad in [
            "",
            "-oProxyCommand=x",
            "user@-host",
            "vm;reboot",
            "ssh:vm\nreboot",
            "http://host",
        ] {
            assert!(normalize(bad).is_err());
        }
        assert!(allowed_navigation(
            &url::Url::parse("http://127.0.0.1:32123/").unwrap(),
            32123
        ));
        for value in [
            "http://127.0.0.1:32124/",
            "https://127.0.0.1:32123/",
            "http://evil.test:32123/",
            "http://user@127.0.0.1:32123/",
        ] {
            assert!(!allowed_navigation(&url::Url::parse(value).unwrap(), 32123));
        }
    }
    #[test]
    fn metadata_never_exposes_secrets_or_grants_future_capabilities() {
        let raw=br#"{"version":1,"ready":true,"mode":"future","viewers":1,"heartbeat_age_seconds":0.2,"password_store":{"backend":"gnome-libsecret","state":"unlocked","secret":"hidden"},"observation":{"available":true,"viewers":2},"lease":"hidden"}"#;
        let value = parse(raw).unwrap();
        assert!(value.ready);
        assert_eq!(value.mode, "unknown");
        assert!(!serde_json::to_string(&value).unwrap().contains("hidden"));
        assert!(parse(br#"{"version":2}"#).is_err());
        let stale = String::from_utf8(raw.to_vec())
            .unwrap()
            .replace("0.2", "90.0");
        let stale = parse(stale.as_bytes()).unwrap();
        assert!(!stale.ready && !stale.observer_ready);
    }
    #[test]
    fn manual_target_is_private_and_separate_from_agent_permissions() {
        let directory = std::env::temp_dir().join(format!(
            "vorto-desk-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let path = directory.join("resident-desktop.json");
        let manager = ResidentDesktopManager::default();
        let saved = manager.save(&path, "vorto-code").unwrap();
        assert_eq!(saved.target, "ssh:vorto-code");
        assert!(!saved.ssh_connected);
        assert_eq!(
            ResidentDesktopManager::default().get(&path).unwrap().target,
            saved.target
        );
        assert!(!directory.join("desktop-preferences.json").exists());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        std::fs::remove_dir_all(directory).unwrap();
        let capability: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/default.json")).unwrap();
        assert_eq!(capability["windows"], serde_json::json!(["main"]));
        assert!(capability.get("remote").is_none());
    }
    #[test]
    #[ignore = "explicit real SSH target; no model, frames or input"]
    fn live_owned_ssh_forward_status_and_cleanup() {
        let target = normalize(
            &std::env::var("VORTOCODE_DESKTOP_ACCEPTANCE_TARGET").expect("explicit target"),
        )
        .unwrap();
        let mut session = Session::start(&target).unwrap();
        let computer = session.computer().unwrap();
        assert!(computer.ready && computer.observer_ready);
        let url = session.viewer().unwrap();
        assert_eq!(session.viewer().unwrap(), url);
        let port = session.port.unwrap();
        use std::io::Write;
        let mut connection = std::net::TcpStream::connect((Ipv4Addr::LOCALHOST, port)).unwrap();
        connection
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        connection
            .write_all(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            .unwrap();
        let mut page = String::new();
        connection.take(32768).read_to_string(&mut page).unwrap();
        assert!(page.contains("200 OK") && page.contains("viewer.js"));
        let connection_id = session.id;
        let manager = ResidentDesktopManager(Mutex::new(Inner {
            loaded: true,
            target,
            session: Some(session),
            ..Default::default()
        }));
        let refreshed = manager.refresh(Path::new("unused")).unwrap();
        assert!(refreshed.ssh_connected && refreshed.computer.unwrap().ready);
        manager.surface(connection_id, "window").unwrap();
        manager.release_workspace(connection_id);
        assert!(manager.get(Path::new("unused")).unwrap().ssh_connected);
        manager.surface(connection_id, "workspace").unwrap();
        manager.release_workspace(connection_id);
        let disconnected = manager.refresh(Path::new("unused")).unwrap();
        assert!(!disconnected.ssh_connected); // passive refresh never reconnects
        assert!(std::net::TcpStream::connect((Ipv4Addr::LOCALHOST, port)).is_err());
        println!("real SSH status, passive refresh, forwarding reuse, surface-scoped cleanup and disconnect without reconnect passed");
    }
}
