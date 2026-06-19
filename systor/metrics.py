"""System metric readers. All readers are best-effort and return None on error.

For stateful metrics (CPU % over time, disk I/O rates, per-process CPU%),
we keep module-level state so consecutive snapshots can compute deltas.
The state is minimal: a few ints and dicts, total ~1 KB.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


# Module-level state for deltas
_prev_cpu_stat: tuple[int, int] | None = None   # (total, idle)
_prev_disk: dict[str, tuple[int, int]] = {}     # name -> (read_sectors, write_sectors)
_prev_net: tuple[int, int, float] | None = None # (rx, tx, ts)
_prev_net_ifaces: dict[str, tuple[int, int]] = {}
_prev_proc: dict[int, tuple[int, int]] = {}     # pid -> (utime, stime) in clock ticks
_last_proc_scan_ts: float = 0.0
_last_disk_ts: float = 0.0
_last_net_ts: float = 0.0


def read_loadavg() -> float | None:
    try:
        parts = Path("/proc/loadavg").read_text().split()
        return float(parts[0])
    except Exception:
        return None


def read_cpu_percent(prev_total: int | None = None, prev_idle: int | None = None) -> float | None:
    """CPU usage % between two /proc/stat reads.

    Pass the previous sample's totals; if absent, this returns None.
    Use _cpu_percent_delta() to get a single value with internal state.
    """
    try:
        if prev_total is None or prev_idle is None:
            return None
        total, idle = _stat_total_idle()
        dt = total - prev_total
        di = idle - prev_idle
        if dt <= 0:
            return 0.0
        return round(100.0 * (dt - di) / dt, 1)
    except Exception:
        return None


def _cpu_percent_delta() -> float | None:
    """Track CPU% across calls (no per-call sleep, so very cheap)."""
    global _prev_cpu_stat
    try:
        total, idle = _stat_total_idle()
        prev = _prev_cpu_stat
        _prev_cpu_stat = (total, idle)
        if prev is None:
            return None
        dt = total - prev[0]
        di = idle - prev[1]
        if dt <= 0:
            return 0.0
        return round(100.0 * (dt - di) / dt, 1)
    except Exception:
        return None


def _stat_total_idle() -> tuple[int, int]:
    line = Path("/proc/stat").read_text().splitlines()[0]  # "cpu  ..."
    vals = [int(x) for x in line.split()[1:]]
    total = sum(vals)
    # idle = idle + iowait
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return total, idle


def read_cpu_temp_c() -> float | None:
    """Max CPU temperature across /sys/class/thermal zones. Returns None if unavailable."""
    try:
        zones = list(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
        temps: list[float] = []
        for z in zones:
            try:
                t = int(z.read_text().strip())
                if t > 0:
                    temps.append(t / 1000.0)
            except Exception:
                continue
        return max(temps) if temps else None
    except Exception:
        return None


def read_meminfo() -> dict[str, int] | None:
    """Returns dict with: total_mb, free_mb, available_mb, used_mb, swap_total_mb, swap_used_mb."""
    try:
        raw: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            if not v:
                continue
            num = v.strip().split()[0]
            try:
                raw[k] = int(num)
            except ValueError:
                pass
        if not raw:
            return None
        return {
            "total_mb": raw.get("MemTotal", 0) // 1024,
            "free_mb": raw.get("MemFree", 0) // 1024,
            "available_mb": raw.get("MemAvailable", raw.get("MemFree", 0)) // 1024,
            "used_mb": (raw.get("MemTotal", 0) - raw.get("MemAvailable", raw.get("MemFree", 0))) // 1024,
            "swap_total_mb": raw.get("SwapTotal", 0) // 1024,
            "swap_used_mb": (raw.get("SwapTotal", 0) - raw.get("SwapFree", 0)) // 1024,
        }
    except Exception:
        return None


def read_disk_usage() -> list[dict]:
    """Returns list of {mount, used_pct, size_gb, used_gb} for real filesystems."""
    try:
        out = subprocess.run(["df", "-PB", "G"], capture_output=True, text=True, timeout=5).stdout
        rows: list[dict] = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
            try:
                pct = int(parts[4].rstrip("%"))
                size_gb = float(parts[1].rstrip("G"))
                used_gb = float(parts[2].rstrip("G"))
            except ValueError:
                continue
            mount = parts[5]
            # skip pseudo filesystems
            if mount.startswith(("/sys", "/proc", "/run", "/dev", "/snap", "/var/lib/docker/overlay")):
                continue
            rows.append({"mount": mount, "used_pct": pct, "size_gb": size_gb, "used_gb": used_gb})
        return rows
    except Exception:
        return []


def _read_diskstats_raw() -> dict[str, tuple[int, int]]:
    """Returns dict of device -> (read_sectors, write_sectors). Only real block devices."""
    out: dict[str, tuple[int, int]] = {}
    try:
        for line in Path("/proc/diskstats").read_text().splitlines():
            parts = line.split()
            if len(parts) < 14:
                continue
            try:
                name = parts[2]
                # skip pseudo / virtual devices
                if any(name.startswith(p) for p in ("loop", "ram", "dm-", "md", "sr", "zd", "nbd", "fd")):
                    continue
                # Whole-disk filter: keep names without trailing digits (sda, sdb, vda, ...)
                # or nvme0n1-style (no 'p' followed by digit at the end).
                if name.startswith(("sd", "vd", "hd", "xvd")):
                    # partitions are sda1, sda2 etc; whole disks are sda, sdb, ...
                    if name[-1].isdigit():
                        continue
                elif name.startswith("nvme"):
                    # nvme0n1 is a whole disk; nvme0n1p1 is a partition
                    if "p" in name and name.rsplit("p", 1)[-1].isdigit():
                        continue
                else:
                    # unknown naming (mmcblk, etc.) — keep if not ending in a partition digit
                    # skip generic partition suffix guess: name followed by digits at end
                    if any(name.endswith(str(d)) for d in range(10)):
                        continue
                read_sectors = int(parts[5])
                write_sectors = int(parts[9])
                out[name] = (read_sectors, write_sectors)
            except (ValueError, IndexError):
                continue
    except Exception:
        pass
    return out


def read_disk_io_mbps(elapsed_sec: float | None = None) -> dict:
    """Returns {read_mbps, write_mbps, devices: [{name, read_mbps, write_mbps}]}.

    Reads /proc/diskstats and computes delta per second since last call.
    First call returns zeros. If elapsed_sec is None, uses wall-clock delta
    from the last call (also returns zeros on first call).
    """
    global _prev_disk, _last_disk_ts
    try:
        cur = _read_diskstats_raw()
        prev = _prev_disk
        now = time.time()
        if elapsed_sec is None:
            elapsed = (now - _last_disk_ts) if _last_disk_ts else 0
        else:
            elapsed = elapsed_sec
        _prev_disk = cur
        _last_disk_ts = now
        if not prev or elapsed <= 0:
            return {"read_mbps": 0.0, "write_mbps": 0.0, "devices": []}
        devs = []
        total_r = 0.0
        total_w = 0.0
        for name, (r, w) in cur.items():
            if name in prev:
                pr, pw = prev[name]
                d_r = max(0, r - pr)
                d_w = max(0, w - pw)
                # sectors are 512 bytes
                r_mbps = round(d_r * 512 / elapsed / (1024 * 1024), 3)
                w_mbps = round(d_w * 512 / elapsed / (1024 * 1024), 3)
                devs.append({"name": name, "read_mbps": r_mbps, "write_mbps": w_mbps})
                total_r += r_mbps
                total_w += w_mbps
        return {"read_mbps": round(total_r, 3), "write_mbps": round(total_w, 3), "devices": devs}
    except Exception:
        return {"read_mbps": 0.0, "write_mbps": 0.0, "devices": []}


def read_uptime_sec() -> int | None:
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        return None


def read_hostname() -> str:
    return os.uname().nodename if hasattr(os, "uname") else (os.environ.get("HOSTNAME") or "unknown")


def read_network_stats() -> dict[str, int] | None:
    """Bytes sent/received from /proc/net/dev. Cumulative counters."""
    try:
        result: dict[str, int] = {"rx_bytes": 0, "tx_bytes": 0}
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            if ":" not in line:
                continue
            iface, rest = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            cols = rest.split()
            try:
                result["rx_bytes"] += int(cols[0])
                result["tx_bytes"] += int(cols[8])
            except (ValueError, IndexError):
                continue
        return result
    except Exception:
        return None


def read_network_interfaces() -> list[dict]:
    """Per-interface network counters + cheap delta rates from /proc/net/dev."""
    global _prev_net_ifaces, _last_net_ts
    try:
        rows = []
        cur: dict[str, tuple[int, int]] = {}
        now = time.time()
        elapsed = (now - _last_net_ts) if _last_net_ts else 0.0
        for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
            if ':' not in line:
                continue
            iface, rest = line.split(':', 1)
            iface = iface.strip()
            if iface == 'lo':
                continue
            cols = rest.split()
            try:
                rx = int(cols[0]); tx = int(cols[8])
            except (ValueError, IndexError):
                continue
            cur[iface] = (rx, tx)
            prev = _prev_net_ifaces.get(iface)
            rx_mbps = tx_mbps = 0.0
            if prev is not None and elapsed > 0:
                rx_mbps = max(0.0, (rx - prev[0]) / elapsed / (1024 * 1024))
                tx_mbps = max(0.0, (tx - prev[1]) / elapsed / (1024 * 1024))
            rows.append({
                'iface': iface,
                'rx_bytes': rx,
                'tx_bytes': tx,
                'total_bytes': rx + tx,
                'rx_mbps': round(rx_mbps, 4),
                'tx_mbps': round(tx_mbps, 4),
            })
        _prev_net_ifaces = cur
        _last_net_ts = now
        rows.sort(key=lambda r: (r['rx_mbps'] + r['tx_mbps'], r['total_bytes']), reverse=True)
        return rows
    except Exception:
        return []


# ---------- Process list (no psutil dep) ----------
def _read_proc_stat(pid: int) -> tuple[str, int, int, int, int] | None:
    """Read /proc/<pid>/stat. Returns (comm, utime, stime, rss_pages, starttime) or None."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read().decode(errors="replace")
        # comm is the second field, in parens, may contain spaces
        lparen = data.index("(")
        rparen = data.rindex(")")
        comm = data[lparen + 1:rparen]
        rest = data[rparen + 2:].split()
        # fields after ) are: state, ppid, pgrp, session, tty_nr, tpgid, flags,
        # minflt, cminflt, majflt, cmajflt, utime, stime, ...
        # utime is field 14 (1-indexed overall = 12 after comm + state), stime is 15
        # After the closing paren: index 11 = utime, 12 = stime, 21 = starttime, 22 = rss
        utime = int(rest[11])
        stime = int(rest[12])
        rss = int(rest[21])  # in pages
        starttime = int(rest[19])
        return comm, utime, stime, rss, starttime
    except Exception:
        return None


