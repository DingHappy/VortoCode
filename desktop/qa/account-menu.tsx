// 头像菜单验收：侧栏底部账号行展开菜单，合成账号数据。?theme=dark 看深色。
import { createRoot } from "react-dom/client";
import { SidebarFooter } from "../src/components/SidebarFooter";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/styles/shell.css";

if (new URLSearchParams(location.search).get("theme") === "dark") document.documentElement.dataset.theme = "dark";

createRoot(document.getElementById("root")!).render(
  <div style={{ display: "grid", gridTemplateColumns: "256px minmax(0, 1fr)", height: "100vh", background: "var(--vc-bg)" }}>
    <aside className="sidebar vc-sidebar" style={{ display: "flex", flexDirection: "column", background: "var(--vc-sidebar)" }}>
      <SidebarFooter identity={{ initial: "S", name: "someone@example.com", detail: "中度会员", title: "VortoCode 账号" }}
        update={null} resident={{ state: "online", name: "常驻电脑", onClick: () => {} }} onOpenSettings={() => {}} menuOpen />
    </aside>
    <div />
  </div>);
