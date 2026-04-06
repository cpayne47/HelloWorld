"""GHIN (Golf Handicap Information Network) client using undetected-chromedriver.

Logs into ghin.com and provides methods for interacting with the GHIN system,
including posting scores.
"""

import logging
import sys
import time
from pathlib import Path

from .config import GHINConfig

logger = logging.getLogger(__name__)

GHIN_LOGIN_URL = "https://www.ghin.com/login"
GHIN_HOME_URL = "https://www.ghin.com/"


class GHINClient:
    """Interacts with GHIN website using a real Chrome browser."""

    def __init__(self, config: GHINConfig):
        self._config = config
        self._driver = None

    def _ensure_browser(self):
        if self._driver is not None:
            return self._driver

        try:
            import undetected_chromedriver as uc
        except ImportError:
            print(
                "undetected-chromedriver is required:\n\n"
                "  pip install undetected-chromedriver\n",
                file=sys.stderr,
            )
            raise SystemExit(1)

        options = uc.ChromeOptions()
        options.add_argument("--no-first-run")
        options.add_argument("--no-service-autorun")
        options.add_argument("--password-store=basic")

        # Disable password save prompts
        prefs = {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
            "profile.password_manager_leak_detection": False,
        }
        options.add_experimental_option("prefs", prefs)

        # Separate persistent profile for GHIN (don't share with Garmin)
        profile_dir = Path.home() / ".garmin_ghin" / "chrome_profile_ghin"
        profile_dir.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={profile_dir}")

        # Auto-detect Chrome version
        chrome_version = None
        try:
            import subprocess
            result = subprocess.run(
                ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                chrome_version = int(result.stdout.strip().split()[-1].split(".")[0])
                logger.info("Detected Chrome version: %d", chrome_version)
        except Exception as e:
            logger.debug("Could not detect Chrome version: %s", e)

        logger.info("Launching Chrome for GHIN...")
        driver = uc.Chrome(options=options, headless=False, version_main=chrome_version)
        self._driver = driver
        return driver

    def _is_logged_in(self, driver) -> bool:
        """Check if we're logged into GHIN by looking at the page content."""
        try:
            page_text = driver.find_element("tag name", "body").text.lower()
            url = driver.current_url.lower()

            # If we're on the login page, we're definitely not logged in
            if "/login" in url:
                logger.debug("On /login URL — not logged in")
                return False

            # If login form elements are visible, we're not logged in
            login_page_markers = ["email address or ghin number", "log in\n"]
            for marker in login_page_markers:
                if marker in page_text:
                    logger.debug("Login page marker found: '%s' — not logged in", marker)
                    return False

            # Check for authenticated indicators
            indicators = ["post score", "post a score", "my stats",
                          "recent scores", "score history", "round history"]
            for indicator in indicators:
                if indicator in page_text:
                    logger.debug("Login detected via text: '%s'", indicator)
                    return True
            # Check if URL suggests we're past login
            if "/profile" in url or "/golfer" in url:
                return True
        except Exception:
            pass
        return False

    def _dismiss_cookie_consent(self, driver) -> None:
        """Click 'Accept all cookies' if the consent banner appears."""
        try:
            buttons = driver.find_elements("tag name", "button")
            for btn in buttons:
                btn_text = btn.text.strip().lower()
                if "accept all" in btn_text or "accept cookies" in btn_text:
                    btn.click()
                    logger.info("Dismissed cookie consent: '%s'", btn.text.strip())
                    time.sleep(1)
                    return
            # Also try common cookie consent selectors
            for selector in ["#onetrust-accept-btn-handler", ".accept-cookies",
                             "[data-testid='accept-cookies']"]:
                try:
                    el = driver.find_element("css selector", selector)
                    el.click()
                    logger.info("Dismissed cookie consent via %s", selector)
                    time.sleep(1)
                    return
                except Exception:
                    continue
        except Exception:
            pass

    def _login(self, driver) -> None:
        """Log into GHIN if not already logged in."""
        driver.get(GHIN_HOME_URL)
        time.sleep(5)

        # Handle cookie consent if it appears
        self._dismiss_cookie_consent(driver)

        # Check if already logged in
        if self._is_logged_in(driver):
            logger.info("Already logged into GHIN")
            print("Already logged into GHIN.")
            return

        # Navigate to login page
        logger.info("Not logged in, navigating to GHIN login...")
        driver.get(GHIN_LOGIN_URL)
        time.sleep(5)

        # Handle cookie consent on login page too
        self._dismiss_cookie_consent(driver)

        print("Logging into GHIN...")
        try:
            page_text = driver.find_element("tag name", "body").text
            logger.debug("Login page text (first 500): %s", page_text[:500])

            # Check for iframes
            iframes = driver.find_elements("tag name", "iframe")
            in_iframe = False
            for iframe in iframes:
                src = iframe.get_attribute("src") or ""
                iframe_id = iframe.get_attribute("id") or ""
                logger.debug("Found iframe: id=%s src=%s", iframe_id, src[:80])
                if "login" in src or "auth" in src or "ghin" in src:
                    driver.switch_to.frame(iframe)
                    in_iframe = True
                    logger.info("Switched into GHIN iframe: %s", iframe_id or src[:50])
                    time.sleep(2)
                    break

            # GHIN login uses GHIN number (not email)
            # Try multiple selectors for the GHIN number field
            ghin_field = None
            for selector in ["css:input[type='text']", "css:input[name='ghinNumber']",
                             "css:input[placeholder*='GHIN']", "css:input[placeholder*='ghin']",
                             "css:input[placeholder*='Number']",
                             "id:ghin-number", "id:ghinNumber", "name:ghinNumber"]:
                method, value = selector.split(":", 1)
                try:
                    if method == "css":
                        ghin_field = driver.find_element("css selector", value)
                    else:
                        ghin_field = driver.find_element(method, value)
                    logger.info("Found GHIN number field via %s", selector)
                    break
                except Exception:
                    continue

            if not ghin_field:
                # Try finding any visible text input
                inputs = driver.find_elements("css selector", "input[type='text'], input[type='number']")
                if inputs:
                    ghin_field = inputs[0]
                    logger.info("Found GHIN number field as first text input")

            if not ghin_field:
                raise RuntimeError("Could not find GHIN number field on login page")

            ghin_field.clear()
            ghin_field.send_keys(self._config.ghin_number)
            logger.info("Entered GHIN number: %s", self._config.ghin_number)

            # Find password field
            pw_field = None
            for selector in ["css:input[type='password']", "id:password", "name:password"]:
                method, value = selector.split(":", 1)
                try:
                    if method == "css":
                        pw_field = driver.find_element("css selector", value)
                    else:
                        pw_field = driver.find_element(method, value)
                    logger.info("Found password field via %s", selector)
                    break
                except Exception:
                    continue

            if not pw_field:
                raise RuntimeError("Could not find password field on GHIN login page")

            pw_field.clear()
            pw_field.send_keys(self._config.password)
            logger.info("Entered password (length %d)", len(self._config.password))

            # Submit the form — try multiple approaches since GHIN is a React SPA
            time.sleep(1)

            # Approach 1: Press Enter in the password field
            from selenium.webdriver.common.keys import Keys
            pw_field.send_keys(Keys.RETURN)
            logger.info("Sent Enter key from password field")

            # If Enter didn't work, try finding and clicking the button
            time.sleep(3)
            if not self._is_logged_in(driver):
                # Enter key might not have worked — try clicking a button
                for selector in ["css:button[type='submit']", "css:button.login-btn",
                                 "css:input[type='submit']"]:
                    try:
                        btn = driver.find_element("css selector", selector.split(":", 1)[1])
                        driver.execute_script("arguments[0].click();", btn)
                        logger.info("Clicked login button via JS: %s", selector)
                        break
                    except Exception:
                        continue
                else:
                    # Try any button with login-ish text
                    buttons = driver.find_elements("tag name", "button")
                    for btn in buttons:
                        btn_text = btn.text.strip().lower()
                        if "sign in" in btn_text or "log in" in btn_text or "login" in btn_text:
                            driver.execute_script("arguments[0].click();", btn)
                            logger.info("Clicked login button by text: '%s'", btn.text.strip())
                            break

            # Switch back if in iframe
            if in_iframe:
                driver.switch_to.default_content()

            # Wait for login to complete
            for i in range(20):
                time.sleep(2)
                if self._is_logged_in(driver):
                    logger.info("GHIN login successful after %ds", (i+1)*2)
                    print("GHIN login successful.")
                    return
                # Log what we see for debugging
                try:
                    url = driver.current_url
                    text = driver.find_element("tag name", "body").text[:200]
                    logger.debug("Waiting for GHIN login... (%ds) url=%s text=%s",
                                 (i+1)*2, url, text)
                except Exception:
                    pass

            print(
                "GHIN login may have timed out.\n"
                "Check the browser — there may be additional prompts.\n"
                "Once you're logged in, press Enter here...",
                file=sys.stderr,
            )
            input()

        except Exception as e:
            logger.error("GHIN auto-login failed: %s", e)
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            print(
                f"GHIN auto-login failed: {e}\n"
                "The browser is still open — please log in manually.\n"
                "Once you're logged in, press Enter here...",
                file=sys.stderr,
            )
            input()

    def login(self) -> None:
        """Public method to ensure we're logged into GHIN."""
        driver = self._ensure_browser()
        self._login(driver)

    def get_page_text(self) -> str:
        """Return the current page text (for debugging)."""
        if self._driver:
            return self._driver.find_element("tag name", "body").text
        return ""

    def close(self) -> None:
        if self._driver:
            self._driver.quit()
            self._driver = None
