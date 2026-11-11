# Systor — Audit Result & Agent Fix Plan

> **Audited:** `systor-main.zip` (v1.1.0, 118 files, ~7.8k lines Python) on 2026-11-11.
> **Method:** extracted the repo, ran the real test suite, ran the project's own Chromium design audit
> (`tools/design_audit.py`, 9 pages x 3 themes x 3 viewports), ran bandit / mypy / coverage, and read the code
> for every item below. Each finding is tagged:
>
> - **[VERIFIED]** reproduced by running code, or read directly at the cited line.
> - **[BY INSPECTION]** the code is wrong as written, but I could not execute that environment (e.g. Python 3.9, a real Docker host).
> - **[CHECK]** a risk worth confirming; not proven.

---

## 0. Verdict: NOT 10/10

Overall: **about 6.7 / 10**. The project is in much better shape than its own older plan documents say (most of the
original P0 problems are fixed), but it is not 10/10 in any sector except arguably Dark-theme accessibility.

| # | Sector | Score | Why not 10 |
|---|---|---|---|
| 1 | Security | **6** | Hard-coded fallback session secret; auth OFF by default so every LAN client is root-equivalent; CSP still `unsafe-inline`; backups contain secrets in plaintext; systemd uses a deny-list |
| 2 | Correctness / logic | **7** | Backup restore fails on Docker/systemd (cross-device `os.replace`); Python 3.9 import break; unbounded preview read; un-encoded download URL |
| 3 | Architecture | **6** | 31 direct storage/SQL calls inside blueprints; workers started inside `create_app()`; `settings.py` is 720 lines at 14% coverage |
| 4 | Performance / reliability | **7** | Good (threads, semaphore, SSE cap, real `/health`); preview/scan paths can still blow memory |
| 5 | Testing | **5** | 106 tests pass, but **46% line coverage**; no coverage gate; CSRF is disabled in tests; `settings.py` 14%, `storage.py` blueprint 23%, `speed.py` 19% |
| 6 | Design / UX / A11y | **7** | Looks professional and consistent in dark themes; 0 contrast failures there. **Paper Light: 87 failures** (one pill style) plus visible polish bugs found by eye (see D08-D16): blank pager buttons, dark bar tracks in Light, clipped mobile nav, toolbar overlapping content, contradictory Storage status. 9.5 px text. Heavy inline JS |
| 7 | Docs / DX | **7** | README `docker run` contradicts the hardened compose; three overlapping plan files; stale claims |
| 8 | Deployment / Docker | **7** | Compose + systemd are well hardened. Restore broken inside it; CI has no image build/scan; Python 3.9 matrix likely red |
| 9 | Code quality | **6** | 67 `except ...: pass` blocks; 33 mypy errors and mypy not in CI; duplicated giant one-line JS functions |
| 10 | API consistency | **6.5** | Mixed error shapes; `/health` leaks internals; CSP weak; no API versioning/schema doc |

### What is already GOOD (do not break these)
Verified fixed in the current code: terminal backdoor removed (`terminal.py` refuses without a hash), tar extraction uses
`filter="data"`, CSRF double-submit exists, `docker.sock` removed from compose, non-root user + `cap_drop: ALL` +
`read_only` + `no-new-privileges`, systemd units sandboxed, `/health` checks collector freshness, metric lock + page-size
derivation, subprocess semaphore, SSE cap of 2, uptime TLS verification on by default, `<h2>` structure (51 across the
app), focus trap + restore on the terminal dialog, zero horizontal overflow on all 27 page/viewport combos, zero
console errors, zero unlabeled controls, zero images without alt.
Test results: 106/106 pytest pass; `ruff` passes with the repo's own config; Playwright audit: overflow 0/27, console errors 0/27.

---

## 1. Rules for the agent executing this file

1. Work **in the order of the phases**. Do not start Phase 2 until Phase 1 acceptance commands pass.
2. **One finding = one commit.** Commit message: `fix(<ID>): <short title>`.
3. **Every fix gets a regression test** in `tests/` named after the ID (e.g. `test_S01_...`). A fix with no failing-then-passing test is not done.
4. Do not change public routes, JSON keys, or config keys unless the finding says so. If you must, add a CHANGELOG entry.
5. Keep the product invariants from `PRODUCT.md`: no external CDN/JS/CSS, no npm build step, runs on Python >= 3.9, near-zero RAM.
6. After **each phase** run the full gate (section 9). If anything regresses, fix it before moving on.
7. Never weaken a test to make it pass. Never skip a test without a written reason.
8. If a finding says `[CHECK]`, confirm it first; if it is not real, write `NOT REPRODUCED` with evidence in the PR and move on.
9. Do **not** edit the three older plan files; Phase 5 deletes/merges them.

---

## 2. Phase 1 — Security (do first)

### S01 — Hard-coded fallback session secret  `CRITICAL` [VERIFIED]
- **Where:** `systor/web.py:46`
  `app.secret_key = auth_boot.get("session_secret") or os.environ.get("SYSTOR_SESSION_SECRET") or "systor-secret-key-32b-secure-fallback"`
- **Problem:** The fallback string is public in the GitHub repo. If `auth.session_secret` is empty (it is empty in `DEFAULT_CONFIG`,
  `config.py` auth block) anyone can forge a Flask session cookie containing `systor_auth=True` and `systor_user=<username>`
  and become logged in as admin. The secret is only generated when auth is saved through the UI (`settings.py:380`); a config
  written by hand, restored from backup, or set via CLI keeps the public secret.
