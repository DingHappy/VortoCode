// 设置 → 模型服务：所有服务（本机模型、VortoCode 会员、线上服务、自定义）同样是一张卡片，其中一个是「默认」。
// - 默认服务：新对话、自动调度和后台任务用它；输入框的模型菜单里所有服务的模型都能直接选。
// - 「设为默认」：原来的默认服务挪进列表，不丢。会员模型由账号管理：不能手改地址和 Key。
// Key 只经原生命令写入本机配置文件，网页层拿不回来（列表里只有 hasKey）；保存自定义服务走原生确认。
// 服务变化后要重启当前引擎，runtime 才会登记新的端点。
import { invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { Eye, EyeOff, Plus, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { confirmAction } from "../../lib/confirm";
import { errorText } from "../../lib/errorText";
import { formatTokenCount } from "../../lib/labels";
import { defaultServiceLabel } from "../../lib/modelChoice";
import { isLocalModelBase, PROVIDER_PRESETS, type ProviderPreset } from "../../lib/providers";
import {
  ACCOUNT_MANAGE_URL, getAccountStatus, getMembership, MEMBER_KEY_SOURCE, type VortoAccountStatus, type VortoMembership,
} from "../../lib/vortoAccount";
import type { DesktopLlmProfileStatus, DesktopLlmProviderStatus } from "../../types";
import { LlmConnectionCheck } from "./LlmConnectionCheck";

/** 与 Rust 侧 MEMBER_PROVIDER_ID 一致：会员模型作为列表里的一个服务时的 ID。 */
export const MEMBER_PROVIDER_ID = "vortocode_member";

type Draft = { id: string; name: string; baseUrl: string; apiKey: string; editing: boolean };

const EMPTY_DRAFT: Draft = { id: "", name: "", baseUrl: "", apiKey: "", editing: false };
const LOCAL_PRESETS = PROVIDER_PRESETS.filter((preset) => !preset.requiresKey);
const ONLINE_PRESETS = PROVIDER_PRESETS.filter((preset) => preset.requiresKey);

type Props = {
  profile: DesktopLlmProfileStatus | null;
  onProfile: (profile: DesktopLlmProfileStatus) => void;
  onRestartRuntime: () => Promise<boolean>;
  /** 默认服务的详细表单是否展开（地址、模型、Key、上下文） */
  editingDefault: boolean;
  onEditDefault: (open: boolean) => void;
  /** 还没有默认服务时，选了预设就去填默认服务的表单 */
  onSetupDefault: (preset: ProviderPreset | null) => void;
  /** 去「常规」登录 VortoCode 账号 */
  onOpenAccount: () => void;
};

function host(url: string): string {
  try { return new URL(url).host; } catch { return url; }
}

export function ModelServices({ profile, onProfile, onRestartRuntime, editingDefault, onEditDefault, onSetupDefault, onOpenAccount }: Props) {
  const providers = profile?.providers ?? [];
  const hasDefault = Boolean(profile?.configured);
  const defaultIsMember = profile?.account?.keySource === MEMBER_KEY_SOURCE;
  const [draft, setDraft] = useState<Draft | null>(null);
  // 一个服务都没有时直接摆出「添加」的几种选择，省掉一次点击
  const [adding, setAdding] = useState(!profile?.configured && (profile?.providers?.length ?? 0) === 0);
  const [showKey, setShowKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [account, setAccount] = useState<{ status: VortoAccountStatus | null; membership: VortoMembership | null }>({ status: null, membership: null });

  useEffect(() => {
    let alive = true;
    void (async () => {
      const status = await getAccountStatus().catch(() => null);
      const membership = status?.signedIn && !status.offline ? await getMembership().catch(() => null) : null;
      if (alive) setAccount({ status, membership });
    })();
    return () => { alive = false; };
  }, [profile?.configured, providers.length]);

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
  }, "保存失败");

  const remove = (provider: DesktopLlmProviderStatus) => run(async () => {
    const member = provider.id === MEMBER_PROVIDER_ID;
    if (!await confirmAction(member
      ? "把会员模型移出列表？以后可以在 设置 → 常规 →「VortoCode 账号」里重新加入。"
      : `删除「${provider.name}」？它的 Key 会从本机配置中移除。`)) return;
    await applied(await invoke<DesktopLlmProfileStatus>("remove_llm_provider", { id: provider.id }), member ? "已移出会员模型" : `已删除 ${provider.name}`);
  }, "删除失败");

  const makeDefault = (provider: DesktopLlmProviderStatus) => run(async () => {
    const current = defaultServiceLabel(profile);
    if (!await confirmAction(`把「${provider.name}」设为默认？新对话和后台任务会用它；「${current}」会留在列表里，随时可以切回来。`)) return;
    const next = provider.id === MEMBER_PROVIDER_ID
      ? await invoke<DesktopLlmProfileStatus>("account_use_membership", { asDefault: true })
      : await invoke<DesktopLlmProfileStatus>("promote_llm_provider", { id: provider.id });
    onEditDefault(false);
    await applied(next, `已把 ${provider.name} 设为默认`);
  }, "设为默认失败");

  const addMember = () => run(async () => {
    const next = await invoke<DesktopLlmProfileStatus>("account_use_membership", { asDefault: false });
    setAdding(false);
    await applied(next, hasDefault ? "已加入会员模型" : "已用会员模型作为默认服务");
  }, "加入会员模型失败");

  const refresh = () => run(async () => {
    onProfile(await invoke<DesktopLlmProfileStatus>("refresh_llm_providers"));
    setNote("模型列表已刷新");
  }, "刷新模型列表失败");

  const choosePreset = (preset: ProviderPreset | null) => {
    setAdding(false);
    if (!hasDefault) { onSetupDefault(preset); return; }
    setDraft(preset
      ? { id: preset.id, name: preset.label, baseUrl: preset.baseUrl, apiKey: "", editing: false }
      : { ...EMPTY_DRAFT });
  };

  const taken = Boolean(draft && !draft.editing && providers.some((item) => item.id === draft.id.trim().toLowerCase()));
  const canSave = Boolean(draft && draft.id.trim() && draft.baseUrl.trim() && !taken
    && (draft.apiKey.trim() || draft.editing || isLocalModelBase(draft.baseUrl)));
  const membership = account.membership;
  const memberListed = defaultIsMember || providers.some((item) => item.id === MEMBER_PROVIDER_ID);

  return (
    <section className="model-services" aria-label="模型服务">
      <p className="model-services-intro">
        输入框的模型菜单里，所有服务的模型都能直接选；「默认」服务用于新对话、自动调度和后台任务。
      </p>

      <ul className="model-service-list">
        {hasDefault && profile && (
          <li className="model-service default">
            <div className="model-service-copy">
              <strong>{defaultIsMember ? `VortoCode 会员${profile.account?.planName ? ` · ${profile.account.planName}` : ""}` : defaultServiceLabel(profile)}<em>默认</em></strong>
              <span>
                {defaultIsMember ? "账号自动管理" : host(profile.baseUrl)} · {profile.model}
                {(profile.contextWindow ?? 0) > 0 ? ` · 上下文 ${formatTokenCount(profile.contextWindow)}` : ""}
                {(profile.models?.length ?? 0) > 1 ? ` · 共 ${profile.models!.length} 个模型` : ""}
              </span>
            </div>
            <div className="model-service-actions">
              {defaultIsMember
                ? <button onClick={() => void openUrl(ACCOUNT_MANAGE_URL.replace(/\/account$/, "/membership"))}>管理会员</button>
                : <button aria-expanded={editingDefault} onClick={() => onEditDefault(!editingDefault)}>{editingDefault ? "收起" : "编辑"}</button>}
            </div>
          </li>
        )}
        {providers.map((provider) => {
          const member = provider.id === MEMBER_PROVIDER_ID;
          return (
            <li className="model-service" key={provider.id}>
              <div className="model-service-copy">
                <strong>{member && membership?.planName ? `${provider.name} · ${membership.planName}` : provider.name}</strong>
                <span>
                  {member
                    ? `${provider.models[0] ?? "会员模型"}${provider.models.length > 1 ? ` 等 ${provider.models.length} 个模型` : ""} · 账号自动管理`
                    : `${host(provider.baseUrl)} · ${provider.models.length} 个模型${provider.hasKey || isLocalModelBase(provider.baseUrl) ? "" : " · 未设置 Key"}`}
                </span>
              </div>
              <div className="model-service-actions">
                <button onClick={() => void makeDefault(provider)} disabled={busy || provider.models.length === 0}
                  title={provider.models.length === 0 ? "还没有模型列表：先刷新，或检查地址和 Key" : undefined}>设为默认</button>
                {!member && <button onClick={() => { setAdding(false); setDraft({ id: provider.id, name: provider.name, baseUrl: provider.baseUrl, apiKey: "", editing: true }); }} disabled={busy}>编辑</button>}
                <button onClick={() => void remove(provider)} disabled={busy}>{member ? "移出" : "删除"}</button>
              </div>
            </li>
          );
        })}
        {!hasDefault && providers.length === 0 && (
          <li className="model-service empty"><div className="model-service-copy">
            <strong>还没有模型服务</strong>
            <span>添加一个就能开始对话：VortoCode 会员登录即用；也可以接本机模型或自己的 Key。</span>
          </div></li>
        )}
      </ul>

      <div className="model-services-toolbar">
        <button className="primary" onClick={() => { setAdding(!adding); setDraft(null); }} disabled={busy} aria-expanded={adding}><Plus size={14} />添加模型服务</button>
        {providers.length > 0 && <button aria-label="刷新模型列表" title="刷新各服务的模型列表" onClick={() => void refresh()} disabled={busy}><RefreshCw size={14} /></button>}
      </div>

      {adding && (
        <div className="model-service-chooser" role="dialog" aria-label="选择要添加的服务">
          <div className="model-service-kind">
            <strong>VortoCode 会员</strong>
            <span>登录账号即可使用，不用填地址和 Key</span>
            {memberListed ? <em>已添加</em>
              : membership?.member && membership.keyEnabled ? <button className="primary" onClick={() => void addMember()} disabled={busy}>加入</button>
                : account.status?.signedIn ? <button onClick={() => void openUrl(ACCOUNT_MANAGE_URL.replace(/\/account$/, "/membership"))}>开通会员</button>
                  : <button onClick={onOpenAccount}>去登录</button>}
          </div>
          <div className="model-service-kind">
            <strong>本机模型</strong>
            <span>在这台电脑上运行，不出网（oMLX、Ollama、LM Studio 等）</span>
            <div>{LOCAL_PRESETS.map((preset) => <button key={preset.id} onClick={() => choosePreset(preset)}>{preset.label}</button>)}</div>
          </div>
          <div className="model-service-kind">
            <strong>线上服务</strong>
            <span>用你自己在各家申请的 Key</span>
            <div>{ONLINE_PRESETS.map((preset) => <button key={preset.id} onClick={() => choosePreset(preset)}>{preset.label}</button>)}</div>
          </div>
          <div className="model-service-kind">
            <strong>自定义</strong>
            <span>任何 OpenAI 兼容接口（/v1/chat/completions）</span>
            <div><button onClick={() => choosePreset(null)}>填写地址</button></div>
          </div>
        </div>
      )}

      {draft && (
        <div className="custom-provider-editor">
          <div className="custom-provider-form">
            <label><span>名称</span>
              <input value={draft.name} placeholder="显示名称，例如 DeepSeek" onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
            </label>
            <label><span>ID <em>菜单里以「ID:模型」区分，只能用小写字母、数字和下划线</em></span>
              <input value={draft.id} disabled={draft.editing} placeholder="my_service" onChange={(event) => setDraft({ ...draft, id: event.target.value })} />
            </label>
            {taken && <p className="custom-providers-note danger">已有同 ID 的服务；要修改请点它的「编辑」。</p>}
            <label><span>接口地址</span>
              <input value={draft.baseUrl} placeholder="https://api.example.com/v1" onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} />
            </label>
            <label><span>API Key</span>
              <span className="custom-provider-key">
                <input
                  type={showKey ? "text" : "password"}
                  value={draft.apiKey}
                  autoComplete="new-password"
                  placeholder={isLocalModelBase(draft.baseUrl) ? "本机无需鉴权时可留空" : draft.editing ? "已保存；修改时输入新 Key" : "API Key"}
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
            <button className="primary" onClick={() => void save()} disabled={busy || !canSave}>{busy ? "保存中…" : draft.editing ? "保存修改" : "添加"}</button>
          </div>
        </div>
      )}

      {note && <p className="custom-providers-note" role="status">{note}</p>}
    </section>
  );
}
