"""Tests for the CPU usage provider."""

import time

import xiaozhi_ble.cpu as cpu
from xiaozhi_ble.cpu import CpuProvider, parse_proc_stat_cpu

_PROC_STAT = """cpu  3357 0 4313 1362393 3604 0 195 0 0 0
cpu0 1700 0 2200 681000 1800 0 100 0 0 0
intr 123456
"""


def test_parse_proc_stat_cpu_reads_busy_and_total():
    busy, total = parse_proc_stat_cpu(_PROC_STAT)
    # busy = user+nice+system+irq+softirq+steal = 3357+0+4313+0+195+0
    assert busy == 7865
    # total = busy + idle + iowait = 7865 + 1362393 + 3604
    assert total == 1373862


def test_parse_proc_stat_cpu_tolerates_short_and_bad_input():
    assert parse_proc_stat_cpu("") is None
    assert parse_proc_stat_cpu("intr 123\n") is None
    assert parse_proc_stat_cpu("cpu  1 2\n") is None
    assert parse_proc_stat_cpu("cpu  a b c d\n") is None
    # Missing trailing fields (iowait/irq/...) count as zero.
    assert parse_proc_stat_cpu("cpu  10 0 20 60\n") == (30, 90)


def _provider_with_counters(monkeypatch, updates, counters, threshold=1.0):
    values = iter(counters)
    monkeypatch.setattr(cpu, "read_cpu_counters", lambda _path: next(values))
    return CpuProvider(updates.append, notify_threshold=threshold)


def test_provider_first_sample_only_establishes_baseline(monkeypatch):
    updates = []
    provider = _provider_with_counters(monkeypatch, updates, [(0, 100), (50, 200)])

    provider._poll_once()
    assert updates == []

    provider._poll_once()
    assert len(updates) == 1
    assert updates[0] == 50.0


def test_provider_reports_only_changes_beyond_threshold(monkeypatch):
    updates = []
    provider = _provider_with_counters(
        monkeypatch,
        updates,
        [
            (0, 100),
            (50, 200),  # 50.0%: first reading, reported
            (55, 300),  # 50.0% -> (55-50)/(300-200)=5.0%? no: busy 5, total 100 -> 5.0%
            # delta busy=5, total=100 -> 5.0%, change 45 points: reported
            (56, 400),  # delta busy=1, total=100 -> 1.0%, change 4: reported
            (57, 500),  # delta busy=1, total=100 -> 1.0%, change 0: skipped
        ],
    )

    for _ in range(5):
        provider._poll_once()

    assert [round(u, 1) for u in updates] == [50.0, 5.0, 1.0]


def test_provider_skips_non_positive_total_delta(monkeypatch):
    updates = []
    provider = _provider_with_counters(
        monkeypatch, updates, [(0, 100), (0, 100), (10, 200)]
    )

    provider._poll_once()
    provider._poll_once()  # total_delta == 0: skipped
    provider._poll_once()  # 10/100 -> 10.0%

    assert [round(u, 1) for u in updates] == [10.0]


def test_provider_disabled_without_proc_stat(tmp_path):
    provider = CpuProvider(lambda _u: None, proc_stat=tmp_path / "missing")
    assert not provider.enabled
    provider.start()
    assert provider._thread is None


def test_provider_poll_loop_starts_and_stops(tmp_path, monkeypatch):
    updates = []
    counters = iter([(0, 100), (25, 200)] * 50)
    monkeypatch.setattr(cpu, "read_cpu_counters", lambda _path: next(counters))
    provider = CpuProvider(
        updates.append, poll_interval_secs=0.05, proc_stat=tmp_path / "stat"
    )
    (tmp_path / "stat").write_text("")

    provider.start()
    try:
        deadline = time.monotonic() + 5.0
        while not updates and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(updates) == 1
        assert updates[0] == 25.0
    finally:
        provider.stop()
    assert provider._thread is None
