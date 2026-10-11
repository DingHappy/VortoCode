import { desktopBuildInfo, type DesktopBuildInfo } from "../lib/buildInfo";

export function BuildVersion({ details = false, info = desktopBuildInfo }: { details?: boolean; info?: DesktopBuildInfo }) {
  const label = `VortoCode ${info.version} · ${info.buildId}`;
  if (!details) return <span className="build-version" title={label} aria-label={`当前版本：${label}`}>v{info.version} · {info.sourceFingerprint.slice(0, 8) || "开发"}</span>;
  return <div className="build-version-details" aria-label="当前应用版本">
    <strong>VortoCode {info.version}</strong>
    <p>构建标识：<code>{info.buildId}</code></p>
    <p>构建时间：{info.builtAt ? new Date(info.builtAt).toLocaleString() : "尚未生成"}</p>
    <small>显示当前窗口加载的版本；更新后需 ⌘Q 退出再打开。</small>
  </div>;
}
