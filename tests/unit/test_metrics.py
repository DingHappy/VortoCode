"""可观测/指标埋点测试（离线）。"""

from src.core import metrics


def test_timer_percentiles():
    metrics.reset()
    for v in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        metrics.record_time("t", v)
    s = metrics.get_timer_stats("t")
    assert s["count"] == 10
    assert 0.4 <= s["p50"] <= 0.6
    assert s["p95"] >= 0.9
    assert s["p99"] == 1.0
