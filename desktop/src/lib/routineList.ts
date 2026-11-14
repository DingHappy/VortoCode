import type { StaffRoutine } from "./routines";
import { matchesCollectionQuery } from "./collectionQuery";

export type RoutineFilter = "all" | "enabled" | "paused";
/** Presentation only. A filter never changes enablement or hides retained records from the service. */
export function filterRoutineItems(items: StaffRoutine[], query: string, filter: RoutineFilter) {
  return items.filter(item => !item.retired && (filter === "all" || item.enabled === (filter === "enabled"))
    && matchesCollectionQuery(query, [item.name, item.member_name, item.role_title]));
}
