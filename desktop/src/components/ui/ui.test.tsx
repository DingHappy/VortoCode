import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Button, IconButton, NavItem, SelectableCard, STATUS_WORDS, StatusPill, Switch } from ".";

describe("ui primitives", () => {
  it("renders real buttons that never submit forms by accident", () => {
    const html = renderToStaticMarkup(<Button variant="primary">发送</Button>);
    expect(html).toContain('type="button"');
    expect(html).toContain("vc-btn vc-btn--primary");
  });

  it("labels icon-only buttons for screen readers and hover", () => {
    const html = renderToStaticMarkup(<IconButton label="关闭" icon={<svg />} />);
    expect(html).toContain('aria-label="关闭"');
    expect(html).toContain('title="关闭"');
  });

  it("uses one word per status across screens", () => {
    expect(STATUS_WORDS).toEqual({ wait: "等你", busy: "在忙", done: "办好了", unknown: "无法确认", failed: "失败" });
    expect(renderToStaticMarkup(<StatusPill status="unknown" />)).toContain("无法确认");
    const custom = renderToStaticMarkup(<StatusPill status="busy">第 2/5 步</StatusPill>);
    expect(custom).toContain("vc-pill--busy");
    expect(custom).toContain("第 2/5 步");
  });

  it("exposes on/off state on switches and selection on cards", () => {
    const on = renderToStaticMarkup(<Switch checked label="日历" onChange={() => {}} />);
    expect(on).toContain('role="switch"');
    expect(on).toContain('aria-checked="true"');
    expect(renderToStaticMarkup(<SelectableCard selected>浅色</SelectableCard>)).toContain('aria-pressed="true"');
  });

  it("marks only the current nav item", () => {
    expect(renderToStaticMarkup(<NavItem label="任务" current />)).toContain('aria-current="page"');
    expect(renderToStaticMarkup(<NavItem label="定时" />)).not.toContain("aria-current");
  });
});
