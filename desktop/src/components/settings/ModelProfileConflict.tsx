import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";
import { confirmAction } from "../../lib/confirm";
import { errorText } from "../../lib/errorText";
import type { DesktopLlmProfileStatus } from "../../types";

/** Read-only recovery. Never rebase a draft onto a different credential/endpoint silently. */
export function ModelProfileConflict({ onUseLatest, onBusyChange }: {
  onUseLatest: (profile: DesktopLlmProfileStatus) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [latest, setLatest] = useState<DesktopLlmProfileStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const alive = useRef(true);
  const pending = useRef(false);
  const region = useRef<HTMLElement>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    region.current?.focus({ preventScroll: true });
    region.current?.scrollIntoView({ block: "nearest" });
  }, []);

  const run = async (adopt: boolean) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setNote(""); onBusyChange?.(true);
    try {
      if (adopt && !await confirmAction("放弃当前模型服务草稿，并载入最新已保存配置？此操作不会保存配置或重启引擎。")) return;
      if (!alive.current) return;
      // Read again after confirmation: the preview may have changed while the sheet was open.
      const profile = await invoke<DesktopLlmProfileStatus>("get_llm_profile");
      if (!alive.current) return;
      if (adopt) onUseLatest(profile);
      else setLatest(profile);
    } catch (error) {
      if (alive.current) {
        setLatest(null);
        setNote(errorText(error, "读取最新配置失败；当前草稿仍保留"));
      }
    } finally {
      pending.current = false;
      onBusyChange?.(false);
      if (alive.current) setBusy(false);
    }
  };
  return <section ref={region} tabIndex={-1} className="settings-read-feedback model-profile-conflict" role="region" aria-label="模型配置冲突处理" aria-busy={busy}>
    <div>
      <strong>已保存配置有更新，当前草稿仍保留</strong>
      <p>先核对最新配置。载入会放弃当前模型草稿；继续编辑不会覆盖已保存内容。</p>
      {latest && <dl>
        <dt>最新默认服务</dt><dd>{latest.configured ? `${latest.baseUrl} · ${latest.model}` : "尚未配置"}</dd>
        <dt>最新模型分工</dt><dd>{Object.entries(latest.roleModels ?? {}).map(([role, model]) => `${({ research: "研究", implement: "实现", review: "审查", assess: "评判器" } as Record<string, string>)[role] ?? role}：${model}`).join("；") || "跟随默认模型"}</dd>
        <dt>其他模型服务</dt><dd>{latest.providers?.map(provider => provider.name).join("、") || "无"}</dd>
      </dl>}
      {note && <p role="alert">{note}</p>}
      <div className="model-services-toolbar">
        <button type="button" disabled={busy} onClick={() => void run(false)}>{busy ? "处理中…" : "核对最新配置"}</button>
        {latest && <button type="button" disabled={busy} onClick={() => void run(true)}>放弃草稿并载入最新配置</button>}
      </div>
    </div>
  </section>;
}
