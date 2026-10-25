import type { GatewayClient } from "../gateway";
import type { JournalContinuation, JournalDaySummary, JournalSnapshot, WeeklyJournalSnapshot } from "../types";
import { ViewMutations, ViewRequests } from "./viewRequests";
import { errorText } from "./errorText";
import { localDay, previousDay } from "./time";

export type JournalClient = Pick<GatewayClient, "listJournalDays" | "getJournal" | "getWeeklyJournal"
  | "getJournalContinuation" | "snapshotJournal" | "addJournalNote">;
export interface JournalActionDeps {
  client: () => JournalClient | null;
  date: () => string;
  note: () => string;
  weekEnd: () => string | undefined;
  setDate: (date: string) => void;
  setView: (view: "day" | "week") => void;
  setJournal: (snapshot: JournalSnapshot | null) => void;
  setDays: (days: JournalDaySummary[]) => void;
  setWeek: (snapshot: WeeklyJournalSnapshot | null) => void;
  setContinuation: (value: JournalContinuation | null) => void;
  setBusy: (busy: boolean) => void;
  clearNote: (expected: string) => void;
  reset: () => void;
  banner: (text: string) => void;
}

/** 沿用 hook 的界面状态；所有异步操作固定发起时的客户端和日期。 */
export function journalActions(deps: JournalActionDeps) {
  const reads = new ViewRequests(deps.client);
  const writes = new ViewMutations(deps.client);
  const automatic = new WeakMap<JournalClient, Promise<void>>();
  const capture = (lane: string, date?: string) => {
    const request = reads.capture(lane);
    return () => request.current() && (date === undefined || date === deps.date());
  };
  const refreshJournalDays = async () => {
    const client = deps.client(); if (!client) return;
    const current = capture("days");
    try { const days = await client.listJournalDays(); if (current()) deps.setDays(days); } catch { /* 旧运行端可无 Journal。 */ }
  };
  const refreshJournal = async (date = deps.date()) => {
    const client = deps.client(); if (!client) return;
    const current = capture("journal", date);
    try { const value = await client.getJournal(date); if (current()) deps.setJournal(value); } catch { /* 保留原快照。 */ }
  };
  const refreshWeeklyJournal = async (date = deps.date()) => {
    const client = deps.client(); if (!client) return;
    const current = capture("week", date);
    try { const value = await client.getWeeklyJournal(date); if (current()) deps.setWeek(value); } catch { /* 保留原快照。 */ }
  };
  const refreshJournalContinuation = async (date: string) => {
    const client = deps.client(), current = capture("continuation", date);
    if (!client || date >= localDay()) { if (date === deps.date()) deps.setContinuation(null); return; }
    try { const value = await client.getJournalContinuation(date); if (current()) deps.setContinuation(value); }
    catch { if (current()) deps.setContinuation(null); }
  };
  const snapshotTodayJournal = (): Promise<void> => {
    const client = deps.client(); if (!client || writes.busy(client)) return Promise.resolve();
    const pending = automatic.get(client); if (pending) return pending;
    const date = deps.date(), current = capture("snapshot", date);
    const publishJournal = date === localDay() ? capture("journal", date) : () => false;
    const promise = (async () => { try {
      const snapshot = await client.snapshotJournal(localDay());
      if (!current()) return;
      if (publishJournal() && date === snapshot.date) deps.setJournal(snapshot);
      await refreshJournalDays();
      if (current() && deps.weekEnd() === snapshot.date) await refreshWeeklyJournal(date);
    } catch { /* 自动快照 best-effort；不重放写入。 */ }
    })().finally(() => { if (automatic.get(client) === promise) automatic.delete(client); });
    automatic.set(client, promise);
    return promise;
  };
  const selectJournalDay = async (date: string) => {
    reads.invalidate();
    deps.setDate(date);
    deps.setJournal(null); deps.setWeek(null); deps.setContinuation(null);
    if (!deps.client()) { deps.setBusy(false); return; }
    const current = capture("busy", date);
    deps.setBusy(true);
    try { await Promise.all([refreshJournal(date), refreshWeeklyJournal(date), refreshJournalContinuation(date)]); }
    finally { if (current()) deps.setBusy(false); }
  };
  const continueFromYesterday = async () => {
    deps.setView("day");
    await selectJournalDay(previousDay(localDay()));
  };
  const saveJournalSnapshot = async () => {
    const client = deps.client(), date = deps.date();
    if (!client || date !== localDay()) return;
    const ticket = writes.begin(); if (!ticket) return;
    const current = capture("busy", date), publish = capture("journal", date);
    deps.setBusy(true);
    try {
      const background = automatic.get(client); if (background) await background;
      if (!ticket.current() || !current()) return;
      const snapshot = await client.snapshotJournal(date);
      if (!current()) return;
      if (publish()) deps.setJournal(snapshot);
      await refreshJournalDays();
      if (!current()) return;
      await refreshWeeklyJournal(date);
      if (current()) deps.banner("今日 Journal 快照已保存；内容未变化时不会重复写盘");
    } catch (error) { if (current()) deps.banner(errorText(error, "Journal 快照保存失败")); }
    finally { ticket.release(); if (current()) deps.setBusy(false); }
  };
  const addJournalNote = async () => {
    const client = deps.client(), date = deps.date(), note = deps.note(), text = note.trim();
    if (!client || !text || date !== localDay()) return;
    const ticket = writes.begin(); if (!ticket) return;
    const current = capture("busy", date), publish = capture("journal", date);
    deps.setBusy(true);
    try {
      const background = automatic.get(client); if (background) await background;
      if (!ticket.current() || !current()) return;
      const snapshot = await client.addJournalNote(date, text);
      if (!current()) return;
      if (publish()) deps.setJournal(snapshot);
      deps.clearNote(note);
      await Promise.all([refreshJournalDays(), refreshWeeklyJournal(date)]);
      if (current()) deps.banner("工作记录已加入今日 Journal；凭据样式内容会自动脱敏");
    } catch (error) { if (current()) deps.banner(errorText(error, "添加 Journal 记录失败")); }
    finally { ticket.release(); if (current()) deps.setBusy(false); }
  };
  const resetJournal = () => { reads.invalidate(); writes.invalidate(); deps.reset(); deps.setBusy(false); };
  return { refreshJournalDays, refreshJournal, refreshWeeklyJournal, refreshJournalContinuation, snapshotTodayJournal,
    selectJournalDay, continueFromYesterday, saveJournalSnapshot, addJournalNote, resetJournal,
    invalidate: () => { reads.invalidate(); writes.invalidate(); } };
}
