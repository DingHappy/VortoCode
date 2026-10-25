export const SOURCE_FRESH_MS = 2 * 60_000;

/** A local read is current only within its time window; clock rollback is not freshness. */
export const freshAt = (checkedAt: number | undefined, now: number, maxAge = SOURCE_FRESH_MS) =>
  checkedAt !== undefined && Number.isFinite(checkedAt) && Number.isFinite(now)
    && checkedAt <= now && now - checkedAt <= maxAge;
