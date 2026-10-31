"""Storage file manager blueprint for Systor: browse, upload, download, trash, checksum."""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from flask import Blueprint, render_template, request, jsonify, abort, send_file, after_this_request
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash

from ..config import load_config, save_config
from .common import client_private, storage_can_write
from ..services.storage_service import (
    storage_cfg, storage_public_cfg, verify_storage_action_password,
    safe_roots, storage_roots_payload, resolve_storage_path,
    resolve_storage_browse_path, mount_payload, auto_storage_roots,
    virtual_root_entries, quick_storage_analysis, dir_size_limited,
    entry_payload, STORAGE_OP_LOG, log_storage_op, trash_destination,
    _storage_deep_state, _storage_deep_lock, _DENY_NAME_RE, is_under,
    format_ts
)
from ..services.smart_service import get_drive_health

storage_bp = Blueprint("storage", __name__)
log = logging.getLogger("systor.blueprints.storage")



def _get_action_pw(body: dict | None = None) -> str:
    pw = ""
    if body:
        pw = str(body.get("action_password") or "")
    return pw or request.headers.get("X-Storage-Password", "") or request.args.get("pw", "")


@storage_bp.route("/storage", endpoint="page_storage")
def page_storage():
    cfg = load_config()
    return render_template("storage.html", cfg=cfg)


@storage_bp.route("/api/storage/settings", methods=["GET", "POST"])
def api_storage_settings():
    cfg = load_config()
    scfg = storage_cfg()
    if request.method == "POST":
        if not storage_can_write():
            return jsonify({"ok": False, "error": "file actions are disabled on public/tunnel access; use LAN/Tailscale/local"}), 403
        body = request.get_json(silent=True) or {}
        ok, msg = verify_storage_action_password(_get_action_pw(body), scfg)
        if not ok:
            return jsonify({"ok": False, "error": msg}), 403
        raw_roots = body.get("allowed_roots") or ""
        clean = []
        if isinstance(raw_roots, str):
            candidate_roots = [x.strip() for x in re.split(r"[\n,]+", raw_roots) if x.strip()]
        elif isinstance(raw_roots, list):
            candidate_roots = [str(x).strip() for x in raw_roots if str(x).strip()]
        else:
            candidate_roots = []
        for r in candidate_roots:
            try:
                rp = Path(str(r)).expanduser().resolve()
                from ..services.storage_service import _DENY_PREFIXES
                if rp.exists() and str(rp) != "/" and not any(str(rp) == x or str(rp).startswith(x + "/") for x in _DENY_PREFIXES):
                    clean.append(str(rp))
            except Exception:
                continue
        cfg.setdefault("storage_page", {})
        if clean:
            cfg["storage_page"]["allowed_roots"] = ",".join(clean)
        cfg["storage_page"]["public_readonly"] = bool(body.get("public_readonly", True))
        cfg["storage_page"]["trash_enabled"] = bool(body.get("trash_enabled", True))
        cfg["storage_page"]["no_right_click"] = bool(body.get("no_right_click", False))
        cfg["storage_page"]["action_password_required"] = bool(body.get("action_password_required", False))
        new_pw = str(body.get("action_password") or "")
        if new_pw:
            cfg["storage_page"]["action_password_hash"] = generate_password_hash(new_pw)
        try:
            cfg["storage_page"]["max_scan_files"] = max(100, min(200000, int(body.get("max_scan_files", 20000))))
        except Exception:
            pass
        save_config(cfg)
        return jsonify({
            "ok": True,
            "message": "Storage settings saved",
            "config": storage_public_cfg(storage_cfg()),
            "roots": storage_roots_payload(),
            "can_write": storage_can_write()
        })
    return jsonify({
        "ok": True,
        "config": storage_public_cfg(scfg),
        "roots": storage_roots_payload(),
        "can_write": storage_can_write(),
        "public": not client_private()
    })


