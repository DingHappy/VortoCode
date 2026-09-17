"""执行真实审批页脚本，验证请求绑定和 409 刷新；DOM/fetch 替身，不冒充 GUI 验收。"""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="审批页脚本行为验证需要 Node.js")
def test_review_submission_keeps_the_viewed_version_and_does_not_auto_retry():
    page = Path(__file__).resolve().parents[2] / "web" / "review.html"
    script = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync(process.argv[1], 'utf8');
const source = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
const elements = new Map();
const element = () => ({style: {}, value: 'review note', textContent: '', innerHTML: '',
  remove() {}, querySelectorAll() { return []; }});
const document = {hidden: false, body: {appendChild() {}}, createElement: element,
  querySelector(key) {
    if (!elements.has(key)) elements.set(key, element());
    return elements.get(key);
  }, querySelectorAll() { return []; }};
let version = 'viewed-A-v1', releasePost, conflict = false;
const requests = [];
const response = (body, status = 200) => ({ok: status < 400, status, json: async () => body});
const context = vm.createContext({document, setTimeout() {}, setInterval() {},
  fetch: async (path, options = {}) => {
    requests.push({path, options});
    if (path === '/api/auth/status') return response({auth_required: false});
    if (options.method === 'POST') {
      if (conflict) { version = 'current-A-v2'; return response({detail: 'stale'}, 409); }
      return new Promise(resolve => { releasePost = () => resolve(response({status: 'running'})); });
    }
    if (path === '/api/pipelines') return response({runs: []});
    if (path === '/api/pipelines/A') return response({run_id: 'A', pipeline: 'example',
      status: 'awaiting_review', awaiting: 'write', review_token: version, stages: []});
    throw Error('unexpected request: ' + path);
  }});
vm.runInContext(source, context);
await vm.runInContext('loadDetail("A")', context);
const send = vm.runInContext('send("approve")', context);
vm.runInContext('cur = {run_id: "B", review_token: "unseen-B"}', context);
releasePost();
await send;
let posts = requests.filter(r => r.options.method === 'POST');
assert.equal(posts.length, 1);
assert.equal(posts[0].path, '/api/pipelines/A/review');
assert.equal(JSON.parse(posts[0].options.body).review_token, 'viewed-A-v1');
assert.equal(vm.runInContext('cur.run_id', context), 'B');

await vm.runInContext('loadDetail("A")', context);
conflict = true;
await vm.runInContext('send("approve")', context);
posts = requests.filter(r => r.options.method === 'POST');
assert.equal(posts.length, 2, '409 must refresh but never approve the replacement automatically');
assert.equal(JSON.parse(posts[1].options.body).review_token, 'viewed-A-v1');
assert.equal(vm.runInContext('cur.review_token', context), 'current-A-v2');
"""
    result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script, str(page)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
