"""
Vendored Kia Connect EU login flow.

This module wraps the upstream community script:
  https://gist.github.com/marvinwankersteen/af92c571881ac76579a037fac4f3a63a

The caller (main.py) is responsible for:
  - Creating the Selenium driver with the correct mobile user agent
  - Driver cleanup (driver.quit) on success or failure

run_flow() performs the same logical steps as the upstream script, but uses
Selenium URL polling instead of interactive input() prompts.
"""

from __future__ import annotations

import logging
import time
from typing import TypedDict
from urllib.parse import parse_qs, quote, urlparse

import httpx
from selenium.webdriver.remote.webdriver import WebDriver

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Upstream flow constants
# ---------------------------------------------------------------------------

AUTH_DOMAIN = "https://idpconnect-eu.kia.com"
URL_REDIRECT = "https://prd.eu-ccapi.kia.com:8080/api/v1/user/oauth2/redirect"
URL_AUTHORIZE_REDIRECT = "https://www.kia.com/api/bin/oneid/login"
URL_AUTHORIZE_REDIRECT_QUOTED = quote(URL_AUTHORIZE_REDIRECT, safe="")
URL_LOGIN = (
    f"{AUTH_DOMAIN}/auth/api/v2/user/oauth2/authorize?"
    f"ui_locales=de&"
    f"scope=openid+profile+email+phone&"
    f"response_type=code&"
    f"client_id=peukiaidm-online-sales&"
    f"redirect_uri={URL_AUTHORIZE_REDIRECT_QUOTED}&"
    f"state=aHR0cHM6Ly93d3cua2lhLmNvbS9kZS8"  # base64 https://www.kia.com/de/
)
CLIENT_ID = "fdc85c00-0a2f-4c64-bcb4-2cfb1500730a"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 4.1.1; Galaxy Nexus Build/JRO03C) "
    "AppleWebKit/535.19 (KHTML, like Gecko) "
    "Chrome/18.0.1025.166 Mobile Safari/535.19_CCS_APP_AOS"
)

# Timeouts / polling
LOGIN_TIMEOUT_SECONDS = 10 * 60
CODE_TIMEOUT_SECONDS = 5 * 60
POLL_SECONDS = 1.0


class TokenBundle(TypedDict):
    refresh_token: str
    access_token: str


def _get_connector_session_key(client: httpx.Client) -> str:
    """Retrieve connector_session_key via upstream authorize endpoint."""
    url = (
        f"{AUTH_DOMAIN}/auth/api/v2/user/oauth2/authorize?"
        f"response_type=code&"
        f"client_id={CLIENT_ID}&"
        f"redirect_uri={URL_REDIRECT}&"
        f"lang=de&"
        f"state=ccsp"
    )

    response = client.get(url)
    response.raise_for_status()

    parsed = urlparse(str(response.url))
    queries = parse_qs(parsed.query)
    try:
        next_uri = queries["next_uri"][0]
        next_queries = parse_qs(urlparse(next_uri).query)
        return next_queries["connector_session_key"][0]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(
            "Could not extract connector_session_key from authorize response URL"
        ) from exc


def _build_oauth_authorize_url(connector_session_key: str) -> str:
    """Build second authorize URL used to obtain OAuth code in browser."""
    return (
        f"{AUTH_DOMAIN}/auth/api/v2/user/oauth2/authorize?"
        f"client_id={CLIENT_ID}&"
        f"redirect_uri={URL_REDIRECT}&"
        f"response_type=code&"
        f"scope=&"
        f"state=ccsp&"
        f"connector_client_id=hmgid1.0-{CLIENT_ID}&"
        f"ui_locales=&"
        f"connector_scope=&"
        f"connector_session_key={connector_session_key}"
    )


def _extract_authorization_code(url: str) -> str:
    """Parse OAuth authorization code from redirect URL."""
    queries = parse_qs(urlparse(url).query)
    try:
        return queries["code"][0]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Could not extract authorization code from URL: {url}") from exc


def _exchange_code_for_tokens(client: httpx.Client, authorization_code: str) -> TokenBundle:
    """Exchange authorization code for access/refresh tokens."""
    data = {
        "grant_type": "authorization_code",
        "code": authorization_code,
        "redirect_uri": URL_REDIRECT,
        "client_id": CLIENT_ID,
        "client_secret": "secret",
    }

    response = client.post(f"{AUTH_DOMAIN}/auth/api/v2/user/oauth2/token", data=data)
    response.raise_for_status()
    payload = response.json()

    refresh_token = payload.get("refresh_token")
    access_token = payload.get("access_token")
    if not refresh_token or not access_token:
        raise RuntimeError("Token endpoint response did not include refresh_token/access_token")

    return {"refresh_token": refresh_token, "access_token": access_token}


def _wait_for_login_completion(driver: WebDriver, timeout_seconds: int) -> None:
    """
    Wait until user has completed login/CAPTCHA in the noVNC browser.

    We consider login completed once the browser leaves idpconnect and lands on
    kia.com. This mirrors the upstream manual instruction where user confirms
    they reached Kia's homepage.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = driver.current_url
        if current.startswith("https://www.kia.com/") and "idpconnect-eu.kia.com" not in current:
            log.info("Detected successful Kia site redirect after login")
            return
        time.sleep(POLL_SECONDS)

    raise TimeoutError(
        "Timed out waiting for user to complete login/reCAPTCHA in noVNC "
        f"({timeout_seconds}s)"
    )


def _wait_for_oauth_code_redirect(driver: WebDriver, timeout_seconds: int) -> str:
    """Wait until browser reaches ccapi redirect URL with ?code=..."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = driver.current_url
        if current.startswith(URL_REDIRECT):
            code = _extract_authorization_code(current)
            log.info("Received OAuth code redirect")
            return code
        time.sleep(POLL_SECONDS)

    raise TimeoutError(
        "Timed out waiting for OAuth redirect with authorization code "
        f"({timeout_seconds}s)"
    )


def run_flow(driver: WebDriver) -> TokenBundle:
    """
    Execute the Kia auth flow against the given Selenium driver.

    Args:
        driver: A Selenium WebDriver configured with Kia's required mobile UA
                and a wider landscape window size for noVNC usability
                (currently 1800x1000).

    Returns:
        {"refresh_token": "...", "access_token": "..."}

    Raises:
        TimeoutError: If user doesn't complete login/code redirect in time.
        RuntimeError: If extraction/token exchange fails.
    """
    log.info("Step 1/5: Opening Kia login URL")
    driver.get(URL_LOGIN)

    log.info("Step 2/5: Waiting for user login + reCAPTCHA completion")
    _wait_for_login_completion(driver, LOGIN_TIMEOUT_SECONDS)

    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9"},
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        log.info("Step 3/5: Fetching connector_session_key")
        connector_session_key = _get_connector_session_key(client)

        log.info("Step 4/5: Opening OAuth authorize URL in same logged-in browser tab")
        driver.get(_build_oauth_authorize_url(connector_session_key))

        log.info("Step 5/5: Waiting for code redirect, then exchanging code for tokens")
        authorization_code = _wait_for_oauth_code_redirect(driver, CODE_TIMEOUT_SECONDS)
        return _exchange_code_for_tokens(client, authorization_code)
