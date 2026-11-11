import type { TaskItem } from "../types";
import type { StaffBudgetSettings, StaffDepartment, StaffMember, StaffRole, StaffSnapshot, StaffUsage } from "./staff";
import type { FeedItem } from "./activityFeed";

export interface DepartmentNoteSnapshot { revision: number; text: string; sources: string[]; tainted: boolean; digest: string }
export interface NoteSuggestion { id: string; digest: string; text: string; task_id: string; round: number; member: string; sources: string[]; tainted: boolean }
export interface NotesDecision { revision?: number; selected?: Record<string, { approve: boolean; digest: string }>;
  owner_text?: string; public?: boolean; resume?: string; cancel_save?: string }
export interface DepartmentDetail {
  department: StaffDepartment; members: StaffMember[]; roles: StaffRole[]; tasks: TaskItem[];
  tasks_complete: boolean;
  notes: { current: Omit<DepartmentNoteSnapshot, "digest">; snapshot: DepartmentNoteSnapshot | null;
    pending: NoteSuggestion[]; history: unknown[]; error: string;
    applying: { request: string; target: Omit<DepartmentNoteSnapshot, "digest"> } | null };
  usage: StaffUsage; budget_settings: StaffBudgetSettings;
}
export interface DepartmentClient {
  readDepartment(id: string): Promise<DepartmentDetail>;
  decideDepartmentNotes(id: string, body: NotesDecision): Promise<DepartmentNoteSnapshot>;
}

/** Every decision starts with a fresh version and exact suggestion digests; no automatic retry. */
export async function decideNotes(client: DepartmentClient, shown: DepartmentDetail, selected: string[], approve: boolean,
  ownerText: string, isPublic: boolean, isCurrent: () => boolean) {
  const fresh = await client.readDepartment(shown.department.id);
  if (!isCurrent()) return;
  if (fresh.department.revision !== shown.department.revision || fresh.notes.applying || !fresh.notes.snapshot
    || fresh.notes.current.revision !== shown.notes.current.revision) throw new Error("部门或笔记已变化，请刷新后重新核对");
  const choices: NonNullable<NotesDecision["selected"]> = {};
  for (const id of selected) {
    const original = shown.notes.pending.find(item => item.id === id), current = fresh.notes.pending.find(item => item.id === id);
    if (!original || !current || original.digest !== current.digest) throw new Error("笔记建议已变化，请刷新后重新选择");
    choices[id] = { approve, digest: current.digest };
  }
  if (!isCurrent()) return;
  const result = await client.decideDepartmentNotes(shown.department.id, { revision: fresh.notes.current.revision,
    selected: choices, owner_text: ownerText, public: isPublic });
  if (isCurrent() && (!result || typeof result.revision !== "number")) throw new Error("笔记保存回执待核对，请刷新");
}

export function departmentItems(snapshot: StaffSnapshot | null): FeedItem[] {
  return (snapshot?.departments ?? []).filter(item => item.status !== "retired" && ((item.pending_notes ?? 0) > 0 || item.notes_pending_save))
    .map(item => ({ id: `department:${item.id}:notes`, departmentId: item.id, title: item.name,
      summary: item.notes_pending_save ? "笔记保存中断，等你核对原决定" : `有 ${item.pending_notes} 条笔记建议等你核对`,
      sourceLabel: "部门", status: "waiting", at: "" }));
}
