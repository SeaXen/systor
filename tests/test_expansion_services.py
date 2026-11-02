"""
Comprehensive automated unit tests for the 7 new architectural expansion features:
1. Drive Health & SMART Insights
2. Service Uptime & Blackbox Health
3. Systemd Service Manager & Audit Logging
4. SSL / TLS Certificate Expiry Tracker
5. System Security Updates & Reboot Indicator
6. Prometheus /metrics Scraping Exporter
7. One-Click Disaster Recovery (Backup & Restore)
"""

import io
import tarfile
from unittest.mock import MagicMock, patch


from systor.storage import Storage
from systor.services.smart_service import parse_smartctl_record, get_drive_health
from systor.services.uptime_service import ping_target
from systor.services.systemd_service import restart_systemd_service
from systor.services.ssl_service import probe_remote_cert, get_ssl_certificates
from systor.services.backup_service import create_backup_archive, restore_backup_archive
from systor.metrics import read_system_updates
from systor.web import create_app


# ---------------------------------------------------------------------------
# Feature 1: Drive Health & SMART Insights Tests
# ---------------------------------------------------------------------------

def test_smart_nvme_parsing():
    """Verify NVMe SMART JSON attributes are accurately extracted."""
    mock_nvme = {
        "device": {"type": "nvme", "protocol": "NVMe"},
        "model_name": "Samsung SSD 980 PRO 1TB",
        "serial_number": "S5GXNF0R123456",
        "user_capacity": {"bytes": 1000204886016},
        "smart_status": {"passed": True},
        "temperature": {"current": 42},
        "power_on_time": {"hours": 1250},
        "nvme_smart_health_information_log": {
            "percentage_used": 3,
            "data_units_written": 2000000,
        }
    }
    res = parse_smartctl_record("nvme0n1", mock_nvme)
    assert res["passed"] is True
    assert res["temperature_c"] == 42
    assert res["power_on_hours"] == 1250
    assert res["nvme_wear_pct"] == 3
    assert res["nvme_tbw"] is not None
    assert res["nvme_tbw"] > 0
    assert res["protocol"] == "NVMe"


def test_smart_sata_parsing():
    """Verify SATA SMART JSON attributes and raw reallocated sector counts."""
    mock_sata = {
        "device": {"type": "ata", "protocol": "ATA"},
        "model_name": "Crucial CT1000MX500",
        "smart_status": {"passed": True},
        "temperature": {"current": 35},
        "power_on_time": {"hours": 8760},
        "ata_smart_attributes": {
            "table": [
                {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 0}},
                {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": 0}},
            ]
        }
    }
    res = parse_smartctl_record("sda", mock_sata)
    assert res["passed"] is True
    assert res["temperature_c"] == 35
    assert res["reallocated_sectors"] == 0
    assert res["pending_sectors"] == 0
    assert res["smart_available"] is True


def test_smart_drive_health_fallback():
    """Verify fallback response when smartctl is absent or failing."""
    with patch("subprocess.run", side_effect=FileNotFoundError("smartctl not found")):
        res = get_drive_health(force_refresh=True)
        assert isinstance(res, list)
        for d in res:
            assert "device" in d
            assert "passed" in d


# ---------------------------------------------------------------------------
# Feature 2: Service Uptime & Blackbox Health Tests
# ---------------------------------------------------------------------------

def test_uptime_storage_crud(tmp_path):
    """Test full CRUD operations for uptime targets in SQLite storage."""
    db_file = tmp_path / "test_systor.db"
    store = Storage(str(db_file))

    # Add target
    tid = store.add_uptime_target("Gateway API", "http://127.0.0.1:8080/health", "GET", 200, 60, 5.0)
    assert tid > 0

    # List targets
    targets = store.list_uptime_targets()
    assert len(targets) == 1
    assert targets[0]["name"] == "Gateway API"

    # Record check (ok)
    store.record_uptime_check(tid, 200, 12.5, True, None)
    summary = store.get_uptime_summary(hours=24)
    assert len(summary) == 1
    assert summary[0]["uptime_pct"] == 100.0

    # Record failing check (down)
    store.record_uptime_check(tid, 500, 4.0, False, "Internal Server Error")
    summary2 = store.get_uptime_summary(hours=24)
    assert summary2[0]["uptime_pct"] == 50.0

    # Update target
    ok = store.update_uptime_target(tid, "Primary Gateway", "http://127.0.0.1:8080/health", "GET", 200, 60, 5.0, False)
    assert ok is True
    t = store.get_uptime_target(tid)
    assert t["name"] == "Primary Gateway"
    assert t["enabled"] == 0

    # Delete target
    deleted = store.delete_uptime_target(tid)
    assert deleted is True
    assert len(store.list_uptime_targets()) == 0


