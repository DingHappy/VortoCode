/** Display metadata from a trusted runtime event, never inferred from model text. */
export interface WebsiteConfirmation {
  version: 1;
  action: "connect" | "open" | "login";
  origin: string;
  account_label: string;
  device_mode: "desktop" | "mobile";
}

export function websiteConfirmation(value: unknown): WebsiteConfirmation | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  const row = value as Record<string, unknown>;
  if (row.version !== 1 || typeof row.action !== "string" || !["connect", "open", "login"].includes(row.action)
    || typeof row.device_mode !== "string" || !["desktop", "mobile"].includes(row.device_mode)
    || typeof row.origin !== "string" || typeof row.account_label !== "string"
    || !row.account_label.trim() || row.account_label.length > 80
    || /[\u0000-\u001f\u007f]/.test(row.account_label)
    || Object.keys(row).some(key => !["version", "action", "origin", "account_label", "device_mode"].includes(key))) return;
  try {
    const url = new URL(row.origin);
    if (!["http:", "https:"].includes(url.protocol) || url.origin !== row.origin) return;
  } catch { return; }
  return { version: 1, action: row.action as WebsiteConfirmation["action"], origin: row.origin,
    account_label: row.account_label, device_mode: row.device_mode as WebsiteConfirmation["device_mode"] };
}

export const WEBSITE_CONFIRMATION_COPY = {
  connect: { title: "这个任务需要连接网站", allow: "允许建立连接", deny: "暂不连接" },
  open: { title: "这个任务需要打开网站", allow: "允许打开", deny: "暂不打开" },
  login: { title: "请在专用浏览器完成登录或验证", allow: "我已完成，核对并继续", deny: "保持暂停" },
} as const;
