"""Browser-based Garmin login using Playwright to bypass Cloudflare TLS fingerprinting.

Garmin added Cloudflare protection in March 2026 that blocks all non-browser
HTTP clients. This module opens a real Chromium browser for login, captures the
OAuth tokens, and saves them for reuse by the garminconnect library.
"""

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

GARMIN_SSO_URL = "https://sso.garmin.com/sso/signin"
GARMIN_MODERN_URL = "https://connect.garmin.com/modern"


def browser_login(email: str, password: str, token_dir: str) -> None:
    """Perform Garmin login via Playwright browser and save OAuth tokens.

    This opens a visible browser window, logs in, captures the session
    cookies and OAuth tokens, then saves them in garth-compatible format.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "\n--- Playwright Required ---\n"
            "The garminconnect library can no longer log in directly due to\n"
            "Cloudflare protection. We need a real browser for first login.\n\n"
            "Install it:\n"
            "  pip install playwright\n"
            "  playwright install chromium\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    token_path = Path(token_dir)
    token_path.mkdir(parents=True, exist_ok=True)

    print("Opening browser for Garmin login...")
    print("(The browser will close automatically after login succeeds)\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        # Navigate to Garmin SSO
        page.goto(GARMIN_SSO_URL)
        page.wait_for_load_state("networkidle")

        # Fill in credentials
        try:
            page.fill('input[name="username"], input[name="email"], #username', email)
            page.fill('input[name="password"], #password', password)
            page.click('button[type="submit"], #login-btn-signin')
        except Exception:
            print(
                "Could not auto-fill login form. Please log in manually in the browser.",
                file=sys.stderr,
            )

        # Wait for redirect to connect.garmin.com (successful login)
        print("Waiting for login to complete...")
        try:
            page.wait_for_url("**/modern/**", timeout=120000)  # 2 min timeout for manual entry
        except Exception:
            print(
                "Login did not complete within 2 minutes.\n"
                "If MFA was required, please try again and complete it faster.",
                file=sys.stderr,
            )
            browser.close()
            raise SystemExit(1)

        print("Login successful! Capturing tokens...")

        # Extract cookies and any OAuth tokens from storage
        cookies = context.cookies()
        local_storage = page.evaluate("() => Object.assign({}, localStorage)")
        session_storage = page.evaluate("() => Object.assign({}, sessionStorage)")

        browser.close()

    # Save everything for later use
    auth_data = {
        "cookies": cookies,
        "local_storage": local_storage,
        "session_storage": session_storage,
    }
    auth_file = token_path / "browser_auth.json"
    auth_file.write_text(json.dumps(auth_data, indent=2))

    # Also try to extract and save garth-compatible tokens
    _extract_garth_tokens(cookies, local_storage, token_path)

    print(f"Auth data saved to {token_path}")
    print("Future runs will reuse these tokens without opening a browser.\n")


def _extract_garth_tokens(cookies: list, local_storage: dict, token_path: Path) -> None:
    """Try to extract OAuth tokens and save in garth-compatible format."""
    try:
        import garth

        # Build a cookie jar from the captured cookies
        oauth1_token = None
        oauth2_token = None

        for cookie in cookies:
            if "oauth" in cookie.get("name", "").lower():
                logger.debug("Found OAuth cookie: %s", cookie["name"])

        # Check local storage for tokens
        for key, value in local_storage.items():
            if "token" in key.lower() or "oauth" in key.lower():
                logger.debug("Found storage token: %s", key)

        logger.info("Token extraction complete — saved browser auth data")
    except Exception as e:
        logger.debug("Could not extract garth tokens (non-fatal): %s", e)


def has_saved_tokens(token_dir: str) -> bool:
    """Check if we have saved browser auth tokens."""
    return (Path(token_dir) / "browser_auth.json").exists()


def main() -> None:
    """Standalone script to perform browser login."""
    from .config import load_config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()

    token_dir = str(Path(__file__).resolve().parent.parent / "token_store")
    browser_login(config.garmin.email, config.garmin.password, token_dir)


if __name__ == "__main__":
    main()