- **Fix:**
  1. Delete the literal fallback. Resolve the secret in this order: config `auth.session_secret` -> env `SYSTOR_SESSION_SECRET` -> a
     **generated random secret persisted to `<data_dir>/session.key`** (mode `0600`, created atomically on first start).
  2. If the key file cannot be written, generate an in-memory random key and log a WARNING (sessions then reset on restart; never fall back to a constant).
  3. Add a helper `get_or_create_session_secret()` in `systor/config.py` and use it in `web.py` and `settings.py:380-383`.
- **Acceptance:** `grep -rn "secure-fallback" systor/` returns nothing. New test creates an app with empty config, forges a cookie
  signed with the old literal string, and asserts the request is **not** authenticated (HTTP 401/redirect to `/login`).

### S02 — Auth disabled by default; any private IP is full admin  `HIGH` [VERIFIED]
- **Where:** `config.py` auth block (`"enabled": False`), `blueprints/common.py:206-224` (`effective_private_ui`, `storage_can_write`), `web.py:106-122`, `security.py` (`is_private_ip` accepts loopback, RFC1918, link-local, and CGNAT `100.64.0.0/10`).
- **Problem:** With defaults the app binds `0.0.0.0:6677` and treats every LAN / Docker-bridge / Tailscale / guest-Wi-Fi client as admin:
  storage write, service restarts, settings, backup restore, and (after the admin-password prompt, which cannot exist without a hash) the console.
  Anyone on the same network, or any container on the Docker bridge, controls the host.
- **Fix:**
  1. **First-run setup gate:** if `auth_enabled()` is false and the request is not from loopback (`127.0.0.1`/`::1`), every page except `/health` and `/login` redirects to a new `/setup` page that forces creating the admin username + password (min length 12, confirm field, strength hint). After setup, write the hash and generate the session secret (S01).
  2. Keep the dashboard `/` and `/api/snapshot` etc. read-only visible without login (as today), but all **mutating** routes and `/settings`, `/storage`, `/logs`, `/apps` log streams require login when the client is not loopback. Implement as one decorator `@admin_required` in `blueprints/common.py` and apply it to every non-GET route plus sensitive GET routes. Do not scatter ad-hoc checks.
  3. `README.md` Quick Start: show `systor passwd` (add the CLI command if missing) as step 1.
  4. Add config `web.trust_cgnat` (default `true` to preserve Tailscale behaviour) so users can narrow the private-range list.
- **Acceptance:** fresh app + request from `192.168.1.50` -> GET `/settings` returns 302 to `/setup`; POST `/api/settings/*` returns 401/403; after setup it works. Tests: `test_S02_first_run_requires_setup`, `test_S02_mutations_require_admin_on_lan`.

### S03 — Content-Security-Policy still allows `unsafe-inline`  `HIGH` [VERIFIED]
- **Where:** `web.py:101` `default-src 'self' 'unsafe-inline'; img-src 'self' data:;`
- **Problem:** Any stored/reflected HTML injection becomes script execution. The app renders data from containers, process names, file names and log lines into `innerHTML` in many places.
- **Fix:** (needs Phase 4 template work for scripts; do the safe parts now)
  1. Now: set `default-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'` and keep `'unsafe-inline'` only for `style-src` until D05 is done.
  2. After D05 (inline JS extracted): `script-src 'self'` only, no `unsafe-inline`.
  3. Theme pre-boot script in `base.html:15-24` becomes `static/js/theme-boot.js` loaded in `<head>` (blocking, tiny).
- **Acceptance:** Playwright test loads all 9 pages with CSP enforced and asserts **0 CSP violation console messages**. `curl -sI /` shows no `unsafe-inline` in `script-src`.

### S04 — Backups contain secrets in plaintext  `HIGH` [VERIFIED]
- **Where:** `services/backup_service.py:~47-70` writes `cfg = load_config()` straight into `config.json` inside the archive (password hash, `session_secret`, Telegram `bot_token`, Discord `webhook_url`, `action_password_hash`). The config *export* route strips these (`settings.py:396`) but the backup does not.
- **Fix:** Strip the same keys from the archive's `config.json` by default. Offer an explicit "include secrets" checkbox in the Settings backup UI that requires re-entering the admin password and shows a warning. On restore, **never** overwrite `auth.*` from the archive unless the archive was created with secrets and the admin confirms.
- **Acceptance:** test creates a backup with a configured bot token and asserts the token string does not appear anywhere in the tarball bytes (decompress and search).

### S05 — Backup restore weaknesses  `MEDIUM` [VERIFIED]
- **Where:** `backup_service.py:90-104`.
  - `".." in member.name` rejects legitimate names containing `..` and still allows symlink/hardlink tricks on the Python < 3.12 path only via the fallback; fallback uses `dest.startswith(str(temp_dir))` which is prefix-unsafe (`/tmp/ab` vs `/tmp/abc`).
  - Restore accepts any size archive from memory (`archive_bytes`) - no upper bound.
- **Fix:** Use `os.path.commonpath([dest, str(temp_dir)]) == str(temp_dir)`; reject any member that is not exactly one of `manifest.json`, `systor.db`, `config.json`; cap upload at 256 MB in the route (`settings.py:702`) with HTTP 413; reject symlinks/hardlinks/devices explicitly on every Python version.
- **Acceptance:** extend `tests/test_backup.py` with: symlink member, hardlink member, absolute path, `../x`, unexpected extra file, oversize body.

