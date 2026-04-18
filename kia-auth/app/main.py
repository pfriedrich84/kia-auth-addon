"""
FastAPI orchestrator for the Kia Auth add-on.

Responsibilities:
  - Serve the single-page web UI (inline inside HA Ingress)
  - Spawn and supervise one Chromium instance at a time via Selenium
  - Drive kia_flow.run_flow() in a background task
  - Expose status + token to the UI via simple JSON polling

Intentionally not included:
  - Persistent storage (HA's kia_uvo integration stores the token itself)
  - API-key auth (HA Ingress already gates access to this add-on)
  - Multiple concurrent sessions (one user, one token at a time)
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from kia_flow import AUTH_DOMAIN, CLIENT_ID, run_flow

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

# Kia's login flow only works with this specific mobile UA (upstream wiki).
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 4.1.1; Galaxy Nexus Build/JRO03C) "
    "AppleWebKit/535.19 (KHTML, like Gecko) "
    "Chrome/18.0.1025.166 Mobile Safari/535.19_CCS_APP_AOS"
)

# Path to Chromium binary and chromedriver inside the Alpine container.
CHROMIUM_BIN = "/usr/bin/chromium-browser"
CHROMEDRIVER_BIN = "/usr/bin/chromedriver"

# How long to keep a successfully-obtained token in memory before auto-clearing.
# Defence-in-depth: HA Ingress already gates access, but tokens grant full Kia
# account access, so keep the exposure window tight.
TOKEN_TTL_SECONDS = 600  # 10 minutes

# Add-on options file written by Home Assistant.
OPTIONS_PATH = Path("/data/options.json")
DEFAULT_NOVNC_RESIZE_MODE = "scale"
ALLOWED_NOVNC_RESIZE_MODES = {"scale", "remote", "off"}
DEFAULT_NOVNC_IP = ""
DEFAULT_NOVNC_URL = ""  # legacy full URL override (kept for backward compatibility)

SUPERVISOR_API = "http://supervisor"

# Watchdog: when user is stuck on login/CAPTCHA for too long, show hints.
AWAITING_LOGIN_HINT_SECONDS = 180

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("kia-auth")


def _normalize_ipv4_address(address: str) -> Optional[str]:
    """Normalize IPv4 (with or without CIDR) and filter unusable values."""
    try:
        ip = (
            ipaddress.ip_interface(address).ip
            if "/" in address
            else ipaddress.ip_address(address)
        )
    except ValueError:
        return None

    if ip.version != 4 or ip.is_loopback or ip.is_link_local:
        return None
    return str(ip)


def _discover_ha_ip_from_supervisor() -> Optional[str]:
    """Ask Supervisor for network info and return a usable HA IPv4 if available."""
    token = os.getenv("SUPERVISOR_TOKEN")
    if not token:
        log.info("SUPERVISOR_TOKEN not available; cannot auto-discover HA IP")
        return None

    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = httpx.get(
            f"{SUPERVISOR_API}/network/info", headers=headers, timeout=5.0
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        log.exception("Failed to query Supervisor network info for HA IP")
        return None

    data = payload.get("data", {})
    interfaces = data.get("interfaces", [])
    for iface in interfaces:
        ipv4_info = iface.get("ipv4", {})
        for address in ipv4_info.get("address", []) or []:
            ip = _normalize_ipv4_address(str(address))
            if ip:
                log.info("Auto-discovered HA IP from Supervisor: %s", ip)
                return ip

    log.warning("Could not find usable IPv4 in Supervisor network info")
    return None


def _load_addon_options() -> tuple[str, str, str]:
    """Read add-on options from /data/options.json with safe fallbacks."""
    resize_mode = DEFAULT_NOVNC_RESIZE_MODE
    novnc_ip = DEFAULT_NOVNC_IP
    novnc_url = DEFAULT_NOVNC_URL

    try:
        options_raw = OPTIONS_PATH.read_text(encoding="utf-8")
        options = json.loads(options_raw)

        configured_mode = str(
            options.get("novnc_resize_mode", DEFAULT_NOVNC_RESIZE_MODE)
        ).lower()
        if configured_mode in ALLOWED_NOVNC_RESIZE_MODES:
            resize_mode = configured_mode
        else:
            log.warning(
                "Invalid novnc_resize_mode=%r in %s; using default %s",
                configured_mode,
                OPTIONS_PATH,
                DEFAULT_NOVNC_RESIZE_MODE,
            )

        configured_ip = str(options.get("novnc_ip", DEFAULT_NOVNC_IP)).strip()
        if configured_ip:
            normalized = _normalize_ipv4_address(configured_ip)
            if normalized:
                novnc_ip = normalized
            else:
                log.warning(
                    "Invalid novnc_ip=%r in %s; expected IPv4 address",
                    configured_ip,
                    OPTIONS_PATH,
                )

        # Legacy override support (older add-on versions exposed novnc_url).
        configured_url = str(options.get("novnc_url", DEFAULT_NOVNC_URL)).strip()
        if configured_url:
            novnc_url = configured_url

        log.info("Configured noVNC resize mode: %s", resize_mode)
        if novnc_ip:
            log.info("Configured noVNC IP override: %s", novnc_ip)
        if novnc_url:
            log.info("Configured legacy noVNC URL override: %s", novnc_url)

    except FileNotFoundError:
        log.info(
            "%s not found; using default noVNC settings (mode=%s)",
            OPTIONS_PATH,
            DEFAULT_NOVNC_RESIZE_MODE,
        )
    except Exception:
        log.exception("Failed to load %s; using default noVNC settings", OPTIONS_PATH)

    return resize_mode, novnc_ip, novnc_url


def _build_novnc_url() -> str:
    """Build noVNC URL from best available source."""
    if NOVNC_URL_OVERRIDE:
        return NOVNC_URL_OVERRIDE

    host = NOVNC_IP_OVERRIDE or DISCOVERED_HA_IP
    if not host:
        return ""

    return (
        f"http://{host}:6080/vnc.html?autoconnect=true"
        f"&resize={NOVNC_RESIZE_MODE}&reconnect=true"
    )


NOVNC_RESIZE_MODE, NOVNC_IP_OVERRIDE, NOVNC_URL_OVERRIDE = _load_addon_options()
DISCOVERED_HA_IP = _discover_ha_ip_from_supervisor()


def _redact(token: str) -> str:
    """Short, log-safe representation of a token."""
    if not token or len(token) < 12:
        return "<redacted>"
    return f"{token[:5]}…{token[-5:]}"


# ----------------------------------------------------------------------------
# Global state (single-user, single-flow service)
# ----------------------------------------------------------------------------


class State(BaseModel):
    status: str = "idle"  # idle | starting | awaiting_login | extracting | completed | failed
    message: str = "Ready. Click Start to begin."
    token: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    status_since: datetime = datetime.utcnow()
    updated_at: datetime = datetime.utcnow()


state = State()
current_task: Optional[asyncio.Task] = None
current_driver: Optional[webdriver.Chrome] = None


def _set(status: str, message: str, token: Optional[str] = None) -> None:
    now = datetime.utcnow()
    if state.status != status:
        state.status_since = now
    state.status = status
    state.message = message
    state.updated_at = now
    if token is not None:
        state.token = token
        state.token_expires_at = now + timedelta(seconds=TOKEN_TTL_SECONDS)
        log.info(
            "Token obtained: %s (expires in %ds)", _redact(token), TOKEN_TTL_SECONDS
        )
    log.info("State → %s: %s", status, message)


def _token_if_valid() -> Optional[str]:
    """Return the stored token, or None if expired / never set."""
    if state.token is None:
        return None
    if state.token_expires_at and datetime.utcnow() > state.token_expires_at:
        log.info("Token expired, clearing from memory")
        state.token = None
        state.token_expires_at = None
        # Keep UI consistent: a completed state without token is misleading.
        if state.status == "completed":
            _set("idle", "Token expired. Click Start to begin a new session.")
        return None
    return state.token


def _apply_watchdog_hints() -> None:
    """Show actionable hint if login/CAPTCHA step is taking unusually long."""
    if state.status != "awaiting_login":
        return

    elapsed = (datetime.utcnow() - state.status_since).total_seconds()
    if elapsed < AWAITING_LOGIN_HINT_SECONDS:
        return

    if state.message.startswith("Still waiting for login"):
        return

    _set(
        "awaiting_login",
        (
            "Still waiting for login. If you are stuck: open noVNC, make sure the "
            "CAPTCHA and Kia login are completed in that browser tab, then wait on "
            "this page. If needed, reset and start a new session."
        ),
    )


# ----------------------------------------------------------------------------
# The flow
# ----------------------------------------------------------------------------


def _build_driver() -> webdriver.Chrome:
    """Construct a Selenium driver attached to Xvfb with Kia's required settings."""
    opts = Options()
    opts.binary_location = CHROMIUM_BIN
    opts.add_argument(f"--user-agent={MOBILE_UA}")
    # Keep Kia's required mobile UA, but use a very wide landscape viewport so
    # the reCAPTCHA/login controls are easier to use over noVNC.
    opts.add_argument("--window-size=2200,1200")
    # Required when running as root inside a container:
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    # Cosmetic: reduces chance of bot-detection flagging the UA:
    opts.add_argument("--disable-blink-features=AutomationControlled")

    service = Service(executable_path=CHROMEDRIVER_BIN)
    return webdriver.Chrome(service=service, options=opts)


