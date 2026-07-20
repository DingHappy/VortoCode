import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// 桌面端的协议版本必须跟 runtime 的 src/gateway/protocol.py 对齐，否则握手直接不兼容。
// 这里在配置阶段把 runtime 的常量读出来注入成编译期常量，测试里用它做漂移守卫——
// 只读一个版本号，不是靠查源码子串去"证明"某段逻辑存在。
function runtimeProtocolVersion(): string {
  const path = fileURLToPath(new URL("../src/gateway/protocol.py", import.meta.url));
  const matched = readFileSync(path, "utf8").match(/^PROTOCOL_VERSION\s*=\s*(\d+)\s*$/m);
  if (!matched) {
    throw new Error("读不到 runtime 的 PROTOCOL_VERSION：src/gateway/protocol.py 结构变了");
  }
  return matched[1];
}

// 纯逻辑单测：node 环境，不起 dev server、不联网、不开真实 WebSocket。
// 这是它能挂进离线门禁（npm run check:ci / scripts/ci-local.sh）的前提。
export default defineConfig({
  define: {
    __RUNTIME_PROTOCOL_VERSION__: runtimeProtocolVersion(),
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    // 兜底：脚本里一律用 `vitest run`，裸 `vitest` 是 watch 模式会把门禁挂死。
    // 万一有人漏了 run，这里也不会进 watch。
    watch: false,
  },
});
