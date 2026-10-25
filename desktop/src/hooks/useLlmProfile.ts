// 模型服务配置（本机配置文件 llm-profile.json；沿用 useJournal 的 hook 模式与三条红线）。
//
// 保存/清除后要重启当前 runtime——那是 runtime 域的事，经注入的 onRestartRuntime 调用
// （返回是否真的重启了，决定提示文案）。
//
// 启动时读一次，供引导卡显示真实状态；之后每次打开设置再读，刷新表单。只在打开设置时读的话，
// 没开过设置的引导卡会一直停在"检查中"。原生层对模型配置有进程内缓存，启动 runtime 时也读
// 同一份，所以这里不会多读一次文件。
import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";

import { restartSavedModelProfile, type ModelSaveKind } from "../lib/modelProfileSave";
import { isModelProfileConflict } from "../lib/modelProfileConflict";
import { errorText } from "../lib/errorText";
import { confirmAction } from "../lib/confirm";
import type { DesktopLlmProfileStatus } from "../types";

/** User edits clear old feedback; native reads and normalized save results do not. */
function useModelDraftValue<T>(initial: T, edited: () => void) {
  const [value, setValue] = useState(initial);
  const edit = useCallback((next: SetStateAction<T>) => { edited(); setValue(next); }, [edited]);
  return [value, setValue, edit] as const;
}

