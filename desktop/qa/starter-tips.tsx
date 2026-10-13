import { useState } from "react";
import { createRoot } from "react-dom/client";
import { AssistantStarterTips } from "../src/components/AssistantMode";
import { starterTips } from "../src/lib/starterTips";
import "../src/App.css";
import "../src/daily.css";

// 空白助理对话的首页：?place=local 看本机通用会话的示例
function Fixture() {
  const resident = new URLSearchParams(location.search).get("place") !== "local";
  const [prompt, setPrompt] = useState("");
  const [shown, setShown] = useState(true);
  return <main className="conversation empty-state" style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
    <div className="conversation-scroll" style={{ flex: 1 }}>
      <div className="home-greeting">
        <h1>有什么要交代的？</h1>
        <p>{resident ? "交代完就可以合上电脑：它在常驻电脑上接着做，做完了在右侧「动态」里回报。" : "说一件要办的事，做完了会在右侧「动态」里回报。"}</p>
        {shown && <AssistantStarterTips tips={starterTips({ resident, projects: ["VortoCode"] })}
          onDismiss={() => setShown(false)} onPick={(tip) => setPrompt(tip.prompt)} />}
      </div>
    </div>
    <div style={{ padding: 16 }}>
      <textarea aria-label="任务输入" value={prompt} onChange={(event) => setPrompt(event.target.value)}
        style={{ width: "100%", minHeight: 72, borderRadius: 12, padding: 12, font: "inherit" }} placeholder="描述任务…" />
    </div>
  </main>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
