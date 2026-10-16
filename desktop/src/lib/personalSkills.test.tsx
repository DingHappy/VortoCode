import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PersonalSkillRequests } from "./personalSkills";
import { PersonalSkillsCard } from "../components/PersonalSkillsCard";
import { PluginsPage } from "../components/PluginsPage";

function deferred() { let resolve!: () => void; const promise = new Promise<void>(done => { resolve = done; }); return { promise, resolve }; }

describe("personal skill runtime ownership", () => {
  it("discards old-host results and does not send follow-up requests", async () => {
    const requests = new PersonalSkillRequests(); requests.bind({}, true);
    const wait = deferred(); const followup = vi.fn(); const apply = vi.fn();
    const old = requests.run(async current => { await wait.promise; if (!current()) return; followup(); apply(); });
    requests.bind({}, true); wait.resolve(); await old;
    expect(followup).not.toHaveBeenCalled(); expect(apply).not.toHaveBeenCalled();
  });
  it("allows a new runtime request while the old runtime is still waiting", async () => {
    const requests = new PersonalSkillRequests(); requests.bind({}, true);
    const wait = deferred(); const old = requests.run(async () => wait.promise);
    requests.bind({}, true); const fresh = vi.fn(); await requests.run(fresh);
    expect(fresh).toHaveBeenCalledOnce(); wait.resolve(); await old;
  });
  it("coalesces duplicate writes and releases after errors", async () => {
    const requests = new PersonalSkillRequests(); requests.bind({}, true);
    const wait = deferred(); const first = requests.run(async () => wait.promise); const duplicate = vi.fn();
    await requests.run(duplicate); expect(duplicate).not.toHaveBeenCalled();
    wait.resolve(); await first;
    await expect(requests.run(async () => { throw Error("fixture"); })).rejects.toThrow("fixture");
    await requests.run(duplicate); expect(duplicate).toHaveBeenCalledOnce();
  });
  it("invalidates requests on disconnect and unmount", async () => {
    const requests = new PersonalSkillRequests(); const owner = {}; requests.bind(owner, true);
    const wait = deferred(); let current!: () => boolean;
    const task = requests.run(async check => { current = check; await wait.promise; });
    requests.bind(owner, false); expect(current()).toBe(false);
    requests.bind(owner, true); expect(current()).toBe(false);
    requests.invalidate(); wait.resolve(); await task;
  });
});

describe("personal skill interface boundary", () => {
  it("requires a connection and explains loading versus execution", () => {
    const html = renderToStaticMarkup(<PersonalSkillsCard client={null} connection="disconnected" />);
    expect(html).toContain("连接助理后管理个人技能"); expect(html).toContain("不代表任务完成");
    expect(html).toContain("disabled");
  });
  it.each(["general", "project", "scratch"] as const)("only displays personal management in %s when General", scope => {
    const html = renderToStaticMarkup(<PluginsPage activeScope={scope} projectName="fixture" inspector={<span>project-fixture</span>}
      personalSkills={<span>personal-fixture</span>} onChooseProject={() => {}} onOpenBrowserSettings={() => {}} />);
    expect(html.includes("personal-fixture")).toBe(scope === "general");
  });
});
