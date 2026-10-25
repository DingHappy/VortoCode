import { invoke } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";

export type ModelConfigurationRead = {
  schemaVersion: 1;
  savedRevision: string;
  runtimes: Array<{ runtimeId: string; scope?: string; status: "applied" | "pending" | "unknown" }>;
};

export function parseModelConfigurationRead(value: unknown): ModelConfigurationRead | null {
  if (!value || typeof value !== "object") return null;
  const read = value as ModelConfigurationRead;
  if (read.schemaVersion !== 1 || typeof read.savedRevision !== "string" || !/^[a-zA-Z0-9-]{1,96}$/.test(read.savedRevision)
    || !Array.isArray(read.runtimes) || read.runtimes.length > 12) return null;
  const ids = new Set<string>();
  for (const item of read.runtimes) {
    if (!item || typeof item.runtimeId !== "string" || !item.runtimeId || item.runtimeId.length > 160
      || ids.has(item.runtimeId) || !["applied", "pending", "unknown"].includes(item.status)
      || item.scope !== undefined && !["general", "scratch", "project"].includes(item.scope)) return null;
    ids.add(item.runtimeId);
  }
  return read;
}

export function modelConfigurationSummary(read: ModelConfigurationRead | null, revision?: string) {
  if (!read || !revision || read.savedRevision !== revision) return "正在核对已保存配置与本机引擎…";
  if (!read.runtimes.length) return "当前没有运行中的本机引擎；下次启动时使用已保存配置。";
  const applied = read.runtimes.filter(item => item.status === "applied").length;
  const pending = read.runtimes.filter(item => item.status === "pending").length;
  const unknown = read.runtimes.length - applied - pending;
  return [applied ? `${applied} 个本机引擎已确认载入当前配置` : "",
    pending ? `${pending} 个仍使用旧配置，需在对应工作区重启` : "",
    unknown ? `${unknown} 个暂时无法确认，可能尚未就绪或不支持回执` : ""].filter(Boolean).join("；") + "。";
}

/** Poll only while visible. Never preserve an old success after a failed read. */
export function ModelConfigurationState({ active, revision }: { active: boolean; revision?: string }) {
  const [read, setRead] = useState<ModelConfigurationRead | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!active) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setRead(null); setFailed(false);
    const poll = async () => {
      try {
        const next = parseModelConfigurationRead(await invoke<unknown>("get_model_configuration_state"));
        if (!next) throw new Error("Invalid model configuration receipt");
        if (alive) { setRead(next); setFailed(false); }
      } catch {
        if (alive) { setRead(null); setFailed(true); }
      } finally {
        if (alive) timer = setTimeout(() => void poll(), 5000);
      }
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [active, revision, attempt]);
  return <section className="settings-card" aria-label="模型配置生效状态">
    <div className="settings-card-head"><strong>本机引擎配置</strong><button type="button" onClick={() => setAttempt(value => value + 1)}>重新核对</button></div>
    <p role="status">{failed ? "暂时无法核对配置生效状态；已保存的配置仍保留，请稍后重试。" : modelConfigurationSummary(read, revision)}</p>
    {!failed && read?.savedRevision === revision && Boolean(read?.runtimes.length) && <details>
      <summary>查看各工作区</summary>
      <ul>{read!.runtimes.map(runtime => <li key={runtime.runtimeId}>
        {{ general: "通用", scratch: "隔离", project: "项目" }[runtime.scope ?? ""] ?? "工作区"} · {runtime.runtimeId}：
        {{ applied: "已载入当前配置", pending: "仍使用旧配置，等待重启", unknown: "暂时无法确认" }[runtime.status]}
      </li>)}</ul>
    </details>}
    <p className="settings-card-note">只核对这台 Mac 托管的引擎启动回执，不代表模型服务可用或任务已完成。常驻实例单独配置。</p>
  </section>;
}
