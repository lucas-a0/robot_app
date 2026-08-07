"""Tests for the WiFi bandwidth provider."""

import time

import xiaozhi_ble.bandwidth as bandwidth
from xiaozhi_ble.bandwidth import BandwidthProvider, parse_proc_net_dev

_PROC_NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 18757725   129811    0    0    0     0          0         0 18757725   129811    0    0    0     0       0          0
 wlan0:  1234567     8000    0    0    0     0          0         0  7654321     6000    0    0    0     0       0          0
"""


def test_parse_proc_net_dev_reads_rx_and_tx_bytes():
    assert parse_proc_net_dev(_PROC_NET_DEV, "wlan0") == (1234567, 7654321)
    assert parse_proc_net_dev(_PROC_NET_DEV, "lo") == (18757725, 18757725)


def test_parse_proc_net_dev_tolerates_missing_and_bad_input():
    assert parse_proc_net_dev("", "wlan0") is None
    assert parse_proc_net_dev(_PROC_NET_DEV, "eth0") is None
    assert parse_proc_net_dev("wlan0: abc\n", "wlan0") is None
    assert parse_proc_net_dev("wlan0: 1 2 3\n", "wlan0") is None


def _provider_with_counters(monkeypatch, updates, counters, times, threshold=10.0):
    values = iter(counters)
    monkeypatch.setattr(
        bandwidth, "read_traffic_counters", lambda _i, _p: next(values)
    )
    monotonic = iter(times)
    monkeypatch.setattr(bandwidth.time, "monotonic", lambda: next(monotonic))
    return BandwidthProvider(
        lambda rx, tx: updates.append((rx, tx)),
        interface="wlan0",
        notify_threshold=threshold,
    )


def test_provider_first_sample_only_establishes_baseline(monkeypatch):
    updates = []
    provider = _provider_with_counters(
        monkeypatch, updates, [(0, 0), (10240, 20480)], [0.0, 2.0]
    )

    provider._poll_once()
    assert updates == []

    provider._poll_once()
    # rx: 10240/1024/2s = 5.0 KB/s, tx: 20480/1024/2s = 10.0 KB/s
    assert updates == [(5.0, 10.0)]


def test_provider_reports_only_changes_beyond_threshold(monkeypatch):
    updates = []
    provider = _provider_with_counters(
        monkeypatch,
        updates,
        [
            (0, 0),
            (10240, 0),  # rx 10.0: first reading, reported
            (11264, 0),  # rx 1.0, change 9 KB/s: skipped
            (51200, 0),  # rx 39.0, change vs reported 29 KB/s: reported
        ],
        [0.0, 1.0, 2.0, 3.0],
    )

    for _ in range(4):
        provider._poll_once()

    assert updates == [(10.0, 0.0), (39.0, 0.0)]


def test_provider_disabled_without_proc_net_dev(tmp_path):
    provider = BandwidthProvider(
        lambda _r, _t: None,
        interface="wlan0",
        proc_net_dev=tmp_path / "missing",
    )
    assert not provider.enabled
    provider.start()
    assert provider._thread is None


def test_provider_auto_detects_wifi_interface(monkeypatch, tmp_path):
    proc_net_dev = tmp_path / "dev"
    proc_net_dev.write_text("")
    monkeypatch.setattr(bandwidth, "detect_wifi_interface", lambda: "wlan9")
    provider = BandwidthProvider(
        lambda _r, _t: None, interface="", proc_net_dev=proc_net_dev
    )
    assert provider._interface == "wlan9"
    assert provider.enabled


def test_provider_poll_loop_starts_and_stops(tmp_path, monkeypatch):
    updates = []
    counters = iter([(0, 0), (102400, 0)] * 50)
    monkeypatch.setattr(
        bandwidth, "read_traffic_counters", lambda _i, _p: next(counters)
    )
    provider = BandwidthProvider(
        lambda rx, tx: updates.append((rx, tx)),
        interface="wlan0",
        poll_interval_secs=0.05,
        proc_net_dev=tmp_path / "dev",
    )
    (tmp_path / "dev").write_text("")

    provider.start()
    try:
        deadline = time.monotonic() + 5.0
        while not updates and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(updates) == 1
        assert updates[0][0] > 0
    finally:
        provider.stop()
    assert provider._thread is None
