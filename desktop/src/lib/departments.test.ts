import { expect, it, vi } from "vitest";
import { decideNotes, departmentItems, type DepartmentClient } from "./departments";
import { departmentFixture, directoryFixture } from "../../qa/department-fixture";
import { assistantAttention } from "./assistantAttention";
import { staffController } from "./staff";

function setup() {
  const detail = departmentFixture();
  const client: DepartmentClient = { readDepartment: vi.fn(async () => structuredClone(detail)),
    decideDepartmentNotes: vi.fn(async () => ({ ...detail.notes.snapshot!, revision: 2 })) };
  return { detail, client };
}
it("sends only explicitly selected suggestions, with their exact digests and the current revision", async () => {
  const { detail, client } = setup(), chosen = detail.notes.pending[0];
  await decideNotes(client, detail, [chosen.id], true, "", true, () => true);
  expect(client.decideDepartmentNotes).toHaveBeenCalledWith(detail.department.id, { revision: 1,
    selected: { [chosen.id]: { approve: true, digest: chosen.digest } }, owner_text: "", public: true });
});
it("discard and direct owner preference use explicit separate decisions", async () => {
  const { detail, client } = setup(), chosen = detail.notes.pending[1];
  await decideNotes(client, detail, [chosen.id], false, "", false, () => true);
  expect(client.decideDepartmentNotes).toHaveBeenLastCalledWith(detail.department.id, { revision: 1,
    selected: { [chosen.id]: { approve: false, digest: chosen.digest } }, owner_text: "", public: false });
  await decideNotes(client, detail, [], true, "先给我一句结论", true, () => true);
  expect(client.decideDepartmentNotes).toHaveBeenLastCalledWith(detail.department.id, { revision: 1, selected: {}, owner_text: "先给我一句结论", public: true });
});
it.each(["department", "notes", "digest", "removed"])("stale %s never causes a write", async field => {
  const { detail, client } = setup(), fresh = structuredClone(detail);
  if (field === "department") fresh.department.revision++;
  else if (field === "notes") fresh.notes.current.revision++;
  else if (field === "digest") fresh.notes.pending[0].digest = "changed";
  else fresh.notes.pending = [];
  vi.mocked(client.readDepartment).mockResolvedValue(fresh);
  await expect(decideNotes(client, detail, [detail.notes.pending[0].id], true, "", true, () => true)).rejects.toThrow("变化");
  expect(client.decideDepartmentNotes).not.toHaveBeenCalled();
});
it("a disposed page or changed client cannot finish a late snapshot", async () => {
  const { detail, client } = setup();
  await decideNotes(client, detail, [detail.notes.pending[0].id], true, "", true, () => false);
  expect(client.decideDepartmentNotes).not.toHaveBeenCalled();
});
it("one department contributes one owner decision without copying private suggestion bodies", () => {
  const snapshot = directoryFixture();
  const items = departmentItems(snapshot);
  expect(items).toHaveLength(1); expect(items[0].departmentId).toBe("dept-public");
  expect(items[0].summary).not.toContain("script"); expect(assistantAttention(items).decisions).toHaveLength(1);
  snapshot.departments![0].pending_notes = 0;
  expect(departmentItems(snapshot)).toHaveLength(0);
});
it("budget settings use a fresh version and never silently retry a write", async () => {
  const snapshot = directoryFixture(), write = vi.fn(async settings => settings);
  const client = { readStaff: vi.fn(async () => structuredClone(snapshot)), setStaffBudgets: write,
    decideStaff: vi.fn(), setStaffStatus: vi.fn() };
  const publish = vi.fn(), controller = staffController(client, publish);
  await controller.refresh();
  await controller.budgets({ revision: 99, daily_tokens: 100, task_tokens: 20 });
  expect(write).not.toHaveBeenCalled();
  await controller.refresh();
  await controller.budgets({ revision: 1, daily_tokens: 100, task_tokens: 20 });
  expect(write).toHaveBeenCalledTimes(1);
  controller.dispose();
});
