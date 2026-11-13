import { useState } from "react";
import type { DesktopLlmProfileStatus } from "../types";
import { modelSettingsState } from "../lib/modelSettings";
import { LlmConnectionCheck } from "./settings/LlmConnectionCheck";

export function OnboardingModel({ profile, baseUrl, model, apiKey, fastModel, strongModel, roleModels, loading, busy, error, conflict, note, onBaseUrl, onModel, onKey, onSave, onReload, onAccount }: {
  profile: DesktopLlmProfileStatus | null; baseUrl: string; model: string; apiKey: string; fastModel: string; strongModel: string; roleModels: Record<string, string>;
  loading: boolean; busy: boolean; error: string; conflict: boolean; note: string;
  onBaseUrl: (value: string) => void; onModel: (value: string) => void; onKey: (value: string) => void;
  onSave: () => void; onReload: () => void; onAccount: () => void;
}) {
  const [models, setModels] = useState<string[]>([]);
  const state = modelSettingsState(profile, { baseUrl, model, apiKey, fastModel, strongModel, roleModels });
  const blocked = loading || busy || Boolean(error) || conflict;
  return <div className="onboarding-model">
    {profile?.configured && !state.dirty && !error && !conflict && <p role="status">模型配置已保存 · {profile.model}。模型列表检查与真实推理是不同的验证。</p>}
    {(error || conflict) && <p role="alert">{error || "已保存配置有更新，请重新载入后再编辑。"}<button disabled={busy} onClick={onReload}>放弃草稿并重新载入</button></p>}
    <fieldset disabled={blocked}><label>模型服务地址<input value={baseUrl} onChange={e => onBaseUrl(e.target.value)} placeholder="本机模型或兼容 API 地址" /></label>
      <label>模型名称<input value={model} onChange={e => onModel(e.target.value)} list="onboarding-models" /><datalist id="onboarding-models">{models.map(value => <option key={value} value={value} />)}</datalist></label>
      <label>API Key<input type="password" autoComplete="new-password" value={apiKey} onChange={e => onKey(e.target.value)} placeholder={state.sameService ? "留空沿用此服务已保存的 Key" : "无需鉴权的本机服务可留空"} /></label>
      <LlmConnectionCheck baseUrl={baseUrl} apiKey={apiKey} model={model} onModels={setModels} />
    </fieldset>
    {state.missing && <p>{state.missing}</p>}
    <div className="settings-actions"><button className="primary" disabled={blocked || !state.dirty || Boolean(state.missing)} onClick={onSave}>{busy ? "正在保存…" : "保存模型配置"}</button><button disabled={busy} onClick={onAccount}>使用 VortoOne 会员模型</button></div>
    {loading && <p role="status">正在读取本机模型配置…</p>}{note && <p role="status">{note}</p>}
  </div>;
}
