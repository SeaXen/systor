import urllib.parse
from systor.web import create_app
from systor.config import save_config, load_config


def test_C03_preview_url_encoding(tmp_path, monkeypatch):
    """C03: Image preview returns properly URL-encoded download_url for files with special characters."""
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    
    # Filename containing #, &, %, space, and unicode
    special_name = "test #1 & special % file.png"
    img_file = storage_root / special_name
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nfakeimagecontent")

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

    preview_req_path = "/api/storage/preview?" + urllib.parse.urlencode({"path": str(img_file)})
    res = client.get(preview_req_path, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["kind"] == "image"
    
    download_url = data["download_url"]
    # Download URL must be properly encoded and not break on raw '#' or '&'
    assert "#" not in download_url
    assert urllib.parse.quote(str(img_file)) in download_url

    # Request the download endpoint using the generated download_url
    dl_res = client.get(download_url, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert dl_res.status_code == 200
    assert dl_res.data == b"\x89PNG\r\n\x1a\nfakeimagecontent"
