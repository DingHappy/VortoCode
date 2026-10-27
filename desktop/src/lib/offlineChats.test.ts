import { describe, expect, it } from "vitest";

import { MAX_TEXT_FILE_BYTES } from "./attachments";
import { dismissOfflineChat, listOfflineChats, offlineChatAttachment, recordOfflineChat } from "./offlineChats";

function memory() {
  const data = new Map<string, string>();
  return { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
}

describe("offline chat list", () => {
  it("记最新的在前、同一段只记一次、最多 20 段，移除只动列表", () => {
    const store = memory();
    recordOfflineChat("a", store, "1");
    recordOfflineChat("b", store, "2");
    recordOfflineChat("a", store, "3");
    expect(listOfflineChats(store)).toEqual([{ sid: "a", at: "3" }, { sid: "b", at: "2" }]);
    for (let index = 0; index < 30; index++) recordOfflineChat(`s${index}`, store, String(index));
    expect(listOfflineChats(store)).toHaveLength(20);
    expect(dismissOfflineChat("s29", store).map((item) => item.sid)).not.toContain("s29");
  });

  it("存储坏了也不报错", () => {
    const store = memory();
    store.setItem("vortocode.desktop.offlineAssistantChats", "{oops");
    expect(listOfflineChats(store)).toEqual([]);
    store.setItem("vortocode.desktop.offlineAssistantChats", JSON.stringify([{ sid: 1 }, { sid: "ok", at: "x" }]));
    expect(listOfflineChats(store)).toEqual([{ sid: "ok", at: "x" }]);
  });
});

describe("offlineChatAttachment", () => {
  it("说明来历、逐条排好，做成 md 文本附件", () => {
    const attachment = offlineChatAttachment({ sid: "s1", title: "周三/开会", transcript: [
      { role: "user", text: "帮我记下周三开会" }, { role: "assistant", text: "记下了" }, { role: "assistant", text: "  " }] });
    expect(attachment).toMatchObject({ id: "offline-s1", name: "本机对话-周三 开会.md", kind: "text" });
    expect(attachment.data).toContain("常驻电脑连不上时");
    expect(attachment.data).toContain("我：帮我记下周三开会\n\n本机助理：记下了");
    expect(attachment.size).toBe(new TextEncoder().encode(attachment.data).length);
  });

  it("太长时保留最近的部分，不超过文本附件上限", () => {
    const transcript = Array.from({ length: 400 }, (_, index) => ({ role: index % 2 ? "assistant" : "user", text: `第${index}条 ${"字".repeat(200)}` }));
    const attachment = offlineChatAttachment({ sid: "s2", title: "", transcript });
    expect(attachment.size).toBeLessThanOrEqual(MAX_TEXT_FILE_BYTES);
    expect(attachment.data).toContain("已省略");
    expect(attachment.data).toContain("第399条");
    expect(attachment.data).not.toContain("第0条 ");
  });
});
