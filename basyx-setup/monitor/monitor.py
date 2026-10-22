"""Docker Compose resource monitor; works on Windows hosts using Linux containers.
Run in the existing monitor container, or on the host with COMPOSE_PROJECT set.
Requires docker Python SDK with stats(one_shot=True) support (API >= 1.41).
CPU 100% means one fully used core, not 100% of the entire Windows host.
Use a new CSV for each run; filter samples using your workload start/end markers.
"""
import csv
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import docker
from docker.errors import DockerException

INTERVAL_SECONDS = float(os.getenv("INTERVAL_SECONDS", "1"))
RUN_ID = os.getenv("MEASUREMENT_RUN_ID", "manual-run")
OUTPUT_PATH = Path(os.getenv("OUTPUT_PATH", "/data/container_resources.csv"))
TARGET_SERVICES = {v.strip() for v in os.getenv("TARGET_SERVICES", "").split(",") if v.strip()}
EXCLUDE_SERVICES = {v.strip() for v in os.getenv("EXCLUDE_SERVICES", "monitor").split(",") if v.strip()}
HEADERS = [
    "sample_index", "timestamp_utc", "timestamp_unix_us", "collected_at_utc",
    "run_id", "container_id", "container_name", "compose_service",
    "cpu_percent", "online_cpus", "docker_cpu_count", "cpu_sample_interval_seconds", "cpu_status",
    "memory_usage_bytes", "memory_working_set_bytes", "memory_working_set_mib",
    "memory_limit_bytes", "memory_percent", "memory_cache_field",
]
_thread_local = threading.local()


def sample_container(container):
    # Separate SDK connections per worker; requests run in parallel.
    if not hasattr(_thread_local, "client"):
        _thread_local.client = docker.from_env(timeout=10)
    stats = _thread_local.client.api.stats(container.id, stream=False, one_shot=True)
    collected = datetime.now(timezone.utc)
    raw_read = stats.get("read")
    if not raw_read:
        raise ValueError("Docker did not provide its statistics timestamp")
    read = datetime.fromisoformat(raw_read.replace("Z", "+00:00"))
    if read.year < 2000:
        raise ValueError("Docker provided an invalid statistics timestamp")
    return container, stats, read, collected


def cpu_values(stats, previous, read):
    """Use OUR previous snapshot, since one_shot need not populate precpu_stats."""
    current = stats.get("cpu_stats", {})
    usage = current.get("cpu_usage", {})
    total = usage.get("total_usage")
    system = current.get("system_cpu_usage")
    cpus = current.get("online_cpus") or len(usage.get("percpu_usage") or [])
    snapshot = (total, system, cpus, read)
    if total is None or system is None or not cpus:
        return "", "", "missing_counters", snapshot
    if previous is None or previous[0] is None or previous[1] is None:
        return "", "", "priming", snapshot
    elapsed = (read - previous[3]).total_seconds()
    cpu_delta = total - previous[0]
    system_delta = system - previous[1]
    if elapsed <= 0 or system_delta <= 0 or cpu_delta < 0 or cpus != previous[2]:
        return "", "", "counter_reset_or_duplicate", snapshot
    cpu = cpu_delta / system_delta * cpus * 100.0
    return f"{cpu:.6f}", f"{elapsed:.6f}", "ok", snapshot


def memory_values(stats):
    memory = stats.get("memory_stats", {})
    if "usage" not in memory:
        raise ValueError("Docker did not provide memory usage")
    usage = int(memory["usage"])
    limit = int(memory.get("limit", 0))
    details = memory.get("stats", {})
    # cgroup v1 aggregate field takes priority; v2 uses inactive_file.
    field = next((k for k in ("total_inactive_file", "inactive_file") if k in details), "")
    # No inactive-file field: retain total usage and explicitly label it.
    cache = int(details[field]) if field else 0
    working_set = max(0, usage - cache)
    percent = f"{working_set / limit * 100:.6f}" if limit else ""
    return usage, working_set, limit, percent, field or "unavailable_total_usage_used"


