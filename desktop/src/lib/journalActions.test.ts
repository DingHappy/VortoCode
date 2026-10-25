import { expect, it, vi } from "vitest";
import { journalActions, type JournalClient } from "./journalActions";
import { localDay, previousDay } from "./time";
import type { JournalSnapshot, JournalDaySummary, WeeklyJournalSnapshot, JournalContinuation } from "../types";

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
const day = (date = localDay()) => ({ date } as JournalSnapshot);
const week = (end_date = localDay()) => ({ end_date } as WeeklyJournalSnapshot);
function client(): JournalClient {
  return { listJournalDays: vi.fn(async () => []), getJournal: vi.fn(async date => day(date)),
    getWeeklyJournal: vi.fn(async date => week(date)), getJournalContinuation: vi.fn(async date => ({ from_date: date } as JournalContinuation)),
    snapshotJournal: vi.fn(async date => day(date)), addJournalNote: vi.fn(async date => day(date)) };
}
function harness(owner = client()) {
  const state = { client: owner as JournalClient | null, date: localDay(), note: "", busy: false, view: "day",
    journal: null as JournalSnapshot | null, days: [] as JournalDaySummary[], week: null as WeeklyJournalSnapshot | null,
    continuation: null as JournalContinuation | null, banners: [] as string[] };
  const actions = journalActions({ client: () => state.client, date: () => state.date, note: () => state.note,
    weekEnd: () => state.week?.end_date, setDate: date => { state.date = date; }, setView: value => { state.view = value; },
    setJournal: value => { state.journal = value; }, setDays: value => { state.days = value; },
    setWeek: value => { state.week = value; }, setContinuation: value => { state.continuation = value; },
    setBusy: value => { state.busy = value; }, clearNote: expected => { if (state.note === expected) state.note = ""; },
    banner: text => state.banners.push(text), reset: () => {
      state.date = localDay(); state.journal = null; state.days = []; state.week = null; state.continuation = null; state.note = "";
    } });
  return { state, actions, owner };
}

it("discards a slow old owner's results and subsequent reads", async () => {
  const h = harness(), slow = deferred<JournalSnapshot>();
  vi.mocked(h.owner.getJournal).mockReturnValueOnce(slow.promise);
  const old = h.actions.refreshJournal();
  h.state.client = client();
  await h.actions.refreshJournal();
  const current = h.state.journal;
  slow.resolve({ ...day(), headline: "old owner" }); await old;
  expect(h.state.journal).toBe(current);
});

it("only the newest request in a date's view can publish", async () => {
  const h = harness(), slow = deferred<JournalSnapshot>();
  vi.mocked(h.owner.getJournal).mockReturnValueOnce(slow.promise);
  const old = h.actions.refreshJournal();
  await h.actions.refreshJournal();
  slow.resolve({ ...day(), headline: "obsolete" }); await old;
  expect(h.state.journal?.headline).not.toBe("obsolete");
});

it("switching dates clears old data and old completion cannot clear the new loading state", async () => {
  const h = harness(), a = deferred<JournalSnapshot>(), b = deferred<JournalSnapshot>();
  const dateA = previousDay(localDay()), dateB = previousDay(dateA);
  vi.mocked(h.owner.getJournal).mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
  const first = h.actions.selectJournalDay(dateA), second = h.actions.selectJournalDay(dateB);
  a.resolve(day(dateA)); await first;
  expect(h.state.busy).toBe(true);
  expect(h.state.journal).toBeNull();
  b.resolve(day(dateB)); await second;
  expect(h.state.busy).toBe(false);
  expect(h.state.journal?.date).toBe(dateB);
  expect(h.state.week?.end_date).toBe(dateB);
  expect(h.state.continuation?.from_date).toBe(dateB);
});

it("reset invalidates outstanding work even if the same client and date are reused", async () => {
  const h = harness(), slow = deferred<JournalSnapshot>();
  vi.mocked(h.owner.getJournal).mockReturnValueOnce(slow.promise);
  const old = h.actions.refreshJournal();
  h.actions.resetJournal();
  slow.resolve(day()); await old;
  expect(h.state.journal).toBeNull();
  expect(h.state.busy).toBe(false);
});

it("a background snapshot does not overwrite the selected historical date", async () => {
  const h = harness();
  h.state.date = previousDay(localDay()); h.state.journal = day(h.state.date);
  await h.actions.snapshotTodayJournal();
  expect(h.state.journal?.date).toBe(h.state.date);
  expect(h.owner.snapshotJournal).toHaveBeenCalledWith(localDay());
});

it("a completed write cannot clear a newer draft", async () => {
  const h = harness(), slow = deferred<JournalSnapshot>();
  h.state.note = "original note";
  vi.mocked(h.owner.addJournalNote).mockReturnValueOnce(slow.promise);
  const writing = h.actions.addJournalNote();
  h.state.note = "new unsaved note";
  slow.resolve(day()); await writing;
  expect(h.state.note).toBe("new unsaved note");
  expect(h.owner.addJournalNote).toHaveBeenCalledOnce();
});

