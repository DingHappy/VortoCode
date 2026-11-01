/** Fence reads, mutations and their follow-up reads to one runtime generation. */
export class PersonalSkillRequests {
  epoch = 0;
  private owner: object | null = null;
  private connected = false;
  private pending = false;
  private transportEpoch = 0;

  private generation(owner: object | null): number {
    return owner && "connectionEpoch" in owner && typeof owner.connectionEpoch === "number" ? owner.connectionEpoch : 0;
  }

  bind(owner: object | null, connected: boolean) {
    const transportEpoch = this.generation(owner);
    if (owner !== this.owner || connected !== this.connected || transportEpoch !== this.transportEpoch) {
      this.owner = owner; this.connected = connected; this.epoch++; this.pending = false;
      this.transportEpoch = transportEpoch;
    }
    return this.epoch;
  }

  invalidate() { this.epoch++; this.pending = false; }

  async run(operation: (current: () => boolean) => Promise<void>) {
    if (!this.owner || !this.connected || this.pending) return;
    const epoch = this.epoch;
    const transportEpoch = this.transportEpoch;
    const current = () => epoch === this.epoch && this.connected && this.generation(this.owner) === transportEpoch;
    if (!current()) return;
    this.pending = true;
    try { await operation(current); }
    finally { if (current()) this.pending = false; }
  }
}
