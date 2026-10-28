import React from "react";
import ReactDOM from "react-dom/client";
import { isTauri } from "@tauri-apps/api/core";
// 设计变量和基础组件先于旧样式加载；两者类名、变量名互不重叠（见 styles/tokens.css）。
import "./styles/tokens.css";
import "./styles/components.css";
import App from "./App";
import { diag, installGlobalErrorLogging } from "./lib/diagnostics";
import { applyThemePreference, readThemePreference } from "./lib/theme";

// macOS 窗口没有标题栏，红黄绿三个按钮压在侧栏品牌行上（位置见 tauri.conf 的 trafficLightPosition）：
// 让样式给它们让出位置。浏览器里预览时没有这三个按钮，不让位。
if (isTauri() && navigator.userAgent.includes("Mac")) document.documentElement.dataset.window = "overlay";

// 渲染前套用外观偏好，避免启动时先闪一下错误的主题。
applyThemePreference(readThemePreference());
installGlobalErrorLogging();
diag("app.start");

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
