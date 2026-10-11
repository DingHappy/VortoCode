// 模型设置里的「测试连接」：只验证不保存；成功时回传服务的模型列表给模型名做候选。
import { invoke } from "@tauri-apps/api/core";
import { useLayoutEffect, useRef, useState } from "react";
import { createConnectionCheck, sameConnectionDraft } from "../../lib/llmConnectionCheck";
import type { ConnectionCheckState } from "../../lib/llmConnectionCheck";

type Props = {
  baseUrl: string;
  apiKey: string;
  model: string;
  onModels: (models: string[]) => void;
};

export function LlmConnectionCheck({ baseUrl, apiKey, model, onModels }: Props) {
  const draft = { baseUrl, apiKey, model };
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const modelsRef = useRef(onModels);
  modelsRef.current = onModels;
  const [state, setState] = useState<ConnectionCheckState | null>(null);
  const checkRef = useRef<ReturnType<typeof createConnectionCheck> | null>(null);
  if (!checkRef.current) checkRef.current = createConnectionCheck(
    () => draftRef.current,
    (value) => invoke("test_llm_connection", value),
    setState,
    (values) => modelsRef.current(values),
  );
  useLayoutEffect(() => {
    checkRef.current!.reset();
    setState(null);
    return () => checkRef.current!.reset();
  }, [baseUrl, apiKey, model]);
  useLayoutEffect(() => {
    modelsRef.current([]);
  }, [baseUrl, apiKey]);
  const active = state && sameConnectionDraft(state.draft, draft) ? state : null;
  const result = active?.result;
  const error = active?.error || "";
  const testing = active?.testing || false;

  const tone = error || (result && !result.ok) ? "danger" : result ? "ok" : "";
  return (
    <div className="llm-connection-check">
      <button onClick={() => void checkRef.current!.test()} disabled={testing || !baseUrl.trim()}>{testing ? "获取中…" : "获取模型列表"}</button>
      {(error || result) && <span role="status" className={`llm-connection-result ${tone}`}>{error || result?.message}</span>}
      <small>仅查询模型列表，不保存配置；推理与工具调用需另行验证。</small>
    </div>
  );
}
