import { existsSync, mkdtempSync, renameSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";

function sameApp(expected, actual) {
  for (const key of ["identifier", "designatedRequirement", "appSha256", "sidecarSha256"]) {
    if (!expected[key] || expected[key] !== actual[key]) throw new Error(`App verification mismatch: ${key}`);
  }
}

/** Only replace a verified bundle after a verified backup. Never launch it. */
export function installApp({ source, target, inspect, copy, backupRoot = tmpdir() }) {
  const signature = inspect(source);
  const before = existsSync(target) ? inspect(target) : null;
  if (before && (before.identifier !== signature.identifier
    || before.designatedRequirement !== signature.designatedRequirement)) {
    throw new Error("Installed signing identity would change; application retained");
  }
  let backup = null;
  if (before) {
    backup = join(mkdtempSync(join(backupRoot, "vortocode-desktop-backup-")), basename(target));
    copy(target, backup);
    sameApp(before, inspect(backup));
  }
  const staging = mkdtempSync(join(dirname(target), ".vortocode-install-"));
  const candidate = join(staging, basename(target));
  const previous = join(staging, "previous.app");
  let movedOld = false;
  let published = false;
  let cleanupSafe = true;
  try {
    copy(source, candidate);
    sameApp(signature, inspect(candidate));
    if (before) { renameSync(target, previous); movedOld = true; }
    renameSync(candidate, target); published = true;
    sameApp(signature, inspect(target));
    return { signature, backup };
  } catch (error) {
    try {
      if (published) renameSync(target, candidate);
      if (movedOld) renameSync(previous, target);
    } catch (rollback) {
      cleanupSafe = false;
      throw new AggregateError([error, rollback], `Install and rollback failed; preserved staging ${staging}, backup ${backup}`);
    }
    throw new Error(`Install failed; previous app retained, backup ${backup ?? "not applicable"}`, { cause: error });
  } finally {
    if (cleanupSafe) rmSync(staging, { recursive: true, force: true });
  }
}
