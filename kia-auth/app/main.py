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
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from kia_flow import run_flow

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

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("kia-auth")


def _load_novnc_resize_mode() -> str:
    """Read noVNC resize mode from /data/options.json with safe fallback."""
    try:
        options_raw = OPTIONS_PATH.read_text(encoding="utf-8")
        options = json.loads(options_raw)
        mode = str(options.get("novnc_resize_mode", DEFAULT_NOVNC_RESIZE_MODE)).lower()
        if mode in ALLOWED_NOVNC_RESIZE_MODES:
            log.info("Configured noVNC resize mode: %s", mode)
            return mode
        log.warning(
            "Invalid novnc_resize_mode=%r in %s; using default %s",
            mode,
            OPTIONS_PATH,
            DEFAULT_NOVNC_RESIZE_MODE,
        )
    except FileNotFoundError:
        log.info("%s not found; using default noVNC resize mode %s", OPTIONS_PATH, DEFAULT_NOVNC_RESIZE_MODE)
    except Exception:
        log.exception("Failed to load %s; using default noVNC resize mode", OPTIONS_PATH)

    return DEFAULT_NOVNC_RESIZE_MODE


NOVNC_RESIZE_MODE = _load_novnc_resize_mode()


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
    updated_at: datetime = datetime.utcnow()


state = State()
current_task: Optional[asyncio.Task] = None
current_driver: Optional[webdriver.Chrome] = None


def _set(status: str, message: str, token: Optional[str] = None) -> None:
    state.status = status
    state.message = message
    state.updated_at = datetime.utcnow()
    if token is not None:
        state.token = token
        state.token_expires_at = datetime.utcnow() + timedelta(seconds=TOKEN_TTL_SECONDS)
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

app = FastAPI(title="Kia Auth", version="0.1.2", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/api/status")
async def status() -> dict:
    token = _token_if_valid()
    return {
        "status": state.status,
        "message": state.message,
        "token": token,
        "updated_at": state.updated_at.isoformat(),
        "version": app.version,
        "novnc_resize_mode": NOVNC_RESIZE_MODE,
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
