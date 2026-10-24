import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { createBuildInfo } from "../scripts/build-info.mjs";

/** Standalone production assets for the fixture; never overwrite Desktop dist. */
export default defineConfig({
  define: { __DESKTOP_BUILD_INFO__: JSON.stringify(createBuildInfo(new URL("../", import.meta.url).pathname)) },
  root: new URL("../", import.meta.url).pathname,
  plugins: [react()],
  resolve: { alias: { "@tauri-apps/plugin-http": new URL("browser-http.ts", import.meta.url).pathname } },
  build: {
    outDir: "qa/dist",
    rollupOptions: { input: { daily: new URL("daily.html", import.meta.url).pathname, workbench: new URL("workbench.html", import.meta.url).pathname, trust: new URL("trust.html", import.meta.url).pathname, content: new URL("content.html", import.meta.url).pathname, sessionInfo: new URL("session-info.html", import.meta.url).pathname, replies: new URL("replies.html", import.meta.url).pathname, mcp: new URL("mcp.html", import.meta.url).pathname, board: new URL("task-board.html", import.meta.url).pathname, settings: new URL("settings.html", import.meta.url).pathname, settingsMember: new URL("settings-member.html", import.meta.url).pathname, goalRecovery: new URL("goal-recovery.html", import.meta.url).pathname, goalIntegration: new URL("goal-integration.html", import.meta.url).pathname } },
  },
});