it.each(["success", "error"])("a write's late %s cannot refresh or report into another project", async outcome => {
  const h = harness(), slow = deferred<JournalSnapshot>();
  vi.mocked(h.owner.snapshotJournal).mockReturnValueOnce(slow.promise);
  const writing = h.actions.saveJournalSnapshot();
  const next = client(); h.state.client = next;
  h.actions.resetJournal();
  if (outcome === "success") slow.resolve(day()); else slow.reject(new Error("old owner error"));
  await writing;
  expect(h.state.journal).toBeNull();
  expect(h.state.banners).toEqual([]);
  expect(h.owner.listJournalDays).not.toHaveBeenCalled();
  expect(next.listJournalDays).not.toHaveBeenCalled();
  expect(h.state.busy).toBe(false);
});

it("successful current writes refresh the same project and report completion", async () => {
  const h = harness(); h.state.note = " note ";
  await h.actions.addJournalNote();
  expect(h.owner.addJournalNote).toHaveBeenCalledWith(localDay(), "note");
  expect(h.state.note).toBe("");
  expect(h.state.banners[0]).toContain("工作记录已加入");
  await h.actions.saveJournalSnapshot();
  expect(h.state.banners[1]).toContain("快照已保存");
  expect(h.state.busy).toBe(false);
});

it("choosing a date while disconnected does not leave loading stuck", async () => {
  const h = harness(); h.state.client = null;
  await h.actions.selectJournalDay(previousDay(localDay()));
  expect(h.state.busy).toBe(false);
  expect(h.state.journal).toBeNull();
});

it("rapid repeat clicks cannot add the same note twice", async () => {
  const h = harness(), slow = deferred<JournalSnapshot>(); h.state.note = "one note";
  vi.mocked(h.owner.addJournalNote).mockReturnValueOnce(slow.promise);
  const first = h.actions.addJournalNote(), second = h.actions.addJournalNote();
  expect(h.owner.addJournalNote).toHaveBeenCalledTimes(1);
  slow.resolve(day()); await Promise.all([first, second]);
});

it("coalesces background snapshots instead of overlapping their writes", async () => {
  const h = harness(), slow = deferred<JournalSnapshot>();
  vi.mocked(h.owner.snapshotJournal).mockReturnValueOnce(slow.promise);
  const first = h.actions.snapshotTodayJournal(), second = h.actions.snapshotTodayJournal();
  expect(h.owner.snapshotJournal).toHaveBeenCalledTimes(1);
  slow.resolve(day()); await Promise.all([first, second]);
});

it("a background snapshot cannot supersede a manual save in progress", async () => {
  const h = harness(), slow = deferred<JournalSnapshot>();
  vi.mocked(h.owner.snapshotJournal).mockReturnValueOnce(slow.promise)
    .mockResolvedValueOnce({ ...day(), headline: "older automatic snapshot" });
  const writing = h.actions.saveJournalSnapshot();
  await h.actions.snapshotTodayJournal();
  slow.resolve({ ...day(), headline: "new manual snapshot" }); await writing;
  expect(h.state.journal?.headline).toBe("new manual snapshot");
  expect(h.owner.snapshotJournal).toHaveBeenCalledTimes(1);
});

it("manual note writes wait for an already running background snapshot", async () => {
  const h = harness(), slow = deferred<JournalSnapshot>(); h.state.note = "note";
  vi.mocked(h.owner.snapshotJournal).mockReturnValueOnce(slow.promise);
  const background = h.actions.snapshotTodayJournal(), writing = h.actions.addJournalNote();
  expect(h.owner.addJournalNote).not.toHaveBeenCalled();
  slow.resolve(day()); await Promise.all([background, writing]);
  expect(h.owner.addJournalNote).toHaveBeenCalledTimes(1);
  expect(h.state.note).toBe("");
});

it("switching projects while waiting for a background snapshot prevents the queued manual write", async () => {
  const h = harness(), slow = deferred<JournalSnapshot>(); h.state.note = "old note";
  vi.mocked(h.owner.snapshotJournal).mockReturnValueOnce(slow.promise);
  const background = h.actions.snapshotTodayJournal(), writing = h.actions.addJournalNote();
  const next = client(); h.state.client = next; h.actions.resetJournal();
  slow.resolve(day()); await Promise.all([background, writing]);
  expect(h.owner.addJournalNote).not.toHaveBeenCalled();
  expect(next.addJournalNote).not.toHaveBeenCalled();
  expect(h.state.busy).toBe(false);
});

it("a failed manual write releases admission without retrying it automatically", async () => {
  const h = harness(); h.state.note = "note";
  vi.mocked(h.owner.addJournalNote).mockRejectedValueOnce(new Error("connection lost"));
  await h.actions.addJournalNote();
  expect(h.owner.addJournalNote).toHaveBeenCalledTimes(1);
  expect(h.state.note).toBe("note"); expect(h.state.busy).toBe(false);
  await h.actions.addJournalNote();
  expect(h.owner.addJournalNote).toHaveBeenCalledTimes(2);
});
