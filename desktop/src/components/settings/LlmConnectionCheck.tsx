// 模型设置里的「测试连接」：只验证不保存；成功时回传服务的模型列表给模型名做候选。
import { invoke } from "@tauri-apps/api/core";
import { useState } from "react";

import { errorText } from "../../lib/errorText";
import type { LlmConnectionTest } from "../../types";

type Props = {
  baseUrl: string;
  apiKey: string;
  model: string;
  onModels: (models: string[]) => void;
};

export function LlmConnectionCheck({ baseUrl, apiKey, model, onModels }: Props) {
  const [result, setResult] = useState<LlmConnectionTest | null>(null);
  const [error, setError] = useState("");
  const [testing, setTesting] = useState(false);

  const test = async () => {
    setTesting(true);
    setError("");
    setResult(null);
    try {
      const outcome = await invoke<LlmConnectionTest>("test_llm_connection", { baseUrl, apiKey, model });
      setResult(outcome);
      if (outcome.models.length) onModels(outcome.models);
    } catch (failure) {
      setError(errorText(failure, "测试连接失败"));
    } finally {
      setTesting(false);
    }
  };

  const tone = error || (result && !result.ok) ? "danger" : result ? "ok" : "";
  return (
    <div className="llm-connection-check">
      <button onClick={() => void test()} disabled={testing || !baseUrl.trim()}>{testing ? "测试中…" : "测试连接"}</button>
      {(error || result) && <span className={`llm-connection-result ${tone}`}>{error || result?.message}</span>}
    </div>
  );
}
