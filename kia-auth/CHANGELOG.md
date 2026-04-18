# Changelog

All notable changes to the Kia Auth add-on will be documented in this file.

## 0.1.1 — Unreleased

### Changed
- noVNC link now opens `vnc_lite.html` with `resize=remote` for better usability.
- Increased virtual desktop/browser size to a wide landscape layout (2200x1200).

## 0.1.0 — Unreleased

### Added
- Initial add-on scaffold for Home Assistant OS
- Multi-arch support: amd64, aarch64
- HA Ingress web UI with start/status/reset endpoints
- noVNC accessible on port 6080 for solving the reCAPTCHA
- Bundled stack: Chromium + chromedriver + Xvfb + x11vnc + noVNC + FastAPI
- s6-overlay service supervision with dependency ordering
- Token auto-clears from memory 10 minutes after being obtained
- Token redaction in logs (first 5 + last 5 chars only)

### Changed
- Implemented `app/kia_flow.py` by vendoring the upstream Kia Connect EU OAuth
  flow (Selenium URL polling + token exchange via HTTP API).
- Token expiry now resets UI state back to `idle` with an explicit "Token
  expired" message to avoid a stale `completed` screen.
- Repository metadata/docs URLs now point to
  `https://github.com/pfriedrich84/kia-auth-addon`.

### Known limitations
- noVNC runs without a password. Do not expose port 6080 outside your LAN.
- If HA is served over HTTPS, the browser may block the HTTP noVNC connection
  as mixed content. Allow mixed content for the HA origin, or access HA over
  plain HTTP while using this add-on.