def main():
    client = docker.from_env(timeout=10)
    info = client.info()
    docker_cpu_count = int(info.get("NCPU") or 0)
    if docker_cpu_count <= 0:
        raise RuntimeError("Docker did not report a valid CPU count")
    if info.get("OSType") != "linux":
        raise RuntimeError("This monitor requires Linux containers. On Windows, use Docker Desktop's Linux-container engine.")
    project = os.getenv("COMPOSE_PROJECT", "").strip()
    own_id = None
    if not project:
        try:
            own = client.containers.get(socket.gethostname())
            own_id = own.id
            project = own.labels.get("com.docker.compose.project", "")
        except DockerException as exc:
            raise RuntimeError("Set COMPOSE_PROJECT when running outside the monitor container.") from exc
    if not project:
        raise RuntimeError("Compose project could not be determined; set COMPOSE_PROJECT.")
    containers = client.containers.list(filters={"label": f"com.docker.compose.project={project}"})
    targets = [c for c in containers if c.id != own_id
               and c.labels.get("com.docker.compose.service", "") not in EXCLUDE_SERVICES
               and (not TARGET_SERVICES or c.labels.get("com.docker.compose.service", "") in TARGET_SERVICES)]
    if not targets:
        raise RuntimeError("No matching running containers.")
    print("Monitoring: " + ", ".join(c.name for c in targets), flush=True)
    print(f"Engine: linux; requested interval: {INTERVAL_SECONDS}s; output: {OUTPUT_PATH}", flush=True)
    print("Container list is fixed for this run. Restart monitor if containers are replaced/added.", flush=True)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    previous = {}
    # Exclusive creation prevents mixing runs or appending to an old CSV schema.
    with OUTPUT_PATH.open("x", newline="", encoding="utf-8", buffering=1) as output:
        writer = csv.DictWriter(output, fieldnames=HEADERS)
        writer.writeheader()
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            index = 0
            deadline = time.monotonic()
            try:
                while True:
                    index += 1
                    futures = [pool.submit(sample_container, c) for c in targets]
                    for future, target in zip(futures, targets):
                        try:
                            container, stats, read, collected = future.result()
                            cpu, elapsed, status, snapshot = cpu_values(stats, previous.get(container.id), read)
                            previous[container.id] = snapshot
                            usage, working_set, limit, percent, cache_field = memory_values(stats)
                        except (DockerException, ValueError, KeyError, TypeError) as exc:
                            print(f"Sample {index}, {target.name}: {exc}", flush=True)
                            previous.pop(target.id, None)
                            continue
                        writer.writerow(dict(
                            sample_index=index, timestamp_utc=read.isoformat(timespec="microseconds"),
                            timestamp_unix_us=int(read.timestamp() * 1_000_000),
                            collected_at_utc=collected.isoformat(timespec="microseconds"), run_id=RUN_ID,
                            container_id=container.id[:12], container_name=container.name,
                            compose_service=container.labels.get("com.docker.compose.service", ""),
                            cpu_percent=cpu, online_cpus=snapshot[2], docker_cpu_count=docker_cpu_count, cpu_sample_interval_seconds=elapsed, cpu_status=status,
                            memory_usage_bytes=usage, memory_working_set_bytes=working_set,
                            memory_working_set_mib=f"{working_set / 1_048_576:.6f}",
                            memory_limit_bytes=limit, memory_percent=percent, memory_cache_field=cache_field,
                        ))
                    output.flush()
                    deadline += INTERVAL_SECONDS
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    else:
                        print(f"Sample {index}: collection exceeded requested interval by {-remaining:.3f}s", flush=True)
                        deadline = time.monotonic()
            except KeyboardInterrupt:
                print("Monitoring stopped; CSV saved.", flush=True)
    client.close()


if __name__ == "__main__":
    main()
