// App.tsx 抽出的测试结果树组件（B8-④b S1，只搬家不改行为）。
// 把一次 test run 的结构化用例结果按文件分组、按通过/失败着色展示。
import type { CommandRunItem } from "../types";

export function TestResultTree({ run }: { run: CommandRunItem }) {
  const results = run.test_results;
  if (!results || results.summary.total === 0) return null;
  const groups = new Map<string, typeof results.cases>();
  for (const testCase of results.cases) {
    const key = testCase.path || "测试用例";
    groups.set(key, [...(groups.get(key) ?? []), testCase]);
  }
  const hasFailure = results.summary.failed + results.summary.errors > 0;
  return (
    <div className={`test-results ${hasFailure ? "failed" : "passed"}`}>
      <div className="test-results-head">
        <div><strong>{results.framework}</strong><span>{results.summary.total} 项结果</span></div>
        <div className="test-result-counts">
          <span className="passed">✓ {results.summary.passed}</span>
          {results.summary.failed > 0 && <span className="failed">× {results.summary.failed}</span>}
          {results.summary.errors > 0 && <span className="error">! {results.summary.errors}</span>}
          {results.summary.skipped > 0 && <span className="skipped">– {results.summary.skipped}</span>}
        </div>
      </div>
      {Array.from(groups.entries()).map(([path, cases]) => (
        <details className="test-result-group" key={path} open={cases.some((item) => ["failed", "error"].includes(item.status))}>
          <summary><span>{path}</span><small>{cases.length}</small></summary>
          {cases.map((testCase, index) => (
            <div className={`test-result-case ${testCase.status}`} key={`${testCase.name}-${index}`}>
              <i>{testCase.status === "passed" ? "✓" : testCase.status === "skipped" ? "–" : "×"}</i>
              <div><strong>{testCase.name}</strong>{testCase.detail && <small>{testCase.detail}</small>}</div>
              {testCase.duration && <time>{testCase.duration}</time>}
            </div>
          ))}
        </details>
      ))}
      {!results.complete && (
        <p className="test-results-note">
          当前 reporter 只命名了 {results.cases.length} 项；汇总计数仍来自测试器最终报告。
        </p>
      )}
      {results.truncated && <p className="test-results-note">用例树仅保留前 500 项。</p>}
    </div>
  );
}
