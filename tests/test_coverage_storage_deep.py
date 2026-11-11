
from systor.services.storage_service import (
    dir_size_immediate, dir_size_limited, entry_payload, disk_usage_bounded,
    mount_payload, log_storage_op, virtual_root_entries,
    quick_storage_analysis
)
from systor.web import create_app


def test_storage_service_helpers(tmp_path):
    """Test disk analysis, dir sizes, mount payload, and trash helpers."""
    # Test dir sizes
    d = tmp_path / "test_dir"
    d.mkdir()
    f1 = d / "file1.txt"
    f1.write_text("hello world " * 100)
    sub = d / "sub"
    sub.mkdir()
    f2 = sub / "file2.txt"
    f2.write_text("systor " * 50)

    tot, files, dirs = dir_size_immediate(d)
    assert files == 1
    assert dirs == 1
    assert tot > 0

    tot_lim, files_lim, dirs_lim = dir_size_limited(d, max_files=1)
    assert files_lim <= 2
    assert tot_lim > 0

    # entry_payload
    p_file = entry_payload(f1)
    assert p_file["type"] == "file"
    assert p_file["size"] > 0
    p_dir = entry_payload(d)
    assert p_dir["type"] == "dir"
    assert p_dir["size"] > 0

    # disk_usage_bounded
    u = disk_usage_bounded(tmp_path, timeout=1.0)
    assert u is not None
    assert len(u) == 3

    # mount_payload
    mp = mount_payload(tmp_path)
    assert mp["total"] > 0
    assert "used_pct" in mp

    # log_storage_op
    log_storage_op("mkdir", str(d), True)
    log_storage_op("error_op", str(d), False, error="permission denied")

    # virtual_root_entries
    v_entries = virtual_root_entries()
    assert isinstance(v_entries, list)

    # quick_storage_analysis
    analysis = quick_storage_analysis(d, limit=50)
    assert analysis["scanned"] >= 1
    assert "summary" in analysis
    assert "files" in analysis
    assert "folders" in analysis


def test_storage_blueprints_deep(tmp_path, monkeypatch):
    """Test POST /api/storage/config, /api/storage/analyze, /api/storage/action copy/move, folder zip download."""
    root1 = tmp_path / "root1"
    root1.mkdir()
    (root1 / "test.txt").write_text("content in root1")

    # Configure storage roots
    cfg = {
        "storage_page": {
            "allowed_roots": str(root1),
            "trash_enabled": True,
            "public_readonly": True,
            "action_password_required": False,
            "max_scan_files": 5000,
        },
        "auth": {"enabled": False},
        "web": {"trusted_proxies": "127.0.0.1"},
    }
    monkeypatch.setattr("systor.blueprints.storage.load_config", lambda: cfg)
    monkeypatch.setattr("systor.blueprints.storage.save_config", lambda c: None)
    monkeypatch.setattr("systor.services.storage_service.load_config", lambda: cfg)
    monkeypatch.setattr("systor.blueprints.common.load_config", lambda: cfg)

    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        # POST /api/storage/settings
        resp = client.post("/api/storage/settings", json={
            "allowed_roots": str(root1),
            "trash_enabled": True,
            "public_readonly": False,
            "action_password_required": False,
            "max_scan_files": 10000,
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        # GET /api/storage/analyze
        resp = client.get(f"/api/storage/analyze?path={root1}", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        # POST /api/storage/action -> copy
        dest_folder = root1 / "dest"
        dest_folder.mkdir()
        resp = client.post("/api/storage/action", json={
            "action": "copy",
            "path": str(root1),
            "items": [str(root1 / "test.txt")],
            "destination": str(dest_folder),
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert (dest_folder / "test.txt").exists()

        # POST /api/storage/action -> move
        f_to_move = root1 / "move_me.txt"
        f_to_move.write_text("move me please")
        resp = client.post("/api/storage/action", json={
            "action": "move",
            "path": str(root1),
            "items": [str(f_to_move)],
            "destination": str(dest_folder),
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert not f_to_move.exists()
        assert (dest_folder / "move_me.txt").exists()

        # Download directory as ZIP: GET /api/storage/download?path=...
        resp = client.get(f"/api/storage/download?path={dest_folder}", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert resp.headers.get("Content-Type") in ("application/zip", "application/x-zip-compressed")

        # Test Trash Restore: POST /api/storage/restore
        trash_dir = root1 / ".systor-trash" / "20261111-120000"
        trash_dir.mkdir(parents=True)
        trashed_file = trash_dir / "restored.txt"
        trashed_file.write_text("I am from trash")

        resp = client.post("/api/storage/restore", json={
            "trash_path": str(trashed_file),
            "destination": str(root1),
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert (root1 / "restored.txt").exists()

        # Storage ops export and clear
        resp = client.get("/api/storage/ops/export", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200

        resp = client.post("/api/storage/ops/clear", json={}, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
