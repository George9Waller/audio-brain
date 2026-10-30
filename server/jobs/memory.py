"""A run's peak memory during one of its steps, so the pods' limits can be set from measurements (INFRA.md I3).

On Linux the kernel keeps a high-water mark of the process's resident memory (`VmHWM` in /proc/self/status), and
writing 5 to /proc/self/clear_refs resets it to what's resident now. So the runner resets it before each step and
reads it after. Each run has a process of its own, so the mark is that run's alone. Where it can't be reset (macOS,
or a kernel that refuses), the peak is the process's since it started (`getrusage`), which is only an upper bound for
the step.
"""

import resource
import sys

PROC = "/proc/self"


def reset_peak():
    """Start a new peak from what's resident now. False if that can't be done here."""
    try:
        with open(f"{PROC}/clear_refs", "w") as refs:
            refs.write("5")
        return True
    except OSError:
        return False


def peak_mb():
    """The highest resident memory since the last reset (or since the process started), in MB."""
    try:
        with open(f"{PROC}/status") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) // 1024  # kB
    except OSError:
        pass
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak // 2**20 if sys.platform == "darwin" else peak // 1024  # bytes on macOS, kB on Linux
