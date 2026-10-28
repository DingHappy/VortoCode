// 资料库的分组和时间：按更新时间倒序，分成今天 / 昨天 / 这 7 天 / 更早；时间读不出来的归「更早」。
import type { ArtifactMeta } from "../types";

export type ArtifactGroup = { key: string; label: string; items: ArtifactMeta[] };

/** 制品时间是引擎本地时间的 ISO 串（不带时区），按本地时间解析。 */
const timeOf = (artifact: ArtifactMeta) => {
  const value = Date.parse(artifact.updated_at || artifact.created_at || "");
  return Number.isFinite(value) ? value : 0;
};

const GROUPS: Array<{ key: string; label: string }> = [
  { key: "today", label: "今天" }, { key: "yesterday", label: "昨天" }, { key: "week", label: "这 7 天" }, { key: "older", label: "更早" },
];

export function groupArtifacts(artifacts: ArtifactMeta[], now: Date = new Date()): ArtifactGroup[] {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 86_400_000;
  const keyOf = (time: number) => !time ? "older"
    : time >= startOfToday ? "today"
    : time >= startOfToday - day ? "yesterday"
    : time >= startOfToday - 6 * day ? "week"
    : "older";
  const sorted = [...artifacts].sort((a, b) => timeOf(b) - timeOf(a));
  return GROUPS
    .map((group) => ({ ...group, items: sorted.filter((artifact) => keyOf(timeOf(artifact)) === group.key) }))
    .filter((group) => group.items.length > 0);
}

/** 行尾时间：今天写时分，今年写月日，更早带年份；读不出来就不写。 */
export function artifactTimeLabel(raw: string, now: Date = new Date()): string {
  const time = Date.parse(raw || "");
  if (!Number.isFinite(time)) return "";
  const date = new Date(time);
  const pad = (value: number) => String(value).padStart(2, "0");
  if (date.toDateString() === now.toDateString()) return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (date.getFullYear() === now.getFullYear()) return `${date.getMonth() + 1}月${date.getDate()}日`;
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
}