async def _run_auth_flow() -> None:
    """Background task: drives the whole flow and updates global state."""
    global current_driver
    try:
        _set("starting", "Launching Chromium...")
        current_driver = await asyncio.to_thread(_build_driver)

        _set(
            "awaiting_login",
            "Browser ready. Open noVNC, solve the reCAPTCHA and log in.",
        )

        # The vendored script blocks until the flow is complete.
        # It will navigate, poll, exchange the code, and return both tokens.
        tokens = await asyncio.to_thread(run_flow, current_driver)

        _set(
            "completed",
            "Token ready. Copy it into the kia_uvo integration's password field.",
            token=tokens["refresh_token"],
        )
    except Exception as e:
        log.exception("Auth flow failed")
        _set("failed", f"{type(e).__name__}: {e}")
    finally:
        if current_driver is not None:
            try:
                await asyncio.to_thread(current_driver.quit)
            except Exception:
                log.exception("Failed to quit driver")
            current_driver = None


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------

app = FastAPI(title="Kia Auth", version="0.1.6", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/api/status")
async def status() -> dict:
    _apply_watchdog_hints()
    token = _token_if_valid()
    return {
        "status": state.status,
        "message": state.message,
        "token": token,
        "updated_at": state.updated_at.isoformat(),
        "version": app.version,
        "novnc_resize_mode": NOVNC_RESIZE_MODE,
        "novnc_url": _build_novnc_url(),
    }


@app.post("/api/start")
async def start() -> dict:
    global current_task
    if current_task is not None and not current_task.done():
        raise HTTPException(status_code=409, detail="Auth flow already running")
    # Clear any previous result before starting fresh
    state.token = None
    state.token_expires_at = None
    current_task = asyncio.create_task(_run_auth_flow())
    return {"ok": True}


@app.post("/api/validate")
async def validate_token() -> dict:
    """Validate current refresh token and return token endpoint diagnostics."""
    refresh_token = _token_if_valid()
    if not refresh_token:
        raise HTTPException(status_code=409, detail="No token available to validate")

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "client_secret": "secret",
    }

    try:
        response = await asyncio.to_thread(
            httpx.post,
            f"{AUTH_DOMAIN}/auth/api/v2/user/oauth2/token",
            data=data,
            timeout=30.0,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Validation request failed: {exc}") from exc

    payload = {}
    try:
        payload = response.json()
    except Exception:
        pass

    if response.status_code != 200:
        detail = payload.get("error_description") or payload.get("error") or response.text
        return {
            "ok": False,
            "message": f"Token rejected ({response.status_code}): {detail}",
        }

    new_refresh = payload.get("refresh_token")
    if new_refresh and isinstance(new_refresh, str) and new_refresh != refresh_token:
        state.token = new_refresh
        state.token_expires_at = datetime.utcnow() + timedelta(seconds=TOKEN_TTL_SECONDS)
        log.info("Validation returned refreshed token: %s", _redact(new_refresh))

    expires_in = payload.get("expires_in")
    return {
        "ok": True,
        "message": "Token is valid.",
        "expires_in": expires_in,
    }


@app.post("/api/reset")
async def reset() -> dict:
    """Clear token + reset to idle. Useful after copying the token."""
    global current_task
    if current_task is not None and not current_task.done():
        raise HTTPException(status_code=409, detail="Can't reset while flow is running")
    state.token = None
    state.token_expires_at = None
    _set("idle", "Ready. Click Start to begin.")
    return {"ok": True}
