// Journal 域 hook（B8-④c S6b，hook 模式**试点**）。
//
// 与纯展示件（S3–S6a）不同：这里把 journal 8 个 state + 全部读写回调从 App 主体收进
// 一个域 hook。可行前提是 ④c 实测：事件总线对 journal 零直写，只调 snapshotTodayJournal
// / refresh* 续期——hook 把这些回调**归还给 App**，总线照旧在 case 里调用，dispatcher
// 仍是 App 内唯一一个（红线 1 不破）；hook 实例挂在 App，不是第二真相源（红线 2 不破）。
//
// 红线 3（hook 专属）：归还的回调必须恒等稳定——App 的 handleProtocolEvent / connect 的
// useCallback 依赖着它们。本文件所有返回函数都过 useCallback；依赖数组与迁移前 App 内
// 同名函数完全一致（refreshJournal/refreshWeeklyJournal 随 journalDate 变、
// snapshotTodayJournal 随 journalDate 与 weeklyJournal?.end_date 变），不引入新的抖动源。
//
// 跨域触点只有一个：保存快照/添加记录的结果提示走注入的 onBanner（App 传 setBanner，
// state setter 恒等稳定）。openJournalAction 不在此处——它写任务与 inspector 域，留 App。
import { useCallback, useState } from "react";
import type { RefObject } from "react";

import type { GatewayClient } from "../gateway";
import { errorText } from "../lib/errorText";
import { localDay, previousDay } from "../lib/time";
import type {
  JournalContinuation,
  JournalDaySummary,
  JournalSnapshot,
  WeeklyJournalSnapshot,
} from "../types";

export function useJournal(
  clientRef: RefObject<GatewayClient | null>,
  onBanner: (text: string) => void,
) {
  const [journal, setJournal] = useState<JournalSnapshot | null>(null);
  const [journalDays, setJournalDays] = useState<JournalDaySummary[]>([]);
  const [weeklyJournal, setWeeklyJournal] = useState<WeeklyJournalSnapshot | null>(null);
  const [journalContinuation, setJournalContinuation] = useState<JournalContinuation | null>(null);
  const [journalView, setJournalView] = useState<"day" | "week">("day");
  const [journalDate, setJournalDate] = useState(localDay);
  const [journalNote, setJournalNote] = useState("");
  const [journalBusy, setJournalBusy] = useState(false);

  const refreshJournalDays = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    try {
      setJournalDays(await client.listJournalDays());
    } catch {
      // 旧 runtime 没有 Journal 时保持空历史。
    }
  }, [clientRef]);

  const refreshJournal = useCallback(async (date = journalDate) => {
    const client = clientRef.current;
    if (!client) return;
    try {
      setJournal(await client.getJournal(date));
    } catch {
      // Journal 是增量能力，不阻断对话与决策中心。
    }
  }, [clientRef, journalDate]);

  const refreshWeeklyJournal = useCallback(async (end = journalDate) => {
    const client = clientRef.current;
    if (!client) return;
    try {
      setWeeklyJournal(await client.getWeeklyJournal(end));
    } catch {
      // 周报是 Journal 的聚合视图，不阻断日报恢复。
    }
  }, [clientRef, journalDate]);

  const refreshJournalContinuation = useCallback(async (date: string) => {
    const client = clientRef.current;
    if (!client || date >= localDay()) {
      setJournalContinuation(null);
      return;
    }
    try {
      setJournalContinuation(await client.getJournalContinuation(date));
    } catch {
      setJournalContinuation(null);
    }
  }, [clientRef]);

  const snapshotTodayJournal = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    try {
      const snapshot = await client.snapshotJournal(localDay());
      if (journalDate === snapshot.date) setJournal(snapshot);
      setJournalDays(await client.listJournalDays());
      if (weeklyJournal?.end_date === snapshot.date) {
        setWeeklyJournal(await client.getWeeklyJournal(snapshot.date));
      }
    } catch {
      // 自动快照 best-effort；手工保存会显示具体错误。
    }
  }, [clientRef, journalDate, weeklyJournal?.end_date]);

  const selectJournalDay = useCallback(async (date: string) => {
    setJournalDate(date);
    setJournalBusy(true);
    try {
      await Promise.all([
        refreshJournal(date),
        refreshWeeklyJournal(date),
        refreshJournalContinuation(date),
      ]);
    } finally {
      setJournalBusy(false);
    }
  }, [refreshJournal, refreshWeeklyJournal, refreshJournalContinuation]);

  const continueFromYesterday = useCallback(async () => {
    const yesterday = previousDay(localDay());
    setJournalView("day");
    await selectJournalDay(yesterday);
  }, [selectJournalDay]);

  const saveJournalSnapshot = useCallback(async () => {
    if (!clientRef.current || journalDate !== localDay()) return;
    setJournalBusy(true);
    try {
      const snapshot = await clientRef.current.snapshotJournal(journalDate);
      setJournal(snapshot);
      await refreshJournalDays();
      await refreshWeeklyJournal(journalDate);
      onBanner("今日 Journal 快照已保存；内容未变化时不会重复写盘");
    } catch (error) {
      onBanner(errorText(error, "Journal 快照保存失败"));
    } finally {
      setJournalBusy(false);
    }
  }, [clientRef, journalDate, refreshJournalDays, refreshWeeklyJournal, onBanner]);

  const addJournalNote = useCallback(async () => {
    const text = journalNote.trim();
    if (!clientRef.current || !text || journalDate !== localDay()) return;
    setJournalBusy(true);
    try {
      setJournal(await clientRef.current.addJournalNote(journalDate, text));
      setJournalNote("");
      await Promise.all([refreshJournalDays(), refreshWeeklyJournal(journalDate)]);
      onBanner("工作记录已加入今日 Journal；凭据样式内容会自动脱敏");
    } catch (error) {
      onBanner(errorText(error, "添加 Journal 记录失败"));
    } finally {
      setJournalBusy(false);
    }
  }, [clientRef, journalNote, journalDate, refreshJournalDays, refreshWeeklyJournal, onBanner]);

  const resetJournal = useCallback(() => {
    setJournal(null);
    setJournalDays([]);
    setWeeklyJournal(null);
    setJournalContinuation(null);
    setJournalDate(localDay());
    setJournalNote("");
  }, []);

  return {
    journal,
    journalDays,
    weeklyJournal,
    journalContinuation,
    journalView,
    journalDate,
    journalNote,
    journalBusy,
    setJournalView,
    setJournalNote,
    refreshJournal,
    refreshJournalDays,
    refreshWeeklyJournal,
    refreshJournalContinuation,
    snapshotTodayJournal,
    selectJournalDay,
    continueFromYesterday,
    saveJournalSnapshot,
    addJournalNote,
    resetJournal,
  };
}
