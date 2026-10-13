// 主要聊天是哪一段：由常驻电脑统一记录（手机、电脑共用一段）。
// 服务器已登记 → 用它；还没登记 → 把本机已有的那段报上去（不搬数据）；
// 旧版常驻电脑没有这个接口或连不上 → 照旧用本机那段。

export interface MainChatRegistry {
  getMainChat(): Promise<string | null>;
  claimMainChat(sid: string): Promise<string>;
}

export async function resolveMainChat(registry: MainChatRegistry, localSid: string): Promise<string> {
  try {
    const registered = await registry.getMainChat();
    if (registered) return registered;
    return (await registry.claimMainChat(localSid)) || localSid;
  } catch {
    return localSid;
  }
}

/** 消息从哪个入口来：手机上的显示一个小标记，电脑上的不标。 */
export function sourceLabel(source: string | undefined): string | null {
  if (!source) return null;
  if (source === "telegram") return "📱 Telegram";
  if (source === "dingtalk") return "📱 钉钉";
  return "📱 手机";
}
