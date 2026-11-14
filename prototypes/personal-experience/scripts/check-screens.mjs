import assert from "node:assert/strict";
import { createServer } from "vite";
import React from "react";
import { renderToString } from "react-dom/server";

// Render the public preview routes offline. Catches branch-specific runtime errors that a bundle build misses.
const server = await createServer({
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
  optimizeDeps: { noDiscovery: true, include: [] },
  appType: "custom",
});
const previousLocation = globalThis.location;
try {
  const { App } = await server.ssrLoadModule("/src/App.jsx");
  for (const screen of [
    "download",
    "welcome",
    "where",
    "model",
    "install",
    "remote",
    "ready",
    "chat",
    "staff",
    "routines",
    "library",
    "settings",
    "approval",
    "recovery",
    "brand",
  ]) {
    globalThis.location = { search: `?screen=${screen}&theme=light` };
    const html = renderToString(React.createElement(App));
    assert.match(html, /<h1\b/, `${screen} needs a readable title`);
    assert.match(
      html,
      /设计演示/,
      `${screen} must identify the synthetic preview`,
    );
    console.log(`PASS ${screen}`);
  }
} finally {
  globalThis.location = previousLocation;
  await server.close();
}
