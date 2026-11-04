import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { TeamNoteActions } from "./TeamNoteActions";

const card = { kind: "team" as const, id: "task-1", status: "review" };
const render = (status?: Parameters<typeof TeamNoteActions>[0]["status"], connected = true) => renderToStaticMarkup(
  <TeamNoteActions card={card} status={status} connected={connected} onOpen={() => {}} onAccept={async () => {}} />);

it("a delivered team offers open, accept and return", () => {
  const html = render("review");
  for (const text of ["打开群聊", "验收通过", "退回…"]) expect(html).toContain(text);
});

it("follows the latest status: once accepted only the chat link stays", () => {
  const html = render("done");
  expect(html).toContain("你已验收"); expect(html).toContain("打开群聊"); expect(html).not.toContain("验收通过");
});

it("without a fresh status it trusts the note; offline it cannot accept", () => {
  expect(render(undefined)).toContain("验收通过");
  const offline = render("review", false);
  expect(offline).toContain("连接中断"); expect(offline).toMatch(/<button disabled="">验收通过/);
});