### S06 — systemd restart uses a deny-list  `MEDIUM` [VERIFIED]
- **Where:** `services/systemd_service.py:23-40` (`_DENY_UNITS`), check at `:111`.
- **Problem:** A deny-list of 16 units means every other unit (e.g. `cron`, `fail2ban`, `wg-quick@*`, `tailscaled`, `nginx`, `cloudflared`) is restartable by any admin session, and anything missing from the list (e.g. `systemd-timesyncd`, `NetworkManager`, `wpa_supplicant`, `user@1000`) can cut the host off.
- **Fix:** Replace with an allow-list: config key `systemd.allowed_units` (list of globs, default `["systor-*.service"]`). The UI lists only allowed units as actionable; others show read-only. Document in README.
- **Acceptance:** `test_S06_restart_denied_for_unlisted_unit`.

### S07 — CSRF hardening  `MEDIUM` [VERIFIED]
- **Where:** `web.py:52-72`, `:102-103`.
- **Problems:** (a) the token is accepted if it matches *either* the cookie *or* the session — a cookie-only match is the classic weak double-submit; require the session value. (b) the `systor_csrf` cookie has no `Secure` flag when served over HTTPS and no `HttpOnly` decision is documented. (c) CSRF is skipped entirely when `TESTING` is set, so **no test proves CSRF works** except any that set `CSRF_CHECK_IN_TESTS`.
- **Fix:** Validate only against `session["systor_csrf"]` (embed the token in a `<meta name="csrf-token">` in `base.html` and have the shared `api()` helper send it as `X-CSRF-Token`; drop the readable cookie). Set `SESSION_COOKIE_SECURE` automatically when `request.is_secure` or `X-Forwarded-Proto=https` from a trusted proxy. Add tests with `CSRF_CHECK_IN_TESTS=True` for 5 representative mutating routes (missing token -> 403, wrong token -> 403, correct -> 200).

### S08 — Smaller security items  `LOW` [VERIFIED]
- `dashboard.py:73, 140` MD5 used for ETags -> `hashlib.md5(..., usedforsecurity=False)` (silences bandit B324 and is FIPS-safe) or switch to `blake2b(digest_size=16)`.
- `storage.py:261,313,315,436` string-built SQL. Values are parameterised and the fragments are internal constants, so this is not exploitable today, but add a one-line comment `# nosec B608 - fragments are internal constants` **and** a unit test proving unknown filter keys are ignored. Do not blanket-ignore.
- `urllib.request.urlopen` at `bot.py:42,128`, `notifier.py:31,55`, `uptime_service.py:70`, `speed.py:46`: add a helper `safe_urlopen(url, ...)` that rejects any scheme other than `http`/`https` (matters because webhook URLs and uptime targets are user-supplied).
- `/health` returns the raw stale-collector message (`dashboard.py:51-55`) to unauthenticated callers. Return `{"ok": false, "status": "stale"}` publicly; full detail only for private clients.
- Login rate limiter is in-memory per process (`common.py`): restarts reset it. Acceptable for a single-admin tool; document it.
- `settings.py:65` and `collector.py:194` use fixed `/tmp/...` paths (bandit B108): use `tempfile.mkstemp` / the data dir.
- Terminal (`terminal.py`) runs `/bin/sh -c <anything>` as the service user by design. It is correctly gated, but add: config switch `terminal.enabled` (default **false**), an audit-log line for every command (user, IP, command, exit code), and a max output size (e.g. 256 KB) so a `cat` of a huge file cannot exhaust memory.

**Phase 1 acceptance:** `bandit -r systor -ll` has 0 HIGH and every MEDIUM is either fixed or has a justified `# nosec` with a test; all `test_S*` tests pass.

---

## 3. Phase 2 — Correctness bugs (real logic errors)

### C01 — Backup restore always fails in Docker (and under systemd `PrivateTmp`)  `HIGH` [VERIFIED mechanism]
- **Where:** `backup_service.py:85` extracts to `tempfile.TemporaryDirectory()` (in `/tmp`), then `:153` `os.replace(restored_db, active_db)`.
- **Problem:** In the shipped compose file `/tmp` is a **tmpfs** and the DB lives on the `systor_data` volume — two filesystems. `os.replace` across filesystems raises `OSError: [Errno 18] Invalid cross-device link` (reproduced in the sandbox with `/dev/shm` -> disk). The user sees "Error swapping database file" and restore never works in the recommended deployment. The code also *claims* the swap is "atomic".
- **Fix:** Create the temp dir **next to the live DB**: `tempfile.TemporaryDirectory(dir=active_db.parent, prefix=".restore-")`. Then `os.replace` is same-filesystem and truly atomic. Make sure the data dir is writable (it is in compose/systemd).
- **Acceptance:** test patches `tempfile` default dir to a different mount (or monkeypatches `os.replace` to raise EXDEV unless source and dest share `st_dev`) and asserts restore still succeeds.

### C02 — Python 3.9 import failure  `HIGH` [BY INSPECTION]
- **Where:** `systor/config.py:287` `def save_config(cfg: dict, path: Path | None = None)` and the file has **no** `from __future__ import annotations`.
- **Problem:** `Path | None` is evaluated when the function is defined; on Python 3.9 this raises `TypeError: unsupported operand type(s) for |`. `pyproject.toml` says `requires-python >= 3.9` and CI runs 3.9, so `import systor.config` (and therefore the whole app) breaks on 3.9. (Every other module already has the future import.)
- **Fix:** add `from __future__ import annotations` as the first statement after the module docstring in `config.py`; add `vermin`/`ruff --target-version py39` check or a CI job that imports every module on 3.9.
- **Acceptance:** CI 3.9 job green; `python3.9 -c "import systor.config, systor.web, systor.cli"` works.

