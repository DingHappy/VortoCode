import { useCallback, useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { PhoneKeys as PhoneKeyState } from "../types";

export type PhoneKeyClient = Pick<GatewayClient, "listPhoneKeys" | "decidePhoneKey" | "removeTrustedPhone">;

const when = (seconds: number) => new Date(seconds * 1000).toLocaleString();

/** 手机写入权限：手机入口在公网上，新手机的钥匙要在这里对过核对码才生效（ADR-011 修订）。 */
export function PhoneKeys({ client, connected }: { client: PhoneKeyClient | null | undefined; connected: boolean }) {
  const [state, setState] = useState<PhoneKeyState | null>(null);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState("");
  const [busy, setBusy] = useState(false);
  const current = useRef(client);
  current.current = client;

  const refresh = useCallback(async () => {
    if (!client || !connected) return;
    try {
      const value = await client.listPhoneKeys();
      if (current.current === client) { setState(value); setError(""); }
    } catch {
      if (current.current === client) setError("手机权限记录暂时读不到，请稍后刷新。");
    }
  }, [client, connected]);

  useEffect(() => { setState(null); setError(""); setConfirming(""); }, [client]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function act(run: () => Promise<void>) {
    setBusy(true);
    try { await run(); setError(""); }
    catch (failure) { if (current.current === client) setError(failure instanceof Error ? failure.message : "操作没成功，请刷新后重试"); }
    finally { setConfirming(""); setBusy(false); await refresh(); }
  }

  if (!client || !connected || (state && !state.pending.length && !state.trusted.length && !error) || (!state && !error)) return null;
  return <section className="assistant-section" aria-label="手机写入权限">
    <h5>手机写入权限</h5>
    {error && <p role="status">{error}</p>}
    {!!state?.pending.length && <p>有手机申请在手机上批准、随手记和撤销。先看手机上显示的核对码，和这里一致才批准；不是你本人，请拒绝并修改账号密码。</p>}
    <ul className="assistant-audit">
      {state?.pending.map(item => <li key={item.device_id}>
        <span className="assistant-audit-text">
          <strong>核对码 {item.code}</strong>
          <small>新手机申请 · {when(item.requested_at)}</small>
        </span>
        {confirming === item.device_id
          ? <><button className="primary" disabled={busy} onClick={() => void act(() => client.decidePhoneKey(item.device_id, true))}>核对码一致，批准</button>
              <button disabled={busy} onClick={() => setConfirming("")}>取消</button></>
          : <><button disabled={busy} onClick={() => setConfirming(item.device_id)}>批准</button>
              <button disabled={busy} onClick={() => void act(() => client.decidePhoneKey(item.device_id, false))}>拒绝</button></>}
      </li>)}
      {state?.trusted.map(item => <li key={item.code}>
        <span className="assistant-audit-text">
          <strong>已信任的手机 · {item.code}</strong>
          <small>{when(item.approved_at)} 起可写入</small>
        </span>
        {confirming === item.code
          ? <><button disabled={busy} onClick={() => void act(() => client.removeTrustedPhone(item.code))}>确认移除</button>
              <button disabled={busy} onClick={() => setConfirming("")}>取消</button></>
          : <button disabled={busy} onClick={() => setConfirming(item.code)}>移除</button>}
      </li>)}
    </ul>
  </section>;
}
