import { describe, expect, it } from "vitest";

import type { ConversationMessage, PromptQueueItem, ProtocolEvent, TurnActivity } from "../types";
import {
  historyMessages, promptQueueFromEvent, systemNotice, thinkingActivity, withAssistantReply, withRunningPrompt, withSayActivity,
} from "./transcript";

const event = (patch: Partial<ProtocolEvent>): ProtocolEvent => ({ type: "agent_emit", ...patch }) as ProtocolEvent;
const running = (patch: Partial<PromptQueueItem> = {}): PromptQueueItem => ({
  id: "r1", version: 1, text: "修登录", mode: "build", position: 0, created_at: "", context_count: 0, ...patch,
});

describe("prompt queue", () => {
  it("drops malformed entries, keeps positions and reads the running item", () => {
    const parsed = promptQueueFromEvent(event({
      type: "agent_queue", paused: true,
      items: [{ id: "a", text: "一" }, { text: "没 id" }, { id: "b", text: 2 }, { id: "c", text: "三", mode: "build", position: 7 }],
      running: { id: "r", text: "跑着" } as unknown as PromptQueueItem,
    }));
    expect(parsed.queued.map(item => [item.id, item.position, item.mode])).toEqual([["a", 0, "plan"], ["c", 7, "build"]]);
    expect(parsed.running?.id).toBe("r");
    expect(parsed.paused).toBe(true);
    expect(promptQueueFromEvent(event({ type: "agent_queue" }))).toEqual({ queued: [], running: null, paused: false });
  });

  it("adds the running prompt once, noting attached source references", () => {
    const added = withRunningPrompt([], running({ context_count: 2 }));
    expect(added).toHaveLength(1);
    expect(added[0]).toMatchObject({ role: "user", rid: "r1", text: "修登录\n\n📎 2 个源码引用" });
    expect(withRunningPrompt(added, running())).toBe(added);
  });
});

it("history keeps roles, rids and the source channel", () => {
  const messages = historyMessages(event({ type: "agent_history", items: [
    { role: "user", text: "早", rid: "a", source: "telegram" }, { role: "tool", text: 3 }, {},
  ] }), "sid1");
  expect(messages).toEqual([
    { id: "history-0-sid1", role: "user", text: "早", rid: "a", source: "telegram" },
    { id: "history-1-sid1", role: "assistant", text: "3", rid: undefined },
    { id: "history-2-sid1", role: "assistant", text: "", rid: undefined },
  ]);
});

describe("activities", () => {
  it("thinking is one row per turn", () => {
    expect(thinkingActivity("r1")).toMatchObject({ id: "r1:thinking", rid: "r1", phase: "thinking", status: "running" });
    expect(thinkingActivity(undefined).id).toBe("legacy:thinking");
  });

  it("a 🔧 narration is skipped once the turn already shows tool activity", () => {
    const tool: TurnActivity = { id: "t", rid: "r1", kind: "tool", status: "running", label: "读文件" };
    expect(withSayActivity([tool], "r1", "🔧 读文件")).toEqual([tool]);
    expect(withSayActivity([tool], "r2", "🔧 读文件")).toHaveLength(2);
    expect(withSayActivity([tool], "r1", "先看一下目录")).toHaveLength(2);
  });
});

describe("replies", () => {
  const reply: ConversationMessage = { id: "m", role: "assistant", text: "好了", rid: "r1" };
  it("a replayed reply for the same turn is not added twice", () => {
    expect(withAssistantReply([reply], event({ text: "又一遍", rid: "r1" }))).toEqual([reply]);
    expect(withAssistantReply([reply], event({ text: "下一回合", rid: "r2" }))).toHaveLength(2);
  });

  it("without a rid, the same text counts as a duplicate", () => {
    const legacy: ConversationMessage = { id: "m", role: "assistant", text: "好了" };
    expect(withAssistantReply([legacy], event({ text: "好了" }))).toEqual([legacy]);
    expect(withAssistantReply([legacy], event({ text: "不一样" }))).toHaveLength(2);
  });

  it("errors and cancellations become system lines", () => {
    expect(systemNotice("error", event({ text: "超时", rid: "r1" }))).toMatchObject({ role: "system", text: "出错：超时", rid: "r1" });
    expect(systemNotice("error", event({})).text).toBe("出错：未知错误");
    expect(systemNotice("cancelled", event({ rid: "r1" }))).toMatchObject({ role: "system", text: "本回合已中断" });
  });
});
