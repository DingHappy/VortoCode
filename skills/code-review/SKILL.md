---
name: code-review
description: 代码审查技能，检查代码质量、安全性和最佳实践
version: "1.0.0"
capabilities:
  - code_review
  - security_analysis
tools:
  - Read
  - Glob
  - Grep
arguments:
  - name: path
    description: 要审查的代码路径
    required: true
  - name: focus
    description: 审查重点
    required: false
    default: "all"
---

## 代码审查任务

审查路径 `$path` 中的代码。

## 审查清单

### 代码质量
- 命名规范：变量、函数、类名是否清晰
- 代码重复：是否有重复代码需要重构
- 函数复杂度：函数是否过于复杂
- 注释质量：注释是否清晰有用

### 安全性
- 输入验证：是否验证所有外部输入
- SQL 注入：是否使用参数化查询
- XSS 攻击：是否转义用户输出
- 敏感信息：是否泄露敏感信息

### 最佳实践
- 错误处理：是否有适当的错误处理
- 日志记录：是否有足够的日志
- 性能考虑：是否有性能问题
- 测试覆盖：是否有足够的测试

## 输出格式

```yaml
verdict: approve | request_changes | escalate
findings:
  - severity: blocker | major | minor
    file: ...
    line: ...
    issue: ...
    suggestion: ...
summary: ...
```
