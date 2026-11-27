"""Bounded, best-effort process and cgroup v2 counters (no host assumptions).

Deltas are observed over a turn, not exclusive per-request CPU attribution.
Missing cgroup fields are omitted, never replaced by fabricated zeros.
"""
from pathlib import Path
from time import process_time_ns

def resource_sample():
    result={"process_cpu_ns":process_time_ns()}
    try:
        rows=Path("/proc/self/cgroup").read_text().splitlines()
        relative=next((row[3:] for row in rows if row.startswith("0::")),None)
        if relative is None or ".." in Path(relative).parts:
            return result
        root=Path("/sys/fs/cgroup")/relative.lstrip("/")
        with (root/"cpu.stat").open() as stream:
            for line in stream.read(4096).splitlines():
                fields=line.split()
                if len(fields)==2 and fields[0] in {"usage_usec","nr_throttled","throttled_usec"}:
                    try:
                        val=int(fields[1])
                    except ValueError:
                        continue
                    if val>=0:
                        result["cgroup_"+fields[0]]=val
        try:
            with (root/"memory.current").open() as stream:
                current=int(stream.read(128).strip())
            if current>=0:
                result["cgroup_memory_bytes"]=current
        except (OSError,ValueError):
            pass
    except (OSError,ValueError):
        pass
    return result


def resource_delta(before,after):
    out={}
    for name,divisor in (("process_cpu_ns",1_000_000),("cgroup_usage_usec",1000),
                         ("cgroup_throttled_usec",1000),("cgroup_nr_throttled",1)):
        if name in before and name in after and after[name]>=before[name]:
            key={"process_cpu_ns":"process_cpu_ms","cgroup_usage_usec":"cgroup_cpu_ms",
                 "cgroup_throttled_usec":"cgroup_throttled_ms",
                 "cgroup_nr_throttled":"cgroup_throttle_events"}[name]
            out[key]=round((after[name]-before[name])/divisor,3)
    if "cgroup_memory_bytes" in after:
        out["cgroup_memory_end_bytes"]=after["cgroup_memory_bytes"]
    return out