def test_uptime_http_check_execution(tmp_path):
    """Test HTTP check execution and non-blocking probe."""
    target = {
        "id": 1,
        "name": "Local Test",
        "url": "http://127.0.0.1:8888",
        "method": "GET",
        "expected_status": 200,
        "timeout_sec": 2.0,
    }
    # Mock urllib response
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        ok, code, latency, err = ping_target(target)
        assert ok is True
        assert code == 200
        assert latency >= 0.0
        assert err is None


# ---------------------------------------------------------------------------
# Feature 3: Systemd Service Manager & Audit Logging Tests
# ---------------------------------------------------------------------------

def test_systemd_deny_list_enforcement(tmp_path):
    """Verify security deny-list stops destructive or PID 1 unit restarts."""
    db_file = tmp_path / "test_systor.db"
    store = Storage(str(db_file))

    for forbidden in ["systemd-journald.service", "init.scope", "dbus.service", "ssh.service", "docker.service", "ufw.service"]:
        ok, msg = restart_systemd_service(forbidden, "admin", "127.0.0.1", store)
        assert ok is False
        assert "protected" in msg.lower() or "invalid" in msg.lower()


def test_systemd_unit_input_validation(tmp_path):
    """Verify unit names must match safe alphanumeric pattern."""
    db_file = tmp_path / "test_systor.db"
    store = Storage(str(db_file))

    malicious = [
        "nginx; rm -rf /",
        "test|reboot",
        "../../etc/passwd",
        "unit\nrestart",
    ]
    for bad in malicious:
        ok, msg = restart_systemd_service(bad, "admin", "127.0.0.1", store)
        assert ok is False
        assert "Invalid" in msg


