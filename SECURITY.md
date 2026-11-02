# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.1.x   | :white_check_mark: |
| < 1.1.0 | :x:                |

## Reporting a Vulnerability

We take the security of Systor seriously. If you discover a security vulnerability, please **do not report it publicly** on GitHub issues.

Instead, please send an encrypted or direct email to:
**drpelagik@gmail.com**

Please include:
- A description of the issue and potential impact
- Step-by-step reproduction instructions or proof-of-concept
- Any affected components or configurations

You will receive an acknowledgment within 48 hours, followed by an assessment and coordinated disclosure plan.

## Security Architecture & Hardening

Systor employs defense-in-depth security principles:

1. **Authentication & Access Control**:
   - Web administration is gated behind private LAN/VPN client validation (`is_request_private`) and reverse-proxy header validation (`trusted_proxies`).
   - Session cookies use `HttpOnly` and `SameSite=Lax`.
   - Double-submit CSRF tokens are validated for all state-changing API endpoints (POST/PUT/DELETE).

2. **Storage Isolation**:
   - File browsing and previewing are strictly restricted to configured `allowed_roots`.
   - Sensitive file patterns (`.env`, private keys, SSH credentials) are denied from preview and download.
   - Tar archive restoration uses strict member extraction filters to prevent path traversal (tar-slip).

3. **Emergency Terminal Hardening**:
   - The emergency console requires an explicit `terminal.enabled: true` configuration.
   - Terminal access requires a configured `auth.password_hash` (no default credentials permitted).
   - Rate limiting is enforced on all authentication attempts.

4. **Service Isolation**:
   - Production systemd units enforce `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`, and explicit `ReadWritePaths`.
   - Docker deployments default to least privilege without mounting `/var/run/docker.sock` unless explicitly opted in.
