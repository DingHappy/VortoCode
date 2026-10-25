type Visibility = Pick<Document, "hidden" | "addEventListener" | "removeEventListener">;

/** 读快照专用：结束后再计时，隐藏窗口降频，失败退避；停止不再启动下一次请求。 */
export function startPolling(read: () => Promise<boolean | void>, options: {
  intervalMs: number;
  hiddenIntervalMs: number;
  maxBackoffMs?: number;
  immediate?: boolean;
  visibility: Visibility;
}): () => void {
  let stopped = false;
  let running = false;
  let failures = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const delay = () => Math.max(
    options.visibility.hidden ? options.hiddenIntervalMs : options.intervalMs,
    Math.min(options.maxBackoffMs ?? 30_000, options.intervalMs * 2 ** failures),
  );
  const schedule = (ms: number) => {
    if (timer !== undefined) clearTimeout(timer);
    if (!stopped) timer = setTimeout(() => { void tick(); }, ms);
  };
  const tick = async () => {
    if (stopped || running) return;
    running = true;
    try {
      failures = await read() === false ? Math.min(failures + 1, 10) : 0;
    } catch {
      failures = Math.min(failures + 1, 10);
    } finally {
      running = false;
      schedule(delay());
    }
  };
  const onVisibility = () => {
    // 前台恢复时立即核对；正在进行的读取不重复启动，随后按新频率继续。
    if (!running) schedule(options.visibility.hidden ? delay() : 0);
  };
  options.visibility.addEventListener("visibilitychange", onVisibility);
  schedule(options.immediate ? 0 : delay());
  return () => {
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
    options.visibility.removeEventListener("visibilitychange", onVisibility);
  };
}
