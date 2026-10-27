// 本机 / 常驻电脑 / 远端项目三种连接之间切换的协调规则，收在一处、可单测。
// 以前散在 App.tsx 的几个 ref 里，由各调用方自己查标志；切换前要弹确认框时，
// 两次切换可以先后通过检查再一起进来。这里把三条规则钉住：
//   1. 同一时刻只允许一个切换（切项目、切范围、连常驻电脑），进门时再查一次；
//   2. 本机引擎启动只允许一个，启动中拒绝会先断开连接的切换；
//   3. 连接被更新的连接取代不算失败，只有真失败才记下来给调用方提示。
// 连接对象本身仍由 SessionConnection 管代次；这里只管「谁在切、连在哪」。

type Ref<T> = { current: T };

export interface CoordinatorListener {
  /** 切换开始/结束：驱动界面上的「正在切换」状态 */
  switching?: (active: boolean) => void;
  /** 本机引擎启动开始/结束 */
  starting?: (active: boolean) => void;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export class ConnectionCoordinator {
  private switchingNow = false;
  private startingNow = false;
  /** 当前连着的远端登记 id；连本机时为 null */
  readonly remote: Ref<string | null> = { current: null };
  /** 当前连着的远端若是常驻助理就记下它的 id；与 remote 相等才算「在常驻电脑上」 */
  readonly assistantHost: Ref<string | null> = { current: null };
  /** 最近一次连接是真的失败了（而不是被更新的连接取代）：只有这时才提示用户 */
  private failed = false;

  constructor(private listener: CoordinatorListener = {}) {}

  listen(listener: CoordinatorListener) { this.listener = listener; }

  get switching() { return this.switchingNow; }
  get starting() { return this.startingNow; }
  /** 本机在切换或引擎在启动：这时不该再发起会断开连接的操作 */
  get busy() { return this.switchingNow || this.startingNow; }
  get onAssistantHost() { return Boolean(this.remote.current) && this.remote.current === this.assistantHost.current; }
  get lastConnectFailed() { return this.failed; }

  /** 连接开始：清掉上一轮的失败记录 */
  connectBegan() { this.failed = false; }
  /** 当前这轮连接真失败了（调用方已确认没被取代） */
  connectFailed() { this.failed = true; }

  /** 切换的单飞门：已有切换在跑就返回 false，不执行 body。 */
  async exclusiveSwitch(body: () => Promise<boolean>): Promise<boolean> {
    if (this.switchingNow) return false;
    this.switchingNow = true;
    this.listener.switching?.(true);
    try {
      return await body();
    } finally {
      this.switchingNow = false;
      this.listener.switching?.(false);
    }
  }

  /** 本机引擎启动的单飞门：已在启动就返回 false。 */
  async exclusiveStart(body: () => Promise<boolean>): Promise<boolean> {
    if (this.startingNow) return false;
    this.startingNow = true;
    this.listener.starting?.(true);
    try {
      return await body();
    } finally {
      this.startingNow = false;
      this.listener.starting?.(false);
    }
  }

  /** 等本机的切换和启动都走完；超时仍忙返回 false。 */
  async waitUntilIdle(timeoutMs = 15_000, stepMs = 200, wait: (ms: number) => Promise<void> = sleep): Promise<boolean> {
    for (let waited = 0; this.busy && waited < timeoutMs; waited += stepMs) await wait(stepMs);
    return !this.busy;
  }
}

export interface AssistantPlacement<Host extends { id: string; name: string }> {
  /** 已登记的常驻电脑；没有就直接用本机 */
  host?: Host;
  connectHost: (host: Host) => Promise<boolean>;
  /** 当前不在本机通用工作区（连着远端或在项目里），需要先切回来 */
  awayFromLocalGeneral: () => boolean;
  switchToLocalGeneral: () => Promise<boolean>;
  notify: (message: string) => void;
}

/**
 * 助理住在哪：优先常驻电脑（合上笔记本也照常干活）；没登记或连不上，退回本机通用工作区。
 * 返回落点：常驻电脑的 id、本机（null），或两边都没落下（undefined）。
 */
export async function placeAssistant<Host extends { id: string; name: string }>(
  deps: AssistantPlacement<Host>,
): Promise<string | null | undefined> {
  if (deps.host) {
    if (await deps.connectHost(deps.host)) return deps.host.id;
    deps.notify(`常驻电脑「${deps.host.name}」暂时连不上，先用本机助理；它不在时你交代的事不会继续做。`);
  }
  if (deps.awayFromLocalGeneral() && !await deps.switchToLocalGeneral()) return undefined;
  return null;
}
