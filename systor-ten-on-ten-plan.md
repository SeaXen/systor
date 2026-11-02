# Systor 10/10 Architecture & Production Hardening Plan

> **SUPERSEDED — do not use.** This plan is retained for historical reference
> only. It was completed for v1.0.0 and claims "52/52 tests passing"; the
> repository now contains 106 unit tests plus 51 Playwright E2E checks.
>
> The current, evidence-based plan is **[`SYSTOR-10x10-PLAN.md`](SYSTOR-10x10-PLAN.md)**,
> which supersedes this file and is verified against a live browser audit.


> Target: Bring `systor` to a 10/10 standard across all 11 engineering sectors.
> Status: **COMPLETE** (All 6 Phases Finished & 52/52 Tests Passing)

---

## 🎯 Architectural Decisions (Cleared via Socratic Gate)
1. **Network Security:** Implement `trusted_proxies` allowlist (default `["127.0.0.1", "::1"]`). Strictly reject `X-Forwarded-For` from untrusted remote peers to fix the critical admin bypass.
2. **Modularity:** Decompose `systor/web.py` (2,726 lines) into clean Flask Blueprints (`dashboard`, `apps`, `network`, `speed`, `storage`, `settings`, `auth`) preserving 100% route and API backward compatibility.
3. **Collector Performance:** Instantiate `Storage` once outside the collection loop to stop running SQLite DDL and migrations on every poll tick.
4. **Daemon Lifecycle:** Move thread initialization out of module import; coordinate systemd restarts cleanly without port collisions.
5. **Installer & DX:** Use `/opt/systor/venv` and symlink `/usr/local/bin/systor` to eradicate PEP 668 `--break-system-packages` global pollution and fix the broken `systor` CLI command.
6. **Testing Fleet:** Build a multi-suite pytest setup covering metrics, storage, collector alerts, security boundaries, and API routes.

---

## 📋 Task Breakdown

### Phase 1: Security & Proxy Trust Hardening
- [x] **Task 1.1:** Add `trusted_proxies` configuration to `systor/config.py` and remove `/root` from default allowed storage roots.
- [x] **Task 1.2:** Implement strict proxy-aware `_client_private()` IP resolution in a new `systor/security.py` module.
- [x] **Task 1.3:** Enforce safe session verification for sensitive storage mutations and admin operations.

### Phase 2: Collector & Storage Optimization
- [x] **Task 2.1:** Refactor `systor/collector.py` to instantiate `Storage` once before the polling loop.
- [x] **Task 2.2:** Secure PID file path (use `/var/lib/systor/collector.pid` with fallback to `/tmp`).
- [x] **Task 2.3:** Harden systemd restart handling in `api_restart_web` to avoid port binding race conditions.

### Phase 3: Flask Modular Blueprints Refactor
- [x] **Task 3.1:** Create `systor/blueprints/` directory with shared helpers and error handlers.
- [x] **Task 3.2:** Extract `auth` blueprint (`/login`, `/logout`, session handling, rate limiter).
- [x] **Task 3.3:** Extract `dashboard` blueprint (`/`, `/api/snapshot`, `/api/series`, `/api/system`, `/health`).
- [x] **Task 3.4:** Extract `apps` blueprint (`/apps`, `/api/apps`, `/api/top-processes`).
- [x] **Task 3.5:** Extract `network` blueprint (`/network`, `/api/network-series`).
- [x] **Task 3.6:** Extract `speed` blueprint (`/speed`, `/api/speed/*`, live test runner).
- [x] **Task 3.7:** Extract `storage` blueprint (`/storage`, `/api/storage/*`, chunked upload, checksum, trash).
- [x] **Task 3.8:** Extract `settings` blueprint (`/settings`, `/api/settings/*`, alerts, logs, restarts).
- [x] **Task 3.9:** Slim down `systor/web.py` into a lightweight `create_app()` factory with controlled worker thread startup.

### Phase 4: Installer, Service Units & CLI Fixes
- [x] **Task 4.1:** Update `install.sh` to build a clean virtualenv in `/opt/systor/venv` and symlink `/usr/local/bin/systor`.
- [x] **Task 4.2:** Update `systemd/systor-*.service` unit files to reference `/opt/systor/venv/bin/python3`.
- [x] **Task 4.3:** Update `uninstall.sh` to clean up the virtualenv and symlink.

### Phase 5: Comprehensive Test Suite
- [x] **Task 5.1:** Add `tests/test_security.py` verifying `X-Forwarded-For` spoofing defense and storage path restrictions.
- [x] **Task 5.2:** Add `tests/test_metrics.py` testing Linux `/proc` and `/sys` delta readers.
- [x] **Task 5.3:** Add `tests/test_collector.py` testing sustained alerts, cooldowns, and recovery notifications.
- [x] **Task 5.4:** Add `tests/test_storage.py` testing SQLite schemas, rollups, and retention purging.
- [x] **Task 5.5:** Add `tests/test_blueprints.py` verifying API endpoints and backwards compatibility.

### Phase 6: Documentation & Final Verification
- [x] **Task 6.1:** Update `README.md` with new security notes and virtualenv instructions.
- [x] **Task 6.2:** Execute full pytest suite and verify all tests pass (52/52 passing).
