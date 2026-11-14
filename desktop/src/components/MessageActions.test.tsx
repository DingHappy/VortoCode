import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { copyMessageText, MessageActions } from "./MessageActions";

it("copies the exact loaded text, including Markdown, newlines and unsafe-looking literal text", async () => {
  const text = "**资料**\n\n<script>literal text</script>\nhttps://example.com/";
  const write = vi.fn().mockResolvedValue(undefined);
  expect(await copyMessageText(text, write)).toBe("copied");
  expect(write).toHaveBeenCalledExactlyOnceWith(text);
});

it("reports clipboard failures without claiming success, and a later explicit retry can succeed", async () => {
  const write = vi.fn().mockRejectedValueOnce(new Error("Clipboard unavailable")).mockResolvedValueOnce(undefined);
  expect(await copyMessageText("已经读取的回复", write)).toBe("failed");
  expect(await copyMessageText("已经读取的回复", write)).toBe("copied");
  expect(write).toHaveBeenCalledTimes(2);
});

it("offers a keyboard-labelled local copy action only for nonempty text", () => {
  const html = renderToStaticMarkup(<MessageActions text="回复" />);
  expect(html).toContain('aria-label="复制消息"');
  expect(html).toContain('role="status"');
  expect(html).not.toContain("已复制");
  expect(renderToStaticMarkup(<MessageActions text={" \n"} />)).toBe("");
});
