export const app = document.querySelector<HTMLElement>("#app")!;
export function el<K extends keyof HTMLElementTagNameMap>(tag: K, text = "", cls = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag); if (text) node.textContent = text; if (cls) node.className = cls; return node;
}
export function button(label: string, action: () => void, cls = "", disabled = false) {
  const node = el("button", label, cls); node.type = "button"; node.disabled = disabled;
  node.addEventListener("click", action); return node;
}
export function field(label: string, value = "", type = "text") {
  const node = el("label", label, "field"), input = el("input"); input.type = type; input.value = value;
  node.append(input); return { node, input };
}
export function base(title: string, subtitle: string, manage = false) {
  const focus = document.activeElement?.getAttribute("data-action");
  app.replaceChildren(); app.className = manage ? "manage" : "";
  const header = el("header"), img = el("img"), identity = el("div"); img.src = "/static/mobile/vorto.png"; img.alt = "";
  identity.append(el("strong", "VortoCode"), el("small", subtitle)); header.append(img, identity);
  app.append(header, el("h1", title));
  if (focus) requestAnimationFrame(() => app.querySelector<HTMLElement>(`[data-action="${focus}"]`)?.focus());
}
export function notice(text: string) { const node = el("p", text, "notice"); node.setAttribute("role", "status"); app.append(node); }
export function card(title: string, copy?: string) {
  const node = el("section", "", "card"); node.append(el("h2", title)); if (copy) node.append(el("p", copy)); return node;
}
export function date(seconds: number) { return new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false }); }
