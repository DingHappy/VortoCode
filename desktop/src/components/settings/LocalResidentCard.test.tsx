import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { LocalResidentView } from './LocalResidentCard';
import { INITIAL_LOCAL_RESIDENT, type LocalResidentState, type LocalResidentStatus } from '../../lib/localResident';

const status: LocalResidentStatus = { supported: true, installed: true, state: 'running', version: '0.2.60', availableVersion: '0.2.61', cronEnabled: false, projectId: 'local-fixture', revision: 'revision', launchId: 'launch', message: '' };
function render(value: LocalResidentStatus | null = status, overrides: Partial<LocalResidentState> = {}, selectedHostId?: string, connected = false) {
  return renderToStaticMarkup(<LocalResidentView state={{ ...INITIAL_LOCAL_RESIDENT, value, ...overrides }} onRefresh={() => {}} onAction={() => {}} canUse selectedHostId={selectedHostId} connected={connected} />);
}
describe('local resident card', () => {
  it('onboarding keeps model-copy consent and schedules disabled', () => {
    const markup = renderToStaticMarkup(<LocalResidentView onboarding state={{ ...INITIAL_LOCAL_RESIDENT, value: { ...status, installed: false, state: 'not_installed' } }}
      onRefresh={() => {}} onAction={() => {}} canUse />);
    expect(markup.match(/type="checkbox"/g)).toHaveLength(1);
    expect(markup).not.toContain('启用定时作业');
    expect(markup.match(/<button[^>]*>安装本机常驻助理<\/button>/)?.[0]).toContain('disabled');
  });
  it('keeps an unknown status explicit and offers no lifecycle mutation', () => {
    const markup = render(null);
    expect(markup).toContain('状态尚未核对');
    expect(markup).not.toContain('>安装本机常驻助理</button>');
    expect(markup).not.toContain('>使用本机常驻助理</button>');
  });
  it('requires separate explicit model-copy consent and defaults scheduled work off', () => {
    const markup = render({ ...status, installed: false, state: 'not_installed' });
    expect(markup).toContain('Desktop 退出模型账号不会撤销这份配置');
    expect(markup).toContain('定时作业');
    expect(markup.match(/<input[^>]*type="checkbox"[^>]*>/g)?.every(input => !input.includes('checked'))).toBe(true);
    expect(markup.match(/<button[^>]*>安装本机常驻助理<\/button>/)?.[0]).toContain('disabled');
    expect(markup).not.toContain('>使用本机常驻助理</button>');
  });
  it('only calls an explicitly selected connected local host the current conversation', () => {
    expect(render()).not.toContain('当前对话正在使用');
    expect(render(status, {}, 'remote-fixture', true)).not.toContain('当前对话正在使用');
    expect(render(status, {}, 'local-fixture', false)).not.toContain('当前对话正在使用');
    expect(render(status, {}, 'local-fixture', true)).toContain('当前对话正在使用');
  });
  it('separates program updates from applying model configuration', () => {
    const markup = render();
    expect(markup).toContain('更新程序');
    expect(markup).toContain('应用当前模型配置');
    expect(markup).toContain('停止本机常驻助理');
    expect(markup).toContain('0.2.60');
    expect(markup).toContain('0.2.61');
  });
  it('does not offer connection for a stopped or stale service', () => {
    const stopped = render({ ...status, state: 'stopped', launchId: undefined });
    expect(stopped).toContain('启动本机常驻助理');
    expect(stopped.match(/<button[^>]*>使用本机常驻助理<\/button>/)?.[0]).toContain('disabled');
    const stale = render(status, { error: '读取失败' }, 'local-fixture', true);
    expect(stale).toContain('上次读取');
    expect(stale).not.toContain('当前对话正在使用');
    expect(stale.match(/<button[^>]*>使用本机常驻助理<\/button>/)?.[0]).toContain('disabled');
    expect(stale).toContain('role="alert"');
  });
  it('keeps cleanup explicit and offers a retry without a running launch', () => {
    const markup = render({ ...status, state: 'cleanup_required', launchId: undefined });
    expect(markup).toContain('登录项待清理');
    const retry = markup.match(/<button[^>]*>重试移除登录项<\/button>/)?.[0];
    expect(retry).toBeDefined();
    expect(retry).not.toContain('disabled');
    for (const label of ['更新程序', '应用当前模型配置', '使用本机常驻助理']) {
      expect(markup.match(new RegExp(`<button[^>]*>${label}</button>`))?.[0]).toContain('disabled');
    }
    expect(markup).not.toContain('>启动本机常驻助理</button>');
  });
});
