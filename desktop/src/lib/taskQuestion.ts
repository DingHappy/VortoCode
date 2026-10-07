import type { TaskItem } from "../types";
import { dispatchTaskIdentity } from "./taskDispatch";

export function taskQuestionIdentity(task: TaskItem, now = Date.now()) {
  if (task.dependencies?.result_valid === false) return null;
  const development = task.kind === "dev" || task.kind === "dev-resume";
  const state = task.development;
  const owner = development
    ? state?.version === 1 && state.owner_session === task.owner_session && state.plan_id === task.plan_id
      && /^sid-[A-Za-z0-9._-]{1,120}$/.test(state.owner_session) && Number.isInteger(state.round) && state.round >= 1
      ? { session: state.owner_session.slice(4), round: state.round } : null
    : dispatchTaskIdentity(task);
  const questions = development ? state?.questions : task.collaboration?.questions;
  if (!owner || task.status !== "blocked" || owner.round >= 3 || !Array.isArray(questions)) return null;
  const open = questions.filter((question) => question.status === "open");
  if (open.length !== 1) return null;
  const question = open[0];
  const deadline = Date.parse(question.expires_at);
  if (question.task_id !== task.id || question.round !== owner.round || question.answerer !== "owner"
      || !/^question-[A-Za-z0-9_-]+$/.test(question.id) || question.id.trim() !== question.id
      || !Number.isFinite(deadline) || deadline <= now || !question.question?.trim()
      || !Array.isArray(question.options) || question.options.length > 3
      || question.options.some((option) => typeof option !== "string")) return null;
  if (development && (question.plan_id !== task.plan_id || !question.block_id?.trim()
      || !/^[a-f0-9]{64}$/.test(question.plan_revision || "") || !question.branch?.startsWith("vorto/"))) return null;
  return { ...owner, question, ...(development ? { development: true } : {}) };
}
