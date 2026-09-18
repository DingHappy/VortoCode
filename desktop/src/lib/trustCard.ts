/**
 * 授权档位卡的视图模型。
 *
 * 抽出来是因为一个真实崩溃：`/api/trust` 在 General 会话下返回 409
 * （"这个功能需要隔离 Scratch；General 不访问文件、Shell 或 Git"），前端 catch 后把 trust
 * 置为 null——但卡片仍然照常渲染，其中一处 tooltip 写着 `TRUST_LABELS[trust!.ceiling]`。
 * 那个 `!` 正好把 TypeScript 的检查关掉了，于是 null 一路走到运行时，**打开设置就白屏**。
 * 而 General 恰恰是启动后的默认状态。
 *
 * 所以判定收到这里，用返回 `null` 表达"这张卡不该出现"，并且一个非空断言都不用。
 */
import type { TrustLevel, TrustStatus } from "../types";

export const TRUST_ORDER: Record<TrustLevel, number> = { ask: 0, reads: 1, full: 2 };

export const TRUST_LABELS: Record<TrustLevel, { title: string; hint: string }> = {
  ask: { title: "每次确认", hint: "写文件、执行命令都问你" },
  reads: { title: "只读免确认", hint: "读文件不问，写和执行仍要你点头" },
  full: { title: "完全信任", hint: "不再逐次确认；污点回合除外" },
};

const ALL_LEVELS: TrustLevel[] = ["ask", "reads", "full"];

export type TrustOption = {
  level: TrustLevel;
  title: string;
  active: boolean;
  blocked: boolean;
  blockedReason?: string;
};

export type TrustCard = {
  title: string;
  hint: string;
  options: TrustOption[];
  note: string;
};

/** 这个会话该显示的授权档位卡；`null` = 拿不到档位（General / 老 runtime），整张卡不显示。 */
export function trustCard(trust: TrustStatus | null | undefined): TrustCard | null {
  if (!trust) return null;
  if (!(trust.effective in TRUST_LABELS) || !(trust.ceiling in TRUST_LABELS)) return null;
  const levels = trust.levels?.length ? trust.levels : ALL_LEVELS;
  return {
    title: TRUST_LABELS[trust.effective].title,
    hint: TRUST_LABELS[trust.effective].hint,
    options: levels.filter((level) => level in TRUST_LABELS).map((level) => {
      const blocked = TRUST_ORDER[level] > TRUST_ORDER[trust.ceiling];
      return {
        level,
        title: TRUST_LABELS[level].title,
        active: trust.level === level,
        blocked,
        blockedReason: blocked
          ? `当前会话（${trust.capability_profile || "未知档案"}）最高只能到「${TRUST_LABELS[trust.ceiling].title}」`
          : undefined,
      };
    }),
    note: trust.level !== trust.effective
      ? `已选「${TRUST_LABELS[trust.level]?.title ?? trust.level}」，但这个会话最高只到「${TRUST_LABELS[trust.effective].title}」，按后者执行。`
      : "读过网页、搜索或 MCP 内容的那一轮，无论哪一档都会重新向你确认（防提示注入）。",
  };
}