@storage_bp.route("/api/storage/mounts")
def api_storage_mounts():
    rows = []
    seen = set()
    candidates = [("Root / SSD", "/")]
    for r in auto_storage_roots():
        label = "Data " + r if r == "/mnt/tb" else Path(r).name or r
        candidates.append((label, r))
    for label, path in candidates:
        try:
            p = Path(path)
            if not p.exists():
                continue
            payload = mount_payload(p)
            key = (payload.get("total"), payload.get("mount"))
            if key in seen:
                continue
            seen.add(key)
            payload.update({"label": label, "path": str(p.resolve())})
            rows.append(payload)
        except Exception:
            continue
    return jsonify({"ok": True, "mounts": rows})


@storage_bp.route("/api/storage/smart")
def api_storage_smart():
    force = request.args.get("refresh") == "1"
    drives = get_drive_health(force_refresh=force)
    return jsonify({"ok": True, "drives": drives, "count": len(drives)})


@storage_bp.route("/api/storage/browse")

def api_storage_browse():
    raw_path = (request.args.get("path") or "").strip()
    if raw_path == "/":
        path = Path("/")
        managed = False
        entries = virtual_root_entries()
    else:
        path, managed, code, err = resolve_storage_browse_path(raw_path)
        if not path or not path.is_dir():
            return jsonify({"ok": False, "error": err or "path is not a directory"}), code
        entries = []
        try:
            for child in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                try:
                    if _DENY_NAME_RE.search(str(child)):
                        continue
                    if managed:
                        rec = entry_payload(child)
                    else:
                        try:
                            st_u = child.lstat()
                            rec = {
                                "name": child.name or str(child),
                                "path": str(child),
                                "type": "symlink" if child.is_symlink() else "dir" if child.is_dir() else "file",
                                "size": st_u.st_size if child.is_file() else None,
                                "mtime": st_u.st_mtime,
                                "mtime_text": format_ts(st_u.st_mtime),
                            }
                        except Exception:
                            continue
                    entries.append(rec)
                except Exception:
                    continue
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    try:
        limit = max(100, min(8000, int(request.args.get("limit", 3000))))
    except (ValueError, TypeError):
        limit = 3000
    entries = entries[:limit]
    breadcrumbs = []
    curr = path
    while curr and curr != curr.parent and str(curr) not in ("/", "\\", ""):
        breadcrumbs.append({"name": curr.name or str(curr), "path": str(curr)})
        curr = curr.parent
    breadcrumbs.append({"name": "/", "path": "/"})
    breadcrumbs.reverse()
    return jsonify({
        "ok": True,
        "path": str(path),
        "managed": managed,
        "can_write": storage_can_write() if managed else False,
        "entries": entries,
        "breadcrumbs": breadcrumbs,
        "mount": mount_payload(path) if str(path) != "/" else None,
    })


@storage_bp.route("/api/storage/analyze")
def api_storage_analyze():
    raw_path = (request.args.get("path") or "").strip()
    if raw_path == "/":
        path = Path("/")
    else:
        path, _, code, err = resolve_storage_browse_path(raw_path)
        if not path or not path.is_dir():
            return jsonify({"ok": False, "error": err or "path is not a directory"}), code
    try:
        limit = max(1, min(200, int(request.args.get("limit", 25))))
    except (ValueError, TypeError):
        limit = 25
    scfg = storage_cfg()
    mode = request.args.get("mode", "quick")
    data = quick_storage_analysis(path, min(scfg["max_scan_files"], 5000))
    return jsonify({
        "ok": True, "mode": mode if mode == "deep" else "quick", "path": str(path),
        "scanned": data["scanned"], "summary": data.get("summary", {}),
        "files": data["files"][:limit], "folders": data["folders"][:limit],
        "old_files": data["old_files"][:limit], "types": data["types"][:limit],
        "duplicates": data.get("duplicates", [])[:limit]
    })


@storage_bp.route("/api/storage/preview")
def api_storage_preview():
    p, code, err = resolve_storage_path(request.args.get("path"), must_exist=True)
    if not p or not p.is_file():
        return jsonify({"ok": False, "error": err or "preview supports files only"}), code
    st = p.stat()
    suffix = p.suffix.lower()
    text_ext = {".txt", ".md", ".log", ".json", ".yaml", ".yml", ".toml", ".csv", ".py", ".sh", ".js", ".ts", ".css", ".html", ".xml"}
    image_ext = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
    if suffix in image_ext:
        return jsonify({"ok": True, "kind": "image", "name": p.name, "size": st.st_size, "download_url": "/api/storage/download?path=" + str(p)})
    if suffix in text_ext or st.st_size < 128 * 1024:
        try:
            data = p.read_text(errors="replace")[:12000]
            return jsonify({"ok": True, "kind": "text", "name": p.name, "size": st.st_size, "text": data})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "kind": "binary", "name": p.name, "size": st.st_size, "message": "Binary/large file; use Download"})


