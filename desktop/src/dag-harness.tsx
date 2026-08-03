/**
 * DevPlanDag 的可视 harness（**开发用，不进生产包**）。
 *
 *     npm run dev  →  http://localhost:5173/dag-harness.html
 *
 * 做法学自 homerail 的 `task-canvas-harness`：给画布一个脱离主应用的入口，
 * 免得改一次渲染就要把整个 app 跑起来、还得真有一条 dev 流水线在跑。
 *
 * 样本与单测共用 `DevPlanDag.fixtures.ts`——两边看的是同一批图，
 * 不会出现「测试里过了、眼睛看到的是另一份」。
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./App.css";
import { DevPlanDag } from "./components/DevPlanDag";
import { ALL_FIXTURES } from "./components/DevPlanDag.fixtures";

function Harness() {
  // 这里本来有个深/浅切换。第一次用 harness 就发现它**毫无作用**：App.css 里
  // `prefers-color-scheme` 与 `[data-theme]` 各 0 处，第 6949 行那个顶层 `:root`
  // 无条件重声明了全部设计变量——**Desktop 对一切走变量的样式而言是单主题（浅色）**，
  // 早期的深色基底已被整体覆盖。留着切换开关等于假装有个不存在的能力，删掉。
  return (
    <div
      style={{
        minHeight: "100vh",
        padding: 24,
        background: "var(--surface-0)",
        color: "var(--accent)",
      }}
    >
      {ALL_FIXTURES.map(({ name, graph }) => (
        <section key={name} style={{ marginBottom: 34 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: 14, color: "var(--muted)" }}>{name}</h3>
          <DevPlanDag graph={graph} />
        </section>
      ))}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Harness />
  </StrictMode>,
);
