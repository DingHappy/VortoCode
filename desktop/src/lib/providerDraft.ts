import { isLocalModelBase, sameLlmBase } from "./providers";
import type { DesktopLlmProviderStatus } from "../types";

export type ProviderDraft = { id: string; name: string; baseUrl: string; apiKey: string; editing: boolean };

/** Form guidance only; the native layer remains authoritative for validation and consent. */
export function providerDraftState(draft: ProviderDraft, providers: DesktopLlmProviderStatus[]) {
  const id = draft.id.trim().toLowerCase();
  const original = draft.editing ? providers.find(provider => provider.id === id) : undefined;
  const sameService = Boolean(original && sameLlmBase(original.baseUrl, draft.baseUrl));
  const canKeepKey = Boolean(sameService && original?.hasKey);
  const dirty = draft.editing
    ? !original || draft.name !== original.name || draft.baseUrl !== original.baseUrl || Boolean(draft.apiKey.trim())
    : Boolean(id || draft.name.trim() || draft.baseUrl.trim() || draft.apiKey.trim());
  const duplicate = !draft.editing && providers.some(provider => provider.id === id);
  let endpointIssue = "";
  try {
    const url = new URL(draft.baseUrl.trim());
    if (url.username || url.password || url.search || url.hash || draft.baseUrl.includes("?") || draft.baseUrl.includes("#")) {
      endpointIssue = "接口地址不能包含用户名、密码、查询参数或片段。";
    } else if (url.protocol !== "https:" && !isLocalModelBase(draft.baseUrl)) {
      endpointIssue = "线上服务请使用 HTTPS；HTTP 仅支持 127.0.0.1 / localhost。";
    }
  } catch { endpointIssue = "请填写有效的模型服务 URL。"; }
  const missing = !id ? "请填写服务 ID。"
    : !/^[a-z][a-z0-9_]{0,23}$/.test(id) ? "ID 以字母开头，只能包含字母、数字和下划线，最长 24 位。"
    : ["default", "anthropic", "cli"].includes(id) ? `ID「${id}」已保留，请使用其他名称。`
    : duplicate ? "已有同 ID 的服务；请编辑已有服务或换一个 ID。"
    : [...draft.name.trim()].length > 40 || /[\u0000-\u001f\u007f-\u009f]/.test(draft.name) ? "服务名称最长 40 个字符，不能包含控制字符。"
    : !draft.baseUrl.trim() ? "请填写接口地址。"
    : endpointIssue || (!isLocalModelBase(draft.baseUrl) && !draft.apiKey.trim() && !canKeepKey
      ? draft.editing && !sameService ? "接口地址已改变，请为新服务填写 API Key。" : "此线上服务需要填写 API Key。" : "");
  return { dirty, sameService, canKeepKey, missing, canSave: dirty && !missing };
}
