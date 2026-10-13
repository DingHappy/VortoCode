// 助理模式空白对话里的「可以这样用」示例：点一下填进输入框（不直接发），改一改再发。
// 只列此刻真能做到的事：定时汇报、派活到项目只有常驻电脑上有（本机通用会话不开调度、没有项目登记）。
// 用熟了就不再打扰：在助理模式里发过几条消息，或点了「不再显示」，就收起。

import { STORAGE_KEYS } from "./storage";

export interface StarterTip {
  id: string;
  label: string;
  prompt: string;
}

/** 发过这么多条就算上手了，不再显示示例。 */
export const STARTER_TIPS_GRADUATE_AFTER = 3;

export function starterTips(options: { resident: boolean; projects: string[] }): StarterTip[] {
  const remember: StarterTip = {
    id: "remember",
    label: "让它记住你的习惯",
    prompt: "记住：我习惯用中文交流，回答尽量简短，先说结论。",
  };
  if (!options.resident) {
    return [
      remember,
      { id: "plan", label: "帮你理一理这周的事", prompt: "帮我把这周要做的事排个优先级：\n1. \n2. \n3. " },
      { id: "draft", label: "起草一段消息", prompt: "帮我起草一段发给同事的消息，说明：" },
    ];
  }
  const tips: StarterTip[] = [
    {
      id: "brief",
      label: "每天早上给你一份简报",
      prompt: "每天早上 8 点给我发一份简报：昨天聊了什么、任务单进展、今天要我处理的事。",
    },
  ];
  const project = options.projects.find((name) => name.trim());
  if (project) {
    tips.push({
      id: "dispatch",
      label: `派一件活到 ${project}`,
      prompt: `帮我在 ${project} 项目里找一个值得修的小问题，写一张任务单给我确认。`,
    });
  }
  tips.push(remember, {
    id: "today",
    label: "问问今天做了什么",
    prompt: "今天我都交代了你哪些事？哪些做完了，哪些还在等我？",
  });
  return tips;
}

interface StarterTipsState {
  sent: number;
  dismissed: boolean;
}

function read(): StarterTipsState {
  try {
    const payload = JSON.parse(localStorage.getItem(STORAGE_KEYS.starterTips) ?? "{}");
    return {
      sent: Number.isFinite(payload?.sent) ? Math.max(0, Number(payload.sent)) : 0,
      dismissed: payload?.dismissed === true,
    };
  } catch {
    return { sent: 0, dismissed: false };
  }
}

function write(state: StarterTipsState): void {
  try {
    localStorage.setItem(STORAGE_KEYS.starterTips, JSON.stringify(state));
  } catch {
    // 存不下（隐私模式等）：本次会话照常显示，下次再说
  }
}

export function shouldShowStarterTips(): boolean {
  const state = read();
  return !state.dismissed && state.sent < STARTER_TIPS_GRADUATE_AFTER;
}

/** 在助理模式里发出一条消息。 */
export function recordAssistantSend(): void {
  const state = read();
  if (state.dismissed || state.sent >= STARTER_TIPS_GRADUATE_AFTER) return;
  write({ ...state, sent: state.sent + 1 });
}

export function dismissStarterTips(): void {
  write({ ...read(), dismissed: true });
}
