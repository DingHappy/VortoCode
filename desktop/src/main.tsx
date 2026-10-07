import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { applyThemePreference, readThemePreference } from "./lib/theme";

// 渲染前套用外观偏好，避免启动时先闪一下错误的主题。
applyThemePreference(readThemePreference());

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
