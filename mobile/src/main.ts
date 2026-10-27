import "./style.css";
import { app, el, button, field, base, notice, card, date } from "./dom.ts";
import { loadAdmin } from "./manage.ts";
import { createMobileReader, MobileClientError } from "../../packages/mobile-client/src/index.ts";
import type { ReadState, Source } from "../../packages/mobile-client/src/index.ts";
import { createPairingSession } from "../../packages/mobile-client/src/pairing.ts";
import type { Pairing } from "../../packages/mobile-client/src/pairing.ts";
import { createBrowserClient } from "../../packages/mobile-client/src/browser.ts";
import type { BrowserStatus } from "../../packages/mobile-client/src/browser.ts";

const account = createBrowserClient({ origin: location.origin, allowInsecureLoopback: true });
let accountStatus: BrowserStatus | null = null, accountRead = false;

let session: ReturnType<typeof createPairingSession> | null = null;
let pairing: Pairing | null = null, name = "我的手机", busy = false, message = "", confirmed = false;
let reader: ReturnType<typeof createMobileReader> | null = null;
let readState: ReadState = { status: "idle", snapshot: null }, phase: "account" | "connect" | "pair" | "read" = "account";
let actionId = 0, controller: AbortController | null = null;
function errorText(error: unknown) {
  if (error instanceof MobileClientError && error.code === "wrong_instance") return "助手身份发生变化，已停止连接。请回电脑端重新核对。";
  if (error instanceof MobileClientError && error.code === "unauthorized") return accountRead || phase === "account" ? "账号登录或设备授权已失效，请重新登录。" : "授权不可用。请在电脑端检查配对或设备授权。";
  return "本次连接没有取得有效回执，请检查服务后重试。";
}
function renderPhone() {
  base(phase === "read" ? "助手概览" : phase === "pair" ? "核对这次连接" : "连接你的常驻助手", "手机只读入口");
  if (message) notice(message);
  if (phase === "account") {
    app.append(card("使用同一个 VortoCode 账号", "登录与这台常驻助手相同的账号，即可读取它的概览，日常使用无需手动核对码。"));
    if (accountStatus?.ready) {
      app.append(button("登录 VortoCode 账号", () => runPhone(async signal => {
        // Recheck status to obtain a fresh context after a failed or cancelled navigation.
        accountStatus = await account.status(signal);
        const url = await account.login(signal); if (!signal.aborted) location.assign(url);
      }), "primary full", busy));
      app.append(el("p", "当前入口连接这台助手，账号下其他助手的统一列表仍在接入。", "muted"));
    } else notice(accountStatus?.message ?? (busy ? "正在检查账号连接…" : "尚未取得账号连接状态。"));
    app.append(el("p", "已有 Desktop 账号会继续沿用，不需要再注册一套账号。", "muted"));
    app.append(button("检查账号连接", () => { void checkAccount(); }, "full", busy));
    app.append(button("高级：手动连接自托管助手", () => { leavePageState(); phase = "connect"; renderPhone(); }, "full"));
  } else if (phase === "connect") {
    app.append(el("p", "先在电脑的设备管理页开放配对窗口，再从这里发起连接。", "muted"));
    app.append(card("这次允许查看", "助手名称、主聊天是否已登记，以及目标摘要。此入口不发送任务、批准事项或接管电脑。"));
    const input = field("设备名称", name); input.input.maxLength = 80; input.input.autocomplete = "off";
    input.input.addEventListener("input", () => { name = input.input.value; });
    const start = button(session ? "用原请求重试" : "发起配对", () => runPhone(async signal => {
      session ??= createPairingSession({ origin: location.origin, allowInsecureLoopback: true }, name);
      pairing = await session.start(signal); phase = "pair";
    }), "primary full", busy || !name.trim()); start.dataset.action = "start";
    input.input.addEventListener("input", () => { start.disabled = busy || !name.trim(); });
    if (session) input.input.disabled = true;
    app.append(input.node, start, button("返回账号入口", resetPhone, "full", busy));
  } else if (phase === "pair" && pairing) {
    const panel = card(pairing.status === "approved" ? "电脑已批准" : "请在电脑端核对");
    panel.append(el("p", pairing.name, "muted"), el("p", pairing.code, "code"), el("p", `配对有效至 ${date(pairing.expires_at)}`, "muted expiry"));
    panel.append(el("p", "电脑上的设备名称与核对码都应一致。批准范围仅为读取助手概览。")); app.append(panel);
    if (pairing.status === "pending" || pairing.status === "approved") {
      if (pairing.status === "approved") {
        const label = el("label", "", "row check"), check = el("input"); check.type = "checkbox"; check.checked = confirmed;
        label.append(check, el("span", "我已核对电脑和手机上的码一致")); app.append(label);
        const connect = button("读取助手概览", () => runPhone(async signal => {
          if (!confirmed || !session) return;
          const client = await session.connect(signal); phase = "read";
          reader = createMobileReader(client, state => { readState = state; if (phase === "read") renderPhone(); });
          await reader.refresh();
        }), "primary full", busy || !confirmed);
        check.addEventListener("change", () => { confirmed = check.checked; connect.disabled = busy || !confirmed; }); app.append(connect);
      } else {
        app.append(button("检查配对结果", () => runPhone(async signal => { pairing = await session!.status(signal); }), "primary full", busy));
      }
      app.append(button("取消本次配对", () => runPhone(async signal => {
        await session!.cancel(signal); session = null; pairing = null; confirmed = false; phase = "connect";
      }), "full", busy));
    } else {
      notice(({ expired: "本次配对已过期，请重新开放窗口并发起配对。", revoked: "电脑已撤销本设备授权。", rejected: "本次配对未获批准。" } as Record<string, string>)[pairing.status] ?? "本次配对已结束。");
      app.append(button("重新连接", resetPhone, "full", busy));
    }
  } else if (phase === "read") {
    const sources = readState.snapshot?.sources;
    const label = readState.status === "current" ? "接口已读取" : readState.status === "loading" ? "正在核对" : "连接待核对";
    app.append(el("span", label, `badge ${readState.status === "current" ? "connected" : "pending"}`));
    if (readState.status === "unavailable") notice(sources ? "以下保留最近读取的内容，请留意各项核对时间。" : "授权或服务当前不可用，请回电脑端检查后重新连接。");
    const identity = card(sources?.profile.data?.name ?? "助手名称待核对");
    identity.append(el("p", sources?.main_chat.data ? (sources.main_chat.data.sid ? "主聊天已登记" : "尚未登记主聊天") : "主聊天状态待核对"));
    if (sources) { identity.append(sourceTime(sources.profile)); identity.append(sourceTime(sources.main_chat, "主聊天")); }
    app.append(identity, el("h2", "目标"));
    if (sources) app.append(sourceTime(sources.goals));
    const goals = sources?.goals.data;
    if (!goals) app.append(el("p", "目标来源尚不可用，不能据此判断是否有待办。", "empty"));
    else if (!goals.items.length) app.append(el("p", "本次来源中没有个人目标。", "empty"));
    else for (const goal of goals.items) {
      const status = ({ active: "跟进中", blocked: "有阻塞", achieved: "已达成", paused: "已暂停", cancelled: "已取消" } as Record<string, string>)[goal.status] ?? "待核对";
      const item = card(goal.objective); item.append(el("span", `原记录状态：${status}`, "badge"));
      if (goal.next_action) item.append(el("p", `下一步：${goal.next_action}`));
      if (goal.blocker) item.append(el("p", `阻塞：${goal.blocker}`)); app.append(item);
    }
    if (goals?.truncated) notice("这里只显示最近 100 条目标，请到电脑端查看完整记录。");
    const refresh = button("刷新概览", () => { void reader?.refresh(); }, "primary full", readState.status === "loading"); refresh.dataset.action = "refresh";
    app.append(refresh);
    if (accountRead) {
      app.append(button("退出手机账号", () => runPhone(async signal => {
        reader?.disconnect(); reader = null;
        await account.logout(signal); accountRead = false; phase = "account";
        accountStatus = await account.status(signal);
      }), "full", busy));
      if (readState.status === "unavailable" && !sources) app.append(button("重新登录", () => { resetPhone(); void checkAccount(); }, "full", busy));
      app.append(el("p", "退出会撤销这次手机会话的读取授权，电脑上的账号不受影响。", "muted"));
    } else app.append(button("断开本页连接", resetPhone, "full"), el("p", "断开本页不会撤销电脑上已有的设备授权；需要时可在设备管理中撤销。", "muted"));
  }
  app.append(el("footer", accountRead ? "账号凭证由助手服务保管，手机使用 HttpOnly 会话 Cookie。刷新页面可继续使用；服务重启或会话到期后需重新登录。" : "默认使用同账号登录。高级手动连接用于自托管与开发验收，其凭证仅保存在本页内存。"));
}
function sourceTime<T>(source: Source<T>, label = "来源") {
  return el("small", `${label} ${source.status === "current" ? "已核对" : "历史待核对"} · ${source.checked_at ? new Date(source.checked_at).toLocaleString("zh-CN", { hour12: false }) : "尚无有效读取"}`, "source-time");
}
async function runPhone(task: (signal: AbortSignal) => Promise<void>) {
  if (busy) return; const owner = ++actionId; controller?.abort(); controller = new AbortController();
  busy = true; message = ""; renderPhone();
  try { await task(controller.signal); } catch (error) { if (owner === actionId) message = errorText(error); }
  finally { if (owner === actionId) { busy = false; renderPhone(); } }
}
function leavePageState() {
  actionId++; controller?.abort(); reader?.disconnect(); reader = null; account.disconnect();
  session = null; pairing = null; confirmed = false; busy = false; message = ""; accountRead = false;
  readState = { status: "idle", snapshot: null };
}
function resetPhone() {
  leavePageState(); phase = "account"; renderPhone();
}
async function checkAccount() {
  await runPhone(async signal => {
    const value = await account.status(signal); if (signal.aborted) return;
    accountStatus = value;
    if (!value.ready || !value.signed_in) return;
    await account.bootstrap(signal); if (signal.aborted) return;
    accountRead = true; phase = "read";
    reader = createMobileReader(account, state => { readState = state; if (phase === "read") renderPhone(); });
    await reader.refresh();
  });
}
window.addEventListener("pagehide", () => {
  if (location.pathname !== "/mobile/manage") leavePageState();
});
window.addEventListener("pageshow", event => { if (event.persisted && location.pathname !== "/mobile/manage") { phase = "account"; void checkAccount(); } });
if (location.pathname === "/mobile/manage") void loadAdmin(); else {
  if (new URL(location.href).searchParams.get("login") === "failed") {
    message = "登录未完成，请确认使用与此助手相同的账号后重试。";
    history.replaceState(null, "", "/mobile");
    renderPhone(); // Keep the callback explanation until the user retries.
    const owner = actionId;
    void account.status().then(value => { if (owner === actionId && phase === "account") { accountStatus = value; renderPhone(); } }).catch(() => {});
  } else { renderPhone(); void checkAccount(); }
}
