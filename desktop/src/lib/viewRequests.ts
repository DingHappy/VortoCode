/** 仅记录界面请求的所属客户端和代次；没有数据缓存或持久化。 */
export class ViewRequests {
  private epoch = 0;
  private lanes = new Map<string, number>();
  constructor(private owner: () => object | null) {}

  capture(lane: string) {
    const owner = this.owner(), epoch = this.epoch;
    const revision = (this.lanes.get(lane) ?? 0) + 1;
    this.lanes.set(lane, revision);
    return { current: () => owner !== null && owner === this.owner() && epoch === this.epoch
      && revision === this.lanes.get(lane) };
  }

  invalidate() { this.epoch++; this.lanes.clear(); }
}

/** UI 写入的同步准入：同一客户端的操作结束前，不重复发起另一笔写入。 */
export class ViewMutations {
  private epoch = 0;
  private pending = new WeakMap<object, symbol>();
  constructor(private owner: () => object | null) {}
  busy(owner = this.owner()) { return owner !== null && this.pending.has(owner); }
  begin() {
    const owner = this.owner();
    if (!owner || this.pending.has(owner)) return null;
    const marker = Symbol(), epoch = this.epoch;
    this.pending.set(owner, marker);
    return {
      current: () => owner === this.owner() && epoch === this.epoch && this.pending.get(owner) === marker,
      release: () => { if (this.pending.get(owner) === marker) this.pending.delete(owner); },
    };
  }
  // 已发出的写入仍持有准入位，直到其结果返回；重置不代表远端已取消。
  invalidate() { this.epoch++; }
}