### C03 — Image preview builds a broken URL  `MEDIUM` [VERIFIED]
- **Where:** `blueprints/storage.py:234` `"download_url": "/api/storage/download?path=" + str(p)`
- **Problem:** the path is not URL-encoded. A file named `photo #1.png`, `a&b.jpg`, or `100%.png` produces a wrong URL (everything after `#`/`&`/`%` is lost or misparsed) -> broken preview or the wrong file.
- **Fix:** `urllib.parse.quote(str(p), safe="/")` or `url_for("storage.api_storage_download", path=str(p))`.
- **Acceptance:** test with a file named `a #b&c%d.png`.

### C04 — Text preview reads the entire file into RAM  `MEDIUM` [VERIFIED]
- **Where:** `storage.py:235-237` `p.read_text(errors="replace")[:12000]`
- **Problem:** the extension branch (`.log`, `.json`, `.csv`, ...) has **no size limit**; a 5 GB log is fully read to return 12 000 characters. This breaks the "<30 MB RAM" promise and can OOM-kill the service.
- **Fix:** open the file and `read(12000)` (bytes, then decode with `errors="replace"`); report `truncated: true` and the real size. Same audit for `checksum-duplicates`, deep scan, and any `read_bytes()`/`read_text()` in `services/storage_service.py`.
- **Acceptance:** test with a sparse 2 GB file: preview returns in < 1 s and process RSS grows < 50 MB.

### C05 — Service-worker serves stale JS forever  `MEDIUM` [VERIFIED]
- **Where:** `static/sw.js:8` `CACHE_NAME = 'systor-shell-v1.1.1'` (package is `1.1.0`), static assets are **cache-first** (`:61-75`), and `app.js` / `dialog.js` / `tokens.css` have no `?v=` query.
- **Problem:** after an upgrade, installed PWAs and returning browsers keep running the old `app.js` until someone manually edits the cache name. Version strings disagree (`1.1.0` vs `1.1.1`). `fetch` handler also falls back to `caches.match('/')` for any failed navigation, which can show a cached **authenticated** dashboard shell to a logged-out user.
- **Fix:** inject the app version into `sw.js` (serve it from a Flask route with `__version__` templated, or add `?v={{ version }}` to every asset URL and use stale-while-revalidate for CSS/JS). Navigation fallback should show a small static `/offline` page, never a cached page. Bump `CACHE_NAME` from `__version__` automatically.
- **Acceptance:** Playwright test: load page, change version, reload, assert new JS is executed; offline navigation shows the offline page, not dashboard content.

### C06 — Logic/behaviour to double-check  [CHECK]
- Restore overwrites `config.json` from the archive with `save_config(new_cfg)` (`backup_service.py:~165`): this can silently replace `auth.*` and web host/port. Resolved by S04, but add a test.
- `collector.py` loads/saves config on every tick in places; confirm it re-reads at most once per N seconds (CPU/IO on Raspberry Pi).
- `uptime_service` marks a target "down" after 2 consecutive failures but the recovery path must reset `_fail_count`; add a test sequence fail,fail,ok,fail -> still "up".
- CHANGELOG 1.0.0 entry still advertises "Chart.js", "Ping and DNS endpoints", "start/stop/restart", "zero-downtime restore" (the file contains its own correction note, which is acceptable, but README must not repeat those claims — grep for them).

**Phase 2 acceptance:** all `test_C*` tests pass; CI matrix green on 3.9-3.12.

---

## 4. Phase 3 — Testing & CI (the weakest sector)

### T01 — Coverage is 46% and ungated  `HIGH` [VERIFIED]
Per-module (pytest-cov, excluding the browser test): `settings.py` 14%, `speed_service.py` 17%, `speed.py` 19%, `notifier.py` 22%,
`blueprints/storage.py` 23%, `cli.py` 26%, `collector.py` 29%, `storage_service.py` 29%, `runtime_service.py` 30%, `network.py` 36%.
- **Fix:** write tests until total >= **80%** (stretch 85%) and **no security-relevant module below 70%** (`settings.py`, `storage.py` blueprint, `storage_service.py`, `common.py`, `web.py`, `backup_service.py`, `terminal.py`). Add `--cov-fail-under=80` to CI. Focus areas, in order: every route in `settings.py` (auth, notifications, backup, restart, logs truncate), every route in `storage.py` (browse/upload/chunk/trash/restore/preview/ops), `notifier.py` (mock `urlopen`), `speed_service.py` (mock subprocess), `collector.py` alert/cool-down state machine, `cli.py` commands.

### T02 — Tests bypass CSRF and real auth  `HIGH` [VERIFIED]
`web.py:58` returns early when `TESTING`. Add a fixture `strict_client` that sets `CSRF_CHECK_IN_TESTS=True` and use it for all state-changing route tests (see S07). Add a "public client" fixture (non-private IP + `CF-Connecting-IP`) and test that **every** route in `app.url_map` is either on an explicit public allow-list or returns 404/403 to it. Generate this test from `app.url_map` so new routes are covered automatically.

