import { expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { credentialStorageController, parseCredentialStorage, type CredentialStorageRead } from "./modelCredentialStorage";
import { ModelCredentialStorageView } from "../components/settings/ModelCredentialStorage";

const legacy = { version: 1 as const, mode: "legacy" as const, pendingCleanup: 0 };
const secure = { version: 1 as const, mode: "keychain" as const, pendingCleanup: 0 };
const deferred = <T,>() => { let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; };

it("rejects unsupported or malformed metadata and projects no secret/reference fields", () => {
  for (const bad of [null, { ...legacy, version: 2 }, { ...legacy, mode: "future" },
    { ...legacy, pendingCleanup: -1 }, { ...legacy, pendingCleanup: 33 }, { ...legacy, pendingCleanup: 1.5 }]) {
    expect(() => parseCredentialStorage(bad)).toThrow();
  }
  expect(parseCredentialStorage({ ...secure, apiKey: "private", reference: "private-ref" })).toEqual(secure);
});

it("only an explicit action with a model revision can mutate; refresh is read only", async () => {
  const update = vi.fn().mockResolvedValue(secure);
  const publish = vi.fn();
  const current = credentialStorageController("rev-1", { read: async () => legacy, update, publish, error: () => "failed" });
  await current.update("migrate"); expect(update).not.toHaveBeenCalled();
  await current.refresh(); expect(update).not.toHaveBeenCalled();
  await current.update("cleanup"); expect(update).not.toHaveBeenCalled();
  await current.update("migrate"); expect(update).toHaveBeenCalledExactlyOnceWith("migrate", "rev-1");
  await current.update("migrate"); expect(update).toHaveBeenCalledTimes(1);
  const unloaded = credentialStorageController(undefined, { read: async () => legacy, update, publish, error: () => "failed" });
  await unloaded.refresh(); await unloaded.update("migrate"); expect(update).toHaveBeenCalledTimes(1);
});

it("lost receipts remove actionability until a new read and never retry writes", async () => {
  const states: CredentialStorageRead[] = [];
  const update = vi.fn().mockRejectedValue(new Error("lost"));
  const current = credentialStorageController("rev-1", { read: async () => legacy, update,
    publish: state => states.push(state), error: () => "请刷新" });
  await current.refresh(); await current.update("migrate"); await current.update("migrate");
  expect(update).toHaveBeenCalledTimes(1);
  expect(states[states.length - 1]).toEqual({ busy: false, value: null, error: "请刷新" });
  await current.refresh(); expect(states[states.length - 1]?.value).toEqual(legacy);
});

it("configuration changes/disposal discard late mutation receipts and serialize operations", async () => {
  const late = deferred<unknown>(), states: CredentialStorageRead[] = [];
  const update = vi.fn(() => late.promise);
  const current = credentialStorageController("old-revision", { read: async () => legacy, update,
    publish: state => states.push(state), error: () => "failed" });
  await current.refresh(); const writing = current.update("migrate");
  await current.update("migrate"); await current.refresh(); expect(update).toHaveBeenCalledTimes(1);
  current.dispose(); const count = states.length; late.resolve(secure); await writing;
  expect(states).toHaveLength(count);
});

it("a metadata refresh cannot release a rejected model revision or permit repeated stale writes", async () => {
  const states: CredentialStorageRead[] = [];
  const update = vi.fn().mockRejectedValue({ code: "model_configuration_conflict", message: "changed" });
  const current = credentialStorageController("stale", { read: async () => legacy, update,
    publish: state => states.push(state), error: () => "failed" });
  await current.refresh(); await current.update("migrate"); await current.refresh(); await current.update("migrate");
  expect(update).toHaveBeenCalledTimes(1);
  expect(states[states.length - 1]?.error).toContain("模型设置中重新载入");
});

it("shows legacy and pending cleanup without claiming cloud sync or a runtime restart", () => {
  const html = renderToStaticMarkup(<ModelCredentialStorageView revision="rev" state={{ busy: false,
    value: { ...legacy, pendingCleanup: 1 }, error: "" }} onRefresh={() => {}} onUpdate={() => {}} />);
  expect(html).toContain("迁入本机钥匙串"); expect(html).toContain("重试清理");
  expect(html).toContain("原生窗口确认"); expect(html).toContain("不重启引擎、不上传云端");
  const unknown = renderToStaticMarkup(<ModelCredentialStorageView state={{ busy: false, value: null, error: "未知" }}
    onRefresh={() => {}} onUpdate={() => {}} />);
  expect(unknown).not.toContain("迁入本机钥匙串");
});
