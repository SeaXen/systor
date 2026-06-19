from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request
from urllib.parse import urlparse


def _cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    env = dict(subprocess.os.environ)
    env.setdefault('PATH', '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin')
    env.setdefault('LANG', 'C.UTF-8')
    env.setdefault('LC_ALL', 'C.UTF-8')
    env.setdefault('HOME', '/root')
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def _safe_float(v):
    try:
        return float(v)
    except Exception:
        return None


def _safe_int(v):
    try:
        return int(v)
    except Exception:
        return None


def _http_probe(name: str, url: str, read_bytes: int = 262144, timeout: int = 25, note: str = '') -> dict:
    ts = int(time.time())
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "systor-speed/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(read_bytes)
        dt = max(0.001, time.time() - t0)
        dl_mbps = round((len(data) * 8) / (dt * 1_000_000), 3)
        return {
            "ts": ts,
            "provider": name,
            "mode": "probe",
            "target": url,
            "server_id": None,
            "run_type": "manual",
            "ping_ms": round(dt * 1000, 1),
            "jitter_ms": None,
            "dl_mbps": dl_mbps,
            "ul_mbps": None,
            "ok": True,
            "note": (note or f"HTTP {r.status} · {r.headers.get('server') or 'server ?'} · {len(data)} bytes").strip(),
            "raw_json": json.dumps({
                "status": r.status,
                "server": r.headers.get("server"),
                "content_type": r.headers.get("content-type"),
                "content_length": r.headers.get("content-length"),
                "bytes": len(data),
                "seconds": round(dt, 3),
            }),
        }


def list_ookla_servers(limit: int = 20) -> list[dict]:
    if not _cmd_exists('speedtest'):
        return []
    p = _run(['speedtest', '--accept-license', '--accept-gdpr', '-L', '-f', 'json'], timeout=240)
    if p.returncode != 0:
        return []
    try:
        j = json.loads(p.stdout)
        rows = []
        for s in (j.get('servers') or [])[:limit]:
            rows.append({
                'id': str(s.get('id')),
                'label': f"{s.get('name','server')} · {s.get('location','')}, {s.get('country','')}",
                'host': s.get('host',''),
                'name': s.get('name',''),
                'location': s.get('location',''),
                'country': s.get('country',''),
            })
        return rows
    except Exception:
        return []


def run_ookla(server_id: str | int | None = None) -> dict:
    ts = int(time.time())
    if not _cmd_exists('speedtest'):
        return {'ts': ts, 'provider': 'ookla', 'mode': 'wan', 'target': 'official speedtest not installed', 'server_id': None, 'run_type': 'manual', 'ok': False, 'note': 'official Ookla CLI not installed', 'raw_json': ''}
    cmd = ['speedtest', '--accept-license', '--accept-gdpr', '-f', 'json']
    if server_id not in (None, '', '0'):
        cmd += ['-s', str(server_id)]
    p = _run(cmd, timeout=240)
    raw = (p.stdout or '').strip()
    if p.returncode != 0 or not raw:
        return {'ts': ts, 'provider': 'ookla', 'mode': 'wan', 'target': f'server {server_id or "auto"}', 'server_id': str(server_id or ''), 'run_type': 'manual', 'ok': False, 'note': (p.stderr or p.stdout or f'exit {p.returncode}').strip()[:280], 'raw_json': raw[:12000]}
    j = json.loads(raw)
    srv = j.get('server', {}) or {}
    ping = j.get('ping', {}) or {}
    dl = j.get('download', {}) or {}
    ul = j.get('upload', {}) or {}
    return {
        'ts': ts,
        'provider': 'ookla',
        'mode': 'wan',
        'target': f"{srv.get('name','server')} · {srv.get('location','')}, {srv.get('country','')}",
        'server_id': str(srv.get('id') or server_id or ''),
        'run_type': 'manual',
        'ping_ms': _safe_float(ping.get('latency')),
        'jitter_ms': _safe_float(ping.get('jitter')),
        'dl_mbps': round(float(dl.get('bandwidth', 0)) * 8 / 1_000_000, 3),
        'ul_mbps': round(float(ul.get('bandwidth', 0)) * 8 / 1_000_000, 3),
        'ok': True,
        'note': f"server id {srv.get('id','?')} · {srv.get('host','?')}",
        'raw_json': raw[:16000],
    }


