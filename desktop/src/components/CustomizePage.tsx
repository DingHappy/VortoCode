// 侧栏「定制」：插件 / 连接 / 技能三个标签。插件和连接是卡片网格，选中一张在右侧看详情、填凭据、检查和移除；
// 技能沿用个人技能卡（保存后须审阅再启用）。按 docs/DESIGN_SYSTEM.md 第 3 节「定制」和设计画布 E5。
// 每个动作能不能做是插件自己定的（只读 / 每次问你 / 不支持），这里只如实写出来，不提供改不了的开关。
import { BookOpen, CalendarDays, CalendarPlus, GitBranch, Mail, Plug, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { connectorCredentialView } from "../lib/connectorStatus";
import type { AssistantConnector, AssistantPlugin, PluginCheck } from "../types";
import { Button, EmptyState, IconButton, SelectableCard } from "./ui";

export type CustomizeTab = "plugins" | "connections" | "skills";
type ConnectorAction = "connect" | "disconnect" | "open" | "copy";
type Level = "read" | "ask" | "never";

const TABS: Array<{ id: CustomizeTab; label: string }> = [
  { id: "plugins", label: "插件" }, { id: "connections", label: "连接" }, { id: "skills", label: "技能" },
];

const LEVEL_WORD: Record<Level, string> = { read: "只读", ask: "每次问你", never: "不支持" };

/** 每个插件做什么、能做哪些动作：和 src/plugins/__init__.py 的注册一一对应。 */
const PLUGIN_INFO: Record<string, { icon: ReactNode; summary: string; actions: Array<{ label: string; level: Level; note?: string }>;
  input: { label: string; placeholder: string; help: string } }> = {
  calendar: {
    icon: <CalendarDays />, summary: "查日程，早报附上今天的安排，到点提醒",
    actions: [{ label: "读日程", level: "read" }],
    input: { label: "日历订阅链接", placeholder: "https://… 或 webcal://…",
      help: "Google 日历：设置 → 该日历 →「iCal 格式的私密地址」；iCloud：共享日历 → 公开日历 → 复制链接。" },
  },
  caldav: {
    icon: <CalendarPlus />, summary: "把事情排进日历，新建前先问你",
    actions: [{ label: "新建日程", level: "ask", note: "会改东西" }],
    input: { label: "邮箱:App 专用密码", placeholder: "you@icloud.com:xxxx-xxxx-xxxx-xxxx",
      help: "iCloud：appleid.apple.com → 登录与安全 → App 专用密码。其他支持 CalDAV 的日历写成 https://账号:密码@服务器；末尾加 #日历名 可指定写进哪个日历。" },
  },
  mail: {
    icon: <Mail />, summary: "未读进早报，新邮件提醒，可以存草稿",
    actions: [{ label: "读收件箱", level: "read" }, { label: "存草稿", level: "ask", note: "会改东西" },
      { label: "发送邮件", level: "never", note: "发出去收不回" }],
    input: { label: "邮箱:授权码", placeholder: "you@qq.com:授权码",
      help: "在邮箱设置里开 IMAP 并生成授权码（客户端专用密码，不是登录密码）。QQ/163/126/iCloud/Gmail 自动识别服务器，其他写成 imaps://邮箱:授权码@服务器。" },
  },
  notion: {
    icon: <BookOpen />, summary: "搜索和阅读你给它看的页面、数据库",
    actions: [{ label: "搜索、读页面", level: "read" }],
    input: { label: "Notion 集成令牌", placeholder: "ntn_…",
      help: "notion.so/profile/integrations 新建内部集成（只勾读取内容），再在要给助理看的页面「连接」里加上它。" },
  },
};

const PLUGIN_STATUS: Record<AssistantPlugin["status"], { word: string; tone: "done" | "muted" | "danger" }> = {
  available: { word: "已接上", tone: "done" }, missing: { word: "未接入", tone: "muted" },
  invalid: { word: "凭据文件不可用", tone: "danger" }, revoked: { word: "已在本机撤销", tone: "muted" },
};

const PROVIDER_NAMES: Record<string, string> = { github: "GitHub" };

type Props = {
  plugins: AssistantPlugin[];
  checks: Record<string, PluginCheck>;
  /** 正在检查、保存或移除的插件名 */
  busy: string;
  onCheck: (plugin: AssistantPlugin) => void;
  onRemove: (plugin: AssistantPlugin) => void;
  /** 保存凭据；成功返回 true，输入框随之清空收起 */
  onSave: (plugin: AssistantPlugin, credential: string) => Promise<boolean>;
  connectors: AssistantConnector[];
  onConnector: (action: ConnectorAction, connector: AssistantConnector) => void;
  /** 个人技能卡 */
  skills: ReactNode;
  connected: boolean;
  /** 起始标签，测试和验收页用 */
  initialTab?: CustomizeTab;
  initialSelected?: string;
};

export function CustomizePage({ plugins, checks, busy, onCheck, onRemove, onSave, connectors, onConnector, skills, connected,
  initialTab = "plugins", initialSelected = "" }: Props) {
  const [tab, setTab] = useState<CustomizeTab>(initialTab);
  const [selected, setSelected] = useState(initialSelected);
  const plugin = tab === "plugins" ? plugins.find((item) => `plugin:${item.name}` === selected) : undefined;
  const connector = tab === "connections" ? connectors.find((item) => `connector:${item.name}` === selected) : undefined;
  const detail = plugin
    ? <PluginDetail key={plugin.name} plugin={plugin} check={checks[plugin.name]} busy={busy === plugin.name}
        onCheck={onCheck} onRemove={onRemove} onSave={onSave} onClose={() => setSelected("")} />
    : connector ? <ConnectorDetail connector={connector} onAction={onConnector} onClose={() => setSelected("")} /> : null;
  return (
    <section className={`vc-customize${detail ? " has-detail" : ""}`}>
      <div className="vc-customize-main">
        <header className="vc-page-head">
          <div>
            <h1>定制</h1>
            <p>让助理接上你常用的东西。凭据只存在助理所在的电脑上，填完不会再显示。</p>
          </div>
        </header>
        <div className="vc-customize-tabs" role="tablist" aria-label="定制">
          {TABS.map((item) => (
            <button key={item.id} type="button" role="tab" id={`vc-customize-tab-${item.id}`} aria-selected={tab === item.id}
              aria-controls="vc-customize-panel" onClick={() => { setTab(item.id); setSelected(""); }}>{item.label}</button>
          ))}
        </div>
        <div id="vc-customize-panel" role="tabpanel" aria-labelledby={`vc-customize-tab-${tab}`}>
          {!connected && tab !== "skills" && <p className="vc-page-error" role="status">还没连上助理，下面是上次读到的状态。</p>}
          {tab === "plugins" && (plugins.length === 0
            ? <EmptyState message={connected ? "这台助理还没有可用的插件。" : "连上助理后显示可用的插件。"} />
            : <ul className="vc-customize-grid">
                {plugins.map((item) => {
                  const info = PLUGIN_INFO[item.name];
                  const status = PLUGIN_STATUS[item.status] ?? { word: item.status, tone: "muted" as const };
                  return <li key={item.name}>
                    <SelectableCard selected={selected === `plugin:${item.name}`} className="vc-customize-card"
                      onClick={() => setSelected(`plugin:${item.name}`)}>
                      <span className="vc-customize-icon" aria-hidden="true">{info?.icon ?? <Plug />}</span>
                      <span className="vc-customize-text">
                        <strong>{item.title}</strong>
                        {info && <span>{info.summary}</span>}
                        <small className={`tone-${status.tone}`}>{busy === item.name ? "处理中…" : status.word}</small>
                      </span>
                    </SelectableCard>
                  </li>;
                })}
              </ul>)}
          {tab === "connections" && (connectors.length === 0
            ? <EmptyState message={connected ? "这台助理还没有可以授权的服务。" : "连上助理后显示可以连接的服务。"} />
            : <ul className="vc-customize-grid">
                {connectors.map((item) => {
                  const view = connectorCredentialView(item);
                  return <li key={item.name}>
                    <SelectableCard selected={selected === `connector:${item.name}`} className="vc-customize-card"
                      onClick={() => setSelected(`connector:${item.name}`)}>
                      <span className="vc-customize-icon" aria-hidden="true"><GitBranch /></span>
                      <span className="vc-customize-text">
                        <strong>{PROVIDER_NAMES[item.provider] ?? item.name}</strong>
                        <span>{item.read_only ? "只读访问仓库、议题和拉取请求" : "访问仓库、议题和拉取请求"}</span>
                        <small className={view.available ? "tone-done" : item.authorizing ? "tone-wait" : "tone-muted"}>{view.label}</small>
                      </span>
                    </SelectableCard>
                  </li>;
                })}
              </ul>)}
          {tab === "skills" && <div className="vc-customize-skills">{skills}</div>}
        </div>
      </div>
      {detail}
    </section>
  );
}

function DetailHead({ icon, title, status, onClose }: { icon: ReactNode; title: string; status: ReactNode; onClose: () => void }) {
  return <header className="vc-customize-detail-head">
    <span className="vc-customize-icon large" aria-hidden="true">{icon}</span>
    <div><strong>{title}</strong><span>{status}</span></div>
    <IconButton label="关闭详情" icon={<X />} onClick={onClose} />
  </header>;
}

function PluginDetail({ plugin, check, busy, onCheck, onRemove, onSave, onClose }: {
  plugin: AssistantPlugin; check?: PluginCheck; busy: boolean;
  onCheck: (plugin: AssistantPlugin) => void; onRemove: (plugin: AssistantPlugin) => void;
  onSave: (plugin: AssistantPlugin, credential: string) => Promise<boolean>; onClose: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const info = PLUGIN_INFO[plugin.name];
  const ready = plugin.status === "available";
  const status = PLUGIN_STATUS[plugin.status] ?? { word: plugin.status, tone: "muted" as const };
  return <aside className="vc-customize-detail" aria-label={`${plugin.title}详情`}>
    <DetailHead icon={info?.icon ?? <Plug />} title={plugin.title} onClose={onClose}
      status={<span className={`tone-${status.tone}`}>{busy ? "处理中…" : status.word}</span>} />
    {info && <section>
      <h2>它能做什么</h2>
      <ul className="vc-customize-actions">
        {info.actions.map((action) => <li key={action.label}>
          <span><span>{action.label}</span>{action.note && <small className={action.level === "never" ? "tone-danger" : undefined}>{action.note}</small>}</span>
          <span className={`vc-customize-level ${action.level}`}>{LEVEL_WORD[action.level]}</span>
        </li>)}
      </ul>
      <p className="vc-customize-note">带回来的内容当外部资料处理，里面的要求不照做。</p>
    </section>}
    <section>
      <h2>凭据</h2>
      {editing ? <form className="vc-customize-form" onSubmit={(event) => {
        event.preventDefault();
        const value = draft.trim();
        if (!value) return;
        void onSave(plugin, value).then((ok) => { if (ok) { setEditing(false); setDraft(""); } });
      }}>
        <label>{info?.input.label ?? "凭据"}
          <input type="password" autoComplete="off" spellCheck={false} value={draft} autoFocus
            placeholder={info?.input.placeholder} onChange={(event) => setDraft(event.target.value)} />
        </label>
        {info && <p className="vc-customize-note">{info.input.help}</p>}
        <div className="vc-customize-buttons">
          <Button variant="secondary" onClick={() => { setEditing(false); setDraft(""); }}>取消</Button>
          <Button variant="primary" type="submit" disabled={!draft.trim() || busy}>保存并检查</Button>
        </div>
      </form> : <p className="vc-customize-note">
        {ready ? "凭据已存在助理所在的电脑上，新开对话生效。" : <>存到助理所在电脑的 <code>{plugin.credential_file}</code>（权限 600），新开对话生效。</>}
      </p>}
      {check && <p className={check.ok ? "vc-customize-note" : "vc-page-error"} role={check.ok ? undefined : "alert"}>{check.detail}</p>}
    </section>
    {!editing && <div className="vc-customize-buttons end">
      {ready && plugin.checkable && <Button variant="secondary" disabled={busy} onClick={() => onCheck(plugin)}>检查连接</Button>}
      <Button variant={ready ? "secondary" : "primary"} disabled={busy} onClick={() => { setEditing(true); setDraft(""); }}>{ready ? "更换凭据" : "填写凭据"}</Button>
      {plugin.status !== "missing" && <Button variant="danger" disabled={busy} onClick={() => onRemove(plugin)}>移除凭据</Button>}
    </div>}
  </aside>;
}

function ConnectorDetail({ connector, onAction, onClose }: {
  connector: AssistantConnector; onAction: (action: ConnectorAction, connector: AssistantConnector) => void; onClose: () => void;
}) {
  const view = connectorCredentialView(connector);
  const name = PROVIDER_NAMES[connector.provider] ?? connector.name;
  return <aside className="vc-customize-detail" aria-label={`${name}详情`}>
    <DetailHead icon={<GitBranch />} title={name} onClose={onClose}
      status={<span className={view.available ? "tone-done" : connector.authorizing ? "tone-wait" : "tone-muted"}>{view.label}</span>} />
    {connector.authorizing && <section className="vc-customize-code">
      <h2>授权</h2>
      <p className="vc-customize-note">在浏览器打开 {name} 授权页，输入下面的验证码，点 Authorize：</p>
      <code aria-label="验证码">{connector.authorizing.user_code}</code>
      <div className="vc-customize-buttons">
        <Button variant="secondary" onClick={() => onAction("copy", connector)}>复制验证码</Button>
        <Button variant="primary" onClick={() => onAction("open", connector)}>打开 {name}</Button>
      </div>
    </section>}
    {(connector.error || connector.credential_error) && !view.available &&
      <p className="vc-page-error" role="alert">{connector.error || connector.credential_error}</p>}
    <section>
      <h2>说明</h2>
      <p className="vc-customize-note">{view.available
        ? "凭据已保存。对话里用到时再连接；每次调用仍按对话的权限来。"
        : "授权后，助理才能在对话里访问它。凭据只存在助理所在的电脑上。"}</p>
      <p className="vc-customize-note">凭据可用不代表服务在线。断开以本机回执为准；{name} 那边的授权和已经发出的请求要另外核对。</p>
    </section>
    <div className="vc-customize-buttons end">
      {!view.available && !connector.authorizing && <Button variant="primary" onClick={() => onAction("connect", connector)}>授权</Button>}
      {view.canClear && <Button variant="danger" onClick={() => onAction("disconnect", connector)}>
        {connector.authorizing ? "取消授权" : connector.local_revocation_supported ? "断开本机连接" : "移除凭据"}</Button>}
    </div>
  </aside>;
}
