// 设置 → 模型 → 自定义供应商：添加各家的 OpenAI 兼容接口和自己的 Key，之后在聊天里以 `ID:模型` 选用。
// Key 只经原生命令写入本机配置文件，网页层拿不回来（列表里只有 hasKey）；保存走原生确认。
// 供应商变化后要重启当前引擎，runtime 才会登记新的端点。
import { invoke } from "@tauri-apps/api/core";
import { Eye, EyeOff, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { confirmAction } from "../../lib/confirm";
import { errorText } from "../../lib/errorText";
import { PROVIDER_PRESETS } from "../../lib/providers";
import type { DesktopLlmProfileStatus, DesktopLlmProviderStatus } from "../../types";
import { LlmConnectionCheck } from "./LlmConnectionCheck";

type Draft = { id: string; name: string; baseUrl: string; apiKey: string; editing: boolean };

const EMPTY_DRAFT: Draft = { id: "", name: "", baseUrl: "", apiKey: "", editing: false };

type Props = {
  profile: DesktopLlmProfileStatus | null;
  onProfile: (profile: DesktopLlmProfileStatus) => void;
  onRestartRuntime: () => Promise<boolean>;
};

export function CustomProviders({ profile, onProfile, onRestartRuntime }: Props) {
  const providers = profile?.providers ?? [];
  const [draft, setDraft] = useState<Draft | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const applied = async (next: DesktopLlmProfileStatus, done: string) => {
    onProfile(next);
    const restarted = await onRestartRuntime();
    setNote(restarted ? `${done}，当前引擎已重启` : `${done}，下次启动引擎时生效`);
  };

  const run = async (work: () => Promise<void>, failure: string) => {
    setBusy(true);
    setNote("");
    try {
      await work();
    } catch (error) {
      setNote(errorText(error, failure));
    } finally {
      setBusy(false);
    }
  };

  const save = () => run(async () => {
    if (!draft) return;
    const next = await invoke<DesktopLlmProfileStatus>("save_llm_provider", {
      id: draft.id, name: draft.name, baseUrl: draft.baseUrl, apiKey: draft.apiKey,
    });
    const saved = next.providers?.find((item) => item.id === draft.id.trim().toLowerCase());
    setDraft(null);
    setShowKey(false);
    await applied(next, saved?.models.length
      ? `已保存 ${saved.name}，可用 ${saved.models.length} 个模型`
      : `已保存 ${saved?.name ?? draft.id}（没有拉到模型列表，可稍后刷新）`);
  }, "保存供应商失败");

  const remove = (provider: DesktopLlmProviderStatus) => run(async () => {
    if (!await confirmAction(`删除供应商「${provider.name}」？它的 Key 会从本机配置中移除。`)) return;
    await applied(await invoke<DesktopLlmProfileStatus>("remove_llm_provider", { id: provider.id }), `已删除 ${provider.name}`);
  }, "删除供应商失败");

  const refresh = () => run(async () => {
    const next = await invoke<DesktopLlmProfileStatus>("refresh_llm_providers");
    onProfile(next);
    setNote("模型列表已刷新");
  }, "刷新模型列表失败");

  const usePreset = (presetId: string | null) => {
    const preset = PROVIDER_PRESETS.find((item) => item.id === presetId);
    setDraft(preset
      ? { id: preset.id, name: preset.label, baseUrl: preset.baseUrl, apiKey: "", editing: false }
      : { ...EMPTY_DRAFT });
  };

  const taken = Boolean(draft && !draft.editing && providers.some((item) => item.id === draft.id.trim().toLowerCase()));
  const canSave = Boolean(draft && draft.id.trim() && draft.baseUrl.trim() && !taken
    && (draft.apiKey.trim() || draft.editing || draft.baseUrl.startsWith("http://127.0.0.1") || draft.baseUrl.startsWith("http://localhost")));

  return (
    <section className="custom-providers" aria-label="自定义供应商">
      <div className="custom-providers-head">
        <div>
          <strong>自定义供应商</strong>
          <p>使用你自己的密钥：填入各家的 API Key，即可在聊天里选用它们的模型。Key 只保存在本机配置文件里。</p>
        </div>
        <div className="custom-providers-toolbar">
          <button aria-label="刷新模型列表" title="刷新模型列表" onClick={() => void refresh()} disabled={busy || providers.length === 0}>
            <RefreshCw size={14} />
          </button>
          <button onClick={() => { setDraft(draft ? null : { ...EMPTY_DRAFT }); setShowKey(false); }} disabled={busy || !profile?.configured}>
            <Plus size={14} />添加供应商
          </button>
        </div>
      </div>

      {!profile?.configured && <p className="custom-providers-note">先在上方保存默认模型服务，再添加其他供应商。</p>}

      {providers.length > 0 && (
        <ul className="custom-provider-list">
          {providers.map((provider) => (
            <li key={provider.id}>
              <div>
                <strong>{provider.name}</strong>
                <span>{provider.id} · {provider.baseUrl} · {provider.models.length} 个模型{provider.hasKey ? "" : " · 未设置 Key"}</span>
              </div>
              <button onClick={() => setDraft({ id: provider.id, name: provider.name, baseUrl: provider.baseUrl, apiKey: "", editing: true })} disabled={busy}>编辑</button>
              <button aria-label={`删除 ${provider.name}`} onClick={() => void remove(provider)} disabled={busy}><Trash2 size={14} /></button>
            </li>
          ))}
        </ul>
      )}

      {draft && (
        <div className="custom-provider-editor">
          {!draft.editing && (
            <div className="provider-presets">
              {PROVIDER_PRESETS.filter((preset) => preset.id !== "local").map((preset) => (
                <button key={preset.id} className={draft.id === preset.id ? "active" : ""} onClick={() => usePreset(preset.id)}>{preset.label}</button>
              ))}
              <button className={draft.id === "" || !PROVIDER_PRESETS.some((preset) => preset.id === draft.id) ? "active" : ""} onClick={() => usePreset(null)}>
                <Plus size={13} />自定义 / Custom
              </button>
            </div>
          )}
          <div className="custom-provider-form">
            <label><span>供应商 ID</span>
              <input value={draft.id} disabled={draft.editing} placeholder="my_provider" onChange={(event) => setDraft({ ...draft, id: event.target.value })} />
            </label>
            {taken && <p className="custom-providers-note danger">已有同 ID 的供应商；要修改请点它的「编辑」。</p>}
            <label><span>名称</span>
              <input value={draft.name} placeholder="显示名称，可留空" onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
            </label>
            <label><span>接口地址</span>
              <input value={draft.baseUrl} placeholder="https://api.example.com/v1" onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} />
            </label>
            <label><span>接口协议</span>
              <select value="openai" disabled aria-label="接口协议"><option value="openai">/chat/completions（OpenAI 兼容格式）</option></select>
            </label>
            <label><span>API Key</span>
              <span className="custom-provider-key">
                <input
                  type={showKey ? "text" : "password"}
                  value={draft.apiKey}
                  autoComplete="new-password"
                  placeholder={draft.editing ? "已保存；修改时输入新 Key" : "API Key"}
                  onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })}
                />
                <button aria-label={showKey ? "隐藏 Key" : "显示 Key"} onClick={() => setShowKey(!showKey)}>{showKey ? <EyeOff size={14} /> : <Eye size={14} />}</button>
              </span>
            </label>
            <div className="custom-provider-test"><span>连接测试</span>
              <LlmConnectionCheck baseUrl={draft.baseUrl} apiKey={draft.apiKey} model="" onModels={() => undefined} />
            </div>
          </div>
          <div className="custom-provider-actions">
            <button onClick={() => { setDraft(null); setShowKey(false); }} disabled={busy}>取消</button>
            <button className="primary" onClick={() => void save()} disabled={busy || !canSave}>{busy ? "保存中…" : draft.editing ? "保存修改" : "确认添加"}</button>
          </div>
        </div>
      )}

      {note && <p className="custom-providers-note">{note}</p>}
    </section>
  );
}