### T03 — CI gaps  `MEDIUM` [VERIFIED]
`.github/workflows/ci.yml` runs ruff + pytest + the Playwright script. Add jobs: `mypy systor` (after fixing the 33 errors, see Q02), `bandit -r systor -ll`, `pip-audit -r requirements.txt`, a **Docker build + Trivy scan on pull requests** (HIGH/CRITICAL fail), and a job that runs `python tools/design_audit.py` (already present — keep it; it must exit 0 after D01). Make `tests/test_webapp_playwright.py` use `pytest.importorskip("playwright")` so a plain `pytest` never errors at collection.

### T04 — Test hygiene  `LOW`
`PRODUCT.md` claims "157 total tests" and "51 Playwright tests": reality is **106 pytest tests + 1 pytest function wrapping 52 browser checks**. Update the numbers or split the browser checks into real pytest parametrised tests so failures are individually reported.

**Phase 3 acceptance:** `pytest --cov=systor --cov-fail-under=80` passes in CI on all four Python versions; CI shows lint, types, bandit, audit, docker-scan, e2e all green.

---

## 5. Phase 4 — Design, UX, accessibility (polish to 10)

### D01 — Paper Light theme: 87 contrast failures  `HIGH` [VERIFIED]
The project's own audit shows Warm Dark **0**, Classic Dark **0**, Paper Light **87 failures / 1342 text nodes (6.5%)**. There is exactly one root cause:
- `style.css:612` `.kind-pill.host { color:#58a6ff; background:rgba(88,166,255,.08) }` -> `#58a6ff` on `#f9f9f7` = **2.36-2.40:1** (needs 4.5:1). Seen on `/apps` and `/network` (the "host" pill, rendered 32 times).
- `style.css:613` `.kind-pill.docker { color:#22d3ee ... }` is the same pattern (cyan on near-white; it only escapes the audit when no Docker rows were present in the seed data). Fix both.
- **Fix:** move pill colours to theme tokens:
  ```css
  :root                { --pill-host-fg:#58a6ff; --pill-docker-fg:#22d3ee; }
  [data-theme="light"] { --pill-host-fg:#1457b8; --pill-docker-fg:#0b6b7d; }
  .kind-pill.host   { color:var(--pill-host-fg); }
  .kind-pill.docker { color:var(--pill-docker-fg); }
  ```
  (both light values are >= 5:1 on `#f9f9f7`; re-measure with the audit). Also replace the hard-coded `border:1px solid #2f3845` at `:611` with `var(--border)`.
- **Acceptance:** `python tools/design_audit.py` prints `GATE: PASS` (0 failures in all 3 themes). Seed with `--seed` and also test with a Docker container present.

### D02 — Text below 11 px  `MEDIUM` [VERIFIED]
Audit reports `minFont: 9.5`. Source: `style.css:643` `.app-full-name .kind-pill { font-size:9.5px }`. Set to `11px` and let the pill grow (`padding:2px 6px` is fine). Add a CSS lint step (`grep -nE "font-size: *(8|9|10)(\.[0-9]+)?px"` must return nothing) and make the audit fail if `minFont < 11`.

### D03 — Hard-coded colours outside tokens  `MEDIUM` [VERIFIED]
Hex literals remain in templates: `base.html` 11, `settings.html` 19, `network.html` 5, `speed.html` 1, plus inline `style="..."` attributes on many elements (`network.html` 24, `settings.html` 20, `apps.html` 16, `base.html` 20). They pass today, but they are the reason Light theme broke before and will again. Move every colour/spacing into classes using tokens; ban `#rrggbb` in templates with a test (`tests/test_design_tokens.py` greps templates).

### D04 — Chart colour reads are slow and wasteful  `LOW` [VERIFIED]
`static/js/app.js:2-20` defines `COLORS` as getters that call `getComputedStyle` on **every access**; charts redraw every poll. Cache the resolved palette once per theme change (the `systor:theme-change` event already exists, but `updateChartColors()` at `:22` is an **empty function**) and read from the cache while drawing.

### D05 — ~150 KB of inline JavaScript in templates  `HIGH` [VERIFIED]
Inline script bytes: dashboard 29 KB, network 29 KB, settings 29 KB, storage 23 KB (with ~20 single lines over 400 characters, e.g. `storage.html:207-221`), speed 23 KB, apps 17 KB, base 11 KB. Consequences: no CSP (S03), no linting, no tests, unreadable diffs, copy-pasted helpers.
- **Fix:** extract to `static/js/pages/<page>.js` + shared `static/js/lib/{api,dom,format,charts,dialogs}.js` (ES modules are fine with `<script type="module">`, still no build step). One `api()` wrapper handles CSRF header, JSON errors, 401 redirect, and toasts. Replace inline `onclick=` (apps 3, network 1, settings 3) with `addEventListener`. Pretty-print the long one-liners. Keep behaviour identical; the Playwright suite is your safety net.
- **Acceptance:** no `<script>` without `src` in any template except none; CSP `script-src 'self'` works (S03).

### D06 — Replace `alert()/prompt()/confirm()`  `MEDIUM` [VERIFIED]
`storage.html` uses `alert`, `prompt` (including for the **storage action password**, shown in clear text) and `confirm`. Build one accessible `<dialog>` helper (`dialog.js` already has focus-trap logic) with: labelled inputs, `type="password"` for passwords, inline errors, toasts for success. This also fixes focus handling for screen readers.

