"""B6-7② run 台账轮转：读取量有界、最旧被轮转、未处理的失败记录不许被删。

这些用例断的是**行为**（造出真实的 run 文件，看 list/prune 怎么处置），不查源码子串。
"""
from src.gateway.decisions import DecisionStore
from src.gateway.runs import CommandRun, RunLedger, max_run_files


def _make(ledger: RunLedger, index: int, status: str = "done") -> CommandRun:
    """造一条已落盘的 run。mtime 靠写入顺序自然递增，index 越大越新。"""
    run = CommandRun.new(f"echo {index}", "terminal")
    run.status = status
    if status in {"failed", "interrupted"}:
        run.code = 1
        run.error = f"boom {index}"
    ledger.save(run)
    return run


def test_max_run_files_reads_env_and_clamps(monkeypatch):
    monkeypatch.delenv("VORTOCODE_MAX_RUN_FILES", raising=False)
    assert max_run_files() == 200

    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "37")
    assert max_run_files() == 37

    # 手滑写个 0 不该把整个运行台账清空——当作没配，回落默认值
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "0")
    assert max_run_files() == 200
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "-5")
    assert max_run_files() == 200

    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "不是数字")
    assert max_run_files() == 200


def test_rotation_keeps_newest_and_drops_oldest(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "10")
    ledger = RunLedger(str(tmp_path))
    created = [_make(ledger, i) for i in range(25)]

    remaining = {run.id for run in ledger.list()}
    assert len(remaining) == 10                       # 文件数被压回上限

    # 留下的必须是最新的 10 条，删掉的是最旧的
    assert remaining == {run.id for run in created[-10:]}
    for run in created[:15]:
        assert ledger.load(run.id) is None


def test_list_limit_bounds_how_many_files_are_parsed(tmp_path, monkeypatch):
    """`limit` 必须真的少读文件，而不是读完再切片——它是给 900ms 轮询用的。"""
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "100")
    ledger = RunLedger(str(tmp_path))
    for i in range(30):
        _make(ledger, i)

    parsed = []
    original_load = RunLedger.load

    def counting_load(self, run_id):
        parsed.append(run_id)
        return original_load(self, run_id)

    monkeypatch.setattr(RunLedger, "load", counting_load)

    got = ledger.list(limit=5)
    assert len(got) == 5
    assert len(parsed) == 5, f"limit=5 却解析了 {len(parsed)} 个文件——切片而非有界读"

    # 且拿到的确实是最新的 5 条
    parsed.clear()
    newest_ids = [run.id for run in ledger.list(limit=5)]
    assert newest_ids == [run.id for run in ledger.list()[:5]]


def test_unprocessed_failed_runs_survive_rotation(tmp_path, monkeypatch):
    """核心红线：人还没处理的失败记录，不许因为"太旧"被删掉。

    决策队列靠 run:<id> 把失败摆到人面前；删掉 = 待办没被看见就静默消失。
    """
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "5")
    ledger = RunLedger(str(tmp_path))

    old_failures = [_make(ledger, i, status="failed") for i in range(4)]
    for i in range(20):                               # 之后灌一堆成功记录把它们挤到最旧
        _make(ledger, 100 + i, status="done")

    for run in old_failures:
        assert ledger.load(run.id) is not None, f"未处理的失败 run {run.id} 被轮转删掉了"

    # 成功记录该轮转的还是轮转了——保护没有退化成"什么都不删"
    done_left = [run for run in ledger.list() if run.status == "done"]
    assert len(done_left) <= 5


def test_dismissed_failed_runs_become_rotatable(tmp_path, monkeypatch):
    """人处理掉（dismiss）之后，失败记录就不再受保护——否则失败记录永远清不掉。"""
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "5")
    ledger = RunLedger(str(tmp_path))
    stale = [_make(ledger, i, status="failed") for i in range(4)]

    store = DecisionStore(str(tmp_path))
    for run in stale:
        store.dismiss(f"run:{run.id}")

    for i in range(20):
        _make(ledger, 100 + i, status="done")

    assert all(ledger.load(run.id) is None for run in stale), "已处理的失败记录仍被永久保护"


