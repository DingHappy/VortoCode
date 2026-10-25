// Journal 状态仍由原 hook 持有；异步操作委托给可单独验证的 journalActions。
// 对外回调恒等稳定，日期和客户端通过最新引用读取；不增加持久化或第二份 Journal 数据。
import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import type { GatewayClient } from "../gateway";
import { journalActions } from "../lib/journalActions";
import { localDay } from "../lib/time";
import type { JournalContinuation, JournalDaySummary, JournalSnapshot, WeeklyJournalSnapshot } from "../types";

export function useJournal(clientRef: RefObject<GatewayClient | null>, onBanner: (text: string) => void) {
  const [journal, setJournal] = useState<JournalSnapshot | null>(null);
  const [journalDays, setJournalDays] = useState<JournalDaySummary[]>([]);
  const [weeklyJournal, setWeeklyJournal] = useState<WeeklyJournalSnapshot | null>(null);
  const [journalContinuation, setJournalContinuation] = useState<JournalContinuation | null>(null);
  const [journalView, setJournalView] = useState<"day" | "week">("day");
  const [journalDate, setJournalDate] = useState(localDay);
  const [journalNote, setJournalNote] = useState("");
  const [journalBusy, setJournalBusy] = useState(false);
  const latest = useRef({ clientRef, journalDate, journalNote, weeklyJournal, onBanner });
  latest.current = { clientRef, journalDate, journalNote, weeklyJournal, onBanner };
  const actionsRef = useRef<ReturnType<typeof journalActions> | null>(null);
  if (!actionsRef.current) actionsRef.current = journalActions({
    client: () => latest.current.clientRef.current,
    date: () => latest.current.journalDate,
    note: () => latest.current.journalNote,
    weekEnd: () => latest.current.weeklyJournal?.end_date,
    setDate: date => { latest.current.journalDate = date; setJournalDate(date); },
    setView: setJournalView,
    setJournal, setDays: setJournalDays,
    setWeek: snapshot => { latest.current.weeklyJournal = snapshot; setWeeklyJournal(snapshot); },
    setContinuation: setJournalContinuation,
    setBusy: setJournalBusy,
    clearNote: expected => setJournalNote(current => current === expected ? "" : current),
    banner: text => latest.current.onBanner(text),
    reset: () => {
      const date = localDay();
      latest.current.journalDate = date;
      latest.current.journalNote = "";
      latest.current.weeklyJournal = null;
      setJournal(null); setJournalDays([]); setWeeklyJournal(null); setJournalContinuation(null);
      setJournalDate(date); setJournalNote("");
    },
  });
  const actions = actionsRef.current;
  useEffect(() => () => actions.invalidate(), [actions]);
  return { journal, journalDays, weeklyJournal, journalContinuation, journalView, journalDate, journalNote, journalBusy,
    setJournalView, setJournalNote, ...actions };
}
