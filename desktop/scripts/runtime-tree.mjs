// 内置 runtime 是 PyInstaller onedir 目录（可执行文件 + _internal/），不再是单个文件。
// 构建清单、签名和发布核验都按「整棵目录树」比对，算法集中在这里，三处才不会各算各的。
import { createHash } from "node:crypto";
import { closeSync, lstatSync, openSync, readFileSync, readSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";

export const RUNTIME_DIR_NAME = "runtime";

export function runtimeExecutableName(platform = process.platform) {
  return platform === "win32" ? "vortocode-runtime.exe" : "vortocode-runtime";
}

export function filesBelow(root) {
  return readdirSync(root, { withFileTypes: true })
    .flatMap((entry) => {
      const path = join(root, entry.name);
      return entry.isDirectory() ? filesBelow(path) : [path];
    })
    .sort();
}

/** 相对路径 + 权限位 + 内容；与 verify-release 记录的 app 树哈希同一算法。 */
export function sha256Tree(root) {
  const hash = createHash("sha256");
  for (const path of filesBelow(root)) {
    const metadata = lstatSync(path);
    hash.update(relative(root, path));
    hash.update(String(metadata.mode & 0o777));
    hash.update(readFileSync(path));
  }
  return hash.digest("hex");
}

export function treeSize(root) {
  return filesBelow(root).reduce((total, path) => total + lstatSync(path).size, 0);
}

const MACHO_MAGICS = new Set(["feedface", "feedfacf", "cefaedfe", "cffaedfe", "cafebabe", "bebafeca"]);

/** 目录里所有 Mach-O（可执行文件、dylib、扩展模块 .so）：macOS 上每一个都要单独签名。 */
export function machOFiles(root) {
  return filesBelow(root).filter((path) => {
    if (lstatSync(path).size < 4) return false;
    const header = Buffer.alloc(4);
    const descriptor = openSync(path, "r");
    try {
      readSync(descriptor, header, 0, 4, 0);
    } finally {
      closeSync(descriptor);
    }
    return MACHO_MAGICS.has(header.toString("hex"));
  });
}
