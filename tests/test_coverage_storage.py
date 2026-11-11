import io
import urllib.parse
from systor.web import create_app
from systor.config import save_config, load_config
from systor.services.storage_service import (
    format_ts, entry_payload, quick_storage_analysis
)


def _setup_storage_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    storage_root = tmp_path / "files"
    storage_root.mkdir()

    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    cfg = load_config()
    cfg["auth"] = {"enabled": False}
    cfg["storage_page"] = {
        "enabled": True,
        "allowed_roots": [str(storage_root)],
        "action_password_hash": "",
        "trash_enabled": True,
        "public_readonly": False,
    }
    save_config(cfg)

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    return app, storage_root


def test_storage_browse_and_mounts(tmp_path, monkeypatch):
    """Test /storage HTML page, /api/storage/settings, mounts, smart, and browse."""
    app, root = _setup_storage_app(tmp_path, monkeypatch)
    client = app.test_client()

    # Create dummy files and subdirs
    (root / "doc.txt").write_text("hello storage", encoding="utf-8")
    sub = root / "subfolder"
    sub.mkdir()
    (sub / "nested.log").write_text("log line", encoding="utf-8")

    # /storage page
    res_page = client.get("/storage", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_page.status_code == 200

    # /api/storage/settings
    res_set = client.get("/api/storage/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_set.status_code == 200
    assert "roots" in res_set.get_json()

    # /api/storage/mounts & smart
    res_mounts = client.get("/api/storage/mounts", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_mounts.status_code == 200
    res_smart = client.get("/api/storage/smart", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_smart.status_code == 200

    # Browse root folder
    browse_url = "/api/storage/browse?" + urllib.parse.urlencode({"path": str(root)})
    res_browse = client.get(browse_url, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_browse.status_code == 200
    browse_data = res_browse.get_json()
    assert browse_data["ok"] is True
    names = [e["name"] for e in browse_data["entries"]]
    assert "doc.txt" in names
    assert "subfolder" in names

    # Browse outside root rejected (403)
    outside_url = "/api/storage/browse?" + urllib.parse.urlencode({"path": str(tmp_path.parent)})
    res_out = client.get(outside_url, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_out.status_code == 403


def test_storage_action_mkdir_rename_delete(tmp_path, monkeypatch):
    """Test /api/storage/action for mkdir, rename, and delete (trash)."""
    app, root = _setup_storage_app(tmp_path, monkeypatch)
    client = app.test_client()

    # Action: mkdir
    res_mkdir = client.post(
        "/api/storage/action",
        json={"action": "mkdir", "path": str(root), "name": "created_dir"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_mkdir.status_code == 200
    assert (root / "created_dir").is_dir()

    # Create file for rename
    orig_f = root / "orig.txt"
    orig_f.write_text("orig content", encoding="utf-8")

    # Action: rename
    res_ren = client.post(
        "/api/storage/action",
        json={"action": "rename", "path": str(root), "items": [str(orig_f)], "name": "renamed.txt"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_ren.status_code == 200
    renamed_f = root / "renamed.txt"
    assert renamed_f.is_file()

    # Action: delete (move to trash)
    res_del = client.post(
        "/api/storage/action",
        json={"action": "delete", "path": str(root), "items": [str(renamed_f)]},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_del.status_code == 200
    assert not renamed_f.exists()

    # Trash listing
    res_trash = client.get("/api/storage/trash", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_trash.status_code == 200
    assert res_trash.get_json()["ok"] is True


def test_storage_upload_chunk_and_regular(tmp_path, monkeypatch):
    """Test chunked file upload and single-shot file upload."""
    app, root = _setup_storage_app(tmp_path, monkeypatch)
    client = app.test_client()

    # Single-shot upload
    res_up = client.post(
        "/api/storage/upload?" + urllib.parse.urlencode({"path": str(root)}),
        data={"files": (io.BytesIO(b"single shot content"), "single.txt")},
        content_type="multipart/form-data",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_up.status_code == 200
    assert (root / "single.txt").read_bytes() == b"single shot content"

    # Chunked upload
    upload_id = "test-uid-99"
    filename = "chunked.bin"
    chunk1 = b"AAAA"
    chunk2 = b"BBBB"
    total_size = len(chunk1) + len(chunk2)

    # Chunk 1
    url1 = f"/api/storage/upload-chunk?path={urllib.parse.quote(str(root))}&filename={filename}&upload_id={upload_id}&offset=0&total={total_size}"
    res1 = client.post(url1, data=chunk1, content_type="application/octet-stream", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res1.status_code == 200

    # Chunk 2
    url2 = f"/api/storage/upload-chunk?path={urllib.parse.quote(str(root))}&filename={filename}&upload_id={upload_id}&offset=4&total={total_size}"
    res2 = client.post(url2, data=chunk2, content_type="application/octet-stream", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res2.status_code == 200
    assert res2.get_json()["done"] is True
    assert (root / filename).read_bytes() == chunk1 + chunk2


def test_storage_ops_and_download(tmp_path, monkeypatch):
    """Test storage ops logging, export, clear, and download."""
    app, root = _setup_storage_app(tmp_path, monkeypatch)
    client = app.test_client()

    test_file = root / "download_me.txt"
    test_file.write_text("file to download", encoding="utf-8")

    # Download file
    dl_url = "/api/storage/download?" + urllib.parse.urlencode({"path": str(test_file)})
    res_dl = client.get(dl_url, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_dl.status_code == 200
    assert res_dl.data == b"file to download"

    # Ops endpoints
    res_ops = client.get("/api/storage/ops", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_ops.status_code == 200
    assert "rows" in res_ops.get_json()

    res_ops_exp = client.get("/api/storage/ops/export", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_ops_exp.status_code == 200

    res_ops_clr = client.post("/api/storage/ops/clear", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_ops_clr.status_code == 200


def test_storage_service_helpers(tmp_path):
    """Test pure helper functions in storage_service.py."""
    ts_str = format_ts(1700000000)
    assert len(ts_str) > 0

    p = tmp_path / "sample.txt"
    p.write_text("demo content", encoding="utf-8")
    payload = entry_payload(p)
    assert payload["name"] == "sample.txt"
    assert payload["type"] == "file"

    analysis = quick_storage_analysis(tmp_path, limit=100)
    assert analysis["scanned"] >= 1
