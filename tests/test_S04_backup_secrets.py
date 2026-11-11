import io
import json
import tarfile
from systor.storage import Storage
from systor.services.backup_service import create_backup_archive
from systor.config import save_config, load_config


def test_S04_backup_strips_secrets_by_default(tmp_path, monkeypatch):
    """S04: Default backup strips bot token, webhook, password hash, and session secret."""
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    cfg = load_config()
    cfg["telegram"] = {"enabled": True, "bot_token": "SECRET_TELEGRAM_BOT_TOKEN_XYZ", "chat_id": "123"}
    cfg["discord"] = {"enabled": True, "webhook_url": "https://discord.com/api/webhooks/SECRET_DISCORD_TOKEN"}
    cfg["auth"] = {"enabled": True, "username": "admin", "password_hash": "SECRET_HASH_STRING_123", "session_secret": "SECRET_SESSION_KEY_456"}
    cfg["storage_page"] = {"action_password_hash": "SECRET_ACTION_HASH_789"}
    save_config(cfg)

    store = Storage(db_path=tmp_path / "data" / "systor.db")
    archive_bytes, filename = create_backup_archive(store, include_secrets=False)

    # Decompress tarball and verify no secret string appears anywhere in the archive bytes
    raw_tar = io.BytesIO(archive_bytes)
    with tarfile.open(fileobj=raw_tar, mode="r:gz") as tar:
        cfg_member = tar.extractfile("config.json")
        assert cfg_member is not None
        cfg_in_tar = json.loads(cfg_member.read().decode("utf-8"))
        assert cfg_in_tar["telegram"]["bot_token"] == ""
        assert cfg_in_tar["discord"]["webhook_url"] == ""
        assert cfg_in_tar["auth"]["password_hash"] == ""
        assert cfg_in_tar["auth"]["session_secret"] == ""
        assert cfg_in_tar["storage_page"]["action_password_hash"] == ""

    # Whole archive bytes check
    for secret in [
        b"SECRET_TELEGRAM_BOT_TOKEN_XYZ",
        b"SECRET_DISCORD_TOKEN",
        b"SECRET_HASH_STRING_123",
        b"SECRET_SESSION_KEY_456",
        b"SECRET_ACTION_HASH_789",
    ]:
        assert secret not in archive_bytes


def test_S04_backup_include_secrets_preserves_on_opt_in(tmp_path, monkeypatch):
    """S04: When include_secrets=True is passed, secrets are preserved."""
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    cfg = load_config()
    cfg["telegram"] = {"enabled": True, "bot_token": "MY_BOT_TOKEN_999"}
    save_config(cfg)

    store = Storage(db_path=tmp_path / "data" / "systor.db")
    archive_bytes, _ = create_backup_archive(store, include_secrets=True)

    raw_tar = io.BytesIO(archive_bytes)
    with tarfile.open(fileobj=raw_tar, mode="r:gz") as tar:
        cfg_member = tar.extractfile("config.json")
        assert cfg_member is not None
        cfg_in_tar = json.loads(cfg_member.read().decode("utf-8"))
        assert cfg_in_tar["telegram"]["bot_token"] == "MY_BOT_TOKEN_999"
