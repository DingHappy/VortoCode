// 模型服务配置（macOS Keychain；沿用 useJournal 的 hook 模式与三条红线）。
//
// 保存/清除后要重启当前 runtime——那是 runtime 域的事，经注入的 onRestartRuntime 调用
// （返回是否真的重启了，决定提示文案）。打开设置时读一次 Keychain，effect 依赖与迁移前一致。
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";

import { errorText } from "../lib/errorText";
import { confirmAction } from "../lib/confirm";
import type { DesktopLlmProfileStatus } from "../types";

export function useLlmProfile(
  settingsOpen: boolean,
  onBanner: (text: string) => void,
  onRestartRuntime: () => Promise<boolean>,
) {
  const [llmProfile, setLlmProfile] = useState<DesktopLlmProfileStatus | null>(null);
  const [llmBaseInput, setLlmBaseInput] = useState("https://token.vortotech.com/v1");
  const [llmModelInput, setLlmModelInput] = useState("mimo-v2.5");
  const [llmKeyInput, setLlmKeyInput] = useState("");
  const [llmProfileBusy, setLlmProfileBusy] = useState(false);

  useEffect(() => {
    if (!settingsOpen) return;
    void invoke<DesktopLlmProfileStatus>("get_llm_profile")
      .then((profile) => {
        setLlmProfile(profile);
        setLlmBaseInput(profile.baseUrl);
        setLlmModelInput(profile.model);
        setLlmKeyInput("");
      })
      .catch((error) => onBanner(error instanceof Error ? error.message : String(error)));
  }, [onBanner, settingsOpen]);

  const saveLlmProfile = async () => {
    if (llmProfileBusy || !llmBaseInput.trim() || !llmModelInput.trim()) return;
    setLlmProfileBusy(true);
    try {
      const profile = await invoke<DesktopLlmProfileStatus>("set_llm_profile", {
        baseUrl: llmBaseInput.trim(),
        apiKey: llmKeyInput.trim(),
        model: llmModelInput.trim(),
      });
      setLlmProfile(profile);
      setLlmKeyInput("");
      const restarted = await onRestartRuntime();
      if (restarted) onBanner(`${profile.provider === "vortocode" ? "VortoCode Relay" : profile.provider === "local" ? "本机模型服务" : "自定义模型服务"}已保存到 macOS Keychain，当前 runtime 已重启`);
    } catch (error) {
      onBanner(errorText(error, "保存模型服务失败"));
    } finally {
      setLlmProfileBusy(false);
    }
  };

  const clearLlmProfile = async () => {
    if (llmProfileBusy || !await confirmAction("清除 macOS Keychain 中的模型服务配置？当前 runtime 会重启。")) return;
    setLlmProfileBusy(true);
    try {
      const profile = await invoke<DesktopLlmProfileStatus>("clear_llm_profile");
      setLlmProfile(profile);
      setLlmBaseInput(profile.baseUrl);
      setLlmModelInput(profile.model);
      setLlmKeyInput("");
      const restarted = await onRestartRuntime();
      if (restarted) onBanner("模型服务配置已清除；需要对话时可随时重新设置");
    } catch (error) {
      onBanner(errorText(error, "清除模型服务失败"));
    } finally {
      setLlmProfileBusy(false);
    }
  };

  return {
    llmProfile, llmBaseInput, setLlmBaseInput, llmModelInput, setLlmModelInput, llmKeyInput, setLlmKeyInput,
    llmProfileBusy, saveLlmProfile, clearLlmProfile,
  };
}
