"""quant_cli 仪表盘渲染测试。

交互式主循环（run_quant_cli）不适合单测；这里覆盖其核心的纯渲染逻辑
_render_dashboard：状态聚合、阶段名映射、日志尾部、last_check 时间格式化。
用 capsys 抓取 stdout，断言渲染内容（ANSI 颜色码不影响子串断言）。
"""

from src.orchestrator.quant_cli import _render_dashboard


class FakePipeline:
    """只实现 _render_dashboard 需要的 get_status()。"""

    def __init__(self, status):
        self._status = status

    def get_status(self):
        return self._status


def test_render_dashboard_shows_stages_status_and_logs(capsys):
    pipeline = FakePipeline({
        "quant_news": {"status": "running", "iterations": 3,
                       "interval": 1800, "last_check": "2026-06-19T10:30:00"},
        "quant_factor": {"status": "stopped", "iterations": 0,
                         "interval": 86400, "last_check": "--"},
    })
    logs = [("10:30:01", "启动成功", "ok"), ("10:30:02", "出错了", "err")]

    _render_dashboard(pipeline, logs)
    out = capsys.readouterr().out

    # 阶段名映射
    assert "新闻采集" in out and "因子研究" in out
    # 状态聚合：1 个 running
    assert "1 个运行中" in out
    # 日志尾部
    assert "启动成功" in out and "出错了" in out
    # last_check 的 ISO 时间被格式化为 HH:MM:SS
    assert "10:30:00" in out


def test_render_dashboard_all_stopped_shows_stopped_text(capsys):
    pipeline = FakePipeline({
        "quant_news": {"status": "stopped", "iterations": 0,
                       "interval": 1800, "last_check": "--"},
    })

    _render_dashboard(pipeline, [])
    out = capsys.readouterr().out

    assert "已停止" in out


def test_render_dashboard_tolerates_unknown_stage_name(capsys):
    # 未在映射表里的循环名应原样显示，不崩溃
    pipeline = FakePipeline({
        "custom_loop": {"status": "running", "iterations": 1,
                        "interval": 60, "last_check": "--"},
    })

    _render_dashboard(pipeline, [])
    out = capsys.readouterr().out

    assert "custom_loop" in out
