import type { SettingsTargetLease } from "../../lib/settingsTarget";
import { settingsTargetSummary } from "../../lib/settingsTarget";
import { PreferenceDetails } from "../ui";

/** Presentation only. The lease and its original client remain the authority for every write. */
export function SettingsContext({ lease, scope }: { lease?: SettingsTargetLease; scope: string }) {
  return <section className="settings-context" aria-label="设置的保存位置">
    <div className="settings-context-name"><span>当前空间</span><strong>{lease?.target.runtimeLabel || "尚未连接"}</strong></div>
    <PreferenceDetails label="保存位置与连接详情">
      <p>{scope}</p><p>{lease ? settingsTargetSummary(lease.target) : "目标尚未核对"}</p>
    </PreferenceDetails>
    {lease && !lease.current() && <p className="settings-target-warning" role="status">当前目标已失效或尚未连接；运行端设置已停用。关闭后重开以核对新目标。</p>}
  </section>;
}
