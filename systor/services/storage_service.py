"""Storage service: safe path resolution, file operations, scanning, and metadata."""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from werkzeug.security import check_password_hash

from ..config import load_config

log = logging.getLogger("systor.services.storage")

STORAGE_OP_LOG = Path("/var/lib/systor/storage_ops.jsonl")
_storage_deep_state = {
    "running": False, "done": False, "path": "", "started": 0, "ended": 0,
    "error": "", "result": None, "scanned": 0, "current": "", "folders_found": 0, "files_found": 0
}
_storage_deep_lock = threading.Lock()

_DENY_PREFIXES = (
    "/etc", "/boot", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/proc", "/sys", "/dev", "/run",
    "/var/lib/docker", "/var/lib/containerd", "/root/.hermes", "/root/.ssh", "/root/.config", "/root/.cache",
)
_DENY_NAME_RE = re.compile(r"(\.env$|id_rsa|id_ed25519|authorized_keys|token|secret|credential|auth\.json)", re.I)


def format_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


def auto_storage_roots() -> list[str]:
    """Default storage roots. By default, only safe data roots (/mnt/tb).

    /root is intentionally NOT included by default to prevent privilege escalation.
    """
    roots: list[str] = []
    for cand in ("/mnt/tb",):
        try:
            p = Path(cand)
            if p.exists() and p.is_dir() and str(p.resolve()) != "/":
                roots.append(str(p.resolve()))
        except Exception:
            pass
    return roots


def storage_cfg() -> dict:
    cfg = load_config().setdefault("storage_page", {})
    roots = cfg.get("allowed_roots") or auto_storage_roots()
    if isinstance(roots, str):
        allowed = [x.strip() for x in re.split(r"[\n,]+", roots) if x.strip()]
    elif isinstance(roots, list):
        allowed = [str(x).strip() for x in roots if str(x).strip()]
    else:
        allowed = auto_storage_roots()
    return {
        "allowed_roots": allowed or auto_storage_roots(),
        "public_readonly": bool(cfg.get("public_readonly", True)),
        "trash_enabled": bool(cfg.get("trash_enabled", True)),
        "no_right_click": bool(cfg.get("no_right_click", False)),
        "action_password_required": bool(cfg.get("action_password_required", False)),
        "action_password_hash": str(cfg.get("action_password_hash") or ""),
        "max_scan_files": max(100, min(200000, int(cfg.get("max_scan_files", 20000) or 20000))),
    }


def storage_public_cfg(cfg: dict) -> dict:
    out = dict(cfg)
    out.pop("action_password_hash", None)
    out["has_action_password"] = bool(cfg.get("action_password_hash"))
    return out


def verify_storage_action_password(password: str, cfg: dict | None = None) -> tuple[bool, str]:
    scfg = cfg or storage_cfg()
    if not scfg.get("action_password_required"):
        return True, ""
    stored = scfg.get("action_password_hash") or ""
    if not stored:
        return False, "Set a Storage action password first in Settings"
    if not password:
        return False, "Action password required"
    try:
        return (check_password_hash(stored, password), "Invalid action password")
    except Exception:
        return False, "Invalid action password config"


def safe_roots() -> list[Path]:
    out = []
    for r in storage_cfg()["allowed_roots"]:
        try:
            rp = Path(r).expanduser().resolve()
            if rp.exists() and str(rp) != "/":
                out.append(rp)
        except Exception:
            continue
    if not out:
        fb = Path("/mnt/tb")
        if fb.exists():
            out.append(fb.resolve())
    return out


def storage_roots_payload() -> list[dict]:
    rows = []
    for r in safe_roots():
        label = "Data " + str(r) if str(r) == "/mnt/tb" else "/root" if str(r) == "/root" else (r.name or str(r))
        rows.append({"path": str(r), "label": label, "readonly": False})
    if Path("/").exists():
        rows.append({"path": "/", "label": "Root / SSD", "readonly": True})
    return rows