def _read_proc_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            data = f.read().replace(b"\x00", b" ").decode(errors="replace").strip()
        return data[:200] or ""
    except Exception:
        return ""


def _read_proc_uid(pid: int) -> str:
    try:
        st = os.stat(f"/proc/{pid}")
        uid = st.st_uid
        import pwd
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            return str(uid)
    except Exception:
        return "?"


def read_top_processes(n: int = 10, by: str = "cpu") -> list[dict]:
    """Return top-n processes by cpu% or memory%.

    CPU% is computed from /proc/<pid>/stat utime+stime delta. The function
    caches previous values and is a no-op on the very first call (returns
    cpu_percent=0 for everything).

    Memory is RSS from /proc/<pid>/stat (in pages, converted to MB).

    The first call always returns cpu_percent=0 because there's no previous
    baseline. The second call (30s later by default) has real values.

    Cost: O(num_procs) syscalls. For 250 procs ~5ms.
    """
    global _prev_proc, _last_proc_scan_ts
    clk_tck = os.sysconf("SC_CLK_TCK") or 100
    now = time.time()
    elapsed = now - _last_proc_scan_ts if _last_proc_scan_ts else None
    _last_proc_scan_ts = now

    procs = []
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except Exception:
        return []

    cur_proc: dict[int, tuple[int, int, int]] = {}
    for pid in pids:
        s = _read_proc_stat(pid)
        if s is None:
            continue
        comm, utime, stime, rss_pages, _start = s
        cur_proc[pid] = (utime, stime, rss_pages)
        prev = _prev_proc.get(pid)
        if prev is not None and elapsed and elapsed > 0:
            d_ut = max(0, utime - prev[0])
            d_st = max(0, stime - prev[1])
            cpu_pct = round(100.0 * (d_ut + d_st) / clk_tck / elapsed, 1)
        else:
            cpu_pct = 0.0
        procs.append({
            "pid": pid,
            "name": comm[:60],
            "user": _read_proc_uid(pid),
            "cpu_percent": cpu_pct,
            "mem_mb": round(rss_pages * 4 / 1024, 1),  # pages * 4 KB → MB
            "cmdline": _read_proc_cmdline(pid),
        })
    _prev_proc = cur_proc

    if by == "mem":
        procs.sort(key=lambda p: p["mem_mb"], reverse=True)
    else:
        procs.sort(key=lambda p: (p["cpu_percent"], p["mem_mb"]), reverse=True)
    return procs[:n]


def read_total_memory_mb() -> int:
    """Returns total RAM in MB. Used to compute mem_percent for processes."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def collect_snapshot() -> dict[str, Any]:
    """One-time snapshot of all metrics. Returned as a single dict."""
    load = read_loadavg()
    temp = read_cpu_temp_c()
    mem = read_meminfo() or {}
    disks = read_disk_usage()
    cpu_pct = _cpu_percent_delta()
    net = read_network_stats() or {}
    net_ifaces = read_network_interfaces()
    disk_io = read_disk_io_mbps()
    return {
        "ts": int(time.time()),
        "hostname": read_hostname(),
        "uptime_sec": read_uptime_sec(),
        "cpu": {
            "load_1m": load,
            "load_5m": _safe_loadavg(1),
            "load_15m": _safe_loadavg(2),
            "temp_c": temp,
            "percent": cpu_pct,
        },
        "memory": mem,
        "disks": disks,
        "disk_io": disk_io,
        "network_interfaces": net_ifaces,
        "network": net,
    }


def _safe_loadavg(idx: int) -> float | None:
    try:
        parts = Path("/proc/loadavg").read_text().split()
        return float(parts[idx])
    except Exception:
        return None