### D07 — Remaining UX polish (do after D01-D06)
- **Loading / empty / error states** for every table and chart (skeleton rows, "No data yet — collector started 2 min ago", retry button on fetch error). Today failures are mostly silent.
- **Stale-data indicator** in the header: a pill "Live / Delayed 12 s / Collector offline" driven by `/api/snapshot` timestamp.
- **Keyboard shortcuts:** `g d` dashboard, `g n` network, `/` focus search, `t` theme; list in a `?` help dialog.
- **Mobile:** collapse the nav into a bottom tab bar under 600 px (touch targets already >= 40 px, keep that).
- **Chart accessibility:** each canvas gets a visually-hidden data table or `aria-describedby` summary ("CPU averaged 23%, peak 71% at 14:05"); add a pattern/dash option so colour is not the only series cue.
- **Logo:** `static/img/systor-logo.png` is **2 MB**; it is not used by templates (the header uses a 7 KB version). Delete it from the package (and from `package-data`) or convert to a < 60 KB WebP/SVG; it bloats the wheel and Docker image.
- **Print / export:** CSV/PNG export button on charts.
- **i18n-ready strings:** move user-visible text into one JS object (no library needed).

### Visual review findings (screenshots reviewed by eye, Chromium, 1440 px and 390 px, Warm Dark + Paper Light)

The automated audit hides text to measure contrast, so it cannot see layout or "does it look right" problems. These were found by looking at real screenshots with sample data. Re-capture the same pages after each fix and compare.

**What already looks good:** consistent card system, clear type hierarchy, tabular numbers, good dark-theme colour balance, tidy tables, sensible sticky header, sensible chart headers (latest / avg / peak).

### D08 — Pager buttons on `/apps` render as blank squares  `HIGH` [VERIFIED visually]
- **Where:** the "1/7" pager next to "Top CPU/RAM/Disk/Net Apps" in `apps.html`. Both buttons show as an empty grey square and an empty near-black square in Paper Light (no arrow glyph, no text, no `aria-label` visible).
- **Fix:** give them real content (inline SVG chevrons using `currentColor`), `aria-label="Previous page"/"Next page"`, disabled state styling, and 40 px touch size. Check the dark themes too.

### D09 — Usage bars look wrong in Paper Light  `HIGH` [VERIFIED visually]
- **Where:** the CPU/RAM/DSK/NET usage bars on `/apps` (and similar tracks on other pages). In Paper Light the bar **track is solid near-black navy** with a tiny coloured fill, so on a light page they look like heavy black bars and the fill is hard to read.
- **Fix:** add `--bar-track` and `--bar-fill-*` tokens; in light use a light neutral track (e.g. `#e6e6e1`) with a visible fill; keep dark values for dark themes. Grep for the hard-coded track colour in `style.css` and replace it with the token.

### D10 — Mobile top navigation is clipped  `HIGH` [VERIFIED visually]
- **Where:** `base.html` nav + `style.css` mobile rules at 390 px. Only 4-5 icon-only tabs are visible, the 5th is cut in half, the rest (Alerts, Logs, Settings) are not reachable without discovering a hidden scroll; the "Console" button wraps to two lines (`>_` / `Console`) and the theme button is squeezed.
- **Fix:** below 600 px use a fixed bottom tab bar (Dashboard, Apps, Network, Speed, More) with a "More" sheet containing Storage/Alerts/Logs/Settings/Console/Theme; icons need visible text labels or `aria-label` + tooltip. `Console` becomes an icon button on mobile. Verify 390, 360 and 320 px widths.

### D11 — Settings sticky toolbar overlaps the first content row  `MEDIUM` [VERIFIED visually]
- **Where:** `/settings` — on first paint the sticky toolbar (saved / status pills / General-Notifications-Auth-Access tabs / Save buttons) covers the top of the "Appearance & Theme" and "Alert thresholds" card headings, which are half-hidden.
- **Fix:** add `scroll-margin-top` / top padding equal to the toolbar height (use a CSS variable measured in JS or fixed token), and make sure anchors and the first card start below it.

### D12 — Storage page shows contradictory and empty status  `MEDIUM` [VERIFIED visually]
- **Where:** `/storage` header says "**actions enabled**" while the Access card says "**Read-only**"; "Mount used" and "Free" show "—" while the Disks table beside them shows "3.2% / 9.80 GB free"; "Current folder: 0 B, 0 dirs · 0 files" while the list shows a file and 12+ folders; the "Type Usage" panel is empty with no explanation.
- **Fix:** derive all four cards from the same payload as the lists; if a value is unknown show a reason ("not scanned — run Disk Analysis") not "—". Make the access wording agree: if the browsed root is read-only, header must say "read-only". Add an empty-state with a call to action to every empty panel. Add a test that cards equal the list totals.
- **Related, [VERIFIED by test]:** browsing `/` is read-only and lists only top-level names; reading/previewing `/etc/passwd` and browsing `/etc`, `/root`, `/home` correctly return 403/400 on a LAN client with no roots configured. Keep it that way and add these as regression tests.

