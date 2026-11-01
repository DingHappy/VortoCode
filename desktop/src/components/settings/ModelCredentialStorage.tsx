import { useEffect, useRef, useState } from "react";
import { errorText } from "../../lib/errorText";
import { credentialStorageController, readCredentialStorage, updateCredentialStorage,
  type CredentialStorageRead, type CredentialStorageAction } from "../../lib/modelCredentialStorage";

export function ModelCredentialStorageView({ state, revision, onRefresh, onUpdate }: {
  state: CredentialStorageRead; revision?: string; onRefresh: () => void;
  onUpdate: (action: CredentialStorageAction) => void;
}) {
  const { value, busy, error } = state;
  return <section className="settings-card" aria-label="模型凭证存储">
    <div className="settings-card-head"><strong>模型凭证存储</strong>
      <button disabled={busy} onClick={onRefresh}>{busy ? "正在核对…" : "刷新状态"}</button></div>
    {error && <p role="alert">{error}</p>}
    {value ? <>
      <p>{value.mode === "legacy" ? "旧模型配置使用明文 Key 格式，可迁入本机钥匙串。"
        : value.mode === "keychain" ? "模型配置已使用钥匙串引用格式。" : "尚无已保存的模型配置。"}</p>
      {value.mode === "legacy" && <button disabled={busy || !revision || !!error} onClick={() => onUpdate("migrate")}>迁入本机钥匙串</button>}
      {value.pendingCleanup > 0 && <>
        <p role="status">还有 {value.pendingCleanup} 条旧凭证副本记录等待清理。</p>
        <button disabled={busy || !revision || !!error} onClick={() => onUpdate("cleanup")}>重试清理</button>
      </>}
    </> : !error && <p className="settings-card-note">{busy ? "正在读取存储状态…" : "状态尚未核对，请刷新。"}</p>}
    <p className="settings-card-note">迁移和清理均经原生窗口确认，不重启引擎、不上传云端。读取状态不会迁移旧配置；无 Key 的本机服务不会创建凭证条目。旧备份中的明文需另行处理。</p>
  </section>;
}

export function ModelCredentialStorage({ revision }: { revision?: string }) {
  const [state, setState] = useState<CredentialStorageRead>({ busy: false, value: null, error: "" });
  const controller = useRef<ReturnType<typeof credentialStorageController> | null>(null);
  useEffect(() => {
    const current = credentialStorageController(revision, {
      read: readCredentialStorage, update: updateCredentialStorage, publish: setState,
      error: failure => errorText(failure, "凭证存储状态未知，请刷新核对后再操作"),
    });
    controller.current = current;
    void current.refresh();
    return () => { current.dispose(); if (controller.current === current) controller.current = null; };
  }, [revision]);
  return <ModelCredentialStorageView state={state} revision={revision}
    onRefresh={() => void controller.current?.refresh()} onUpdate={action => void controller.current?.update(action)} />;
}