def test_running_runs_are_never_rotated(tmp_path, monkeypatch):
    """没结束的 run 删了，recover_interrupted 就再也认不出这条失联进程。"""
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "5")
    ledger = RunLedger(str(tmp_path))
    live = [_make(ledger, i, status=status)
            for i, status in enumerate(["queued", "running", "cancelling"])]

    for i in range(20):
        _make(ledger, 100 + i, status="done")

    for run in live:
        assert ledger.load(run.id) is not None, f"未结束的 run {run.id} 被删了"

    # 且它们仍能被 recover_interrupted 捞出来标成 interrupted
    recovered = {run.id for run in ledger.recover_interrupted()}
    assert recovered == {run.id for run in live}


def test_rotation_exceeds_cap_rather_than_deleting_protected(tmp_path, monkeypatch):
    """全是受保护记录时，宁可超出上限也不删——上限是软的，证据不是。"""
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "3")
    ledger = RunLedger(str(tmp_path))
    failures = [_make(ledger, i, status="failed") for i in range(12)]

    assert len({run.id for run in ledger.list()}) == 12
    for run in failures:
        assert ledger.load(run.id) is not None


def test_prune_removes_corrupt_files(tmp_path, monkeypatch):
    """损坏的记录读不出状态，谈不上保护，顺手清掉而不是让它永久占位。"""
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "3")
    ledger = RunLedger(str(tmp_path))
    directory = tmp_path / ".vortocode" / "runs"
    directory.mkdir(parents=True, exist_ok=True)
    junk = directory / "run-corrupt00.json"
    junk.write_text("{ 这不是合法 json", encoding="utf-8")

    for i in range(8):
        _make(ledger, i)

    assert not junk.exists()


def test_updating_an_existing_run_does_not_trigger_rotation(tmp_path, monkeypatch):
    """_monitor 每收一段输出就 save 一次；那条路径上不该做轮转扫描。"""
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "10")
    ledger = RunLedger(str(tmp_path))
    run = _make(ledger, 0, status="running")

    calls = []
    original_prune = RunLedger.prune

    def counting_prune(self, max_files=0):
        calls.append(max_files)
        return original_prune(self, max_files)

    monkeypatch.setattr(RunLedger, "prune", counting_prune)

    for chunk in range(5):                            # 模拟输出不断追加
        run.output += f"line {chunk}\n"
        ledger.save(run)

    assert calls == [], "更新已有记录也触发了轮转扫描"


def test_decision_queue_still_surfaces_old_failed_runs(tmp_path, monkeypatch):
    """端到端：失败 run 被挤成最旧之后，决策队列仍然捞得到它。"""
    from src.gateway.decisions import build_decision_queue

    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "5")
    ledger = RunLedger(str(tmp_path))
    failed = _make(ledger, 0, status="failed")
    for i in range(20):
        _make(ledger, 100 + i, status="done")

    queue = build_decision_queue(runs=ledger.list(), dismissed=set())
    ids = {item["id"] for item in queue}
    assert f"run:{failed.id}" in ids, "老的失败 run 从决策队列里消失了"


def test_cron_records_also_rotate(tmp_path, monkeypatch):
    """cron 走的是直接 save（不经 create），轮转钩子必须也覆盖它。"""
    monkeypatch.setenv("VORTOCODE_MAX_RUN_FILES", "6")
    ledger = RunLedger(str(tmp_path))
    for i in range(20):
        run = CommandRun.new(f"routine {i}", "cron")
        run.status = "done"
        run.code = 0
        ledger.save(run)

    assert len(ledger.list()) == 6


def test_ledger_without_directory_is_empty(tmp_path):
    ledger = RunLedger(str(tmp_path / "nope"))
    assert ledger.list() == []
    assert ledger.prune() == []
