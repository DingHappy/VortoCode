import { useState } from "react";
import { createRoot } from "react-dom/client";
import { TurnTimeline } from "../src/components/TurnTimeline";
import { finishRunningActivities, hydrateActivities, protocolActivity, upsertActivity } from "../src/protocol/activities";
import "../src/styles/tokens.css";
import "../src/App.css";
import type { ProtocolEvent } from "../src/types";

const initial = () => hydrateActivities([
  { type: "agent_tool", id: "read", rid: "fixture", name: "read_file", summary: "读取项目配置", status: "succeeded", args: { path: "src/settings.json" }, result: '{"theme":"system"}', duration_ms: 125 },
  { type: "agent_tool", id: "check", rid: "fixture", name: "run_command", summary: "检查项目", status: "running", args: { command: "npm run check" } },
]);

function Fixture() {
  const [items, setItems] = useState(initial);
  const [active, setActive] = useState(true);
  const [output, setOutput] = useState(false);
  const [round, setRound] = useState(0);
  const [narrow, setNarrow] = useState(false);
  const event = (fields: Partial<ProtocolEvent>) => setItems(previous => upsertActivity(previous,
    protocolActivity({ type: "agent_tool", rid: "fixture", ...fields } as ProtocolEvent)!));
  const finish = () => { setActive(false); setOutput(true); };
  return <main style={{ padding: 32, background: "var(--vc-bg)", color: "var(--vc-text)", height: "100%", overflow: "auto" }}>
    <div style={{ maxWidth: 760, margin: "0 auto" }}>
      <h1>工具调用与结果 · 合成数据</h1>
      <p>使用生产组件和事件映射；不连接模型、不执行命令。</p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 24 }}>
        <button onClick={() => { event({ id: "check", name: "run_command", summary: "检查项目", status: "succeeded", result: "命令退出码 0。检查通过。", duration_ms: 2400 }); finish(); }}>明确成功回执</button>
        <button onClick={() => { event({ id: "check", name: "run_command", summary: "检查项目", status: "failed", result: "命令退出码 1。\n输出尾部：\nsrc/settings.ts:42 参数类型不匹配", duration_ms: 2400 }); finish(); }}>失败回执</button>
        <button onClick={() => { event({ id: "write", name: "write_file", summary: "更新项目配置", status: "blocked", args: { path: "src/settings.json" }, result: "本次写入未获授权，未执行。" }); }}>未执行回执</button>
        <button onClick={() => { setItems(previous => finishRunningActivities(previous, "fixture", "completed")); finish(); }}>结束但缺少回执</button>
        <button onClick={() => { event({ id: "check", name: "run_command", summary: "检查项目", status: "cancelled", result: "工具执行已取消" }); finish(); }}>取消回执</button>
        <button onClick={() => { setItems(initial()); setActive(true); setOutput(false); setRound(value => value + 1); }}>开始新回合</button>
        <button onClick={() => setNarrow(value => !value)}>切换窄区域</button>
        <button onClick={() => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; }}>切换主题</button>
      </div>
      <section style={{ maxWidth: narrow ? 340 : undefined }}>
        <TurnTimeline key={round} items={items} active={active} hasOutput={output} />
        {output && <p>合成最终回复：请根据上方调用回执核对结果。</p>}
      </section>
    </div>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
