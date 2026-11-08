// 环境变量从 VORTOCODE_* 改名为 VORTOONE_*（ADR-018 批次 B）。与引擎 src/env_compat.py 同一口径：
// 导入即把旧名字换成新名字并删掉旧名字，两个都设了以新名字为准。读环境变量的脚本把它放在第一个 import。
export function adoptLegacyEnv(env = process.env) {
  const moved = Object.keys(env).filter((key) => key.startsWith("VORTOCODE_")).sort();
  for (const key of moved) {
    const name = `VORTOONE_${key.slice("VORTOCODE_".length)}`;
    if (!(name in env)) env[name] = env[key];
    delete env[key];
  }
  return moved;
}

adoptLegacyEnv();
