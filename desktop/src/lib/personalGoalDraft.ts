type GoalDraftFields = { objective: string; criteria: string; next: string; due: string; followUp: string };

export type PersonalGoalDraft = GoalDraftFields & {
  requestId: string;
  submittedRequestId: string | null;
  foundGoalId: string | null;
};

type GoalDraftAction =
  | { type: "edit"; field: keyof GoalDraftFields; value: string; requestId: string }
  | { type: "submit"; requestId: string }
  | { type: "read"; goalIds: readonly string[]; requestId: string }
  | { type: "reset"; requestId: string };

export function emptyPersonalGoalDraft(requestId: string): PersonalGoalDraft {
  return { requestId, objective: "", criteria: "", next: "", due: "", followUp: "",
    submittedRequestId: null, foundGoalId: null };
}

// Request identity belongs to the draft, not to render memoization. Only a
// validated, current-source read may reconcile a submitted draft with storage.
export function personalGoalDraft(state: PersonalGoalDraft, action: GoalDraftAction): PersonalGoalDraft {
  switch (action.type) {
    case "edit":
      return state[action.field] === action.value ? state
        : { ...state, [action.field]: action.value, requestId: action.requestId,
          submittedRequestId: null, foundGoalId: null };
    case "submit":
      return action.requestId === state.requestId ? { ...state, submittedRequestId: action.requestId } : state;
    case "read": {
      const id = `personal-${state.requestId}`;
      return state.submittedRequestId === state.requestId && action.goalIds.includes(id)
        ? { ...emptyPersonalGoalDraft(action.requestId), foundGoalId: id } : state;
    }
    case "reset":
      return emptyPersonalGoalDraft(action.requestId);
  }
}