def is_under(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_storage_path(value: str | None, must_exist: bool = True) -> tuple[Path | None, int, str]:
    """Resolve and validate a path inside allowed roots. Returns (Path, status_code, error)."""
    roots = safe_roots()
    if not roots:
        return None, 400, "no allowed storage roots configured"
    raw = (value or str(roots[0])).strip()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = roots[0] / p
    try:
        rp = p.resolve(strict=must_exist)
    except FileNotFoundError:
        try:
            rp = p.parent.resolve(strict=True) / p.name
        except Exception:
            return None, 404, "target parent directory does not exist"
    except Exception as e:
        return None, 400, f"invalid path: {e}"

    sp = str(rp)
    if sp == "/" or any(sp == x or sp.startswith(x + "/") for x in _DENY_PREFIXES):
        return None, 403, "blocked system or secret path"
    if _DENY_NAME_RE.search(sp):
        return None, 403, "blocked secret-like path"
    if not any(is_under(root, rp) for root in roots):
        return None, 403, "path escapes allowed roots"
    return rp, 200, ""


def resolve_storage_browse_path(value: str | None) -> tuple[Path | None, bool, int, str]:
    """Resolve browse path. Returns (Path, managed, status_code, error)."""
    raw = (value or "").strip()
    if raw == "/":
        return Path("/"), False, 200, ""
    roots = safe_roots()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (roots[0] / p) if roots else Path("/mnt/tb") / p
    try:
        rp = p.resolve(strict=True)
    except FileNotFoundError:
        return None, False, 404, "path does not exist"
    except Exception as e:
        return None, False, 400, str(e)

    sp = str(rp)
    if sp == "/" or any(sp == x or sp.startswith(x + "/") for x in _DENY_PREFIXES):
        return None, False, 403, "blocked system or secret path"
    if _DENY_NAME_RE.search(sp):
        return None, False, 403, "blocked secret-like path"
    managed = any(is_under(root, rp) for root in roots)
    return rp, managed, 200, ""


def dir_size_immediate(path: Path) -> tuple[int, int, int]:
    total = files = dirs = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_file(follow_symlinks=False):
                        try:
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                        except Exception:
                            pass
                    elif entry.is_dir(follow_symlinks=False):
                        dirs += 1
                except Exception:
                    continue
    except Exception:
        pass
    return total, files, dirs


def dir_size_limited(path: Path, max_files: int = 1200) -> tuple[int, int, int]:
    total = files = dirs = 0
    seen = 0
    try:
        for root, dnames, fnames in os.walk(path):
            dnames[:] = [d for d in dnames if d not in (".systor-trash", ".Trash")]
            dirs += len(dnames)
            for name in fnames:
                seen += 1
                if seen > max_files:
                    return total, files, dirs
                try:
                    fp = Path(root) / name
                    if fp.is_symlink():
                        continue
                    total += fp.stat().st_size
                    files += 1
                except Exception:
                    continue
    except Exception:
        pass
    return total, files, dirs


def entry_payload(p: Path) -> dict:
    try:
        st = p.lstat()
    except Exception:
        st = None
    typ = "symlink" if p.is_symlink() else "dir" if p.is_dir() else "file"
    size = 0
    if typ == "file" and st:
        size = st.st_size
    elif typ == "dir":
        size, _f, _d = dir_size_immediate(p)
    return {
        "name": p.name or str(p),
        "path": str(p),
        "type": typ,
        "size": size,
        "mtime": st.st_mtime if st else 0,
        "mtime_text": format_ts(st.st_mtime if st else 0),
    }


def disk_usage_bounded(path: Path, timeout: float = 2.0):
    box: dict = {}
    def _run():
        try:
            u = shutil.disk_usage(str(path))
            box["ok"] = (u.total, u.used, u.free)
        except Exception as e:
            box["err"] = str(e)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    return box.get("ok")


def mount_payload(path: Path) -> dict:
    try:
        usage = disk_usage_bounded(path, timeout=2.0)
    except Exception:
        return {"mount": str(path), "total": 0, "used": 0, "free": 0, "used_pct": 0, "unavailable": True}
    if usage is None:
        return {"mount": str(path), "total": 0, "used": 0, "free": 0, "used_pct": 0, "unavailable": True}
    total, used, free = usage
    return {
        "mount": str(path),
        "total": total,
        "used": used,
        "free": free,
        "used_pct": round((used / total) * 100, 1) if total else 0,
    }


def log_storage_op(action: str, path: str, ok: bool, error: str = "") -> None:
    try:
        STORAGE_OP_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": int(time.time()),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "path": path,
            "ok": bool(ok),
            "error": error[:300],
        }
        with STORAGE_OP_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def trash_destination(p: Path) -> Path:
    roots = safe_roots()
    root = next((r for r in roots if is_under(r, p)), roots[0] if roots else Path("/mnt/tb"))
    trash = root / ".systor-trash" / datetime.now().strftime("%Y%m%d-%H%M%S")
    trash.mkdir(parents=True, exist_ok=True)
    return trash / p.name


