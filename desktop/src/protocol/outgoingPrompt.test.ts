import { describe, expect, it } from "vitest";

import type { Attachment } from "../lib/attachments";
import { agentFrame, outgoingMessage } from "./outgoingPrompt";

const image: Attachment = { id: "i", name: "shot.png", kind: "image", size: 1, data: "data:image/png;base64,AA" };
const notes: Attachment = { id: "t", name: "notes.md", kind: "text", size: 1, data: "第一行" };
const context = [{ path: "src/a.ts" }, { path: "src/b.ts", startLine: 3, endLine: 9 }];

describe("outgoing prompt", () => {
  it("splits whole-file references from line ranges and only sends a model when given one", () => {
    const frame = agentFrame({ text: "修一下", context, attachments: [], rid: "r1" }, "build", null);
    expect(frame).toEqual({
      type: "agent", text: "修一下", images: [], audio: [], mode: "build",
      context_files: ["src/a.ts"], context_selections: [{ path: "src/b.ts", start: 3, end: 9 }],
      rid: "r1", want_reasoning: false,
    });
    expect(agentFrame({ text: "x", context: [], attachments: [], rid: "r1" }, "build", "fast")).toMatchObject({ model: "fast" });
  });

  it("text attachments go into the body, images into the media field", () => {
    const frame = agentFrame({ text: "", context: [], attachments: [image, notes], rid: "r1" }, "build", null);
    expect(frame.text).toBe("请查看我添加的附件。\n\n附件 notes.md：\n```\n第一行\n```");
    expect(frame.images).toEqual([image.data]);
  });

  it("the local echo shows image thumbnails and lists the other attachments and references", () => {
    const message = outgoingMessage({ text: "看看", context, attachments: [image, notes], rid: "r1" });
    expect(message).toMatchObject({ role: "user", rid: "r1", images: [image.data],
      text: "看看\n\n📎 src/a.ts · src/b.ts:L3–L9 · notes.md" });
    expect(outgoingMessage({ text: "", context, attachments: [], rid: "r2" }).text).toMatch(/^请分析我选择的本地文件。/);
    expect(outgoingMessage({ text: "hi", context: [], attachments: [], rid: "r3" })).not.toHaveProperty("images");
  });
});