@storage_bp.route("/api/storage/upload-chunk", methods=["POST"])
def api_storage_upload_chunk():
    if not storage_can_write():
        return jsonify({"ok": False, "error": "upload is LAN/Tailscale/local only"}), 403
    ok, msg = verify_storage_action_password(_get_action_pw())
    if not ok:
        return jsonify({"ok": False, "error": msg}), 403
    dest, code, err = resolve_storage_path(request.args.get("path"), must_exist=True)
    if not dest or not dest.is_dir():
        return jsonify({"ok": False, "error": err or "destination is not a folder"}), code
    filename = secure_filename(request.args.get("filename") or "")
    upload_id = re.sub(r"[^a-zA-Z0-9_.-]", "", request.args.get("upload_id") or "")[:80]
    if not filename or not upload_id:
        return jsonify({"ok": False, "error": "missing filename/upload_id"}), 400
    try:
        offset = int(request.args.get("offset") or 0)
        total = int(request.args.get("total") or 0)
    except Exception:
        return jsonify({"ok": False, "error": "bad offset/total"}), 400
    tmpdir = dest / ".systor-upload-tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    part, code, err = resolve_storage_path(str(tmpdir / f"{upload_id}.part"), must_exist=False)
    if not part:
        return jsonify({"ok": False, "error": err}), code
    data = request.get_data(cache=False)
    current = part.stat().st_size if part.exists() else 0
    if current != offset:
        return jsonify({"ok": False, "error": "offset mismatch", "expected_offset": current}), 409
    with part.open("ab") as f:
        f.write(data)
    written = offset + len(data)
    if total and written >= total:
        target, code, err = resolve_storage_path(str(dest / filename), must_exist=False)
        if not target:
            return jsonify({"ok": False, "error": err}), code
        if target.exists():
            stem, suffix = target.stem, target.suffix
            i = 1
            while target.exists() and i < 1000:
                target, _, _ = resolve_storage_path(str(dest / f"{stem}-{i}{suffix}"), must_exist=False)
                i += 1
        part.rename(target)
        log_storage_op("upload-chunk", str(target), True)
        return jsonify({"ok": True, "done": True, "saved": str(target), "written": written})
    return jsonify({"ok": True, "done": False, "written": written})


@storage_bp.route("/api/storage/upload", methods=["POST"])
def api_storage_upload():
    if not storage_can_write():
        return jsonify({"ok": False, "error": "upload is LAN/Tailscale/local only"}), 403
    ok, msg = verify_storage_action_password(_get_action_pw())
    if not ok:
        return jsonify({"ok": False, "error": msg}), 403
    dest, code, err = resolve_storage_path(request.args.get("path"), must_exist=True)
    if not dest or not dest.is_dir():
        return jsonify({"ok": False, "error": err or "destination is not a folder"}), code
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "error": "no files uploaded"}), 400
    saved = []
    try:
        for f in files:
            name = secure_filename(f.filename or "")
            if not name:
                continue
            target, _, _ = resolve_storage_path(str(dest / name), must_exist=False)
            if target and target.exists():
                stem, suffix = target.stem, target.suffix
                i = 1
                while target.exists() and i < 1000:
                    target, _, _ = resolve_storage_path(str(dest / f"{stem}-{i}{suffix}"), must_exist=False)
                    i += 1
            if target:
                f.save(target)
                saved.append(str(target))
                log_storage_op("upload", str(target), True)
        return jsonify({"ok": True, "message": f"Uploaded {len(saved)} file(s)", "saved": saved})
    except Exception as e:
        log_storage_op("upload", str(dest), False, str(e))
        return jsonify({"ok": False, "error": str(e)}), 400


