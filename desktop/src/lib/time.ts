// 本地日期辅助（B8-④c S6b 从 App.tsx 上收，供 App 与 useJournal 共用）。
// Journal 域以「本地日」为键：刻意用本地时区拼 YYYY-MM-DD，
// 不能换成 toISOString().slice(0,10)——那是 UTC 日，晚间会提前跨天。
export function localDay(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

export function previousDay(value: string): string {
  const selected = new Date(`${value}T12:00:00`);
  selected.setDate(selected.getDate() - 1);
  const month = String(selected.getMonth() + 1).padStart(2, "0");
  const day = String(selected.getDate()).padStart(2, "0");
  return `${selected.getFullYear()}-${month}-${day}`;
}
