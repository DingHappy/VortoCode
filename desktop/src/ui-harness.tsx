/**
 * 设计变量和基础组件的预览页（**开发用，不进生产包**）。
 *
 *     npm run dev  →  http://localhost:1420/ui-harness.html
 *
 * 只加载 styles/ 下的新样式、不加载 App.css，看到的就是组件本身的样子。
 * 每迁一屏前后用它截图对照 docs/DESIGN_SYSTEM.md。
 */
import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { CalendarDays, Clock3, Folder, ListChecks, MessageSquare, Plus, Puzzle, Settings, X } from "lucide-react";

import "./styles/tokens.css";
import "./styles/components.css";
import {
  Button,
  Card,
  EmptyState,
  GroupTitle,
  IconButton,
  ListRow,
  Menu,
  MenuItem,
  MenuSeparator,
  NavItem,
  SelectableCard,
  StatusPill,
  Switch,
  Tag,
  type UiStatus,
} from "./components/ui";
import { applyThemePreference, type ThemePreference } from "./lib/theme";
import { GoalProgressHarness } from "./goal-progress-harness";

const COLOR_ROLES = [
  "bg", "sidebar", "surface", "selected", "fill", "hover", "border", "divider",
  "text", "text-secondary", "muted", "ink", "accent", "accent-bg",
  "wait-text", "wait-bg", "wait-dot", "danger-text", "danger-bg", "danger-border",
  "done-text", "done-bg", "unknown-text", "unknown-bg",
];
const SIZES = ["greeting", "title", "heading", "chat", "body", "small", "caption"];
const STATUSES: UiStatus[] = ["wait", "busy", "done", "unknown", "failed"];

const section = { marginBottom: "var(--vc-space-10)" } as const;
const heading = {
  margin: "0 0 var(--vc-space-3)",
  fontSize: "var(--vc-size-heading)",
  fontWeight: "var(--vc-weight-strong)",
} as const;

// ?theme=dark 直接打开深色，方便无界面截图。
const initialTheme: ThemePreference = new URLSearchParams(location.search).get("theme") === "dark" ? "dark" : "light";
applyThemePreference(initialTheme);
document.body.style.margin = "0";