export function useLlmProfile(
  settingsOpen: boolean,
  onBanner: (text: string) => void,
  onRestartRuntime: () => Promise<boolean>,
) {
  const [llmProfileConflict, setLlmProfileConflict] = useState(false);
  const [llmSaveNote, setLlmSaveNote] = useState("");
  const [llmSaveKind, setLlmSaveKind] = useState<ModelSaveKind>("idle");
  const clearFeedback = useCallback(() => { setLlmSaveNote(""); setLlmSaveKind("idle"); }, []);
  const [llmProfile, setLlmProfile] = useState<DesktopLlmProfileStatus | null>(null);
  const [llmBaseInput, setLlmBaseInput, editLlmBaseInput] = useModelDraftValue("https://api.openai.com/v1", clearFeedback);
  const [llmModelInput, setLlmModelInput, editLlmModelInput] = useModelDraftValue("mimo-v2.6-pro", clearFeedback);
  const [llmKeyInput, setLlmKeyInput, editLlmKeyInput] = useModelDraftValue("", clearFeedback);
  const [llmFastInput, setLlmFastInput, editLlmFastInput] = useModelDraftValue("", clearFeedback);
  const [llmStrongInput, setLlmStrongInput, editLlmStrongInput] = useModelDraftValue("", clearFeedback);
  const [llmRolesInput, setLlmRolesInput, editLlmRolesInput] = useModelDraftValue<Record<string, string>>({}, clearFeedback);
  const [llmProfileBusy, setLlmProfileBusy] = useState(false);
  const [llmProfileLoading, setLlmProfileLoading] = useState(true);
  const [llmProfileError, setLlmProfileError] = useState("");
  const [readAttempt, setReadAttempt] = useState(0);
  const readGeneration = useRef(0);
  const reloadLlmProfile = () => setReadAttempt(attempt => attempt + 1);
  // 读过一次（成功或失败）。失败时不能让引导卡永远停在"检查中"：显示为待配置，点进设置会重读。
  const [llmProfileChecked, setLlmProfileChecked] = useState(false);
  const loadedOnceRef = useRef(false);

  /** 原生层返回了新的配置（登录、供应商变化）：同步状态和表单，免得表单拿旧地址覆盖回去。 */
  const replaceProfile = (profile: DesktopLlmProfileStatus) => {
    setLlmProfile(profile);
    setLlmBaseInput(profile.baseUrl);
    setLlmModelInput(profile.model);
    setLlmFastInput(profile.fastModel ?? "");
    setLlmStrongInput(profile.strongModel ?? "");
    setLlmRolesInput(profile.roleModels ?? {});
    setLlmKeyInput("");
  };

  const applyLlmProfile = (profile: DesktopLlmProfileStatus) => {
    // Account/provider mutations supersede any earlier read, including its late errors.
    ++readGeneration.current;
    if (llmProfileConflict) onBanner("已载入最新配置，请重新编辑需要的内容");
    clearFeedback();
    setLlmProfileConflict(false);
    replaceProfile(profile);
    setLlmProfileLoading(false);
    setLlmProfileError("");
    setLlmProfileChecked(true);
    loadedOnceRef.current = true;
  };

  useEffect(() => {
    if (!settingsOpen && loadedOnceRef.current) { setLlmProfileLoading(false); return; }
    const generation = ++readGeneration.current;
    let alive = true;
    const current = () => alive && generation === readGeneration.current;
    setLlmProfileLoading(true);
    setLlmProfileError("");
    clearFeedback();
    void invoke<DesktopLlmProfileStatus>("get_llm_profile")
      .then(profile => { if (current()) { setLlmProfileConflict(false); replaceProfile(profile); } })
      .catch(error => {
        if (!current()) return;
        const message = errorText(error, "读取本机模型配置失败");
        setLlmProfileError(message); onBanner(message);
      })
      .finally(() => { if (current()) { loadedOnceRef.current = true; setLlmProfileLoading(false); setLlmProfileChecked(true); } });
    return () => { alive = false; };
  }, [onBanner, settingsOpen, readAttempt, clearFeedback]);

  const saveLlmProfile = async () => {
    if (llmProfileBusy || llmProfileLoading || llmProfileError || llmProfileConflict || !llmBaseInput.trim() || !llmModelInput.trim()) return;
    setLlmProfileBusy(true);
    setLlmSaveNote("");
    setLlmSaveKind("idle");
    try {
      const profile = await invoke<DesktopLlmProfileStatus>("set_llm_profile", {
        expectedRevision: llmProfile?.revision ?? null,
        baseUrl: llmBaseInput.trim(),
        apiKey: llmKeyInput.trim(),
        model: llmModelInput.trim(),
        fastModel: llmFastInput.trim() || null,
        strongModel: llmStrongInput.trim() || null,
        roleModels: llmRolesInput,
      });
      applyLlmProfile(profile);
      const feedback = await restartSavedModelProfile(onRestartRuntime, "保存");
      setLlmSaveNote(feedback.message); setLlmSaveKind(feedback.kind); onBanner(feedback.message);
    } catch (error) {
      setLlmProfileConflict(isModelProfileConflict(error));
      const message = errorText(error, "保存模型服务失败");
      setLlmSaveNote(message); setLlmSaveKind("error"); onBanner(message);
    } finally {
      setLlmProfileBusy(false);
    }
  };

  const clearLlmProfile = async () => {
    if (llmProfileBusy || llmProfileLoading || llmProfileError || llmProfileConflict || !await confirmAction("清除本机保存的模型服务配置？当前 runtime 会重启。")) return;
    setLlmProfileBusy(true);
    setLlmSaveNote("");
    setLlmSaveKind("idle");
    try {
      const profile = await invoke<DesktopLlmProfileStatus>("clear_llm_profile", { expectedRevision: llmProfile?.revision ?? null });
      applyLlmProfile(profile);
      const feedback = await restartSavedModelProfile(onRestartRuntime, "清除");
      setLlmSaveNote(feedback.message); setLlmSaveKind(feedback.kind); onBanner(feedback.message);
    } catch (error) {
      setLlmProfileConflict(isModelProfileConflict(error));
      const message = errorText(error, "清除模型服务失败");
      setLlmSaveNote(message); setLlmSaveKind("error"); onBanner(message);
    } finally {
      setLlmProfileBusy(false);
    }
  };

  return {
    llmProfile, applyLlmProfile, llmProfileChecked, llmBaseInput, setLlmBaseInput: editLlmBaseInput, llmModelInput, setLlmModelInput: editLlmModelInput,
    llmKeyInput, setLlmKeyInput: editLlmKeyInput, llmFastInput, setLlmFastInput: editLlmFastInput, llmStrongInput, setLlmStrongInput: editLlmStrongInput,
    llmRolesInput, setLlmRolesInput: editLlmRolesInput,
    llmProfileBusy, llmProfileConflict, llmSaveNote, llmSaveKind, llmProfileLoading, llmProfileError, reloadLlmProfile, saveLlmProfile, clearLlmProfile,
  };
}
