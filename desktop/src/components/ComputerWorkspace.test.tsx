import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { ComputerWorkspaceView } from "./ComputerWorkspace";
import { validComputerFrame } from "../hooks/useComputerWorkspace";

it("only accepts the native owned-loopback viewer URL shape", () => {
  expect(validComputerFrame("http://127.0.0.1:12345/")).toBe(true);
  for(const value of ["https://example.com/","http://user@127.0.0.1:1234/","http://127.0.0.1:1234/other","javascript:alert(1)"])
    expect(validComputerFrame(value)).toBe(false);
});
it("removes a frame after the connection is replaced and does not invoke any action while rendering", () => {
  const act=vi.fn();
  const html=renderToStaticMarkup(<ComputerWorkspaceView view={{target:"ssh:vm",id:1,url:"http://127.0.0.1:12345/"}}
    live={false} onClose={act} onRetry={act} onWindow={act}/>);
  expect(html).not.toContain("<iframe"); expect(html).toContain("画面连接已结束"); expect(act).not.toHaveBeenCalled();
  const live=renderToStaticMarkup(<ComputerWorkspaceView view={{target:"ssh:vm",id:1,url:"http://127.0.0.1:12345/"}}
    onClose={act} onRetry={act} onWindow={act}/>);
  expect(live).toContain('sandbox="allow-scripts allow-same-origin"'); expect(live).not.toContain("allow-top-navigation");
});
it("助理面板里的电脑入口是一个小按钮：设置过就「打开电脑看」，没设置就「连接电脑」", async () => {
  const { ComputerLauncherView } = await import("./ComputerWorkspace");
  type Status = Parameters<typeof ComputerLauncherView>[0]["status"];
  const set = { target: "ssh:vorto-code", sshConnected: false, error: null, checkedAt: null } as unknown as Status;
  const html = renderToStaticMarkup(<ComputerLauncherView status={set} busy={false} onOpen={() => {}} onSettings={() => {}} />);
  expect(html).toContain('class="vc-panel-computer"');
  expect(html).toContain('title="vorto-code · 未连接"');
  expect(html).toContain("打开电脑看</button>");
  expect(renderToStaticMarkup(<ComputerLauncherView status={null} busy={false} onOpen={() => {}} onSettings={() => {}} />)).toContain("连接电脑</button>");
  expect(renderToStaticMarkup(<ComputerLauncherView status={set} busy onOpen={() => {}} onSettings={() => {}} />)).toContain("正在连接…</button>");
});
