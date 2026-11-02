"""SMART disk health and NVMe wear monitoring service.

Extracts physical drive health using smartctl (--json) with in-memory caching
and safe fallback to /sys/block when smartctl is not available.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("systor.services.smart")

_smart_cache: dict[str, Any] = {"ts": 0.0, "drives": []}
_smart_lock = threading.Lock()
_CACHE_TTL_SEC = 60.0  # Prevent disk spin-ups


def get_drive_health(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return SMART health and wear status for all detected physical drives."""
    now = time.time()
    with _smart_lock:
        if not force_refresh and (now - _smart_cache["ts"]) < _CACHE_TTL_SEC and _smart_cache["drives"]:
            return _smart_cache["drives"]

    smartctl_bin = shutil.which("smartctl")
    drives: list[dict[str, Any]] = []

    if smartctl_bin:
        drives = _probe_with_smartctl(smartctl_bin)

    if not drives:
        # Fallback to sysfs disk detection
        drives = _probe_with_sysfs()

    with _smart_lock:
        _smart_cache["ts"] = time.time()
        _smart_cache["drives"] = drives

    return drives


def _probe_with_smartctl(smartctl_bin: str) -> list[dict[str, Any]]:
    drives: list[dict[str, Any]] = []
    try:
        proc = subprocess.run(
            [smartctl_bin, "--scan", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        data = json.loads(proc.stdout) if proc.stdout else {}
        devices = data.get("devices", [])
    except Exception as e:
        log.debug("smartctl scan failed: %s", e)
        devices = []

    for dev in devices:
        dev_name = dev.get("name")
        if not dev_name:
            continue
        try:
            p = subprocess.run(
                [smartctl_bin, "-a", "-j", dev_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            d_json = json.loads(p.stdout) if p.stdout else {}
            record = _parse_smartctl_record(dev_name, d_json)
            drives.append(record)
        except Exception as e:
            log.debug("smartctl parse failed for %s: %s", dev_name, e)

    return drives


def parse_smartctl_record(dev_name: str, d: dict[str, Any]) -> dict[str, Any]:
    return _parse_smartctl_record(dev_name, d)


def _parse_smartctl_record(dev_name: str, d: dict[str, Any]) -> dict[str, Any]:
    device_info = d.get("device", {})
    user_cap = d.get("user_capacity", {})
    capacity_bytes = user_cap.get("bytes", 0)
    capacity_gb = round(capacity_bytes / (1024 ** 3), 1) if capacity_bytes else None

    smart_status = d.get("smart_status", {})
    passed = smart_status.get("passed")

    protocol = device_info.get("protocol", "ATA")
    temp_obj = d.get("temperature", {})
    temp_c = temp_obj.get("current")

    power_on = d.get("power_on_time", {})
    power_on_hours = power_on.get("hours")

    nvme_wear_pct = None
    nvme_tbw = None
    reallocated_sectors = None
    pending_sectors = None

    nvme_smart = d.get("nvme_smart_health_information_log", {})
    if nvme_smart:
        protocol = "NVMe"
        nvme_wear_pct = nvme_smart.get("percentage_used")
        if temp_c is None:
            temp_c = nvme_smart.get("temperature")
        if power_on_hours is None:
            power_on_hours = nvme_smart.get("power_on_hours")
        units_written = nvme_smart.get("data_units_written", 0)
        # 1 data unit = 512,000 bytes (1000 sectors * 512)
        if units_written:
            nvme_tbw = round((units_written * 512000) / (1024 ** 4), 2)

    # ATA / SATA Attributes
    ata_tables = d.get("ata_smart_attributes", {}).get("table", [])
    for attr in ata_tables:
        attr_id = attr.get("id")
        raw_val = attr.get("raw", {}).get("value")
        if attr_id == 5:  # Reallocated Sectors Count
            reallocated_sectors = raw_val
        elif attr_id == 197:  # Current Pending Sector Count
            pending_sectors = raw_val
        elif attr_id in (194, 190) and temp_c is None:
            temp_c = raw_val

    return {
        "device": dev_name,
        "type": device_info.get("type", protocol.lower()),
        "protocol": protocol,
        "model": d.get("model_name") or device_info.get("model_name") or "Unknown Model",
        "serial": d.get("serial_number") or "N/A",
        "firmware": d.get("firmware_version") or "N/A",
        "capacity_gb": capacity_gb,
        "passed": passed,
        "temperature_c": temp_c,
        "power_on_hours": power_on_hours,
        "nvme_wear_pct": nvme_wear_pct,
        "nvme_tbw": nvme_tbw,
        "reallocated_sectors": reallocated_sectors,
        "pending_sectors": pending_sectors,
        "smart_available": True,
    }


def _probe_with_sysfs() -> list[dict[str, Any]]:
    """Fallback physical disk scanner using Linux /sys/block."""
    drives: list[dict[str, Any]] = []
    block_dir = Path("/sys/block")
    if not block_dir.exists():
        return drives

    for dev_path in block_dir.iterdir():
        name = dev_path.name
        # Skip loop, ram, dm (device-mapper), and cdrom devices
        if name.startswith(("loop", "ram", "dm-", "sr", "zram")):
            continue

        try:
            size_path = dev_path / "size"
            size_sectors = int(size_path.read_text().strip()) if size_path.exists() else 0
            if size_sectors == 0:
                continue
            capacity_gb = round((size_sectors * 512) / (1024 ** 3), 1)

            rot_path = dev_path / "queue" / "rotational"
            rotational = int(rot_path.read_text().strip()) == 1 if rot_path.exists() else False

            model_path = dev_path / "device" / "model"
            model = model_path.read_text().strip() if model_path.exists() else name

            drives.append({
                "device": f"/dev/{name}",
                "type": "hdd" if rotational else "ssd",
                "protocol": "NVMe" if "nvme" in name else "SATA/SCSI",
                "model": model,
                "serial": "N/A",
                "firmware": "N/A",
                "capacity_gb": capacity_gb,
                "passed": None,
                "temperature_c": None,
                "power_on_hours": None,
                "nvme_wear_pct": None,
                "nvme_tbw": None,
                "reallocated_sectors": None,
                "pending_sectors": None,
                "smart_available": False,
                "note": "smartctl not installed. Install smartmontools for full SMART data.",
            })
        except Exception as e:
            log.debug("sysfs probe error for %s: %s", name, e)

    return drives
