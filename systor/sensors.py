"""Hardware sensors and peripheral telemetry reader.

Monitors NVIDIA GPUs (via nvidia-smi), Raspberry Pi hardware health (via vcgencmd),
and UPS battery status (via NUT upsc). All readers fail gracefully with zero external
dependencies and minimal CPU overhead.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any

log = logging.getLogger("systor.sensors")

_SENSORS_CACHE: dict[str, Any] = {
    "ts": 0.0,
    "data": {
        "present": False,
        "gpu": [],
        "rpi": {"present": False},
        "ups": {"present": False},
    },
}
_CACHE_TTL = 3.0  # seconds


def get_gpu_metrics() -> list[dict[str, Any]]:
    """Query NVIDIA GPU metrics via nvidia-smi CSV output."""
    nvsmi = shutil.which("nvidia-smi")
    if not nvsmi:
        return []

    try:
        query_fields = [
            "name",
            "utilization.gpu",
            "memory.used",
            "memory.total",
            "temperature.gpu",
            "power.draw",
        ]
        cmd = [
            nvsmi,
            f"--query-gpu={','.join(query_fields)}",
            "--format=csv,noheader,nounits",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if proc.returncode != 0 or not proc.stdout.strip():
            return []

        gpus: list[dict[str, Any]] = []
        for idx, line in enumerate(proc.stdout.strip().splitlines()):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 6:
                try:
                    name = parts[0]
                    gpu_util = float(parts[1]) if parts[1] != "[Not Supported]" else 0.0
                    mem_used = float(parts[2]) if parts[2] != "[Not Supported]" else 0.0
                    mem_total = float(parts[3]) if parts[3] != "[Not Supported]" else 0.0
                    temp_c = float(parts[4]) if parts[4] != "[Not Supported]" else 0.0
                    power_w = float(parts[5]) if parts[5] != "[Not Supported]" else 0.0
                    mem_pct = round((mem_used / mem_total * 100.0), 1) if mem_total > 0 else 0.0

                    gpus.append({
                        "id": idx,
                        "name": name,
                        "gpu_util_pct": gpu_util,
                        "mem_used_mb": mem_used,
                        "mem_total_mb": mem_total,
                        "mem_used_pct": mem_pct,
                        "temp_c": temp_c,
                        "power_w": power_w,
                    })
                except (ValueError, TypeError) as parse_err:
                    log.debug("Error parsing nvidia-smi line '%s': %s", line, parse_err)
        return gpus
    except Exception as e:
        log.debug("Failed to query nvidia-smi: %s", e)
        return []


def get_rpi_metrics() -> dict[str, Any]:
    """Inspect Raspberry Pi hardware status: throttling, undervoltage, and SoC temp."""
    vcgencmd = shutil.which("vcgencmd")
    rpi_data: dict[str, Any] = {
        "present": False,
        "throttled": False,
        "undervoltage": False,
        "throttled_code": "0x0",
        "temp_c": None,
    }

    # Detect SoC thermal zone if vcgencmd or Linux sysfs is present
    sysfs_temp = "/sys/class/thermal/thermal_zone0/temp"
    if os.path.isfile(sysfs_temp):
        try:
            with open(sysfs_temp, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val.isdigit():
                    rpi_data["temp_c"] = round(int(val) / 1000.0, 1)
        except Exception:
            pass

    if not vcgencmd:
        return rpi_data

    try:
        proc = subprocess.run([vcgencmd, "get_throttled"], capture_output=True, text=True, timeout=2)
        if proc.returncode == 0 and "throttled=" in proc.stdout:
            raw_code = proc.stdout.strip().split("=")[-1]
            code_int = int(raw_code, 16)
            rpi_data["present"] = True
            rpi_data["throttled_code"] = raw_code
            # Bit 0: Under-voltage detected
            # Bit 1: Arm frequency capped
            # Bit 2: Currently throttled
            # Bit 16: Under-voltage has occurred
            # Bit 17: Arm frequency capped has occurred
            # Bit 18: Throttling has occurred
            rpi_data["undervoltage"] = bool(code_int & 0x1 or code_int & 0x10000)
            rpi_data["throttled"] = bool(code_int & 0x6 or code_int & 0x60000)
            return rpi_data
    except Exception as e:
        log.debug("Failed to query vcgencmd: %s", e)

    return rpi_data


def get_ups_metrics() -> dict[str, Any]:
    """Query Network UPS Tools (NUT) upsc client for battery metrics."""
    upsc = shutil.which("upsc")
    ups_data: dict[str, Any] = {
        "present": False,
        "name": "",
        "status": "",
        "battery_pct": None,
        "load_pct": None,
        "runtime_mins": None,
    }
    if not upsc:
        return ups_data

    try:
        # Find default UPS
        proc_list = subprocess.run([upsc, "-l"], capture_output=True, text=True, timeout=2)
        if proc_list.returncode != 0 or not proc_list.stdout.strip():
            return ups_data

        ups_name = proc_list.stdout.strip().splitlines()[0].strip()
        if not ups_name:
            return ups_data

        proc_detail = subprocess.run([upsc, ups_name], capture_output=True, text=True, timeout=3)
        if proc_detail.returncode != 0:
            return ups_data

        ups_data["present"] = True
        ups_data["name"] = ups_name
        for line in proc_detail.stdout.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip()
                if k == "ups.status":
                    ups_data["status"] = v
                elif k == "battery.charge":
                    try:
                        ups_data["battery_pct"] = float(v)
                    except ValueError:
                        pass
                elif k == "ups.load":
                    try:
                        ups_data["load_pct"] = float(v)
                    except ValueError:
                        pass
                elif k == "battery.runtime":
                    try:
                        ups_data["runtime_mins"] = round(float(v) / 60.0, 1)
                    except ValueError:
                        pass
        return ups_data
    except Exception as e:
        log.debug("Failed to query upsc: %s", e)
        return ups_data


def get_all_sensors(force_refresh: bool = False) -> dict[str, Any]:
    """Aggregate all hardware sensors with lightweight thread-safe caching."""
    now = time.time()
    if not force_refresh and (now - _SENSORS_CACHE["ts"]) < _CACHE_TTL:
        return _SENSORS_CACHE["data"]

    gpu_list = get_gpu_metrics()
    rpi_info = get_rpi_metrics()
    ups_info = get_ups_metrics()

    present = bool(gpu_list or rpi_info.get("present") or ups_info.get("present"))

    data = {
        "present": present,
        "gpu": gpu_list,
        "rpi": rpi_info,
        "ups": ups_info,
    }

    _SENSORS_CACHE["ts"] = now
    _SENSORS_CACHE["data"] = data
    return data
