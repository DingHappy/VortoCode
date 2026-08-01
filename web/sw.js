// PWA service worker（P3）——**只为"可安装"存在，刻意什么都不缓存**。
//
// 为什么不做离线缓存：这是一个实时控制台（WebSocket + 权威快照），离线壳除了
// 展示一具连不上的尸体没有任何用；而 SW 缓存的每一条都是未来的"改了代码为什么
// 浏览器还是旧的"排查成本（本仓已经为 relay 侧前缀缓存/路由契约维护过足够多的
// 一致性纪律，不再新增一层）。Chrome 的安装条件只要求 fetch 监听器存在。
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => { /* 全部走网络，绝不拦截 */ });
