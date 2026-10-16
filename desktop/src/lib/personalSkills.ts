/** Fence reads, mutations and their follow-up reads to one runtime generation. */
export class PersonalSkillRequests {
  epoch = 0;
  private owner: object | null = null;
  private connected = false;
  private pending = false;

  bind(owner: object | null, connected: boolean) {
    if (owner !== this.owner || connected !== this.connected) {
      this.owner = owner; this.connected = connected; this.epoch++; this.pending = false;
    }
    return this.epoch;
  }

  invalidate() { this.epoch++; this.pending = false; }

  async run(operation: (current: () => boolean) => Promise<void>) {
    if (!this.owner || !this.connected || this.pending) return;
    const epoch = this.epoch;
    const current = () => epoch === this.epoch && this.connected;
    this.pending = true;
    try { await operation(current); }
    finally { if (current()) this.pending = false; }
  }
}