def test_systemd_restart_audit_logging(tmp_path):
    """Verify restarts are recorded to the SQLite audit log."""
    db_file = tmp_path / "test_systor.db"
    store = Storage(str(db_file))

    with patch("shutil.which", return_value="/usr/bin/systemctl"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            ok, msg = restart_systemd_service("nginx.service", "admin", "10.0.0.5", store)
            assert ok is True
            assert "Successfully" in msg

            # Verify audit log
            logs = store.recent_systemd_actions(limit=10)
            assert len(logs) == 1
            assert logs[0]["unit"] == "nginx.service"
            assert logs[0]["action"] == "restart"
            assert logs[0]["user"] == "admin"
            assert logs[0]["ip"] == "10.0.0.5"
            assert logs[0]["success"] == 1


# ---------------------------------------------------------------------------
# Feature 4: SSL / TLS Certificate Expiry Tracker Tests
# ---------------------------------------------------------------------------

def test_ssl_probe_invalid_host():
    """Verify graceful handling and descriptive error on unreachable host."""
    res = probe_remote_cert("invalid.domain.that.does.not.exist.test", port=443, timeout=1.0)
    assert res is not None
    assert res["status"] == "error"
    assert "error" in res
    assert res["valid"] is False


def test_ssl_get_certificates_aggregation():
    """Verify aggregation of certificates with custom targets."""
    certs = get_ssl_certificates(custom_targets=["127.0.0.1"])
    assert isinstance(certs, list)
    assert len(certs) == 1
    assert certs[0]["host"] == "127.0.0.1"


# ---------------------------------------------------------------------------
# Feature 5: System Security Updates & Reboot Indicator Tests
# ---------------------------------------------------------------------------

def test_read_system_updates(tmp_path):
    """Verify detection of reboot-required flag and security updates."""
    # Clean system
    with patch("pathlib.Path.exists", return_value=False):
        res = read_system_updates()
        assert res["reboot_required"] is False
        assert res["updates_security"] == 0

    # With pending updates
    with patch("pathlib.Path.exists", side_effect=lambda: True):
        with patch("pathlib.Path.read_text", side_effect=lambda: "4 updates are security updates.\n"):
            res = read_system_updates()
            assert res["reboot_required"] is True
            assert res["updates_security"] == 4


# ---------------------------------------------------------------------------
# Feature 6: Prometheus /metrics Scraping Exporter Tests
# ---------------------------------------------------------------------------

def test_prometheus_metrics_endpoint(monkeypatch, tmp_path):
    """Verify Prometheus OpenMetrics format exposition at /metrics."""
    db_file = tmp_path / "systor.db"
    store = Storage(str(db_file))
    monkeypatch.setattr("systor.blueprints.common.get_storage", lambda: store)
    monkeypatch.setattr("systor.web.start_apps_cache_worker", lambda: None)
    monkeypatch.setattr("systor.services.uptime_service.start_uptime_worker", lambda: None)

    mock_snap = {
        "uptime_sec": 3600,
        "cpu": {"percent": 15.5, "load_1m": 0.45, "load_5m": 0.50, "load_15m": 0.55, "temp_c": 48.0},
        "memory": {"total_mb": 16000, "used_mb": 4000, "free_mb": 12000, "available_mb": 11500},
        "disks": [{"mount": "/", "used_pct": 42.0, "used_gb": 42.0, "total_gb": 100.0}],
        "disk_io": {"read_mbps": 1.2, "write_mbps": 3.4},
        "network": {"rx_bytes": 1024000, "tx_bytes": 512000},
    }
    monkeypatch.setattr("systor.blueprints.dashboard.collect_snapshot", lambda: mock_snap)

    app = create_app(start_worker=False)
    client = app.test_client()

    resp = client.get("/metrics", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("Content-Type", "")

    body = resp.get_data(as_text=True)
    assert "# HELP systor_uptime_seconds" in body
    assert "# TYPE systor_uptime_seconds counter" in body
    assert "systor_uptime_seconds 3600" in body
    assert "# HELP systor_cpu_percent" in body
    assert "systor_cpu_percent 15.5" in body
    assert "# HELP systor_memory_bytes" in body
    assert 'systor_memory_bytes{state="total"}' in body
    assert "# HELP systor_disk_used_percent" in body
    assert 'systor_disk_used_percent{mount="/"}' in body


# ---------------------------------------------------------------------------
# Feature 7: One-Click Disaster Recovery (Backup & Restore) Tests
# ---------------------------------------------------------------------------

def test_backup_and_restore_cycle(tmp_path):
    """Test full cycle: export .tar.gz archive, mutate data, and restore from archive."""
    db_path = tmp_path / "systor.db"
    store = Storage(str(db_path))
    tid = store.add_uptime_target("Backup Target", "http://example.com", "GET", 200, 60, 5.0)
    fake_cfg = {"system_name": "Systor Original"}

    # 1. Create backup archive
    archive_bytes, filename = create_backup_archive(store, fake_cfg)
    assert filename.startswith("systor-backup-")
    assert filename.endswith(".tar.gz")
    assert len(archive_bytes) > 0

    # Verify archive contents
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        names = tar.getnames()
        assert "manifest.json" in names
        assert "systor.db" in names
        assert "config.json" in names

    # 2. Mutate database (delete target)
    store.delete_uptime_target(tid)
    assert len(store.list_uptime_targets()) == 0

    # 3. Restore from archive
    success, msg = restore_backup_archive(archive_bytes, store)
    assert success is True
    assert "successfully" in msg

    # 4. Verify data restored
    restored_store = Storage(str(db_path))
    targets = restored_store.list_uptime_targets()
    assert len(targets) == 1
    assert targets[0]["name"] == "Backup Target"


def test_restore_archive_path_traversal_protection(tmp_path):
    """Verify archive containing directory traversal paths is safely rejected."""
    db_path = tmp_path / "systor.db"
    store = Storage(str(db_path))

    # Craft malicious tar.gz with .. in path
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w:gz") as tar:
        data = b"malicious content"
        ti = tarfile.TarInfo(name="../../etc/shadow")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))

    malicious_bytes = bio.getvalue()

    success, msg = restore_backup_archive(malicious_bytes, store)
    assert success is False
    assert "Dangerous path" in msg