def list_librespeed_servers(limit: int = 40) -> list[dict]:
    if not _cmd_exists('librespeed-cli'):
        return []
    p = _run(['librespeed-cli', '--list'], timeout=180)
    if p.returncode != 0:
        return []
    rows = []
    listing = (p.stdout or '').strip() or (p.stderr or '')
    for line in listing.splitlines():
        line = line.strip()
        if not line or ': ' not in line:
            continue
        sid, rest = line.split(': ', 1)
        if not sid.isdigit():
            continue
        label = rest
        url = ''
        if '(' in rest and ')' in rest:
            parts = rest.split('(')
            for chunk in parts[::-1]:
                if chunk.startswith('https://') or chunk.startswith('http://'):
                    url = chunk.split(')')[0]
                    break
        rows.append({'id': sid, 'label': label, 'url': url})
        if len(rows) >= limit:
            break
    return rows


def run_librespeed(server_id: str | int | None = None) -> dict:
    ts = int(time.time())
    if not _cmd_exists('librespeed-cli'):
        return {'ts': ts, 'provider': 'librespeed', 'mode': 'wan', 'target': 'librespeed-cli', 'server_id': None, 'run_type': 'manual', 'ok': False, 'note': 'librespeed-cli not installed', 'raw_json': ''}
    cmd = ['librespeed-cli', '--json']
    if server_id not in (None, '', '0'):
        cmd += ['--server', str(server_id)]
    p = _run(cmd, timeout=240)
    raw = (p.stdout or '').strip()
    if p.returncode != 0:
        return {'ts': ts, 'provider': 'librespeed', 'mode': 'wan', 'target': f'server {server_id or "auto"}', 'server_id': str(server_id or ''), 'run_type': 'manual', 'ok': False, 'note': (p.stderr or p.stdout or f'exit {p.returncode}').strip()[:280], 'raw_json': raw[:12000]}
    if raw in ('', 'null'):
        return {'ts': ts, 'provider': 'librespeed', 'mode': 'wan', 'target': f'server {server_id or "auto"}', 'server_id': str(server_id or ''), 'run_type': 'manual', 'ok': False, 'note': 'no usable LibreSpeed server response', 'raw_json': raw}
    j = json.loads(raw)
    if isinstance(j, list):
        j = j[0] if j else {}
    srv = j.get('server', {}) or {}
    return {
        'ts': ts,
        'provider': 'librespeed',
        'mode': 'wan',
        'target': f"{srv.get('name', 'server')} ({srv.get('url', '')})".strip(),
        'server_id': str(server_id or ''),
        'run_type': 'manual',
        'ping_ms': _safe_float(j.get('ping')),
        'jitter_ms': _safe_float(j.get('jitter')),
        'dl_mbps': _safe_float(j.get('download')),
        'ul_mbps': _safe_float(j.get('upload')),
        'ok': True,
        'note': f"jitter {j.get('jitter', '?')} ms",
        'raw_json': raw[:16000],
    }


def run_notion_probe() -> dict:
    return _http_probe('notion', 'https://www.notion.so/', read_bytes=512000, timeout=30, note='fixed endpoint probe · DL only')


def run_cloudflare_probe() -> dict:
    return _http_probe('cloudflare', 'https://speed.cloudflare.com/__down?bytes=10000000', read_bytes=10_000_000, timeout=30, note='Cloudflare edge probe · DL only · browser test button is more representative')


