import { createRoot } from "react-dom/client";
import { MarkdownMessage } from "../src/components/MarkdownMessage";
import { BuildVersion } from "../src/components/BuildVersion";
import "../src/App.css";

const text = "天气回复夹具（非实时数据）\n\n| 时段 | 天气 | 温度 | 风速 | 降水概率 |\n|---|---|---:|---|---:|\n|早晨|☀️ **晴**|22°C|3–7 km/h|6%|\n|中午|多云|25°C|8 km/h|6%|\n\n这是原组件的隔离渲染，不连接模型或修改会话。\n\n```text\n| 原始 | 代码 |\n|---|---|\n```\n\n| 安全 | 内容 |\n|---|---|\n|<img src=x onerror=alert(1)>|[不可执行](javascript:alert)|";
createRoot(document.getElementById("root")!).render(<main style={{ padding: 24, height: "100%", overflow: "auto" }}>
  <h1>日常回复验收</h1><BuildVersion /><BuildVersion details /><section style={{ width: 420, maxWidth: "100%" }}><MarkdownMessage text={text} /></section>
</main>);
