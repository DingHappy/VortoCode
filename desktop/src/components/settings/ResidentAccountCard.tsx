import { useEffect, useRef, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { ACCOUNT_CHANGED_EVENT } from "../../lib/vortoAccount";
import { errorText } from "../../lib/errorText";
import { readResidentAccount, updateResidentAccount, residentAccountController, type ResidentAccountState } from "../../lib/residentAccount";

export function ResidentAccountStatus({ state, onRefresh, onUpdate }: {
  state: ResidentAccountState; onRefresh: () => void; onUpdate: (bind: boolean) => void;
}) {
  const { value, busy, error } = state;
  return <section className="settings-card" aria-label="手机与常驻助手">
    <div className="settings-card-head"><strong>手机与常驻助手</strong><button disabled={busy} onClick={onRefresh}>{busy ? "正在核对…" : "刷新状态"}</button></div>
    {error && <p role="alert">{error}</p>}
    {value ? <>
      <p>{value.name} · {value.registered ? value.belongsToCurrent === true ? "已关联当前账号" : value.belongsToCurrent === false ? "已关联其他账号" : "已有账号关联，当前登录待核对" : "尚未关联账号"}</p>
      <p className="settings-card-note">助手地址：{value.baseUrl}</p>
      <div className="settings-card-actions">
        {!value.registered && <button className="primary" disabled={busy} onClick={() => onUpdate(true)}>关联当前账号</button>}
        {value.registered && <button disabled={busy} onClick={() => onUpdate(false)}>解除关联</button>}
        {value.registered && value.belongsToCurrent === true && value.mobileUrl && <button disabled={busy} onClick={() => void openUrl(value.mobileUrl!)}>打开手机入口</button>}
      </div>
      {value.mobileUrl ? <p className="settings-card-note">手机入口：{value.mobileUrl}。账号服务仍需登记该入口的回调地址。</p>
        : <p className="settings-card-note">此服务尚未配置手机访问地址；关联账号后还需配置 HTTPS 入口和账号回调。</p>}
      {value.belongsToCurrent === false && <p className="settings-card-note">不会覆盖原账号；如需更换，先明确解除原关联，再关联新账号。</p>}
    </> : !error && <p className="settings-card-note">{busy ? "正在读取常驻助手的账号关联…" : "状态尚未核对，请刷新。"}</p>}
    <p className="settings-card-note">关联与解除均需原生窗口确认。解除会撤销账号手机读取授权。退出这台 Mac 的账号会保留常驻助手归属；手动配对单独管理。</p>
  </section>;
}

export function ResidentAccountCard({ projectId }: { projectId?: string }) {
  const [state, setState] = useState<ResidentAccountState>({ busy: false, value: null, error: "" });
  const controller = useRef<ReturnType<typeof residentAccountController> | null>(null);
  useEffect(() => {
    setState({ busy: false, value: null, error: "" });
    if (!projectId) return;
    const current = residentAccountController(projectId, {
      read: () => readResidentAccount(projectId), update: (value, bind) => updateResidentAccount(projectId, value, bind),
      publish: setState, error: error => errorText(error, "无法读取账号关联，请刷新后重试"),
    });
    controller.current = current;
    void current.refresh();
    const changed = () => { current.invalidate(); void current.refresh(); };
    window.addEventListener(ACCOUNT_CHANGED_EVENT, changed);
    return () => { current.dispose(); if (controller.current === current) controller.current = null; window.removeEventListener(ACCOUNT_CHANGED_EVENT, changed); };
  }, [projectId]);
  if (!projectId) return <section className="settings-card" aria-label="手机与常驻助手"><strong>手机与常驻助手</strong><p className="settings-card-note">请先在“电脑与连接”中登记常驻电脑，再关联当前 VortoCode 账号。手机入口连接常驻助手，本机项目不会自动成为手机助手。</p></section>;
  return <ResidentAccountStatus state={state} onRefresh={() => void controller.current?.refresh()} onUpdate={bind => void controller.current?.update(bind)} />;
}
