## Summary / 改动说明

<!-- What changed and why. Keep each PR to a single topic. / 改了什么、为什么；每个 PR 保持单一主题。 -->

Closes #

## Verification / 验证

<!-- Commands you ran and their results. / 实际跑过的命令与结果。 -->

- [ ] `./scripts/ci-local.sh` (or `quick`) passes / 通过
- [ ] Added or updated tests; bug fixes include a regression test / 补了测试，修 bug 附回归测试
- [ ] New API routes are reflected in `tests/integration/server_routes_baseline.json` (if any) / 新增路由已更新基线

## Security checklist / 安全自查

- [ ] No secrets, private hostnames, internal IPs or real account IDs in code, docs or examples / 未提交密钥、私有域名、内网地址或真实账号标识
- [ ] Shell execution goes through `require_shell()`; file paths go through `resolve_within()` / 命令执行与文件路径走既有闸门
- [ ] Tests stay offline and deterministic (live tests skipped by default) / 测试离线且确定

## Remaining risks / 剩余风险

<!-- Behavior changes, known limitations, follow-ups. / 行为变化、已知限制、后续事项。 -->
