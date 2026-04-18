# Changelog

All notable changes to the Kia Auth add-on will be documented in this file.

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

### Known limitations
- `app/kia_flow.py` is a placeholder — upstream
  [`KiaFetchApiTokens.py`](https://gist.github.com/marvinwankersteen/af92c571881ac76579a037fac4f3a63a)
  must be vendored in before the add-on can actually produce tokens.
- noVNC runs without a password. Do not expose port 6080 outside your LAN.
- If HA is served over HTTPS, the browser may block the HTTP noVNC connection
  as mixed content. Allow mixed content for the HA origin, or access HA over
  plain HTTP while using this add-on.
