// 新增高优先级事项的系统通知（开关、基线与去重）。
//
// 连接期间（begin…end 之间）读到的事项一律只进基线：重连后不会把旧事项当成新的再推一遍。
// 已推送的 id 存在 localStorage（最多 200 条）。系统权限被收回就自动关掉开关并提示。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";

import { notificationCandidates, takeUnseen } from "../lib/decisionNotifications";
import { errorText } from "../lib/errorText";
import { loadNotifiedDecisionIds, persistNotifiedDecisionIds, STORAGE_KEYS } from "../lib/storage";
import type { DecisionItem, DesktopRuntimeInbox } from "../types";

export function useDecisionNotifications({ runtimeInboxes, decisionItems, currentRuntimeId, onBanner }: {
  runtimeInboxes: DesktopRuntimeInbox[];
  decisionItems: DecisionItem[];
  currentRuntimeId: string;
  onBanner: (text: string) => void;
}) {
  const notifiedDecisionIdsRef = useRef<Set<string>>(loadNotifiedDecisionIds());
  const decisionNotificationReadyRef = useRef(false);
  const decisionNotificationSyncingRef = useRef(false);
  const [notificationsEnabled, setNotificationsEnabled] = useState(
    () => localStorage.getItem(STORAGE_KEYS.notificationsEnabled) === "true",
  );
  const [notificationSyncVersion, setNotificationSyncVersion] = useState(0);
  const globalNotificationItems = useMemo(() => notificationCandidates(runtimeInboxes, decisionItems, currentRuntimeId),
    [currentRuntimeId, decisionItems, runtimeInboxes]);

  useEffect(() => {
    if (!notificationsEnabled) {
      decisionNotificationReadyRef.current = false;
      return;
    }
    const baseline = decisionNotificationSyncingRef.current || !decisionNotificationReadyRef.current;
    const unseen = takeUnseen(globalNotificationItems, notifiedDecisionIdsRef.current, baseline);
    if (baseline) {
      persistNotifiedDecisionIds(notifiedDecisionIdsRef.current);
      decisionNotificationReadyRef.current = true;
      return;
    }
    if (unseen.length === 0) return;
    persistNotifiedDecisionIds(notifiedDecisionIdsRef.current);
    void (async () => {
      try {
        if (!await isPermissionGranted()) {
          localStorage.setItem(STORAGE_KEYS.notificationsEnabled, "false");
          setNotificationsEnabled(false);
          onBanner("系统通知权限已关闭；应用内决策队列不受影响");
          return;
        }
        sendNotification({
          title: "VortoCode 有新的待决策事项",
          body: `新增 ${unseen.length} 项高优先级事项，打开 Desktop 查看。`,
        });
      } catch {
        // 系统投递失败不能影响 Agent、决策队列或台账刷新。
      }
    })();
  // onBanner 是 App 的 setBanner（恒等稳定）；依赖与迁移前一致。
  }, [globalNotificationItems, notificationSyncVersion, notificationsEnabled]);

  const toggleSystemNotifications = async () => {
    if (notificationsEnabled) {
      localStorage.setItem(STORAGE_KEYS.notificationsEnabled, "false");
      decisionNotificationReadyRef.current = false;
      setNotificationsEnabled(false);
      onBanner("系统通知已关闭；应用内决策队列仍会保留");
      return;
    }
    try {
      let granted = await isPermissionGranted();
      if (!granted) granted = (await requestPermission()) === "granted";
      if (!granted) {
        onBanner("系统没有授予通知权限；VortoCode 不会反复请求");
        return;
      }
      takeUnseen(globalNotificationItems, notifiedDecisionIdsRef.current, true);
      persistNotifiedDecisionIds(notifiedDecisionIdsRef.current);
      decisionNotificationReadyRef.current = true;
      localStorage.setItem(STORAGE_KEYS.notificationsEnabled, "true");
      setNotificationsEnabled(true);
      onBanner("系统通知已开启；现有事项只建立基线，之后仅提醒新增高优先级决策");
    } catch (error) {
      onBanner(errorText(error, "当前环境无法启用系统通知"));
    }
  };

  /** 开始连接：之后读到的事项先进基线。 */
  const beginNotificationSync = useCallback(() => {
    decisionNotificationReadyRef.current = false;
    decisionNotificationSyncingRef.current = true;
  }, []);
  /** 连接结束（成功或失败）：按此刻的事项建立基线。 */
  const endNotificationSync = useCallback(() => {
    decisionNotificationSyncingRef.current = false;
    setNotificationSyncVersion((value) => value + 1);
  }, []);
  /** 断开：不再处于同步中（基线保持）。 */
  const stopNotificationSync = useCallback(() => { decisionNotificationSyncingRef.current = false; }, []);
  /** 切项目：下一次读到的事项重新建立基线。 */
  const resetNotificationBaseline = useCallback(() => {
    decisionNotificationReadyRef.current = false;
    decisionNotificationSyncingRef.current = false;
  }, []);

  return {
    notificationsEnabled, toggleSystemNotifications,
    beginNotificationSync, endNotificationSync, stopNotificationSync, resetNotificationBaseline,
  };
}
