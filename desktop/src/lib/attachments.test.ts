import { describe, expect, it } from "vitest";

import {
  type Attachment,
  MAX_MEDIA_PER_KIND,
  checkAttachment,
  classifyAttachment,
  composeMessageText,
  mediaPayload,
} from "./attachments";

const item = (kind: Attachment["kind"], name: string, size = 10, data = "x"): Attachment => ({ id: name, name, kind, size, data });

describe("attachments", () => {
  it("classifies by extension and rejects formats the runtime cannot read", () => {
    expect(classifyAttachment("shot.PNG")).toBe("image");
    expect(classifyAttachment("note.m4a")).toBe("audio");
    expect(classifyAttachment("data.csv")).toBe("text");
    expect(classifyAttachment("clip.webm")).toBeNull(); // 中转站不收 webm 音频
    expect(classifyAttachment("report.pdf")).toBeNull();
    expect(classifyAttachment("book.xlsx")).toBeNull();
    expect(classifyAttachment("pasted", "image/png")).toBe("image");
  });

  it("enforces per-kind counts and size limits", () => {
    const six = Array.from({ length: MAX_MEDIA_PER_KIND }, (_, i) => item("image", `${i}.png`));
    expect(checkAttachment({ name: "7.png", kind: "image", size: 10 }, six).ok).toBe(false);
    expect(checkAttachment({ name: "a.mp3", kind: "audio", size: 10 }, six).ok).toBe(true);
    expect(checkAttachment({ name: "huge.png", kind: "image", size: 7 * 1024 * 1024 }, []).ok).toBe(false);
    expect(checkAttachment({ name: "big.txt", kind: "text", size: 101 * 1024 }, []).ok).toBe(false);
    const nearlyFull = [item("text", "a.txt", 150 * 1024)];
    expect(checkAttachment({ name: "b.txt", kind: "text", size: 60 * 1024 }, nearlyFull).ok).toBe(false);
  });

  it("appends text attachments as named blocks and splits media into payload fields", () => {
    const attachments = [item("text", "a.md", 5, "# 标题\n```js\nx\n```"), item("image", "p.png", 5, "data:image/png;base64,AA"), item("audio", "v.mp3", 5, "data:audio/mpeg;base64,BB")];
    const text = composeMessageText("总结一下", attachments);
    expect(text.startsWith("总结一下\n\n附件 a.md：\n```")).toBe(true);
    expect(text.split("```").length).toBe(3); // 附件里的 ``` 被替换，不会提前闭合代码块
    expect(composeMessageText("", [item("text", "only.txt", 1, "hi")])).toBe("附件 only.txt：\n```\nhi\n```");
    expect(mediaPayload(attachments)).toEqual({ images: ["data:image/png;base64,AA"], audio: ["data:audio/mpeg;base64,BB"] });
  });
});
