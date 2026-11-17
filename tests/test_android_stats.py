from types import SimpleNamespace

import pytest

from multipak.android_stats import cpu_percent, sample


def test_stats_reads_only_active_valid_container_cgroups():
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 3
        return "usage_usec 2000000\nuser_usec 1000000\nsystem_usec 1000000\n805306368\n1\n"

    rows = [
        {"id": "androida", "can_stop": True},
        {"id": "androidb", "can_stop": False},
        {"id": "../outside", "can_stop": True},
    ]
    counters = sample(SimpleNamespace(run=run), rows)
    assert len(calls) == 1 and calls[0][0] == "cat"
    assert all(path.startswith("/sys/fs/cgroup/lxc.payload.androida/") for path in calls[0][1:])
    assert counters["androida"]["memory"] == 805306368
    assert counters["androida"]["usage"] == 2000000
    assert counters["androida"]["frozen"] is True


@pytest.mark.parametrize("output", ["", "permission denied", "usage_usec -1\n100\n0\n"])
def test_missing_or_invalid_metrics_are_unavailable_not_zero(output):
    assert (
        sample(SimpleNamespace(run=lambda *a, **k: output), [{"id": "androida", "can_stop": True}])
        == {}
    )


def test_metrics_can_disappear_during_collection():
    def run(*args, **kwargs):
        raise RuntimeError("container stopped")

    assert sample(SimpleNamespace(run=run), [{"id": "androida", "can_stop": True}]) == {}


def test_cpu_uses_counter_delta_and_handles_restart_and_freezing():
    first = dict(usage=1000000, timestamp=10.0, frozen=False)
    second = dict(usage=3500000, timestamp=15.0, frozen=False)
    assert cpu_percent(first, second) == 50.0
    assert cpu_percent(None, second) is None
    assert cpu_percent(second, first) is None
    assert cpu_percent(second, second) is None
    assert cpu_percent(None, dict(second, frozen=True)) == 0.0
