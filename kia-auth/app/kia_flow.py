"""
Vendored Kia Connect EU login flow.

This module wraps the upstream community script:
  https://gist.github.com/marvinwankersteen/af92c571881ac76579a037fac4f3a63a

The caller (main.py) is responsible for:
  - Creating the Selenium driver with the correct mobile user agent
  - Updating UI status ("awaiting_login", "extracting", ...) by observing state changes
  - Driver cleanup (driver.quit) on success or failure

So run_flow() just needs to:
  1. Navigate to Kia's first authorize URL
  2. Poll driver.current_url until login + reCAPTCHA are complete (user does this via noVNC)
  3. Extract connector_session_key
  4. Navigate to the second authorize URL
  5. Wait for the ccapi redirect URL containing ?code=...
  6. Exchange the code for refresh_token + access_token
  7. Return both tokens

To fill in: paste the upstream KiaFetchApiTokens.py logic into run_flow(),
removing all input() prompts (poll driver.current_url with sleep() instead).
"""

from __future__ import annotations

import logging
from typing import TypedDict

from selenium.webdriver.remote.webdriver import WebDriver

log = logging.getLogger(__name__)


class TokenBundle(TypedDict):
    refresh_token: str
    access_token: str


def run_flow(driver: WebDriver) -> TokenBundle:
    """
    Execute the Kia auth flow against the given Selenium driver.

    Blocks until tokens are obtained, the user cancels, or an error occurs.
    Expected to take 30s–10min depending on how fast the user solves the CAPTCHA.

    Args:
        driver: A Selenium WebDriver configured with the mobile user agent
                and a suitable window size (400x800).

    Returns:
        {"refresh_token": "...", "access_token": "..."}

    Raises:
        TimeoutError: If the user doesn't complete login within the timeout.
        RuntimeError: If the flow fails at any stage (missing code, bad response, etc.)
    """
    raise NotImplementedError(
        "Vendor the upstream KiaFetchApiTokens.py logic here. "
        "See the module docstring above for the expected contract."
    )
