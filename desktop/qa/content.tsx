import { useState } from "react";
import { createRoot } from "react-dom/client";
import { PluginsPage } from "../src/components/PluginsPage";
import "../src/App.css";
function Fixture() {
  const [note, setNote] = useState("");
  return <main style={{ height: "100%", overflow: "auto", padding: 24 }}><output role="status">{note}</output>
    <PluginsPage activeScope="general" projectName="" inspector={null} onChooseProject={() => setNote("选择项目入口")} onOpenBrowserSettings={() => setNote("浏览器设置入口")} />
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
