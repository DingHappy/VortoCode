// 模型服务预设：只预填各家公开的 OpenAI 兼容接口地址。拿不准的模型名一律留空——
// 「测试连接」会列出该服务实际提供的模型供选择，避免硬编码过时的版本号。
export interface ProviderPreset {
  id: string;
  label: string;
  baseUrl: string;
  model: string;
  requiresKey: boolean;
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  { id: "vortocode", label: "VortoCode Relay", baseUrl: "https://token.vortotech.com/v1", model: "mimo-v2.6-pro", requiresKey: true },
  { id: "openai", label: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "", requiresKey: true },
  { id: "deepseek", label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat", requiresKey: true },
  { id: "kimi", label: "Kimi", baseUrl: "https://api.moonshot.cn/v1", model: "", requiresKey: true },
  { id: "glm", label: "智谱 GLM", baseUrl: "https://open.bigmodel.cn/api/paas/v4", model: "", requiresKey: true },
  { id: "qwen", label: "千问", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus", requiresKey: true },
  { id: "minimax", label: "MiniMax", baseUrl: "https://api.minimaxi.com/v1", model: "", requiresKey: true },
  { id: "local", label: "本机模型", baseUrl: "http://127.0.0.1:11434/v1", model: "", requiresKey: false },
];

function normalizeBase(url: string): string {
  return url.trim().replace(/\/+$/, "").toLowerCase();
}

/** 当前地址对应哪个预设；本机回环地址都算「本机模型」，其他未知地址返回 null（自定义）。 */
export function presetForBaseUrl(baseUrl: string): ProviderPreset | null {
  const base = normalizeBase(baseUrl);
  if (!base) return null;
  if (/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?(\/|$)/.test(base)) {
    return PROVIDER_PRESETS.find((preset) => preset.id === "local") ?? null;
  }
  return PROVIDER_PRESETS.find((preset) => normalizeBase(preset.baseUrl) === base) ?? null;
}

/** 与原生层同一规则比较服务地址（去空白和末尾斜杠）：同一地址才允许留空 Key 沿用已保存的。 */
export function sameLlmBase(left: string, right: string): boolean {
  const normalize = (value: string) => value.trim().replace(/\/+$/, "");
  return normalize(left) !== "" && normalize(left) === normalize(right);
}
