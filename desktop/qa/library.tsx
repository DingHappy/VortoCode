// 资料库验收：合成数据，不连引擎。?theme=dark 看深色；点一行打开页内预览。
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ArtifactCenter } from "../src/components/ArtifactCenter";
import type { ArtifactMeta } from "../src/types";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/styles/shell.css";

const pad = (value: number) => String(value).padStart(2, "0");
const local = (daysAgo: number, hour: number) => {
  const date = new Date(); date.setDate(date.getDate() - daysAgo); date.setHours(hour, 12, 0, 0);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(hour)}:12:00`;
};
const items: ArtifactMeta[] = [
  { id: "brief", title: "今日早报 · 合成示例", kind: "markdown", version: 1, created_at: local(0, 9), updated_at: local(0, 9), bytes: 6400, url: "" },
  { id: "plan", title: "本周发布计划（合成）", kind: "html", version: 3, created_at: local(0, 8), updated_at: local(0, 11), bytes: 18200, url: "" },
  { id: "compare", title: "三款笔记软件对比", kind: "html", version: 1, created_at: local(1, 20), updated_at: local(1, 20), bytes: 24100, url: "" },
  { id: "trip", title: "周末短途路线草案", kind: "markdown", version: 2, created_at: local(4, 15), updated_at: local(4, 16), bytes: 5300, url: "" },
  { id: "old", title: "九月账单整理", kind: "html", version: 1, created_at: local(40, 10), updated_at: local(40, 10), bytes: 9800, url: "" },
];
const preview = `<!doctype html><html><body style="font-family: -apple-system, sans-serif; padding: 28px; color: #222">
<h1 style="font-size: 22px">本周发布计划（合成）</h1><p>这是一份合成的预览内容，用来看资料库页内预览的版式。</p>
<ul><li>周二：合并设计迁移第 4 步</li><li>周三：发版并在装好的 App 里核对</li></ul></body></html>`;

if (new URLSearchParams(location.search).get("theme") === "dark") document.documentElement.dataset.theme = "dark";

function Harness() {
  const [selected, setSelected] = useState("");
  const empty = new URLSearchParams(location.search).has("empty");
  return <div style={{ display: "grid", gridTemplateColumns: "256px minmax(0, 1fr)", height: "100vh", background: "var(--vc-bg)" }}>
    <div style={{ background: "var(--vc-sidebar)" }} />
    <ArtifactCenter artifacts={empty ? [] : items} loading={false} error="" selectedId={selected}
      version={3} versions={{ id: "plan", current: 3, versions: [{ v: 1, ts: "", bytes: 1 }, { v: 2, ts: "", bytes: 1 }, { v: 3, ts: "", bytes: 1 }] }}
      previewLoading={false} previewHtml={preview} onRefresh={() => {}} onOpen={setSelected} onVersion={() => {}} onOpenExternal={() => {}} />
  </div>;
}

createRoot(document.getElementById("root")!).render(<Harness />);
