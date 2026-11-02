"""Systor design & accessibility audit.

Boots the real Flask app in-process, drives it with Chromium via Playwright, and
measures the things a screenshot cannot prove:

  * pixel-accurate WCAG AA contrast (text is hidden, one full-page screenshot is
    taken, and the real background pixel behind every text box is sampled)
  * horizontal overflow per viewport
  * console errors and failed requests
  * heading structure, form-label coverage, touch-target size
  * canvas/chart legibility
  * dialog focus management

Usage
-----
    python tools/design_audit.py                 # audit, print report, exit 1 on failure
    python tools/design_audit.py --shots out/    # also write screenshots
    python tools/design_audit.py --seed          # insert 6h of synthetic metrics first
    python tools/design_audit.py --json out.json

Requires: playwright (with a chromium browser) and Pillow.
Install with:  pip install -r requirements-dev.txt && playwright install chromium
If Playwright is missing the script prints a clear message and exits 0, so it never
breaks a plain `pytest`/CI run.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import socket
import sys
import threading
import time
from io import BytesIO
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAGES = ["/", "/apps", "/network", "/speed", "/storage", "/alerts", "/logs", "/settings", "/login"]
THEMES = ["warm-dark", "light", "classic-dark"]
VIEWPORTS = [("desktop", 1440, 900), ("tablet", 768, 1024), ("mobile", 390, 844)]
HOST, PORT = "127.0.0.1", 6677
BASE = f"http://{HOST}:{PORT}"

# Thresholds. WCAG 2.2 AA: 4.5:1 normal text, 3:1 large text, 3:1 non-text (charts).
TEXT_NORMAL, TEXT_LARGE, NON_TEXT = 4.5, 3.0, 3.0

JS_COLLECT = r"""
() => {
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  let n;
  while ((n = walker.nextNode())) {
    const txt = (n.nodeValue || '').trim();
    if (!txt || txt.length < 2) continue;
    const el = n.parentElement;
    if (!el || seen.has(el)) continue;
    seen.add(el);
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    if (el.closest('.sr-only')) continue;
    const idx = out.length;
    el.setAttribute('data-audit-idx', String(idx));
    out.push({
      text: txt.slice(0, 46),
      sel: el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className.trim()
        ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''),
      color: cs.color,
      size: Math.round(parseFloat(cs.fontSize) * 10) / 10,
      weight: parseInt(cs.fontWeight, 10) || 400,
      x: r.left, y: r.top, w: r.width, h: r.height
    });
  }
  return out;
}
"""

JS_HIDE = """() => {
  document.querySelectorAll('[data-audit-idx]').forEach(el => {
    el.style.setProperty('color', 'transparent', 'important');
  });
}"""

JS_SHOW = """() => {
  document.querySelectorAll('[data-audit-idx]').forEach(el => { el.style.removeProperty('color'); });
}"""

JS_STRUCTURE = r"""
() => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const inputs = q('input, select, textarea').filter(e => e.type !== 'hidden');
  const unlabeled = inputs.filter(e => {
    if (e.getAttribute('aria-label') || e.getAttribute('aria-labelledby')) return false;
    if (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)) return false;
    if (e.closest('label')) return false;
    return true;
  }).map(e => e.tagName.toLowerCase() + (e.name ? `[name=${e.name}]` : '') + (e.id ? `#${e.id}` : ''));
  const small = q('a, button, input, select, [role="button"]').filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && (r.height < 24 || r.width < 24);
  });
  const smallSamples = small.slice(0, 12).map(e => {
    const r = e.getBoundingClientRect();
    return e.tagName.toLowerCase()
      + (typeof e.className === 'string' && e.className.trim()
         ? '.' + e.className.trim().split(/\s+/)[0] : '')
      + ' ' + Math.round(r.width) + 'x' + Math.round(r.height);
  });
  return {
    h1: q('h1').length, h2: q('h2').length,
    main: !!document.querySelector('#main-content'),
    skipLink: !!document.querySelector('a.sr-only[href^="#"]'),
    ariaCurrent: q('[aria-current="page"]').length,
    inputs: inputs.length, unlabeled: unlabeled,
    imgsNoAlt: q('img').filter(i => !i.hasAttribute('alt')).length,
    btnsNoName: q('button, a').filter(b => !(b.textContent || '').trim()
      && !b.getAttribute('aria-label') && !b.getAttribute('title')).length,
    smallTargets: small.length,
    minFont: (() => {
      let m = Infinity;
      q('body *').forEach(e => {
        if (e.children.length === 0 && (e.textContent || '').trim()) {
          const n = parseFloat(getComputedStyle(e).fontSize);
          if (!isNaN(n) && n < m) m = n;
        }
      });
      return isFinite(m) ? Math.round(m * 10) / 10 : null;
    })(),
    smallTargetSamples: smallSamples
  };
}
"""

JS_CHART = r"""
() => Array.from(document.querySelectorAll('canvas')).map(c => {
  const r = c.getBoundingClientRect();
  if (r.width < 5 || r.height < 5) return {empty: true, w: Math.round(r.width)};
  const ctx = c.getContext('2d');
  if (!ctx) return {error: 'noctx'};
  const d = ctx.getImageData(0, 0, c.width, c.height).data;
  const m = new Map();
  for (let i = 0; i < d.length; i += 28) {
    const k = d[i] + ',' + d[i+1] + ',' + d[i+2];
    m.set(k, (m.get(k) || 0) + 1);
  }
  const s = [...m.entries()].sort((a, b) => b[1] - a[1]);
  const bg = s.length ? s[0][0] : null;
  let ink = 0;
  for (let i = 0; i < d.length; i += 28) {
    if (d[i] + ',' + d[i+1] + ',' + d[i+2] !== bg) ink++;
  }
  return {w: Math.round(r.width), h: Math.round(r.height), distinct: s.length,
          ink: Math.round(ink / (d.length / 28) * 1000) / 1000,
          dominant: s.slice(0, 4).map(([k, v]) => k + ':' + v)};
})
"""

# --------------------------------------------------------------------------- helpers
def _lum(c):
    def f(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def _ratio(a, b):
    l1, l2 = _lum(a), _lum(b)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def _parse_rgb(s):
    try:
        p = [float(x) for x in s[s.index("(") + 1:s.index(")")].split(",")]
        return (p[0], p[1], p[2])
    except Exception:
        return None


def seed_metrics():
    """Insert 6h of synthetic samples so charts actually draw (no /proc on Windows)."""
    from systor.storage import Storage
    s = Storage()
    random.seed(7)
    now = int(time.time())
    rows, ifaces = [], []
    for i in range(4320):
        ts = now - (4320 - i) * 5
        t = i / 4320.0
        load = round(0.4 + 1.6 * abs(math.sin(t * 9)) + random.uniform(0, 0.3), 2)
        cpu = round(min(99.0, 8 + 55 * abs(math.sin(t * 7)) + random.uniform(0, 6)), 1)
        temp = round(42 + 22 * abs(math.sin(t * 5)) + random.uniform(0, 2), 1)
        total, used = 15884, int(3200 + 2600 * abs(math.sin(t * 3)) + random.uniform(0, 200))
        rows.append((ts, load, round(load * .9, 2), round(load * .8, 2), cpu, temp, total, used,
                     total - used, total - used - 900, int(180 + 900 * abs(math.sin(t * 4))), 2048,
                     round(28 + 40 * abs(math.sin(t * 6)), 1), round(180 + 60 * math.sin(t * 6), 1),
                     512.0, "/", round(random.uniform(0, 40), 2), round(random.uniform(0, 25), 2),
                     int(3.2e9 + t * 5e8), int(1.1e9 + t * 2e8), "systor-host"))
        ifaces.append((ts, "eth0", int(2.4e9 + i * 90000), int(8.0e8 + i * 31000)))
        ifaces.append((ts, "wlan0", int(6.0e8 + i * 22000), int(1.4e8 + i * 7000)))
    con = s._conn()
    with s._lock, con as c:
        for t in ("samples", "iface_samples", "alerts", "speedtests", "uptime_checks", "uptime_targets"):
            c.execute("DELETE FROM " + t)
        c.executemany(
            "INSERT INTO samples (ts, load_1m, load_5m, load_15m, cpu_pct, cpu_temp, mem_total_mb,"
            " mem_used_mb, mem_free_mb, mem_avail_mb, swap_used_mb, swap_total_mb, disk_used_pct,"
            " disk_used_gb, disk_size_gb, disk_mount, disk_read_mbps, disk_write_mbps, net_rx_bytes,"
            " net_tx_bytes, hostname) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        c.executemany("INSERT INTO iface_samples (ts, iface, rx_bytes, tx_bytes) VALUES (?,?,?,?)", ifaces)
        c.execute("INSERT INTO alerts (ts, metric, severity, value, threshold, message)"
                  " VALUES (?, 'cpu_load_1m', 'alert', 4.8, 4.0, 'Cpu Load 1M: 4.8 > 4.0')", (now - 900,))
    print("[audit] seeded %d samples" % s.stats()["samples"], file=sys.stderr)


def measure(page, shots_dir=None, tag=""):
    """Return contrast rows for the current page; optionally save a screenshot."""
    from PIL import Image
    els = page.evaluate(JS_COLLECT)
    page.evaluate(JS_HIDE)
    png = page.screenshot(full_page=True, type="png")
    page.evaluate(JS_SHOW)
    img = Image.open(BytesIO(png)).convert("RGB")
    W, H = img.size
    px = img.load()
    sy = page.evaluate("() => window.scrollY")
    for e in els:
        cx = max(0, min(W - 1, int(e["x"] + e["w"] / 2)))
        cy = max(0, min(H - 1, int(e["y"] + e["h"] / 2 + sy)))
        counts = {}
        for dx in range(-4, 5, 2):
            for dy in range(-4, 5, 2):
                x, y = cx + dx, cy + dy
                if 0 <= x < W and 0 <= y < H:
                    c = px[x, y]
                    counts[c] = counts.get(c, 0) + 1
        bg = max(counts, key=counts.get) if counts else (255, 255, 255)
        fg = _parse_rgb(e["color"])
        if not fg:
            e["ratio"] = None
            continue
        e["bg"] = "rgb" + str(tuple(bg))
        e["ratio"] = round(_ratio(fg, bg), 2)
        e["large"] = e["size"] >= 24 or (e["size"] >= 18.66 and e["weight"] >= 700)
        e["need"] = TEXT_LARGE if e["large"] else TEXT_NORMAL
    if shots_dir is not None:
        shots_dir.mkdir(parents=True, exist_ok=True)
        img.save(shots_dir / ((tag or "page") + ".png"))
    return els


def main():
    ap = argparse.ArgumentParser(description="Systor design & accessibility audit")
    ap.add_argument("--seed", action="store_true", help="insert 6h of synthetic metrics first")
    ap.add_argument("--shots", metavar="DIR", help="write screenshots here")
    ap.add_argument("--json", metavar="FILE", help="write the full JSON report here")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright not installed. Run: pip install -r requirements-dev.txt "
              "&& playwright install chromium", file=sys.stderr)
        return 0
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("SKIP: Pillow not installed. Run: pip install -r requirements-dev.txt", file=sys.stderr)
        return 0

    if args.seed:
        seed_metrics()

    from werkzeug.serving import make_server
    from systor.web import create_app
    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    app.config["AUDIT_MODE"] = True
    srv = make_server(HOST, args.port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(80):
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex((HOST, args.port)) == 0:
                break
        time.sleep(0.1)
    base = "http://%s:%d" % (HOST, args.port)
    shots = Path(args.shots) if args.shots else None

    report = {"contrast": {}, "pages": {}, "charts": {}, "focus": {}}
    total_fail = total_nodes = 0
    overflow_hits = console_hits = 0
    struct_totals = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for vname, w, h in VIEWPORTS:
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            for path in PAGES:
                errs, reqfail = [], []
                page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errs.append("PAGEERROR: %s" % e))
                page.on("requestfailed", lambda r: reqfail.append(r.url))
                try:
                    page.goto(base + path, wait_until="networkidle", timeout=25000)
                    page.wait_for_timeout(500)
                except Exception as e:
                    report["pages"]["%s:%s" % (vname, path)] = {"error": str(e)[:200]}
                    continue
                ov = page.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                st = page.evaluate(JS_STRUCTURE)
                report["pages"]["%s:%s" % (vname, path)] = {
                    "overflow": ov, "console_errors": errs[:6], "request_failures": reqfail[:4],
                    "structure": st, "charts": page.evaluate(JS_CHART)}
                overflow_hits += 1 if ov > 0 else 0
                console_hits += 1 if (errs or reqfail) else 0
                # minFont / smallTargetSamples are per-page extremes, not additive
                for k in ("h1", "h2", "main", "skipLink", "ariaCurrent",
                          "inputs", "imgsNoAlt", "btnsNoName", "smallTargets"):
                    if isinstance(st.get(k), int):
                        struct_totals[k] = struct_totals.get(k, 0) + st[k]
                if isinstance(st.get("minFont"), (int, float)):
                    struct_totals["minFont"] = min(
                        struct_totals.get("minFont", 1e9), st["minFont"])
                struct_totals["smallTargetSamples"] = (
                    struct_totals.get("smallTargetSamples", [])
                    + (st.get("smallTargetSamples") or []))[:12]
            ctx.close()

        for theme in THEMES:
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.add_init_script(
                "try { localStorage.setItem('systor_theme', '%s'); } catch(e){}" % theme)
            for path in PAGES:
                key = "%s:%s" % (theme, path)
                try:
                    page.goto(base + path, wait_until="networkidle", timeout=25000)
                    page.wait_for_timeout(800)
                    applied = page.evaluate(
                        "() => document.documentElement.getAttribute('data-theme')")
                    els = measure(page, shots, "%s__%s" % (theme, path.strip("/") or "root"))
                    charts = page.evaluate(JS_CHART)
                except Exception as e:
                    report["contrast"][key] = {"error": str(e)[:200]}
                    continue
                fails = [e for e in els if e["ratio"] is not None and e["ratio"] < e["need"]]
                report["contrast"][key] = {
                    "theme_applied": applied, "checked": len(els), "failures": len(fails),
                    "failing": [{"r": e["ratio"], "sel": e["sel"], "sz": e["size"],
                                 "t": e["text"], "fg": e["color"], "bg": e.get("bg")}
                                for e in sorted(fails, key=lambda x: x["ratio"])[:20]]}
                report["charts"][key] = charts
                total_fail += len(fails)
                total_nodes += len(els)
            ctx.close()

        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_timeout(700)
        fr = {}
        if page.query_selector("#terminal-drawer-toggle"):
            page.click("#terminal-drawer-toggle")
            page.wait_for_timeout(500)
            fr["terminal"] = {
                "role": page.evaluate(
                    "() => document.getElementById('terminal-drawer').getAttribute('role')"),
                "ariaModal": page.evaluate(
                    "() => document.getElementById('terminal-drawer').getAttribute('aria-modal')"),
                "focusInside": page.evaluate(
                    "() => document.getElementById('terminal-drawer').contains(document.activeElement)"),
            }
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            fr["terminal"]["escCloses"] = page.evaluate(
                "() => getComputedStyle(document.getElementById('terminal-drawer')).display === 'none'")
            fr["terminal"]["focusRestored"] = page.evaluate(
                "() => document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null")
        report["focus"] = fr
        ctx.close()
        browser.close()

    srv.shutdown()
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("=" * 74)
    print("SYSTOR DESIGN AUDIT")
    print("=" * 74)
    for theme in THEMES:
        c = f = 0
        for k, v in report["contrast"].items():
            if k.startswith(theme + ":"):
                c += v.get("checked", 0)
                f += v.get("failures", 0)
        pct = round(100.0 * f / c, 1) if c else 0
        print("  contrast %-13s %4d failures / %4d nodes (%5.1f%%)  [%s]"
              % (theme, f, c, pct, "PASS" if f == 0 else "FAIL"))
    n = len(VIEWPORTS) * len(PAGES)
    print("  overflow  %d/%d page-viewport combos" % (overflow_hits, n))
    print("  console   %d/%d page-viewport combos with errors" % (console_hits, n))
    print("  structure %s" % struct_totals)
    print("  focus     %s" % json.dumps(report["focus"]))
    print("=" * 74)

    worst = []
    for k, v in report["contrast"].items():
        for e in v.get("failing", [])[:5]:
            worst.append((e["r"], k, e["sel"], e["sz"], e["t"], e["fg"], e.get("bg")))
    worst.sort()
    if worst:
        print("\nWORST 25 CONTRAST FAILURES")
        for r, k, sel, sz, t, fg, bg in worst[:25]:
            print("  %5.2f  %-24s %-30s %4.1fpx  %-22s on %-20s %r"
                  % (r, k, sel[:30], sz, fg, bg, t[:24]))
    ok = (total_fail == 0 and overflow_hits == 0 and console_hits == 0)
    print("\nGATE: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

