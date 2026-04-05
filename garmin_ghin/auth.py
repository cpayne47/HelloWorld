"""Garmin Connect cookie-based authentication.

Since Garmin blocks automated logins via Cloudflare (March 2026), we use
cookies from a real browser session. Two methods:
  1. Paste cookies from browser dev tools
  2. Auto-read cookies from Chrome via browser_cookie3
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_DIR = Path(__file__).resolve().parent.parent / "token_store"
COOKIE_FILE = TOKEN_DIR / "garmin_cookies.json"

GARMIN_DOMAINS = [".garmin.com", "connect.garmin.com", "sso.garmin.com"]


def save_cookie_string(cookie_string: str) -> None:
    """Parse a raw Cookie header string and save as JSON."""
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)

    cookies = {}
    for pair in cookie_string.strip().split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, value = pair.split("=", 1)
            cookies[name.strip()] = value.strip()

    COOKIE_FILE.write_text(json.dumps(cookies, indent=2))
    logger.info("Saved %d cookies to %s", len(cookies), COOKIE_FILE)
    print(f"Saved {len(cookies)} cookies to {COOKIE_FILE}")


def load_cookies() -> dict[str, str]:
    """Load saved cookies. Returns dict of name->value."""
    if not COOKIE_FILE.exists():
        return {}
    return json.loads(COOKIE_FILE.read_text())


def has_saved_cookies() -> bool:
    return COOKIE_FILE.exists()


def import_from_chrome() -> None:
    """Auto-import Garmin cookies from Chrome browser."""
    try:
        import browser_cookie3
    except ImportError:
        print(
            "browser_cookie3 not installed. Install it:\n"
            "  pip install browser_cookie3\n\n"
            "Or use the paste method instead:\n"
            "  python -m garmin_ghin.cli login --paste"
        )
        raise SystemExit(1)

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)

    print("Reading cookies from Chrome...")
    print("(Chrome must be closed, and you may see a Keychain access prompt)\n")

    try:
        cj = browser_cookie3.chrome(domain_name=".garmin.com")
    except Exception as e:
        print(f"Failed to read Chrome cookies: {e}")
        print("\nMake sure:")
        print("  1. Chrome is installed and you've logged into connect.garmin.com")
        print("  2. Chrome is fully closed (not just minimized)")
        print("  3. You grant Keychain access if prompted")
        raise SystemExit(1)

    cookies = {}
    for cookie in cj:
        if any(d in cookie.domain for d in GARMIN_DOMAINS):
            cookies[cookie.name] = cookie.value

    if not cookies:
        print("No Garmin cookies found in Chrome.")
        print("Log into connect.garmin.com in Chrome first, then try again.")
        raise SystemExit(1)

    COOKIE_FILE.write_text(json.dumps(cookies, indent=2))
    print(f"Imported {len(cookies)} Garmin cookies from Chrome.")