def run_provider(provider: str, cfg: dict | None = None, server_id: str | int | None = None, run_type: str = 'manual') -> dict:
    provider = (provider or 'ookla').strip().lower()
    speed_cfg = ((cfg or {}).get('speed') or {})
    chosen_server = server_id
    if provider == 'ookla':
        chosen_server = chosen_server if chosen_server not in (None, '') else speed_cfg.get('ookla_server_id')
        row = run_ookla(chosen_server)
    elif provider == 'librespeed':
        chosen_server = chosen_server if chosen_server not in (None, '') else speed_cfg.get('librespeed_server_id')
        row = run_librespeed(chosen_server)
    elif provider == 'notion':
        row = run_notion_probe()
    elif provider == 'cloudflare':
        row = run_cloudflare_probe()
    else:
        raise ValueError(f'unknown provider: {provider}')
    row['run_type'] = run_type or 'manual'
    if chosen_server not in (None, '') and not row.get('server_id'):
        row['server_id'] = str(chosen_server)
    return row


def run_many(providers: list[str], cfg: dict | None = None, server_id: str | int | None = None, run_type: str = 'manual') -> list[dict]:
    out = []
    for p in providers:
        try:
            sid = server_id if len(providers) == 1 else None
            out.append(run_provider(p, cfg=cfg, server_id=sid, run_type=run_type))
        except Exception as e:
            out.append({'ts': int(time.time()), 'provider': p, 'mode': 'wan', 'target': p, 'server_id': None, 'run_type': run_type or 'manual', 'ok': False, 'note': str(e), 'raw_json': ''})
    return out


def iperf_status(port: int = 5201) -> dict:
    try:
        out = subprocess.run(['ss', '-ltnp'], capture_output=True, text=True, timeout=5).stdout.splitlines()
        hits = [ln.strip() for ln in out if f':{int(port)}' in ln and 'iperf3' in ln]
        return {'running': bool(hits), 'port': int(port), 'lines': hits[:5]}
    except Exception as e:
        return {'running': False, 'port': int(port), 'lines': [], 'error': str(e)}


def start_iperf_server(port: int = 5201) -> dict:
    st = iperf_status(port)
    if st.get('running'):
        return st | {'started': False}
    subprocess.Popen(['iperf3', '-s', '-p', str(int(port))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    st = iperf_status(port)
    return st | {'started': bool(st.get('running'))}


def local_ipv4s() -> list[str]:
    vals = []
    try:
        out = subprocess.run(['ip', '-4', 'addr', 'show'], capture_output=True, text=True, timeout=5).stdout.splitlines()
        for line in out:
            line = line.strip()
            if not line.startswith('inet '):
                continue
            part = line.split()[1]
            ip = part.split('/')[0]
            if ip != '127.0.0.1':
                vals.append(ip)
    except Exception:
        pass
    return vals


def speed_alert_triggered(result: dict, cfg: dict) -> tuple[bool, str]:
    speed_cfg = cfg.get('speed', {}) or {}
    min_dl = _safe_float(speed_cfg.get('min_download_mbps'))
    min_ul = _safe_float(speed_cfg.get('min_upload_mbps'))
    dl = _safe_float(result.get('dl_mbps'))
    ul = _safe_float(result.get('ul_mbps'))
    reasons = []
    if min_dl is not None and dl is not None and dl < min_dl:
        reasons.append(f'DL {dl:.2f} < {min_dl:.2f} Mbps')
    if min_ul is not None and ul is not None and ul < min_ul:
        reasons.append(f'UL {ul:.2f} < {min_ul:.2f} Mbps')
    return (bool(reasons), ' · '.join(reasons))


def build_speed_alert(result: dict, host: str) -> tuple[str, str]:
    subject = f"🐢 Speed alert · {result.get('provider', 'speed')}"
    body = "\n".join([
        f"Host: {host}",
        f"Provider: {result.get('provider', '?')}",
        f"Target: {result.get('target', '?')}",
        f"DL: {result.get('dl_mbps', '—')} Mbps",
        f"UL: {result.get('ul_mbps', '—')} Mbps",
        f"Ping: {result.get('ping_ms', '—')} ms",
        f"Jitter: {result.get('jitter_ms', '—')} ms",
        f"Run: {result.get('run_type', '?')}",
        f"Note: {result.get('note', '')}",
    ])
    return subject, body
