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

/** 面板里的时间点：今天/昨天/明天 HH:MM，其余写月日；解析不了就原样（去掉 T）。
 * 按来源自己的时区读、不换算成本机时区：「定时」页的「每天 09:00」也是助理所在电脑的钟点，换算了反而对不上。 */
export function whenLabel(value: string, now: Date = new Date()): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::[\d.]+)?(Z|[+-]\d{2}:?\d{2})?$/.exec(value.trim());
  if (!match) return value.replace("T", " ");
  const [, year, month, day, hour, minute, zone] = match;
  // today：来源时区里的「此刻」，统一用 UTC 读取年月日
  let today: Date;
  if (zone) {
    const offset = zone === "Z" ? 0 : (zone[0] === "-" ? -1 : 1) * (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(-2)));
    today = new Date(now.getTime() + offset * 60000);
  } else {
    today = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  }
  const diff = Math.round((Date.UTC(+year, +month - 1, +day)
    - Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate())) / 86400000);
  const prefix = diff === 0 ? "今天" : diff === -1 ? "昨天" : diff === 1 ? "明天"
    : +year === today.getUTCFullYear() ? `${+month}月${+day}日` : `${year}年${+month}月${+day}日`;
  return `${prefix} ${hour}:${minute}`;
}

const UNITS: Record<string, string> = { s: "秒", m: "分钟", h: "小时", d: "天" };

/** 「定时」的周期写法各来源不一（每 1 分钟 / 每 30m / every 1h / 一次 · ISO 时间），统一成中文读法。 */
export function scheduleLabel(value: string, now: Date = new Date()): string {
  const text = value.trim()
    .replace(/^(?:每|every)\s*(\d+)\s*([smhd])$/i, (_, n: string, unit: string) => `每 ${n} ${UNITS[unit.toLowerCase()]}`)
    .replace(/^每\s*1\s*(秒|分钟|小时|天)$/, "每$1")
    .replace(/^每1天$/, "每天");
  return text.replace(/\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?/g, (iso) => whenLabel(iso, now));
}
