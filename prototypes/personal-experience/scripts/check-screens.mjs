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
    "channel",
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
  globalThis.location = {
    search: "?screen=chat&theme=dark&example=conversation",
  };
  const conversation = renderToString(React.createElement(App));
  assert.match(
    conversation,
    /09:02/,
    "the example conversation renders message metadata",
  );
  assert.match(
    conversation,
    /本周资料摘要/,
    "the example renders the document handoff",
  );
  assert.match(
    conversation,
    /本次对话/,
    "the example renders its optional details",
  );
  console.log("PASS conversation example");
} finally {
  globalThis.location = previousLocation;
  await server.close();
}