@storage_bp.route("/api/storage/download")
def api_storage_download():
    if not client_private():
        abort(403, "download is LAN/Tailscale/local only")
    ok, msg = verify_storage_action_password(_get_action_pw())
    if not ok:
        abort(403, msg)
    src, code, err = resolve_storage_path(request.args.get("path"), must_exist=True)
    if not src:
        abort(code, err)
    if src.is_file():
        return send_file(src, as_attachment=True, download_name=src.name)
    if src.is_dir():
        tmpdir = Path(tempfile.mkdtemp(prefix="systor-download-"))
        zpath = tmpdir / f"{src.name or 'download'}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            base = src.parent
            count = 0
            for root, dnames, fnames in os.walk(src):
                dnames[:] = [d for d in dnames if d not in (".systor-trash", ".Trash")]
                for name in fnames:
                    fp = Path(root) / name
                    if fp.is_symlink() or _DENY_NAME_RE.search(str(fp)):
                        continue
                    zf.write(fp, fp.relative_to(base))
                    count += 1
                    if count >= 5000:
                        break
                if count >= 5000:
                    break
        @after_this_request
        def _cleanup(resp):
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass
            return resp
        return send_file(zpath, as_attachment=True, download_name=zpath.name)
    abort(400, "not a downloadable file or folder")


@storage_bp.route("/api/storage/action", methods=["POST"])
def api_storage_action():
    if not storage_can_write():
        return jsonify({"ok": False, "error": "file actions are disabled on public/tunnel access; use LAN/Tailscale/local"}), 403
    body = request.get_json(silent=True) or {}
    ok, msg = verify_storage_action_password(_get_action_pw(body))
    if not ok:
        return jsonify({"ok": False, "error": msg}), 403
    action = str(body.get("action") or "").strip().lower()
    base, code, err = resolve_storage_path(body.get("path"), must_exist=True)
    if not base:
        return jsonify({"ok": False, "error": err}), code
    items = body.get("items") or []
    if isinstance(items, str):
        items = [items]
    try:
        if action == "mkdir":
            name = re.sub(r"[\\/]+", "", str(body.get("name") or "").strip())
            if not name:
                raise ValueError("missing folder name")
            target, code, err = resolve_storage_path(str(base / name), must_exist=False)
            if not target:
                raise ValueError(err)
            target.mkdir(parents=False, exist_ok=False)
            log_storage_op(action, str(target), True)
            return jsonify({"ok": True, "message": f"Created {target.name}"})
        if action in ("copy", "move"):
            dest, code, err = resolve_storage_path(body.get("destination"), must_exist=True)
            if not dest or not dest.is_dir():
                raise ValueError(err or "destination is not a folder")
            for it in items:
                src, _, _ = resolve_storage_path(it, must_exist=True)
                if not src:
                    continue
                target = dest / src.name
                target, _, _ = resolve_storage_path(str(target), must_exist=False)
                if not target:
                    continue
                if action == "copy":
                    if src.is_dir():
                        shutil.copytree(src, target)
                    else:
                        shutil.copy2(src, target)
                else:
                    shutil.move(str(src), str(target))
                log_storage_op(action, f"{src} -> {target}", True)
            return jsonify({"ok": True, "message": f"{action} complete ({len(items)} item(s))"})
        if action == "rename":
            if len(items) != 1:
                raise ValueError("select exactly one item")
            src, code, err = resolve_storage_path(items[0], must_exist=True)
            if not src:
                raise ValueError(err)
            name = re.sub(r"[\\/]+", "", str(body.get("name") or "").strip())
            if not name:
                raise ValueError("missing new name")
            target, code, err = resolve_storage_path(str(src.parent / name), must_exist=False)
            if not target:
                raise ValueError(err)
            src.rename(target)
            log_storage_op(action, f"{src} -> {target}", True)
            return jsonify({"ok": True, "message": "Renamed"})
        if action == "delete":
            if not storage_cfg()["trash_enabled"]:
                raise ValueError("trash disabled; permanent delete not allowed")
            for it in items:
                src, _, _ = resolve_storage_path(it, must_exist=True)
                if not src:
                    continue
                target = trash_destination(src)
                shutil.move(str(src), str(target))
                log_storage_op("trash", f"{src} -> {target}", True)
            return jsonify({"ok": True, "message": f"Moved to trash ({len(items)} item(s))"})
        raise ValueError("unknown action")
    except Exception as e:
        log_storage_op(action or "unknown", str(body.get("items") or body.get("path") or ""), False, str(e))
        return jsonify({"ok": False, "error": str(e)}), 400


