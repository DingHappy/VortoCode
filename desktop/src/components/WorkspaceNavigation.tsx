import { Archive, Clock3, ListChecks, MoreHorizontal, Puzzle, ShieldCheck, Target, UserRound, Users } from "lucide-react";
import { useEffect, useId, useState } from "react";
import type { AssistantPage } from "./AssistantMode";
import { NavItem } from "./ui";

/** Only navigates to existing views. Counts and actions remain in their original domain surfaces. */
export function WorkspaceNavigation({ current, onPage, onStaff }: { current: AssistantPage | null; onPage: (page: AssistantPage) => void; onStaff?: () => void }) {
  const extra = current === "goals" || current === "customize" || current === "identity" || current === "audit";
  const [more, setMore] = useState(extra), id = useId();
  useEffect(() => { if (extra) setMore(true); }, [current, extra]);
  return <nav className="vc-workspace-navigation" aria-label="个人空间功能">
    <NavItem icon={<ListChecks aria-hidden="true" />} label="工作动态" current={current === "tasks"} onClick={() => onPage("tasks")} />
    <NavItem icon={<Clock3 aria-hidden="true" />} label="定时工作" current={current === "schedule"} onClick={() => onPage("schedule")} />
    {onStaff && <NavItem icon={<Users aria-hidden="true" />} label="专员" onClick={onStaff} />}
    <NavItem icon={<Archive aria-hidden="true" />} label="资料库" current={current === "library"} onClick={() => onPage("library")} />
    <NavItem icon={<MoreHorizontal aria-hidden="true" />} label="更多" aria-expanded={more} aria-controls={id} onClick={() => setMore(open => !open)} />
    <div id={id} className="vc-workspace-more" hidden={!more}>
      <NavItem icon={<Target aria-hidden="true" />} label="个人目标" current={current === "goals"} onClick={() => onPage("goals")} />
      <NavItem icon={<Puzzle aria-hidden="true" />} label="插件与连接" current={current === "customize"} onClick={() => onPage("customize")} />
      <NavItem icon={<UserRound aria-hidden="true" />} label="身份与记忆" current={current === "identity"} onClick={() => onPage("identity")} />
      <NavItem icon={<ShieldCheck aria-hidden="true" />} label="审核记录" current={current === "audit"} onClick={() => onPage("audit")} />
    </div>
  </nav>;
}
