import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import type { AssistantPlugin } from "../types";
import { CustomizePage } from "./CustomizePage";

const plugins: AssistantPlugin[] = [
  { name: "calendar", title: "日历（只读）", status: "available", credential_file: "~/.vortocode/connectors/calendar.token", checkable: true },
  { name: "notion", title: "Notion（只读）", status: "missing", credential_file: "~/.vortocode/connectors/notion.token", checkable: true },
  { name: "mail", title: "邮件（读信、存草稿）", status: "invalid", credential_file: "~/.vortocode/connectors/mail.token", checkable: true },
];

const page = (patch: Partial<Parameters<typeof CustomizePage>[0]> = {}) => renderToStaticMarkup(
  <CustomizePage plugins={plugins} checks={{}} busy="" onCheck={() => {}} onRemove={() => {}} onSave={async () => true}
    connectors={[]} onConnector={() => {}} skills={<p>个人技能卡</p>} connected {...patch} />);

it("三个标签；插件是卡片网格，写出状态，没选中时不出详情", () => {
  const html = page();
  expect(html).toContain("<h1>定制</h1>");
  expect(html.match(/role="tab"/g)).toHaveLength(3);
  expect(html).toContain('aria-selected="true"');
  expect(html.match(/vc-customize-card/g)).toHaveLength(3);
  expect(html).toContain("已接上");
  expect(html).toContain("未接入");
  expect(html).toContain("凭据文件不可用");
  expect(html).not.toContain("vc-customize-detail");
  expect(page({ busy: "calendar" })).toContain("处理中…");
});

it("接上的插件：详情里能检查、更换、移除，检查结果照实写", () => {
  const html = page({ initialSelected: "plugin:calendar", checks: { calendar: { ok: true, status: "available", detail: "能读到，共 12 条日程" } } });
  expect(html).toContain("vc-customize-detail");
  expect(html).toContain(">检查连接</button>");
  expect(html).toContain(">更换凭据</button>");
  expect(html).toContain(">移除凭据</button>");
  expect(html).toContain("能读到，共 12 条日程");
  expect(html).not.toContain("<input");                       // 输入框要点了才出现
});

it("没接的插件：给「填写凭据」主按钮，说凭据放哪；不给移除和检查", () => {
  const html = page({ initialSelected: "plugin:notion" });
  expect(html).toContain('vc-btn--primary">填写凭据</button>');
  expect(html).toContain("<code>~/.vortocode/connectors/notion.token</code>");
  expect(html).not.toContain(">移除凭据</button>");
  expect(html).not.toContain(">检查连接</button>");
});

it("凭据坏了：不给检查，失败的检查结果用 alert 报", () => {
  const html = page({ initialSelected: "plugin:mail", checks: { mail: { ok: false, status: "invalid", detail: "凭据文件不可用" } } });
  expect(html).not.toContain(">检查连接</button>");
  expect(html).toContain('role="alert"');
  expect(html).toContain(">移除凭据</button>");
});

it("每个动作照插件的实际能力写：邮件能读、存草稿要问你、发送不支持", () => {
  const html = page({ initialSelected: "plugin:mail" });
  expect(html).toContain("读收件箱");
  expect(html).toContain("每次问你");
  expect(html).toContain("发出去收不回");
  expect(html).toContain("不支持");
  expect(html).not.toContain("<select");                      // 改不了的就不给开关
});

it("技能标签放个人技能卡；没连上时写明是上次读到的", () => {
  expect(page({ initialTab: "skills" })).toContain("个人技能卡");
  expect(page({ connected: false })).toContain("上次读到的状态");
  expect(page({ plugins: [], connected: false })).toContain("连上助理后显示可用的插件");
});
