// 定制验收：合成数据，不连引擎。?tab=connections|skills 换标签，?sel=plugin:mail 打开详情，?theme=dark 看深色。
import { createRoot } from "react-dom/client";
import { CustomizePage, type CustomizeTab } from "../src/components/CustomizePage";
import { PersonalSkillsCard } from "../src/components/PersonalSkillsCard";
import type { AssistantConnector, AssistantPlugin } from "../src/types";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";

const params = new URLSearchParams(location.search);
if (params.get("theme") === "dark") document.documentElement.dataset.theme = "dark";

const plugins: AssistantPlugin[] = [
  { name: "calendar", title: "日历（只读）", status: "available", credential_file: "~/.vortocode/connectors/calendar.token", checkable: true },
  { name: "mail", title: "邮件（读信、存草稿）", status: "available", credential_file: "~/.vortocode/connectors/mail.token", checkable: true },
  { name: "notion", title: "Notion（只读）", status: "missing", credential_file: "~/.vortocode/connectors/notion.token", checkable: true },
  { name: "caldav", title: "日历写入（建日程）", status: "invalid", credential_file: "~/.vortocode/connectors/caldav.token", checkable: true },
];
const connectors: AssistantConnector[] = [{
  name: "github", provider: "github", read_only: true, connected: false, expired: false, credential_status: "missing",
  credential_available: false, authorizing: params.has("authorizing") ? { user_code: "WDJB-MJHT", verification_uri: "https://example.invalid", expires_at: 0 } : null, error: "",
}];

createRoot(document.getElementById("root")!).render(
  <div style={{ display: "grid", gridTemplateColumns: "256px minmax(0, 1fr)", height: "100vh", background: "var(--vc-bg)" }}>
    <div style={{ background: "var(--vc-sidebar)" }} />
    <CustomizePage plugins={plugins} checks={{ mail: { ok: true, status: "available", detail: "能读到收件箱，3 封未读（合成）" } }} busy=""
      onCheck={() => {}} onRemove={() => {}} onSave={async () => true} connectors={connectors} onConnector={() => {}}
      skills={<PersonalSkillsCard client={null} connection="disconnected" />} connected
      initialTab={(params.get("tab") as CustomizeTab) || "plugins"} initialSelected={params.get("sel") || ""} />
  </div>);
