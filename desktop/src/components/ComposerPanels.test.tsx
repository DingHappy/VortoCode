import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import type { PromptQueueItem, TrustStatus } from "../types";
import { AttachmentChips, ComposerModelPicker, ConfirmCard, ContextChips, PromptQueuePanel, TrustChip, WorkspaceRequestCard } from "./ComposerPanels";

const noop = () => {};
// runtime 发来的两种计划授权请求的开头（与 lib/onboarding 识别的一致）
const PLAN_EXECUTION_CONFIRMATION_PREFIX = "plan 阶段分析已完成，需要你授权才能动手。";
const PLAN_BUDGET_CONFIRMATION_PREFIX = "plan 阶段预算已到，尚未形成可审阅计划；继续需要授权。";
const card = (text: string, tainted = false) => renderToStaticMarkup(
  <ConfirmCard confirm={{ id: "c1", text, tainted }} trust={null} trustBusy={false} connected
    onTrust={noop} onCheck={noop} onAnswer={noop} />);

it("names what is being approved, and the taint warning always wins", () => {
  expect(card("删除 build/ 目录？")).toContain("Runtime 请求确认");
  expect(card("删除 build/ 目录？")).toContain("允许一次");
  expect(card(`${PLAN_EXECUTION_CONFIRMATION_PREFIX} 改登录`)).toContain("计划已就绪，授权后在隔离工作区继续");
  expect(card(`${PLAN_EXECUTION_CONFIRMATION_PREFIX} 改登录`)).toContain("授权继续");
  expect(card(`${PLAN_BUDGET_CONFIRMATION_PREFIX} 改登录`)).toContain("规划未完成，授权后继续当前任务");
  const tainted = card(`${PLAN_EXECUTION_CONFIRMATION_PREFIX} 改登录`, true);
  expect(tainted).toContain("外部内容回合需要人工确认"); expect(tainted).toContain("允许一次");
  expect(tainted).toContain("tainted"); expect(tainted).not.toContain("授权继续");
});

it("workspace request offers the matching switch", () => {
  const scratch = renderToStaticMarkup(<WorkspaceRequestCard request={{ scope: "scratch", reason: "要写临时文件", task: "" }}
    onDecline={noop} onAccept={noop} />);
  expect(scratch).toContain("这个任务需要隔离 Scratch"); expect(scratch).toContain("创建 Scratch"); expect(scratch).not.toContain("<code>");
  const project = renderToStaticMarkup(<WorkspaceRequestCard request={{ scope: "project", reason: "要改代码", task: "修登录" }}
    onDecline={noop} onAccept={noop} />);
  expect(project).toContain("选择项目"); expect(project).toContain("<code>修登录</code>");
});

it("queue shows four items and counts the rest", () => {
  const items = Array.from({ length: 6 }, (_, index) => ({ id: `q${index}`, version: 1, text: `第 ${index + 1} 条`,
    mode: "build", position: index, created_at: "", context_count: index === 0 ? 2 : 0 })) as PromptQueueItem[];
  const html = renderToStaticMarkup(<PromptQueuePanel items={items} paused={false} onRunNow={noop} onRemove={noop} />);
  expect(html).toContain("接下来"); expect(html).toContain("第 4 条"); expect(html).not.toContain("第 5 条");
  expect(html).toContain("还有 2 条"); expect(html).toContain("2 个引用");
  expect(renderToStaticMarkup(<PromptQueuePanel items={items.slice(0, 1)} paused onRunNow={noop} onRemove={noop} />)).toContain("已暂停");
});

it("model picker: resident model, not connected yet, or the choice list", () => {
  const base = { checked: true, choices: [], value: "", profile: null, onChoose: noop, onManage: noop };
  expect(renderToStaticMarkup(<ComposerModelPicker {...base} onResident residentModel="mimo-v2" />)).toContain("mimo-v2");
  expect(renderToStaticMarkup(<ComposerModelPicker {...base} onResident={false} />)).toContain("未连接模型");
  expect(renderToStaticMarkup(<ComposerModelPicker {...base} onResident={false} checked={false} />)).toBe("");
  const choices = [{ value: "auto", label: "自动", hint: "按任务挑" }, { value: "m1", label: "模型一", hint: "快", badges: ["看图"] }];
  const html = renderToStaticMarkup(<ComposerModelPicker {...base} onResident={false} choices={choices} value="m1" />);
  expect(html).toContain('class="composer-model-name">模型一'); expect(html).toContain("模型一 · 快 · 看图");
  expect(html).toContain("管理模型、添加本机或线上服务");
});

it("attachment chips show image thumbnails and named removers; nothing renders when empty", () => {
  const html = renderToStaticMarkup(<AttachmentChips onRemove={noop} items={[
    { id: "a", name: "shot.png", kind: "image", size: 1, data: "data:image/png;base64,AA" },
    { id: "b", name: "notes.md", kind: "text", size: 1, data: "x" },
  ]} />);
  expect(html).toContain('src="data:image/png;base64,AA"');
  expect(html).toContain("移除附件 notes.md");
  expect(renderToStaticMarkup(<AttachmentChips items={[]} onRemove={noop} />)).toBe("");
});

it("context chips label line ranges", () => {
  const html = renderToStaticMarkup(<ContextChips onRemove={noop} items={[{ path: "src/a.ts" }, { path: "src/b.ts", startLine: 2, endLine: 4 }]} />);
  expect(html).toContain("src/b.ts:L2–L4");
  expect(html).toContain("移除 src/a.ts");
  expect(renderToStaticMarkup(<ContextChips items={[]} onRemove={noop} />)).toBe("");
});

it("trust chip hides with one level and shows the effective level when the chosen one is capped", () => {
  const trust = (patch: Partial<TrustStatus>) => ({ level: "full", effective: "reads", levels: ["ask", "reads"], ...patch }) as TrustStatus;
  const capped = renderToStaticMarkup(<TrustChip trust={trust({})} busy={false} onChange={noop} />);
  expect(capped).toContain('<option value="reads" selected="">只读自动</option>');
  expect(renderToStaticMarkup(<TrustChip trust={trust({ levels: ["ask"] })} busy={false} onChange={noop} />)).toBe("");
  expect(renderToStaticMarkup(<TrustChip trust={null} busy={false} onChange={noop} />)).toBe("");
});
