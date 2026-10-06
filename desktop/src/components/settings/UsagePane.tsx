// 设置 → 使用情况：模型服务额度（原生层用已保存的 Key 查 OpenAI 兼容 billing 接口，Key 不进 webview）。
import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useState } from "react";

import { errorText } from "../../lib/errorText";
import type { LlmUsageSummary } from "../../types";

function usd(value: number): string {
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function UsagePane() {
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