### D13 — Dashboard first-load inconsistencies  `MEDIUM` [VERIFIED visually]
- The header chip shows "CPU 100 %" (or a different number) while the CPU Usage card shows "— %" and "now" in the same screenshot; one of the two data paths is stale or defaults badly on the first poll. Show a skeleton until the first valid sample, and read both from the same snapshot.
- Header chips "CPU W — · C —" and "RAM W — · C —" are cryptic (warn/crit thresholds?) and clipped. Replace with a single clear status chip per metric or add a tooltip and `aria-label`, and never show `—` where a value exists.
- Card titles are truncated with ellipsis ("CPU ...", "MEM...") even at 1440 px; allow two-line labels or shorten to "Load" and "Memory".
- Charts have **no x-axis time labels** — nothing tells the user what time range they are looking at except the dropdown. Add 4-6 time ticks (HH:MM, or day for long ranges) and a hover tooltip with exact time and value.
- Y-axis ticks use odd values (66.67 / 33.33, 5333 / 2667) and unnecessary decimals. Use "nice" ticks (0/25/50/75/100, 0/2k/4k/6k/8k) and units on the axis.
- Flat network/disk lines in the seeded test data are fine, but add an explicit "no traffic" empty state rather than a perfectly flat line when all values are equal/zero.

### D14 — Settings form alignment and leftover copy  `LOW` [VERIFIED visually]
- In "Speed defaults" the checkboxes ("Auto speed test", "Notify on low speed") are grey squares that look different from the teal checkboxes in the thresholds table, and their labels wrap awkwardly. Use one shared checkbox/switch component and a consistent label/control grid.
- Theme cards carry development-era badges: "New Design", "Previous Dark", "Day Mode", plus "ExamForge ..." wording in the description (this is copied from another project). Remove them; describe each theme in one neutral sentence.
- Form rows are not on a common grid (inputs have different widths). Define `.form-row` with a fixed label column.

### D15 — Light theme chart lines are faint  `LOW` [VERIFIED visually]
Cyan and yellow lines on near-white are visible but weak, and axis labels are small light grey. Use the darker light-theme chart tokens already proposed in D01 and raise axis label size/contrast, then re-check with the audit's chart legibility measurement (the earlier run reported chart ink of only 0.004 in unseeded mode — always audit with `--seed`).

### D16 — Theme control state  `LOW` [BY INSPECTION]
The header button always read "Warm Dark" in my light-theme screenshots because the label is not refreshed when the theme changes outside the button. Update the label (and `aria-pressed`/`aria-label`) on the `systor:theme-change` event, and verify persistence with the key `systor_theme`.

**Visual acceptance:** a Playwright script captures every page in 3 themes x 3 viewports (27 images) into `docs/screens/` with text visible; the agent opens each one, lists any visible defect, fixes it, and re-captures. Add pixel-diff regression tests (`pytest-playwright` snapshot or Pillow diff with a small tolerance) so these polish bugs cannot come back.

---

## 6. Phase 5 — Architecture & code quality

### Q01 — Blueprints still talk to storage/SQL  `MEDIUM` [VERIFIED]
31 direct `get_storage()` / `.execute(` / `._conn()` calls in `systor/blueprints/*.py`, contradicting the stated rule `web -> blueprints -> services -> storage`. Move each into the matching service module; blueprints may only validate input, call a service, and format JSON.

### Q02 — Typing and error handling  `MEDIUM` [VERIFIED]
- 33 mypy errors in 14 files (e.g. `settings.py:48`, `cli.py:23`). Fix, then add `mypy systor` to CI.
- 67 `except ...: pass` and 12 `except ...: continue` blocks swallow failures silently. Replace with `log.debug(..., exc_info=True)` at minimum; narrow exception types (`OSError`, `ValueError`, `sqlite3.Error`). Never swallow in security or backup code.
- `ruff` in CI uses default rules only. Add a real rule set to `pyproject.toml`: `select = ["E","F","W","I","B","UP","S","BLE","SIM"]` with justified per-file ignores for `tests/` and `tools/`.

### Q03 — Worker threads started inside `create_app()`  `MEDIUM` [VERIFIED]
`web.py:172-175` starts `start_apps_cache_worker()` and `start_uptime_worker()` whenever the app is created. A multi-worker WSGI deployment (gunicorn `-w 4`) would run N copies. Move workers into the collector process (or behind an explicit `systor serve` flag) and make them idempotent via a lock file.

### Q04 — Split the monsters  `LOW`
`settings.py` (720 lines), `storage.py` blueprint (535), `storage.py` core (542), `metrics.py` (516) mix concerns. Split settings into `settings_auth`, `settings_notify`, `settings_backup`, `settings_system`. Do this **after** T01 so tests protect the refactor.

### Q05 — Config
Add schema validation for `config.yaml` (hand-rolled, no new dependency): unknown keys -> warning, wrong types -> clear error naming the key. Add `systor config check`. Document every key in `docs/CONFIG.md` and add a test that fails when a key exists in `DEFAULT_CONFIG` but not in that doc.

---

## 7. Phase 6 — Docs, packaging, deployment

