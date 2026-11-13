import { useEffect, useRef, useState } from 'react';
import type { DesktopProjectProfile } from '../../types';
import { INITIAL_LOCAL_RESIDENT, localResidentClient, localResidentController, type LocalResidentAction, type LocalResidentClient, type LocalResidentInstall, type LocalResidentState, type LocalResidentStatus } from '../../lib/localResident';
import './LocalResidentCard.css';

type LocalResidentViewProps = {
  state: LocalResidentState;
  onRefresh: () => void;
  onAction: (action: LocalResidentAction, options?: LocalResidentInstall) => void;
  selectedHostId?: string;
  connected?: boolean;
  canUse?: boolean;
  blocked?: boolean;
};
const STATE_LABELS: Record<LocalResidentStatus['state'], string> = { not_installed: '尚未安装', stopped: '已停止', starting: '正在启动', running: '正在运行', unavailable: '状态无法确认' };

export function LocalResidentView({ state, onRefresh, onAction, selectedHostId, connected = false, canUse = false, blocked = false }: LocalResidentViewProps) {
  const [copyModelProfile, setCopyModelProfile] = useState(false);
  const [cronEnabled, setCronEnabled] = useState(false);
  const { value, busy, error, notice, checkedAt } = state;
  const ready = Boolean(value?.supported && value.revision && !error && !busy && !blocked && !['starting', 'unavailable'].includes(value.state));
  const using = Boolean(!error && value?.state === 'running' && value.projectId && value.projectId === selectedHostId && connected);
  return <section className="settings-card local-resident-card" aria-label="本机常驻助理">
    <div className="settings-card-head"><strong>本机常驻助理</strong><button disabled={busy} onClick={onRefresh}>{busy ? '正在核对…' : '刷新状态'}</button></div>
    <p className="settings-card-note">退出 Desktop 后仍可运行；这台 Mac 需保持开机，睡眠时暂停。</p>
    {error && <p className="local-resident-error" role="alert">{error}</p>}
    {value ? <>
      <p className="local-resident-status">{error ? '上次读取：' : ''}{STATE_LABELS[value.state]}{using ? ' · 当前对话正在使用' : ''}</p>
      {checkedAt && <p className="settings-card-note">{error ? '上次' : '本次'}核对：{new Date(checkedAt).toLocaleTimeString()}</p>}
      {value.message && <p className="settings-card-note">{value.message}</p>}
      {!value.supported ? <p className="settings-card-note">此设备暂不支持本机常驻助理。可连接其他电脑上的常驻助理。</p>
        : !value.installed ? <>
          <div className="local-resident-options">
            <label><input type="checkbox" checked={copyModelProfile} disabled={busy || blocked} onChange={event => setCopyModelProfile(event.target.checked)} /><span>同意复制当前已保存的模型配置</span></label>
            <p className="settings-card-note">配置独立保存；Desktop 退出模型账号不会撤销这份配置。</p>
            <label><input type="checkbox" checked={cronEnabled} disabled={busy || blocked} onChange={event => setCronEnabled(event.target.checked)} /><span>启用定时作业</span></label>
          </div>
          <div className="settings-actions"><button className="primary" disabled={!ready || !copyModelProfile} onClick={() => onAction('install', { cronEnabled, copyModelProfile })}>安装本机常驻助理</button></div>
        </> : <>
          <p className="settings-card-note">程序 {value.version || '版本待核对'} · Desktop 提供 {value.availableVersion || '版本待核对'} · 定时作业{value.cronEnabled ? '已开启' : '未开启'}</p>
          <div className="settings-actions" aria-label="本机常驻助理控制">
            {value.state === 'stopped' && <button className="primary" disabled={!ready} onClick={() => onAction('start')}>启动本机常驻助理</button>}
            {!using && <button className="primary" disabled={!ready || value.state !== 'running' || !value.launchId || !canUse} onClick={() => onAction('connect')}>使用本机常驻助理</button>}
          </div>
          <details className="settings-runtime-details"><summary>程序与模型配置</summary>
            <p className="settings-card-note">更新程序保留常驻配置。模型配置需单独应用；Desktop 退出模型账号不会撤销这份配置。</p>
            <div className="settings-actions">
              <button disabled={!ready} onClick={() => onAction('update')}>更新程序</button>
              <button disabled={!ready} onClick={() => onAction('apply_model')}>应用当前模型配置</button>
              {value.state === 'running' && <button className="danger" disabled={!ready || !value.launchId} onClick={() => onAction('stop')}>停止本机常驻助理</button>}
            </div>
          </details>
        </>}
      <details className="settings-runtime-details"><summary>运行范围</summary><p className="settings-card-note">启动后随登录运行；停止后不再自动启动。只接受本机连接。安装不会启动服务或切换当前对话；即时通讯与定时心跳不会启用。停止、更新和应用配置前会由原生窗口核对活动并确认。</p></details>
    </> : <p className="settings-card-note">{busy ? '正在读取本机常驻状态…' : '状态尚未核对，请刷新。'}</p>}
    {blocked && <p className="settings-card-note">请先处理其他设置的修改或操作，再管理本机常驻助理。</p>}
    {notice && <p className="settings-card-note" role="status">{notice}</p>}
  </section>;
}

export type LocalResidentCardProps = {
  onUse?: (profile: DesktopProjectProfile) => Promise<void>;
  selectedHostId?: string;
  connected?: boolean;
  onStatus?: (status: LocalResidentStatus) => void;
  onBusyChange?: (busy: boolean) => void;
  blocked?: boolean;
  client?: LocalResidentClient;
};
export function LocalResidentCard({ onUse, selectedHostId, connected, onStatus, onBusyChange, blocked = false, client = localResidentClient }: LocalResidentCardProps) {
  const [state, setState] = useState<LocalResidentState>(INITIAL_LOCAL_RESIDENT);
  const callbacks = useRef({ onUse, onStatus, onBusyChange, blocked });
  callbacks.current = { onUse, onStatus, onBusyChange, blocked };
  const controller = useRef<ReturnType<typeof localResidentController> | null>(null);
  useEffect(() => {
    const current = localResidentController(client, next => { setState(next); callbacks.current.onBusyChange?.(next.busy); }, async profile => {
      if (callbacks.current.blocked || !callbacks.current.onUse) throw new Error('当前设置尚未就绪，请核对后重新连接');
      await callbacks.current.onUse(profile);
    }, status => callbacks.current.onStatus?.(status));
    controller.current = current;
    void current.refresh();
    return () => { current.dispose(); if (controller.current === current) controller.current = null; callbacks.current.onBusyChange?.(false); };
  }, [client]);
  return <LocalResidentView state={state} onRefresh={() => void controller.current?.refresh()} onAction={(action, options) => {
    if (!callbacks.current.blocked) void controller.current?.change(action, options);
  }} selectedHostId={selectedHostId} connected={connected} canUse={Boolean(onUse)} blocked={blocked} />;
}
