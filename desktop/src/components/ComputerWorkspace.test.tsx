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
