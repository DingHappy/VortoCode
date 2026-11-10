"""来源限频容量及并发准入的离线行为测试。"""

from concurrent.futures import ThreadPoolExecutor

from src.web import mobile_rate_limit as module


def test_source_capacity_refuses_new_sources_without_evicting_existing_limits(monkeypatch):
    monkeypatch.setattr(module, "MAX_SOURCES", 2)
    now = [1000.0]
    limiter = module.MobileRateLimiter(clock=lambda: now[0])
    for _ in range(120):
        assert limiter.admit("a") == 0
    assert limiter.admit("b") == 0
    assert limiter.admit("c") > 0
    assert limiter.admit("a") == 60
    now[0] += module.IDLE_SECONDS
    assert limiter.admit("c") == 0
    assert limiter.admit("a") == 0


def test_concurrent_admissions_share_one_source_budget():
    limiter = module.MobileRateLimiter(clock=lambda: 1000.0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: limiter.admit("a"), range(240)))
    assert results.count(0) == 120 and results.count(60) == 120


def test_inflight_failure_responses_do_not_extend_active_backoff():
    now = [1000.0]
    limiter = module.MobileRateLimiter(clock=lambda: now[0])
    assert limiter.admit("a") == 0
    for _ in range(5):
        limiter.authentication_failed("a")
    now[0] += 10
    for _ in range(20):
        limiter.authentication_failed("a")
    assert limiter.admit("a") == 20
    now[0] += 20
    assert limiter.admit("a") == 0
