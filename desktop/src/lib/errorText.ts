/**
 * 统一把"抓到的东西"翻译成给人看的一句话。
 *
 * 为什么需要它：Tauri 的 `invoke` 在后端返回 `Err(String)` 时 **reject 的是字符串**，不是
 * `Error`。于是 `cause instanceof Error ? cause.message : "项目目录不可用"` 这种写法会整条
 * 丢掉后端如实给出的原因（"请选择 Git 工作区：fatal: not a git repository"），只留下一句
 * 笼统兜底——真机上人拿到等于没拿到。
 *
 * 顺序：Error.message → 非空字符串 → 形似 {message}/{error} 的对象 → fallback。
 */
export function errorText(cause: unknown, fallback: string): string {
  if (cause instanceof Error && cause.message.trim()) return cause.message;
  if (typeof cause === "string" && cause.trim()) return cause;
  if (cause && typeof cause === "object") {
    const record = cause as { message?: unknown; error?: unknown };
    for (const candidate of [record.message, record.error]) {
      if (typeof candidate === "string" && candidate.trim()) return candidate;
    }
  }
  return fallback;
}
