import type { GoalCriterion, PersonalGoal } from "../types";

const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string";
const identifier = (value: unknown): value is string => text(value) && Boolean(value.trim());
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const strings = (value: unknown) => Array.isArray(value) && value.every(text);
const optional = (item: Record<string, unknown>, keys: string[], check: (value: unknown) => boolean) =>
  keys.every(key => item[key] === undefined || check(item[key]));
function stamp(value: unknown): boolean {
  if (!text(value)) return false;
  if (value === "") return true;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!parts) return false;
  const [year, month, day, hour, minute] = parts.slice(1, 6).map(Number);
  const second = Number(parts[6] ?? 0);
  const days = [31, year % 400 === 0 || (year % 4 === 0 && year % 100 !== 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return year >= 1 && day >= 1 && day <= days[month - 1] && hour < 24 && minute < 60 && second < 60
    && Number.isFinite(Date.parse(value));
}
const unique = (items: Array<{ id: unknown }>) => new Set(items.map(item => item.id)).size === items.length;

function followUp(value: unknown): boolean {
  return record(value) && optional(value, ["id", "text", "reason"], text)
    && optional(value, ["status"], identifier) && optional(value, ["attempts"], count)
    && optional(value, ["due_at", "accepted_at", "attempted_at", "user_acknowledged_at"], stamp);
}

function goal(value: unknown): value is PersonalGoal {
  if (!record(value) || !text(value.id) || !/^personal-[A-Za-z0-9_-]{1,64}$/.test(value.id)
      || value.kind !== "personal" || !count(value.revision) || value.revision < 1
      || !identifier(value.objective) || !identifier(value.status)
      || ![value.constraints, value.non_goals, value.task_ids].every(strings)
      || !stamp(value.due_at) || !stamp(value.follow_up_at)
      || typeof value.overdue !== "boolean" || typeof value.follow_up_due !== "boolean"
      || !optional(value, ["next_action", "blocker", "follow_up_preview"], text)
      || !optional(value, ["created", "updated"], stamp)
      || !optional(value, ["follow_up_runtime_configured"], item => typeof item === "boolean")
      || !optional(value, ["follow_up"], followUp)
      || !optional(value, ["follow_up_history"], items => Array.isArray(items) && items.every(followUp))) return false;
  const criteria = value.acceptance_criteria, evidence = value.evidence, progress = value.progress;
  if (!Array.isArray(criteria) || !criteria.every(item => record(item) && identifier(item.id)
      && identifier(item.text) && identifier(item.status) && Array.isArray(item.evidence_ids) && item.evidence_ids.every(identifier))
      || !unique(criteria) || !Array.isArray(evidence) || !evidence.every(item => record(item) && identifier(item.id)
        && text(item.criterion_id) && identifier(item.kind) && text(item.summary) && typeof item.passed === "boolean"
        && optional(item, ["source", "stale_reason", "verified_commit"], text) && optional(item, ["created"], stamp))
      || !unique(evidence) || !record(progress) || ![progress.total, progress.passed, progress.failed].every(count)) return false;
  return progress.total === criteria.length && progress.passed === criteria.filter(item => item.status === "passed").length
    && progress.failed === criteria.filter(item => item.status === "failed").length;
}

/** Validate the fields consumed by both views; preserve additive fields and unknown state names. */
export function readPersonalGoalSnapshot(value: unknown): PersonalGoal[] {
  if (!Array.isArray(value) || !value.every(goal) || !unique(value)) {
    throw new Error("个人目标数据格式异常，请刷新核对");
  }
  return value;
}

/** A stored status or progress count alone is not acceptance evidence. */
export function hasConfirmedPersonalCriterion(goal: PersonalGoal, criterion: GoalCriterion): boolean {
  if (!criterion || !identifier(criterion.id) || !Array.isArray(goal.acceptance_criteria)
      || goal.acceptance_criteria.filter(item => item?.id === criterion.id).length !== 1 || !Array.isArray(goal.evidence)) return false;
  const ids = Array.isArray(criterion.evidence_ids) ? criterion.evidence_ids : [];
  const lastId = ids[ids.length - 1];
  if (!identifier(lastId)) return false;
  const matches = goal.evidence.filter(item => item && item.id === lastId);
  const evidence = matches.length === 1 ? matches[0] : undefined;
  return criterion.status === "passed" && evidence?.passed === true
    && evidence.kind === "personal_acceptance" && evidence.source === "user_confirmation" && evidence.criterion_id === criterion.id
    && identifier(evidence.summary) && !evidence.stale_reason;
}

export function hasConfirmedPersonalGoal(goal: PersonalGoal): boolean {
  const criteria = goal.acceptance_criteria;
  return identifier(goal.id) && goal.status === "achieved" && Array.isArray(criteria) && criteria.length > 0
    && criteria.every(criterion => hasConfirmedPersonalCriterion(goal, criterion));
}