function Harness() {
  const [theme, setTheme] = useState<ThemePreference>(initialTheme);
  const [nav, setNav] = useState("对话");
  const [calendar, setCalendar] = useState(true);
  const [look, setLook] = useState("浅色");

  const switchTheme = (next: ThemePreference) => {
    setTheme(next);
    applyThemePreference(next);
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        gridTemplateColumns: "var(--vc-sidebar-w) minmax(0, 1fr)",
        background: "var(--vc-bg)",
        color: "var(--vc-text)",
        fontFamily: "var(--vc-font-sans)",
        fontSize: "var(--vc-size-body)",
      }}
    >
      <aside style={{ background: "var(--vc-sidebar)", padding: "var(--vc-space-3)" }}>
        {[
          ["对话", <MessageSquare key="i" />],
          ["任务", <ListChecks key="i" />],
          ["定时", <Clock3 key="i" />],
          ["资料库", <Folder key="i" />],
          ["定制", <Puzzle key="i" />],
        ].map(([label, icon]) => (
          <NavItem
            key={label as string}
            icon={icon}
            label={label}
            meta={label === "任务" ? "2" : undefined}
            current={nav === label}
            onClick={() => setNav(label as string)}
          />
        ))}
        <GroupTitle>最近</GroupTitle>
        <NavItem label="整理下周日程" />
        <NavItem label="看看 Codex 卡在哪" />
      </aside>

      <main style={{ padding: "var(--vc-space-8) var(--vc-page-pad)", maxWidth: 960 }}>
        <div style={{ display: "flex", gap: "var(--vc-space-2)", marginBottom: "var(--vc-space-8)" }}>
          {(["light", "dark"] as const).map((value) => (
            <Button key={value} variant={theme === value ? "primary" : "secondary"} onClick={() => switchTheme(value)}>
              {value === "light" ? "浅色" : "深色"}
            </Button>
          ))}
        </div>

        <section style={section}>
          <h2 style={heading}>颜色</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: "var(--vc-space-3)" }}>
            {COLOR_ROLES.map((role) => (
              <div key={role} style={{ fontSize: "var(--vc-size-caption)", color: "var(--vc-muted)" }}>
                <div
                  style={{
                    height: 40,
                    marginBottom: "var(--vc-space-1)",
                    border: "1px solid var(--vc-border)",
                    borderRadius: "var(--vc-radius-control)",
                    background: `var(--vc-${role})`,
                  }}
                />
                {role}
              </div>
            ))}
          </div>
        </section>

        <section style={section}>
          <h2 style={heading}>字号</h2>
          {SIZES.map((size) => (
            <div
              key={size}
              style={{
                fontSize: `var(--vc-size-${size})`,
                fontFamily: size === "greeting" ? "var(--vc-font-serif)" : undefined,
                marginBottom: "var(--vc-space-2)",
              }}
            >
              {size === "greeting" ? "晚上好，今天想先做什么？" : `${size} · 帮你盯着 Codex，等你点头再发`}
            </div>
          ))}
        </section>

        <section style={section}>
          <h2 style={heading}>按钮</h2>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--vc-space-2)", alignItems: "center" }}>
            <Button variant="primary">发送</Button>
            <Button>修改</Button>
            <Button variant="text">不发</Button>
            <Button variant="danger">删除这条定时</Button>
            <Button variant="primary" size="lg" icon={<Plus />}>新对话</Button>
            <Button disabled>不可用</Button>
            <IconButton label="设置" icon={<Settings />} />
            <IconButton label="关闭" icon={<X />} />
          </div>
        </section>

        <section style={section}>
          <h2 style={heading}>状态</h2>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--vc-space-2)" }}>
            {STATUSES.map((status) => <StatusPill key={status} status={status} dot />)}
            <StatusPill status="wait">等你点头</StatusPill>
            <StatusPill status="busy">第 2/5 步</StatusPill>
            <StatusPill status="done">已送达</StatusPill>
            <Tag>日历</Tag>
          </div>
        </section>

        <section style={section}>
          <h2 style={heading}>列表与卡片</h2>
          <Card>
            <ul className="vc-list">
              <ListRow lead={<CalendarDays />} title="读取下周日程" description="来自日历插件 · 2 分钟前" trail={<StatusPill status="done" />} />
              <ListRow lead={<MessageSquare />} title="提醒 Codex 补测试" description="VortoCode · 等你决定发不发" trail={<StatusPill status="wait" />} />
              <ListRow lead={<ListChecks />} title="整理赚钱方向的调研" description="常驻助理 · 已用 12 分钟" trail="12 分钟" />
            </ul>
          </Card>
          <div style={{ display: "flex", gap: "var(--vc-space-3)", marginTop: "var(--vc-space-4)" }}>
            {["浅色", "深色", "跟随系统"].map((option) => (
              <SelectableCard key={option} selected={look === option} onClick={() => setLook(option)} style={{ width: 140 }}>
                {option}
              </SelectableCard>
            ))}
          </div>
        </section>

        <section style={section}>
          <h2 style={heading}>菜单、开关、空状态</h2>
          <div style={{ display: "flex", gap: "var(--vc-space-8)", alignItems: "flex-start" }}>
            <Menu style={{ width: 240 }}>
              <MenuItem icon={<Plus />} label="添加文件或照片" />
              <MenuItem icon={<CalendarDays />} label="日历" />
              <MenuSeparator />
              <MenuItem icon={<Puzzle />} label="管理插件" />
            </Menu>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--vc-space-3)" }}>
              <label style={{ display: "flex", alignItems: "center", gap: "var(--vc-space-3)" }}>
                <Switch checked={calendar} label="日历" onChange={setCalendar} /> 日历 {calendar ? "已开启" : "已关闭"}
              </label>
              <EmptyState message="还没有定时任务。" action={<Button icon={<Plus />}>新建定时</Button>} />
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {new URLSearchParams(location.search).get("view") === "goal-progress" ? <GoalProgressHarness /> : <Harness />}
  </StrictMode>,
);
