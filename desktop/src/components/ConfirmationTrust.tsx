import { useState } from "react";
import type { TrustLevel, TrustStatus } from "../types";
import { TRUST_LABELS, TRUST_ORDER } from "../lib/trustCard";

/** Saving a workspace preference never answers the pending operation. */
export function ConfirmationTrust({ trust, busy, connected, tainted, onChange }: {
  trust: TrustStatus | null; busy: boolean; connected: boolean; tainted: boolean;
  onChange: (level: TrustLevel) => void | Promise<void>;
}) {
  const [selected, setSelected] = useState<TrustLevel>("reads");
  if (!trust) return null;
  const higher = trust.levels.filter(level => level in TRUST_ORDER
    && TRUST_ORDER[level] > TRUST_ORDER[trust.effective]
    && TRUST_ORDER[level] <= TRUST_ORDER[trust.ceiling]);
  const target = higher.includes(selected) ? selected : higher[0];
  return <aside className="confirmation-trust" aria-label="本工作区授权">
    <span>本工作区：{TRUST_LABELS[trust.effective]?.title ?? "未知授权"}</span>
    {target && <div className="confirmation-trust-actions">
      <select aria-label="提高授权级别" value={target} disabled={busy || !connected}
        onChange={event => setSelected(event.target.value as TrustLevel)}>
        {higher.map(level => <option key={level} value={level}>{TRUST_LABELS[level].title}</option>)}
      </select>
      <button disabled={busy || !connected} onClick={() => void onChange(target)}>
        {busy ? "正在保存…" : "保存授权级别"}
      </button>
    </div>}
    <small>{target ? "保存用于本工作区后续操作；本次仍需点击允许。" : "本次仍需单独确认。"}
      {trust.supervised ? " 写入、执行和外发始终逐次审批。" : " 硬拦截不受授权级别影响。"}
      {tainted ? " 本轮已读不可信内容，提升档位也不会免确认。" : ""}
    </small>
  </aside>;
}