def virtual_root_entries() -> list[dict]:
    rows = []
    try:
        for p in Path("/").iterdir():
            try:
                sp = str(p.resolve())
                if any(sp == x or sp.startswith(x + "/") for x in _DENY_PREFIXES):
                    continue
                st = p.lstat()
                typ = "symlink" if p.is_symlink() else "dir" if p.is_dir() else "file"
                rows.append({
                    "name": p.name or str(p),
                    "path": str(p),
                    "type": typ,
                    "size": st.st_size if typ == "file" else None,
                    "mtime": st.st_mtime,
                    "mtime_text": format_ts(st.st_mtime),
                })
            except Exception:
                continue
    except Exception:
        pass
    rows.sort(key=lambda r: (r.get("type") != "dir", r.get("name", "").lower()))
    return rows


def quick_storage_analysis(path: Path, limit: int) -> dict:
    files = []
    folders = []
    old = []
    type_map = {}
    scanned = 0
    now = time.time()
    if str(path) == "/":
        entries = virtual_root_entries()
    else:
        try:
            entries = [entry_payload(x) for x in path.iterdir() if not x.is_symlink() and x.name not in (".systor-trash", ".Trash")]
        except Exception:
            entries = []
    for e in entries:
        scanned += 1
        if scanned > limit:
            break
        if e.get("type") == "dir":
            folders.append({"path": e["path"], "size": int(e.get("size") or 0)})
        else:
            size = int(e.get("size") or 0)
            mtime = float(e.get("mtime") or now)
            age = int((now - mtime) / 86400)
            rec = {"path": e["path"], "size": size, "mtime": mtime, "age_days": age}
            files.append(rec)
            if size > 50 * 1024 * 1024 and age >= 30:
                old.append(rec)
            ext = Path(e["path"]).suffix.lower() or "[none]"
            cur = type_map.setdefault(ext, {"ext": ext, "count": 0, "size": 0})
            cur["count"] += 1
            cur["size"] += size
    files.sort(key=lambda x: x["size"], reverse=True)
    folders.sort(key=lambda x: x["size"], reverse=True)
    old.sort(key=lambda x: (x["age_days"], x["size"]), reverse=True)
    dup_map = {}
    for rec in files:
        key = (Path(rec["path"]).name.lower(), rec["size"])
        dup_map.setdefault(key, []).append(rec)
    dups = [
        {"name": k[0], "size": k[1], "count": len(v), "paths": [g["path"] for g in v[:6]]}
        for k, v in dup_map.items() if len(v) > 1 and k[1] > 0
    ]
    dups.sort(key=lambda x: (x["count"], x["size"]), reverse=True)
    types = sorted(type_map.values(), key=lambda x: x["size"], reverse=True)
    return {
        "files": files,
        "folders": folders,
        "old_files": old,
        "types": types,
        "duplicates": dups,
        "scanned": scanned,
        "summary": {
            "files": len(files),
            "folders": len(folders),
            "old_large": len(old),
            "duplicates": len(dups),
            "types": len(types),
            "total_file_size": sum(x.get("size", 0) for x in files),
        },
    }