@storage_bp.route("/api/storage/ops")
def api_storage_ops():
    rows = []
    try:
        if STORAGE_OP_LOG.exists():
            rows = [json.loads(x) for x in STORAGE_OP_LOG.read_text(encoding="utf-8").splitlines()[-100:] if x.strip()]
            rows = list(reversed(rows))
    except Exception:
        rows = []
    return jsonify({"ok": True, "rows": rows})


@storage_bp.route("/api/storage/trash")
def api_storage_trash():
    rows = []
    for root in safe_roots():
        trash = root / ".systor-trash"
        if not trash.exists():
            continue
        try:
            for batch in sorted(trash.iterdir(), reverse=True):
                if not batch.is_dir():
                    continue
                for item in batch.iterdir():
                    try:
                        st = item.lstat()
                        rows.append({
                            "name": item.name, "path": str(item), "root": str(root),
                            "size": st.st_size if item.is_file() else None,
                            "type": "dir" if item.is_dir() else "file",
                            "mtime": st.st_mtime, "mtime_text": format_ts(st.st_mtime)
                        })
                    except Exception:
                        continue
        except Exception:
            continue
    return jsonify({"ok": True, "rows": rows[:300]})


@storage_bp.route("/api/storage/restore", methods=["POST"])
def api_storage_restore():
    if not storage_can_write():
        return jsonify({"ok": False, "error": "restore is LAN/Tailscale/local only"}), 403
    body = request.get_json(silent=True) or {}
    ok, msg = verify_storage_action_password(_get_action_pw(body))
    if not ok:
        return jsonify({"ok": False, "error": msg}), 403
    try:
        src = Path(str(body.get("trash_path") or "")).expanduser().resolve(strict=True)
    except Exception:
        return jsonify({"ok": False, "error": "invalid trash path"}), 400
    root = next((r for r in safe_roots() if is_under(r / ".systor-trash", src)), None)
    if not root:
        return jsonify({"ok": False, "error": "not a Systor trash item"}), 403
    dest_dir, code, err = resolve_storage_path(body.get("destination") or str(root), must_exist=True)
    if not dest_dir or not dest_dir.is_dir():
        return jsonify({"ok": False, "error": err or "destination is not a folder"}), code
    target = dest_dir / src.name
    i = 1
    while target.exists():
        target = dest_dir / f"{src.stem}-restored-{i}{src.suffix}"
        i += 1
    shutil.move(str(src), str(target))
    log_storage_op("restore", f"{src} -> {target}", True)
    return jsonify({"ok": True, "message": "Restored", "target": str(target)})


@storage_bp.route("/api/storage/ops/export")
def api_storage_ops_export():
    ok, msg = verify_storage_action_password(_get_action_pw())
    if not ok:
        abort(403, msg)
    if not STORAGE_OP_LOG.exists():
        STORAGE_OP_LOG.parent.mkdir(parents=True, exist_ok=True)
        STORAGE_OP_LOG.write_text("")
    return send_file(STORAGE_OP_LOG, as_attachment=True, download_name="systor-storage-ops.jsonl")


@storage_bp.route("/api/storage/ops/clear", methods=["POST"])
def api_storage_ops_clear():
    if not storage_can_write():
        return jsonify({"ok": False, "error": "clear is LAN/Tailscale/local only"}), 403
    body = request.get_json(silent=True) or {}
    ok, msg = verify_storage_action_password(_get_action_pw(body))
    if not ok:
        return jsonify({"ok": False, "error": msg}), 403
    STORAGE_OP_LOG.parent.mkdir(parents=True, exist_ok=True)
    STORAGE_OP_LOG.write_text("")
    return jsonify({"ok": True, "message": "Storage operation log cleared"})
