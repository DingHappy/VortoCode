import { app, el, button, field, base, notice, card, date } from "./dom.ts";
import type { Pairing } from "../../packages/mobile-client/src/pairing.ts";

interface Overview { instance_id: string | null; window_expires_at: number | null; pairings: Pairing[] }
interface DeviceRecord { id: string; name: string; expires_at: number; revoked_at: number | null }
let busy = false, epoch = 0, overview: Overview | null = null, devices: DeviceRecord[] = [], errorMessage = "";
async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(path, { method, credentials: "same-origin", redirect: "error", cache: "no-store",
    headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? "管理员登录已失效，或当前地址不满足 HTTPS 要求。" : "服务没有接受本次操作，请刷新核对；核对码、窗口或请求可能已变化。");
  return response.json() as Promise<T>;
}
function render() {
  base("手机设备管理", "在电脑端核对与授权", true);
  app.append(el("p", "只开放助手概览读取权限。手机不会得到管理员令牌，也不会获得聊天、批准或桌面控制能力。", "muted"));
  if (errorMessage) notice(errorMessage);
  const window = card("1. 开放配对窗口", overview?.window_expires_at ? `本次窗口有效至 ${date(overview.window_expires_at)}。重新开放会使原待确认请求失效。` : "窗口当前关闭。开放后，请在手机上访问同一服务的 /mobile。");
  const actions = el("div", "", "row");
  actions.append(button("开放 5 分钟", () => action(async () => { await request("/api/mobile/pairing-window", "POST", { enabled: true }); }), "primary", busy),
    button("关闭窗口", () => action(async () => { await request("/api/mobile/pairing-window", "POST", { enabled: false }); }), "", busy || !overview?.window_expires_at));
  window.append(actions); const link = el("a", "打开手机只读入口"); link.href = "/mobile"; link.target = "_blank"; link.rel = "noopener"; window.append(link); app.append(window);
  app.append(el("h2", "2. 核对手机请求"));
  const pending = overview?.pairings.filter(item => item.status === "pending") ?? [];
  if (!pending.length) app.append(el("p", "尚无待确认请求。手机发起后，点击刷新查看。", "empty"));
  for (const item of pending) {
    const panel = card(item.name, "请对照手机上显示的核对码，确认是你正在连接的设备。"); panel.append(el("p", item.code, "code"));
    panel.append(el("p", `有效至 ${date(item.expires_at)} · 授权有效期 30 天`, "muted"));
    const code = field("输入手机上显示的核对码"); code.input.maxLength = 9; code.input.autocomplete = "off"; code.input.spellcheck = false; panel.append(code.node);
    const approve = button("确认一致，授权只读", () => {
      const value = code.input.value;
      void action(async () => { await request(`/api/mobile/pairings/${encodeURIComponent(item.id)}/decision`, "POST", { code: value, approve: true }); });
    }, "primary", true);
    code.input.addEventListener("input", () => { approve.disabled = busy || code.input.value.replaceAll("-", "").toUpperCase() !== item.code.replaceAll("-", ""); });
    const row = el("div", "", "row"); row.append(approve, button("拒绝", () => action(async () => {
      await request(`/api/mobile/pairings/${encodeURIComponent(item.id)}/decision`, "POST", { code: item.code, approve: false });
    }), "", busy)); panel.append(row); app.append(panel);
  }
  app.append(el("h2", "已授权设备"));
  if (!devices.length) app.append(el("p", "还没有设备授权。", "empty"));
  for (const device of devices) {
    const panel = card(device.name, device.revoked_at !== null ? "授权已撤销" : `有效至 ${date(device.expires_at)}`);
    panel.append(el("p", "权限：读取助手概览", "muted"));
    if (device.revoked_at === null) {
      const revoke = button("撤销此设备…", () => {
        const confirmation = el("div", "", "notice"); confirmation.append(el("p", "撤销后，该设备下一次读取会被拒绝。"),
          button("确认撤销", () => action(async () => { await request(`/api/mobile/devices/${encodeURIComponent(device.id)}`, "DELETE"); }), "danger", busy));
        revoke.disabled = true; panel.append(confirmation);
      }, "danger", busy); panel.append(revoke);
    }
    app.append(panel);
  }
  const refresh = button("刷新设备与请求", () => { void action(async () => {}); }, "full", busy); refresh.dataset.action = "admin-refresh"; app.append(refresh);
  app.append(button("退出管理员登录", () => action(async () => { await request("/api/auth/logout", "POST"); }), "full", busy));
}
export async function loadAdmin() {
  const owner = ++epoch;
  try {
    const auth = await request<{ auth_required: boolean; authed: boolean }>("/api/auth/status");
    if (owner !== epoch) return;
    if (!auth.auth_required || !auth.authed) {
      overview = null; devices = []; base("手机设备管理", "管理员身份核对", true);
      if (errorMessage) notice(errorMessage);
      if (!auth.auth_required) { notice("服务尚未配置管理员鉴权，设备接入暂不可用。请先在服务配置中设置 API 令牌。"); return; }
      app.append(el("p", "这里使用电脑端的管理员身份。不要在手机只读页面输入管理员令牌。", "muted"));
      const login = field("管理员令牌", "", "password"); login.input.autocomplete = "current-password";
      const form = el("form"), submit = el("button", "登录设备管理", "primary full"); submit.type = "submit";
      form.append(login.node, submit); form.addEventListener("submit", event => {
        event.preventDefault(); let token = login.input.value; login.input.value = "";
        void action(async () => { const pending = request("/api/auth/login", "POST", { token }); token = ""; await pending; });
      }); app.append(form); return;
    }
    const [next, paired] = await Promise.all([request<Overview>("/api/mobile/pairings"), request<{ devices: DeviceRecord[] }>("/api/mobile/devices")]);
    if (owner !== epoch) return;
    overview = next; devices = paired.devices; render();
  } catch (error) {
    if (owner === epoch) { base("设备管理暂不可用", "连接待核对", true); notice(error instanceof Error ? error.message : "请重新连接"); app.append(button("重试", () => { void loadAdmin(); }, "full")); }
  }
}
async function action(task: () => Promise<void>) {
  if (busy) return; busy = true; errorMessage = "";
  app.querySelectorAll("button").forEach(button => { button.disabled = true; });
  try { await task(); } catch (error) { errorMessage = error instanceof Error ? error.message : "操作未取得有效回执"; }
  finally { busy = false; await loadAdmin(); }
}
window.addEventListener("pagehide", () => { epoch++; });
