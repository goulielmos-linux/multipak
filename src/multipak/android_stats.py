"""Read cgroup v2 resource counters without starting or entering Android."""

import math
import time

from .android_instances import valid_id


def sample(runner, rows):
    result = {}
    for row in rows:
        identifier = row.get("id")
        if not valid_id(identifier) or not row.get("can_stop"):
            continue
        root = "/sys/fs/cgroup/lxc.payload." + identifier
        try:
            output = runner.run(
                ["cat", root + "/cpu.stat", root + "/memory.current", root + "/cgroup.freeze"],
                timeout=3,
                output_limit=4096,
            )
            lines = output.strip().splitlines()
            usage = next(int(line.split()[1]) for line in lines if line.startswith("usage_usec "))
            memory, frozen = int(lines[-2]), int(lines[-1])
            if usage < 0 or memory < 0 or frozen not in (0, 1):
                continue
            result[identifier] = dict(
                usage=usage, memory=memory, frozen=bool(frozen), timestamp=time.monotonic()
            )
        except (OSError, RuntimeError, TimeoutError, ValueError, IndexError, StopIteration):
            continue
    return result


def cpu_percent(previous, current):
    if current["frozen"]:
        return 0.0
    if previous is None:
        return None
    elapsed = current["timestamp"] - previous["timestamp"]
    usage = current["usage"] - previous["usage"]
    if elapsed <= 0 or usage < 0 or not math.isfinite(elapsed):
        return None
    # Like Podman stats: 100% represents one fully occupied CPU core.
    return usage / (elapsed * 10_000)
