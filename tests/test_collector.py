"""Tests for collector sustained-threshold alert logic, cooldowns, and recovery notifications."""
import time

from systor import collector


def setup_function():
    collector._metric_state.clear()


def test_eval_metric_sustained_requirement():
    # Requires 3 consecutive breaches to trigger
    res1 = collector._eval_metric("cpu_load", 5.0, 4.0, sustained_needed=3, higher_is_worse=True, cooldown_sec=300)
    assert res1 is None  # count = 1

    res2 = collector._eval_metric("cpu_load", 5.5, 4.0, sustained_needed=3, higher_is_worse=True, cooldown_sec=300)
    assert res2 is None  # count = 2

    # 3rd consecutive breach triggers alert!
    res3 = collector._eval_metric("cpu_load", 6.0, 4.0, sustained_needed=3, higher_is_worse=True, cooldown_sec=300)
    assert res3 == ("alert", 6.0, 4.0)

    # 4th breach right after is throttled by cooldown
    res4 = collector._eval_metric("cpu_load", 6.2, 4.0, sustained_needed=3, higher_is_worse=True, cooldown_sec=300)
    assert res4 is None


def test_eval_metric_cooldown_expiry(monkeypatch):
    base_time = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: base_time)

    # Trigger initial alert
    collector._eval_metric("temp", 90.0, 80.0, sustained_needed=1, higher_is_worse=True, cooldown_sec=60)

    # Still bad, but only 30s elapsed -> throttled
    monkeypatch.setattr(time, "time", lambda: base_time + 30.0)
    res_throttled = collector._eval_metric("temp", 91.0, 80.0, sustained_needed=1, higher_is_worse=True, cooldown_sec=60)
    assert res_throttled is None

    # After cooldown (65s), repeat alert fires
    monkeypatch.setattr(time, "time", lambda: base_time + 65.0)
    res_repeat = collector._eval_metric("temp", 92.0, 80.0, sustained_needed=1, higher_is_worse=True, cooldown_sec=60)
    assert res_repeat == ("alert", 92.0, 80.0)


def test_eval_metric_recovery():
    # Trigger alert
    collector._eval_metric("ram", 200, 500, sustained_needed=1, higher_is_worse=False, cooldown_sec=60)

    # Metric recovers above threshold -> returns recover
    rec = collector._eval_metric("ram", 800, 500, sustained_needed=1, higher_is_worse=False, cooldown_sec=60)
    assert rec == ("recover", 800, 500)

    # Normal conditions continue -> None
    normal = collector._eval_metric("ram", 850, 500, sustained_needed=1, higher_is_worse=False, cooldown_sec=60)
    assert normal is None


def test_eval_metric_glitch_reset():
    # If a spike drops before reaching sustained threshold, count resets
    collector._eval_metric("cpu", 99.0, 80.0, sustained_needed=3, higher_is_worse=True, cooldown_sec=60)
    collector._eval_metric("cpu", 98.0, 80.0, sustained_needed=3, higher_is_worse=True, cooldown_sec=60)

    # Normal sample resets count
    collector._eval_metric("cpu", 20.0, 80.0, sustained_needed=3, higher_is_worse=True, cooldown_sec=60)

    # Next spike starts from count = 1 again
    res = collector._eval_metric("cpu", 99.0, 80.0, sustained_needed=3, higher_is_worse=True, cooldown_sec=60)
    assert res is None


def test_fmt_helper():
    assert "85%" in collector._fmt("disk_used_pct", 85, 80)
    assert "°C" in collector._fmt("cpu_temp_c", 75, 70)
    assert "MB" in collector._fmt("mem_free_mb", 400, 500)


def test_build_alert_body():
    snap = {
        "hostname": "test-box",
        "cpu": {"percent": 95, "load_1m": 8.5, "load_5m": 7.0, "load_15m": 6.0, "temp_c": 82},
        "memory": {"available_mb": 400, "used_mb": 15600, "swap_used_mb": 100},
    }
    # Alert message
    body = collector._build_alert_body(
        subject="HIGH CPU LOAD",
        metric_key="cpu_load",
        kind="alert",
        value=8.5,
        threshold=4.0,
        snap=snap,
        worst_disk=None,
        msg="CPU load: 8.5 > 4.0",
    )
    assert "🖥️ test-box" in body
    assert "📌 HIGH CPU LOAD" in body
    assert "⚠️ check now" in body

    # Recovery message
    rec_body = collector._build_alert_body(
        subject="HIGH CPU LOAD RECOVERED",
        metric_key="cpu_load",
        kind="recover",
        value=2.0,
        threshold=4.0,
        snap=snap,
        worst_disk=None,
        msg="CPU load: 2.0 < 4.0",
    )
    assert "✅ back to normal" in rec_body
