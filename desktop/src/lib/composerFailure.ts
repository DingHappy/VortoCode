/** Reporting a failed send is independent of whether its original draft can still be restored. */
export function reportComposerFailure(options: {
  current: boolean; restore: () => boolean; recover: () => void; markFailed: () => void;
  notify: (text: string) => void; message: string; followUp?: string;
}): boolean {
  options.markFailed();
  const restored = options.current && options.restore();
  if (restored) options.recover();
  const detail = !options.current ? "已切换会话，原输入未恢复" : restored ? "已恢复原输入和附件" : "当前草稿已保留";
  options.notify(`${options.message}；${detail}${options.followUp ? `。${options.followUp}` : ""}`);
  return restored;
}
