import { invoke } from "@tauri-apps/api/core";

export interface StaffRole {
  id: string; title: string; brief: string; enabled: boolean; revision: number; builtin: boolean;
  grants: { operations: string[]; tools: string[]; sees: string[]; web: boolean; project: boolean };
  engine: string[]; max_steps: number; timeout_seconds: number;
}
export interface StaffMember {
  id: string; name: string; role: string; engine: string; model: string; revision: number;
  status: "active" | "paused" | "retired"; busy: number;
  recent_tasks: Array<{ id: string; status: string; review: string; delivery: string; prompt?: string }>;
}
export interface StaffProposal {
  id: string; digest: string; nonce: string; status: string; reason: string; requires_desktop_key: boolean;
  snapshot: { kind: "role" | "member"; data: Record<string, unknown>; previous: unknown; role_snapshot?: StaffRole };
}
export interface StaffSnapshot {
  roles: StaffRole[]; members: StaffMember[]; proposals: StaffProposal[];
  approval_key: { instance: string; key: { id: string; public_key: string } | null };
}
export interface StaffProof { public_key: string; signature: string }
export interface StaffSignRequest { instance: string; proposalId?: string; digest?: string; nonce?: string }
export interface StaffClient {
  readStaff(): Promise<StaffSnapshot>;
  decideStaff(id: string, approve: boolean, digest: string, signature?: string): Promise<unknown>;
  bindStaffKey(proof: StaffProof, authorization: string): Promise<unknown>;
  setStaffStatus(id: string, status: StaffMember["status"], revision: number): Promise<unknown>;
}
export interface StaffState { value: StaffSnapshot | null; busy: boolean; error: string }
export function signStaff(request: StaffSignRequest): Promise<StaffProof> {
  return invoke<StaffProof>("staff_approval_sign", { instance: request.instance, proposalId: request.proposalId ?? null,
    digest: request.digest ?? null, nonce: request.nonce ?? null }).catch(error => {
      throw new Error(typeof error === "string" ? error : "批准钥匙操作未完成，提议保持待批；请在 Desktop 上重试");
    });
}

/** Every write uses a freshly checked snapshot; a disposed page cannot finish a late signature. */
export function staffController(client: StaffClient, publish: (state: StaffState) => void,
  sign: (request: StaffSignRequest) => Promise<StaffProof> = signStaff) {
  let active = true, serial = 0;
  let state: StaffState = { value: null, busy: false, error: "" };
  function emit(patch: Partial<StaffState>) { if (active) { state = { ...state, ...patch }; publish(state); } }
  async function refresh() {
    if (!active || state.busy) return;
    const read = ++serial;
    emit({ busy: true, error: "" });
    try { const value = await client.readStaff(); if (active && read === serial) emit({ value }); }
    catch (error) { if (active && read === serial) emit({ error: error instanceof Error ? error.message : "名录读取失败，请刷新" }); }
    finally { if (active && read === serial) emit({ busy: false }); }
  }
  async function change(run: (fresh: StaffSnapshot) => Promise<void>) {
    if (!active || state.busy || !state.value || state.error) return;
    ++serial;
    emit({ busy: true, error: "" });
    try {
      const fresh = await client.readStaff();
      if (!active) return;
      await run(fresh);
      if (!active) return;
      const value = await client.readStaff();
      if (active) emit({ value });
    } catch (error) { if (active) emit({ error: error instanceof Error ? error.message : "结果待核对，请刷新后再操作" }); }
    finally { if (active) emit({ busy: false }); }
  }
  return {
    refresh,
    dispose() { active = false; ++serial; },
    status(member: StaffMember, status: StaffMember["status"]) {
      return change(async fresh => {
        const current = fresh.members.find(item => item.id === member.id);
        if (!current || current.revision !== member.revision) throw new Error("专员已变化，请刷新后重新核对");
        await client.setStaffStatus(member.id, status, member.revision);
      });
    },
    enroll(authorization: string) {
      return change(async fresh => {
        if (fresh.approval_key.key) throw new Error("已登记批准钥匙，请刷新核对");
        if (!authorization.trim()) throw new Error("请先取得主机本地一次性授权码");
        const instance = fresh.approval_key.instance;
        const proof = await sign({ instance });
        if (!active) return;
        await client.bindStaffKey(proof, authorization.trim());
        if (!active) return;
        const bound = await client.readStaff();
        if (bound.approval_key.instance !== instance || bound.approval_key.key?.public_key !== proof.public_key)
          throw new Error("批准钥匙登记结果待核对，请刷新");
      });
    },
    decide(proposal: StaffProposal, approve: boolean) {
      return change(async fresh => {
        const current = fresh.proposals.find(item => item.id === proposal.id);
        if (!current || !["pending", "applying"].includes(current.status) || current.digest !== proposal.digest)
          throw new Error("提议已变化，请刷新后重新核对");
        let signature: string | undefined;
        if (approve && current.requires_desktop_key) {
          const instance = fresh.approval_key.instance;
          if (!fresh.approval_key.key) throw new Error("请先用主机本地一次性授权码登记批准钥匙，提议保持待批");
          const proof = await sign({ instance, proposalId: current.id, digest: current.digest, nonce: current.nonce });
          if (!active) return;
          if (proof.public_key !== fresh.approval_key.key?.public_key) throw new Error("批准钥匙与登记不一致，未批准");
          signature = proof.signature;
        }
        if (active) await client.decideStaff(current.id, approve, current.digest, signature);
      });
    },
  };
}
