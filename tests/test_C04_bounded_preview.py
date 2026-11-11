import urllib.parse
from systor.web import create_app
from systor.config import save_config, load_config


def test_C04_bounded_text_preview(tmp_path, monkeypatch):
    """C04: Text preview reads at most 12000 chars from disk and sets truncated: True."""
    storage_root = tmp_path / "storage"
    storage_root.mkdir()

    # Create a large text file (500 KB)
    large_file = storage_root / "server.log"
    chunk = "A" * 1024 + "\n"
    with open(large_file, "w", encoding="utf-8") as f:
        for _ in range(500):
            f.write(chunk)

    assert large_file.stat().st_size > 500 * 1024

    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))

    cfg = load_config()
    cfg["storage_page"] = {
        "enabled": True,
        "allowed_roots": [str(storage_root)],
        "action_password_hash": "",
    }
    save_config(cfg)

    app = create_app(start_worker=False)
    client = app.test_client()

    preview_req = "/api/storage/preview?" + urllib.parse.urlencode({"path": str(large_file)})
    res = client.get(preview_req, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["kind"] == "text"
    assert len(data["text"]) == 12000
    assert data["truncated"] is True
    assert data["size"] == large_file.stat().st_size
