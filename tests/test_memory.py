"""Tests for the memory usage provider."""

import time

import xiaozhi_ble.memory as memory
from xiaozhi_ble.memory import MemoryProvider, parse_proc_meminfo

_PROC_MEMINFO = """MemTotal:        8060928 kB
MemFree:          512000 kB
MemAvailable:    4030464 kB
Buffers:          128000 kB
Cached:          2048000 kB
SwapTotal:       2097148 kB
"""

_PROC_MEMINFO_NO_AVAILABLE = """MemTotal:        1024000 kB
MemFree:          100000 kB
Buffers:           50000 kB
Cached:           250000 kB
"""


def test_parse_proc_meminfo_uses_memavailable():
    used, total = parse_proc_meminfo(_PROC_MEMINFO)
    assert total == 8060928
    # used = MemTotal - MemAvailable
    assert used == 8060928 - 4030464


def test_parse_proc_meminfo_falls_back_without_memavailable():
    used, total = parse_proc_meminfo(_PROC_MEMINFO_NO_AVAILABLE)
    assert total == 1024000
    # available = MemFree + Buffers + Cached = 400000
    assert used == 624000


def test_parse_proc_meminfo_tolerates_short_and_bad_input():
    assert parse_proc_meminfo("") is None
    assert parse_proc_meminfo("MemFree: 123 kB\n") is None
    assert parse_proc_meminfo("MemTotal: 0 kB\n") is None
    assert parse_proc_meminfo("MemTotal: abc kB\n") is None
    # Clamps MemAvailable above MemTotal.
    used, total = parse_proc_meminfo("MemTotal: 100 kB\nMemAvailable: 200 kB\n")
    assert (used, total) == (0, 100)


def _provider_with_usage(monkeypatch, updates, samples, threshold=1.0):
    values = iter(samples)
    monkeypatch.setattr(memory, "read_memory_usage", lambda _path: next(values))
    return MemoryProvider(
        lambda used, total, percent: updates.append((used, total, percent)),
        notify_threshold=threshold,
    )


def test_provider_first_sample_is_reported(monkeypatch):
    updates = []
    # 512 MiB used of 1024 MiB -> 50.0%
    provider = _provider_with_usage(monkeypatch, updates, [(512 * 1024, 1024 * 1024)])

    provider._poll_once()

    assert updates == [(512, 1024, 50.0)]


def test_provider_reports_only_changes_beyond_threshold(monkeypatch):
    updates = []
    provider = _provider_with_usage(
        monkeypatch,
        updates,
        [
            (500 * 1024, 1000 * 1024),  # 50.0%: first reading, reported
            (505 * 1024, 1000 * 1024),  # 50.5%: change 0.5, skipped
            (520 * 1024, 1000 * 1024),  # 52.0%: change 2.0, reported
            (520 * 1024, 1000 * 1024),  # 52.0%: no change, skipped
        ],
    )

    for _ in range(4):
        provider._poll_once()

    assert [(u, t, round(p, 1)) for u, t, p in updates] == [
        (500, 1000, 50.0),
        (520, 1000, 52.0),
    ]


def test_provider_skips_unreadable_sample(monkeypatch):
    updates = []
    provider = _provider_with_usage(
        monkeypatch, updates, [None, (100 * 1024, 200 * 1024)]
    )

    provider._poll_once()
    provider._poll_once()

    assert [(u, t, round(p, 1)) for u, t, p in updates] == [(100, 200, 50.0)]


def test_provider_disabled_without_proc_meminfo(tmp_path):
    provider = MemoryProvider(lambda *_a: None, proc_meminfo=tmp_path / "missing")
    assert not provider.enabled
    provider.start()
    assert provider._thread is None


def test_provider_poll_loop_starts_and_stops(tmp_path, monkeypatch):
    updates = []
    samples = iter([(256 * 1024, 512 * 1024)] * 50)
    monkeypatch.setattr(memory, "read_memory_usage", lambda _path: next(samples))
    provider = MemoryProvider(
        lambda used, total, percent: updates.append((used, total, percent)),
        poll_interval_secs=0.05,
        proc_meminfo=tmp_path / "meminfo",
    )
    (tmp_path / "meminfo").write_text("")

    provider.start()
    try:
        deadline = time.monotonic() + 5.0
        while not updates and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(updates) == 1
        assert updates[0] == (256, 512, 50.0)
    finally:
        provider.stop()
    assert provider._thread is None
