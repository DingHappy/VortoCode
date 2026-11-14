import type { AssistantAvatar, AssistantMemory, AssistantProfile, MemoryProposal } from "../types";

/** Window-local display snapshot only; not a second registry or an authorization. */
export function roleProfileSnapshot(profile: AssistantProfile, avatar: AssistantAvatar | null,
  memories: AssistantMemory[], proposals: MemoryProposal[]) {
  return structuredClone({ profile, avatar, memories, proposals });
}
