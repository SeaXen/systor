# Systor Configuration Guide

Systor uses a lightweight YAML-compatible configuration format with multi-layer resolution and strict schema validation.

## Resolution Order (Later Overrides Earlier)

1. **Built-in Defaults**: defined in `systor.config.DEFAULT_CONFIG`
2. **System Config**: `/etc/systor/config.yaml`
3. **User Config**: `~/.config/systor/config.yaml`
4. **Local Directory**: `./systor.yaml`
5. **Environment Variables**: `SYSTOR_*` (e.g. `SYSTOR_WEB_PORT=6677`)

---

## Schema Overview

### Collector (`collector`)
| Key | Type | Default | Description |
|---|---|---|---|
| `poll_interval_sec` | integer (>= 1) | `5` | Sampling cadence in seconds |
| `retention_days` | integer (>= 1) | `7` | Retention period for raw granular metrics |
| `rollup_after_hours` | integer (>= 1) | `24` | Age after which samples are rolled into 5-minute averages |
| `rollup_retention_days` | integer (>= 1) | `90` | Retention period for rolled-up historical samples |

### Web Server (`web`)
| Key | Type | Default | Description |
|---|---|---|---|
| `host` | string | `"0.0.0.0"` | Bind address (`0.0.0.0` for LAN, `127.0.0.1` for loopback) |
| `port` | integer (1-65535) | `6677` | HTTP listening port |
| `trusted_proxies` | string or list | `"127.0.0.1, ::1"` | Trusted reverse proxy IPs/CIDRs for client IP resolution |
| `trust_cgnat` | boolean | `true` | Treat CGNAT (100.64.0.0/10) as private (Tailscale) |

### Authentication (`auth`)
| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `false` | Enable password authentication |
| `mode` | string | `"admin_only"` | Authentication mode (`admin_only`) |
| `username` | string | `"admin"` | Primary administrator username |
| `password_hash` | string | `""` | Werkzeug scrypt/pbkdf2 password hash |
| `idle_timeout_min` | integer (>= 0) | `0` | Session idle timeout in minutes (0 = disabled) |
| `max_fails` | integer (>= 1) | `5` | Maximum failed attempts before IP cooldown |
| `cooldown_sec` | integer (>= 0) | `60` | Duration of lockout after excessive failures |

### Threshold Alerts (`thresholds`)
Alerts fire when a monitored metric exceeds `value` continuously for `duration_min`:
- `cpu_load_1m`: `{"enabled": true, "value": 4.0, "duration_min": 2}`
- `cpu_temp_c`: `{"enabled": true, "value": 85.0, "duration_min": 3}`
- `mem_free_mb`: `{"enabled": true, "value": 500, "duration_min": 2}`
- `swap_used_mb`: `{"enabled": true, "value": 4096, "duration_min": 2}`
- `disk_used_pct`: `{"enabled": true, "value": 90, "duration_min": 5}`
- `cooldown_sec`: `600` (10 minutes between duplicate alerts)

### Notification Channels
- **Telegram** (`telegram`): `enabled`, `bot_token`, `chat_id`
- **Discord** (`discord`): `enabled`, `webhook_url`

---

## Validating Configuration

Verify your configuration syntax, schema, and value bounds using the CLI:

```bash
systor config check
```

Output:
```text
✓ Configuration is valid.
```

If misconfigured, Systor reports actionable warnings and errors with specific offending keys:
```text
❌ Error: web.port must be an integer between 1 and 65535
❌ Error: collector.poll_interval_sec must be an integer >= 1
❌ Configuration check failed with 2 error(s).
```

To view the fully resolved configuration as JSON:
```bash
systor config show
```
