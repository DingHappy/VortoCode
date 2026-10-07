// 模型服务配置（本机配置文件 llm-profile.json；沿用 useJournal 的 hook 模式与三条红线）。
//
// 保存/清除后要重启当前 runtime——那是 runtime 域的事，经注入的 onRestartRuntime 调用
// （返回是否真的重启了，决定提示文案）。
//
// 启动时读一次，供引导卡显示真实状态；之后每次打开设置再读，刷新表单。只在打开设置时读的话，
// 没开过设置的引导卡会一直停在"检查中"。原生层对模型配置有进程内缓存，启动 runtime 时也读
// 同一份，所以这里不会多读一次文件。
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";

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
  const [llmModelInput, setLlmModelInput] = useState("mimo-v2.6-pro");
  const [llmKeyInput, setLlmKeyInput] = useState("");
  const [llmFastInput, setLlmFastInput] = useState("");
  const [llmStrongInput, setLlmStrongInput] = useState("");
  const [llmProfileBusy, setLlmProfileBusy] = useState(false);
  // 读过一次（成功或失败）。失败时不能让引导卡永远停在"检查中"：显示为待配置，点进设置会重读。
  const [llmProfileChecked, setLlmProfileChecked] = useState(false);
  const loadedOnceRef = useRef(false);

  /** 原生层返回了新的配置（登录、供应商变化）：同步状态和表单，免得表单拿旧地址覆盖回去。 */
  const applyLlmProfile = (profile: DesktopLlmProfileStatus) => {
    setLlmProfile(profile);
    setLlmBaseInput(profile.baseUrl);
    setLlmModelInput(profile.model);
    setLlmFastInput(profile.fastModel ?? "");
    setLlmStrongInput(profile.strongModel ?? "");
    setLlmKeyInput("");
  };

  useEffect(() => {
    if (!settingsOpen && loadedOnceRef.current) return;
    loadedOnceRef.current = true;
    void invoke<DesktopLlmProfileStatus>("get_llm_profile")
      .then((profile) => {
        setLlmProfile(profile);
        setLlmBaseInput(profile.baseUrl);
        setLlmModelInput(profile.model);
        setLlmFastInput(profile.fastModel ?? "");
        setLlmStrongInput(profile.strongModel ?? "");
        setLlmKeyInput("");
      })
      .catch((error) => onBanner(error instanceof Error ? error.message : String(error)))
      .finally(() => setLlmProfileChecked(true));
  }, [onBanner, settingsOpen]);

  const saveLlmProfile = async () => {
    if (llmProfileBusy || !llmBaseInput.trim() || !llmModelInput.trim()) return;
    setLlmProfileBusy(true);
    try {
      const profile = await invoke<DesktopLlmProfileStatus>("set_llm_profile", {
        baseUrl: llmBaseInput.trim(),
        apiKey: llmKeyInput.trim(),
        model: llmModelInput.trim(),
        fastModel: llmFastInput.trim() || null,
        strongModel: llmStrongInput.trim() || null,
      });
      setLlmProfile(profile);
      setLlmKeyInput("");
      const restarted = await onRestartRuntime();
      if (restarted) onBanner(`${profile.provider === "vortocode" ? "VortoCode Relay" : profile.provider === "local" ? "本机模型服务" : "自定义模型服务"}已保存到本机配置文件，当前 runtime 已重启`);
    } catch (error) {
      onBanner(errorText(error, "保存模型服务失败"));
    } finally {
      setLlmProfileBusy(false);
    }
  };

  const clearLlmProfile = async () => {
    if (llmProfileBusy || !await confirmAction("清除本机保存的模型服务配置？当前 runtime 会重启。")) return;
    setLlmProfileBusy(true);
    try {
      const profile = await invoke<DesktopLlmProfileStatus>("clear_llm_profile");
      setLlmProfile(profile);
      setLlmBaseInput(profile.baseUrl);
      setLlmModelInput(profile.model);
      setLlmFastInput("");
      setLlmStrongInput("");
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
    llmProfile, applyLlmProfile, llmProfileChecked, llmBaseInput, setLlmBaseInput, llmModelInput, setLlmModelInput,
    llmKeyInput, setLlmKeyInput, llmFastInput, setLlmFastInput, llmStrongInput, setLlmStrongInput,
    llmProfileBusy, saveLlmProfile, clearLlmProfile,
  };
}
