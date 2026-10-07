// 设置 → 使用情况：模型服务额度（原生层用已保存的 Key 查 OpenAI 兼容 billing 接口，Key 不进 webview）。
import { invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useCallback, useEffect, useState } from "react";

import { errorText } from "../../lib/errorText";
import type { DesktopAccount, LlmUsageSummary } from "../../types";

const PLAN_URL = "https://token.vortotech.com/panel/plan";

function planDate(seconds?: number | null): string {
  if (!seconds) return "—";
  const date = new Date(seconds * 1000);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** Token Plan 的额度按周期发放，通用 billing 接口对它只会回「不限额度 / 已用 0」——那是误导，
 *  所以这里不查它，只显示套餐本身，并把用量明细交给中转站（登录态才能读到周期额度）。 */
function TokenPlanUsage({ account }: { account: DesktopAccount }) {
  const [error, setError] = useState("");
  return (
    <div className="settings-stack">
      <section className="settings-card" aria-label="Token Plan">
        <div className="settings-card-head">
          <strong>Token Plan（Desktop 套餐）</strong>
          <button onClick={() => void openUrl(PLAN_URL).catch((failure) => setError(errorText(failure, "无法打开浏览器")))}>查看用量</button>
        </div>
        <div className="usage-metrics">
          <div><span>套餐</span><strong>{account.planName || "Token Plan"}</strong></div>
          <div><span>到期</span><strong>{planDate(account.planExpiry)}</strong></div>
          <div><span>账号</span><strong>{account.displayName || account.username}</strong></div>
        </div>
        {error && <p className="settings-card-note danger">{error}</p>}
        <p className="settings-card-note">套餐额度按周期重置，剩余额度和明细在中转站「套餐」页查看。套餐信息在登录时读取，续费或换档后重新登录即可刷新。</p>
      </section>
    </div>
  );
}

function usd(value: number): string {
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function UsagePane({ account }: { account?: DesktopAccount }) {
  if (account?.keySource === "token_plan") return <TokenPlanUsage account={account} />;
  return <BillingUsage />;
}

function BillingUsage() {
  const [usage, setUsage] = useState<LlmUsageSummary | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setUsage(await invoke<LlmUsageSummary>("get_llm_usage"));
    } catch (failure) {
      setError(errorText(failure, "读取额度失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const remaining = usage?.hardLimitUsd != null && usage.usedUsd != null
    ? Math.max(0, usage.hardLimitUsd - usage.usedUsd)
    : null;

  return (
    <div className="settings-stack">
      <section className="settings-card" aria-label="模型服务额度">
        <div className="settings-card-head">
          <strong>模型服务额度</strong>
          <button onClick={() => void refresh()} disabled={loading}>{loading ? "读取中…" : "刷新"}</button>
        </div>
        {error && <p className="settings-card-note danger">{error}</p>}
        {usage && !usage.available && <p className="settings-card-note">{usage.message}</p>}
        {usage?.available && (
          <div className="usage-metrics">
            <div>
              <span>额度</span>
              <strong>{usage.unlimited ? "不限额度" : usage.hardLimitUsd != null ? usd(usage.hardLimitUsd) : "—"}</strong>
            </div>
            <div>
              <span>近 {usage.periodDays} 天已用</span>
              <strong>{usage.usedUsd != null ? usd(usage.usedUsd) : "—"}</strong>
            </div>
            <div>
              <span>剩余</span>
              <strong>{usage.unlimited ? "不限" : remaining != null ? usd(remaining) : "—"}</strong>
            </div>
          </div>
        )}
        <p className="settings-card-note">数据来自当前模型服务的额度接口，按该服务的计费口径显示。</p>
      </section>
    </div>
  );
}
