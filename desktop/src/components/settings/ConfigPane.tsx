// 设置 → 配置：Desktop 用到的本机配置文件，可复制路径或在 Finder 中显示。
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";

import { errorText } from "../../lib/errorText";
import type { UserInstructions } from "../../types";

type ConfigFile = { kind: "llmProfile" | "userInstructions"; title: string; description: string; path?: string };

export function ConfigPane({ llmProfilePath }: { llmProfilePath?: string }) {
  const [instructionsPath, setInstructionsPath] = useState<string>();
  const [status, setStatus] = useState("");

  useEffect(() => {
    invoke<UserInstructions>("get_user_instructions")
      .then((value) => setInstructionsPath(value.path))
      .catch(() => undefined);
  }, []);

  const files: ConfigFile[] = [
    { kind: "llmProfile", title: "模型服务配置", description: "地址、模型名与 API Key（权限 600，仅你的账户可读）", path: llmProfilePath },
    { kind: "userInstructions", title: "全局指令 AGENTS.md", description: "对所有会话生效的说明；也可在「个性化」里编辑", path: instructionsPath },
  ];

  const copy = async (path: string) => {
    try {
      await navigator.clipboard.writeText(path);
      setStatus("路径已复制");
    } catch (failure) {
      setStatus(errorText(failure, "复制失败"));
    }
  };

  const reveal = async (kind: ConfigFile["kind"]) => {
    try {
      await invoke("reveal_desktop_config", { kind });
    } catch (failure) {
      setStatus(errorText(failure, "无法在 Finder 中显示"));
    }
  };

  return (
    <div className="settings-stack">
      <section className="settings-card" aria-label="配置文件">
        {files.map((file) => (
          <div className="config-file-row" key={file.kind}>
            <div>
              <strong>{file.title}</strong>
              <span>{file.description}</span>
              <code>{file.path ?? "—"}</code>
            </div>
            <div className="config-file-actions">
              <button onClick={() => file.path && void copy(file.path)} disabled={!file.path}>复制路径</button>
              <button onClick={() => void reveal(file.kind)}>在 Finder 中显示</button>
            </div>
          </div>
        ))}
        {status && <p className="settings-card-note">{status}</p>}
      </section>
      <p className="settings-intro">项目级指令放在仓库根目录的 AGENTS.md（或本地私有的 .vortocode/AGENTS.md），只在该项目里生效。</p>
    </div>
  );
}
