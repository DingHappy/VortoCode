"""dogfood 运维模板守门（b4）——examples/ 里的配置模板必须能被**真解析器**吃下。

模板是给人抄的：如果 cron.yaml.example 的 schedule 语法或字段漂移到解析器不认，
用户照抄就直接坏。这里用真 loader 加载，漂移即红。
"""

import shutil
from pathlib import Path


_EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_cron_example_parses_with_real_loader(tmp_path):
    from src.gateway import cron as _cron
    d = tmp_path / ".vortocode"
    d.mkdir()
    shutil.copy(_EXAMPLES / "cron.yaml.example", d / "cron.yaml")
    jobs = _cron.load_jobs(str(tmp_path))
    # loader 对非法 schedule 是"跳过不炸"——示例里任何一条写坏都会让数量掉下去（这才是真守门）
    assert {j.name for j in jobs} == {"nightly_evals", "ci_red_autofix", "dep_check"}, \
        f"示例作业没全被解析（写坏的会被静默跳过）：{[j.name for j in jobs]}"
    for j in jobs:
        assert isinstance(j.schedule, _cron.Schedule)             # 已由 loader 解析
        assert j.enabled is False, f"示例作业必须默认关（{j.name}）——模板不许开箱即烧 token"


def test_heartbeat_backlog_examples_exist_and_match_conventions():
    hb = (_EXAMPLES / "HEARTBEAT.md.example").read_text(encoding="utf-8")
    assert "HEARTBEAT_OK" in hb                       # 与 gateway/heartbeat 的丢弃约定一致
    assert "BACKLOG" in hb
    bl = (_EXAMPLES / "BACKLOG.md.example").read_text(encoding="utf-8")
    assert "- [ ]" in bl and "- [~]" in bl            # 领活标记约定（claim_backlog_item）


def test_launchd_example_is_valid_plist_xml():
    import plistlib
    p = _EXAMPLES / "launchd" / "com.vortocode.serve.plist.example"
    data = plistlib.loads(p.read_bytes())             # XML 合法 + plist 结构合法
    assert data["Label"] == "com.vortocode.serve"
    assert data["ProgramArguments"][-2:] == ["--port", "8080"]
    # 自主作业开关模板必须默认关（与 OPS 手册"零自主消耗"承诺一致）
    env = data["EnvironmentVariables"]
    assert env["VORTOCODE_CRON"] == "0" and env["VORTOCODE_HEARTBEAT"] == "0"


def test_ops_doc_references_exist():
    ops = (Path(__file__).resolve().parents[2] / "docs" / "OPS.md").read_text(encoding="utf-8")
    for ref in ("cron.yaml.example", "HEARTBEAT.md.example", "BACKLOG.md.example",
                "com.vortocode.serve.plist.example", "doctor"):
        assert ref in ops, f"OPS.md 缺关键引用: {ref}"
