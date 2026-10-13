type Entry = { key: string; promise: Promise<void>; current: () => boolean; operation: () => Promise<void>;
  resolve: () => void; reject: (error: unknown) => void };
type Queue = { pending: Map<string, Entry>; entries: Entry[]; running?: Entry; draining: boolean };

/** Ordinary polling coalesces; explicit task checks are kept and run in order. */
export class AssistantRefreshQueue {
  private readonly owners = new WeakMap<object, Map<string, Queue>>();

  request(owner: object, scope: string, refreshId: string | undefined, current: () => boolean, operation: () => Promise<void>): Promise<void> {
    if (!current()) return Promise.resolve();
    let scopes = this.owners.get(owner);
    if (!scopes) { scopes = new Map(); this.owners.set(owner, scopes); }
    let queue = scopes.get(scope);
    if (!queue) { queue = { pending: new Map(), entries: [], draining: false }; scopes.set(scope, queue); }
    const key = refreshId ?? "";
    if (queue.pending.has(key)) return queue.pending.get(key)!.promise;
    if (!refreshId && queue.pending.size > 0) return (queue.entries[queue.entries.length - 1] ?? queue.running)!.promise;
    if (queue.pending.size >= 16) return Promise.reject(new Error("待核对任务较多，请稍后再试"));
    let resolve!: () => void;
    let reject!: (error: unknown) => void;
    const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no; });
    const entry: Entry = { key, promise, resolve, reject, current, operation };
    queue.pending.set(key, entry); queue.entries.push(entry);
    if (!queue.draining) void this.drain(scopes, scope, queue);
    return promise;
  }

  private async drain(scopes: Map<string, Queue>, scope: string, queue: Queue) {
    queue.draining = true;
    while (queue.entries.length) {
      const entry = queue.entries.shift()!;
      queue.running = entry;
      try { if (entry.current()) await entry.operation(); entry.resolve(); }
      catch (error) { entry.reject(error); }
      finally { queue.pending.delete(entry.key); queue.running = undefined; }
    }
    queue.draining = false;
    if (scopes.get(scope) === queue) scopes.delete(scope);
  }
}
