import type { ReactNode } from "react";
import type { SettingsTargetLease } from "../../lib/settingsTarget";
import "./SettingsTargetSection.css";

/** Keep original forms mounted, but hide and disable a source that is no longer current. */
export function SettingsTargetSection({ owner, children, lease, unavailable, label }: {
  owner: "desktop" | "workspace" | "space" | "project"; children: ReactNode;
  lease?: SettingsTargetLease; unavailable?: boolean; label?: string;
}) {
  const local = owner === "desktop";
  const title = local ? "这台 Mac" : owner === "space" ? "个人空间" : owner === "project" ? "项目" : "当前工作区";
  const connected = Boolean(lease?.current()) && !unavailable;
  const stale = Boolean(lease?.target.ready) && !lease?.current();
  return <section className="settings-target-section" aria-label={label ?? `${title}的设置`}>
    <div className="settings-target-label"><strong>{title}</strong><span>{local ? "生效范围见各项设置"
      : owner === "project" ? lease?.target.projectRoot || "项目尚未核对"
        : lease?.target.runtimeLabel || "目标尚未核对"}</span></div>
    {!local && !connected && <p className="settings-target-warning" role="status">{stale
      ? "运行端、会话或连接已变化。原目标设置已锁定；关闭后重新打开设置再核对。本机设置仍可使用。"
      : unavailable ? "当前目标不提供这项设置。请切到对应的个人空间或项目，再打开设置。"
        : "当前目标尚未连接。连接后重新打开设置再核对。"}</p>}
    <fieldset className="settings-target-fields" disabled={!local && !connected} hidden={!local && !connected}>
      {local || !unavailable && lease?.target.ready && lease.client ? children : null}
    </fieldset>
  </section>;
}