- **R01 README contradicts the hardened compose** [VERIFIED]: `README.md:~127-128` `docker run` mounts `/var/run/docker.sock` and `/dev:/dev:ro` and omits the `read_only`, `cap_drop`, `user` options that `docker-compose.yml` uses. Make the `docker run` block equivalent to the compose file; describe docker-socket-proxy as the opt-in way to get container stats; explain what features degrade without `/dev` (SMART).
- **R02 Collapse the three plan files** [VERIFIED]: `systor-ten-on-ten-plan.md` (marked superseded), `SYSTOR-10x10-PLAN.md` (partly done, partly stale: it says 0 `<h2>`, `docker.sock` present, CSRF missing — all now fixed), `systor-expansion-plan.md`. Replace all three with this file's open items in `ROADMAP.md`; move history to `docs/history/`.
- **R03 Versioning:** single source of truth. `pyproject.toml` (1.1.0), `systor/__init__.py` (1.1.0), `sw.js` (1.1.1) must come from one place; add a test asserting they match; add a release checklist.
- **R04 Dockerfile:** pin the base image by digest; add `org.opencontainers.image.version` / `revision` labels from build args; add `docker-publish.yml` steps for PR build (no push), SBOM, provenance, Trivy, and tag with the commit SHA as well as semver.
- **R05 Installer:** `install.sh` is `curl | sudo bash`. Add checksum/signature verification for release tarballs, `--dry-run`, and print exactly what it will change. Same review for `uninstall.sh`.
- **R06 Add a real upgrade path:** DB schema version table + ordered migrations (`storage.py` currently does ad-hoc `ALTER`s on connect); test upgrading a v1.0 DB fixture.
- **R07 Security docs:** extend `SECURITY.md` with the threat model (who is "private", what the terminal can do, how to expose via tunnel safely), supported versions, and a disclosure contact.

---

## 8. Features worth adding (after everything above is green)

Priority order, each is optional and must keep the zero-dependency invariant:

1. **Setup wizard + `systor doctor`** — checks permissions, collector freshness, DB size, missing tools (smartctl, speedtest, docker), prints fixes. Biggest onboarding win.
2. **Alert rules UI** — per-metric thresholds with duration, severity, quiet hours, test button, and **alert acknowledgement/snooze**; today thresholds live in config.
3. **More notification channels** — email (SMTP), ntfy, Gotify, Pushover, generic webhook with templating.
4. **Retention controls & DB health page** — size, row counts, VACUUM/ANALYZE button, downsampling policy (raw 7 d -> 1-min 90 d -> 1-h forever).
5. **Multi-host view (lightweight)** — optional "agent" mode pushing snapshots to one hub; list of hosts with status. Keep single-host as the default.
6. **Per-container history** — persist container CPU/RAM/net series (not only live), top-N over time.
7. **Process actions** — kill/renice with confirm and audit trail (admin only).
8. **Disk forecasting** — linear/seasonal "disk full in N days" using the existing history; show on dashboard and alert on it.
9. **Audit log page** — every admin action (login, restart, delete, restore, terminal command) with IP and user; export CSV.
10. **2FA (TOTP)** for the admin account (pure-stdlib TOTP is ~40 lines).
11. **API tokens** with scopes for Prometheus/Home Assistant instead of the single `metrics.token`; publish an OpenAPI document at `/api/openapi.json`.
12. **Backups on a schedule** with rotation and an off-box destination (local path / SFTP via system `scp`).
13. **Status page mode** — a trimmed public page with uptime bars for uptime targets (the data already exists).
14. **Temperature/fan/power sensors page** with per-sensor history and thresholds.
15. **Light/dark auto + high-contrast theme + reduced-data mode** for slow links.

---

## 9. Definition of Done — commands the agent must run (all must pass)

```bash
# 1. unit/integration tests + coverage gate
pip install -e ".[dev]" bandit mypy pip-audit
pytest -q --ignore=tests/test_webapp_playwright.py --cov=systor --cov-fail-under=80

# 2. static analysis
ruff check .
mypy systor
bandit -r systor -ll              # 0 HIGH; every MEDIUM fixed or justified with a test
pip-audit -r requirements.txt

# 3. browser, design and accessibility gate
playwright install --with-deps chromium
python tools/design_audit.py      # must print: GATE: PASS  (0 contrast failures in all 3 themes, minFont >= 11)
python tests/test_webapp_playwright.py

# 4. Python version matrix (CI): 3.9 3.10 3.11 3.12 3.13 all green
python3.9 -c "import systor.config, systor.web, systor.cli"

# 5. image
docker build -t systor:test . && trivy image --exit-code 1 --severity HIGH,CRITICAL systor:test

# 6. regression greps (each must print nothing)
grep -rn "secure-fallback" systor/
grep -rnE "font-size: *(8|9|10)(\.[0-9]+)?px" systor/static/css/
grep -rnE "#[0-9a-fA-F]{6}" systor/templates/
grep -rn "unsafe-inline" systor/web.py
```

**Final acceptance checklist**
- [ ] S01-S08 closed, each with a regression test
- [ ] C01-C05 closed, each with a regression test; C06 items confirmed or marked NOT REPRODUCED
- [ ] Coverage >= 80% in CI, gate enforced; CSRF and public-client route tests generated from `app.url_map`
- [ ] Design audit `GATE: PASS` in all three themes; no text < 11 px; no inline JS; CSP without `unsafe-inline`
- [ ] D08-D16 visual defects closed; before/after screenshots committed for all 9 pages x 3 themes x 3 viewports and reviewed by eye
- [ ] README, compose, Dockerfile and docs agree with each other; versions come from one source
- [ ] CHANGELOG has a `1.2.0` entry listing every change above, with **no claim that is not true in the code**

---

## 10. Out of scope / not verified by this audit

- I could not run the app on real hardware, a real Docker host, systemd, GPU/UPS/Raspberry Pi sensors, or Python 3.9, so findings C02 (3.9) and the Docker-specific part of C01 are by inspection plus a reproduced mechanism.
- I did not test the Telegram bot, Discord webhook, or Ookla speedtest against live services.
- I did not run a penetration test; Security findings come from code reading and tooling.
