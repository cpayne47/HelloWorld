"""GHIN (Golf Handicap Information Network) client using undetected-chromedriver.

Logs into ghin.com and provides methods for interacting with the GHIN system,
including posting scores.
"""

import logging
import re
import sys
import time
from pathlib import Path

from .config import GHINConfig
from .scorecard import Scorecard

logger = logging.getLogger(__name__)

GHIN_LOGIN_URL = "https://www.ghin.com/login"
GHIN_HOME_URL = "https://www.ghin.com/"
GHIN_POST_SCORE_URL = "https://www.ghin.com/post-score"

# Courses considered "Home" — all others are "Away"
HOME_COURSES = [
    "desert mountain",
    "poppy hills",
    "lake wildwood",
]


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

    @staticmethod
    def _is_home_course(course_name: str) -> bool:
        """Check if a course should be scored as Home."""
        name_lower = course_name.lower()
        return any(home in name_lower for home in HOME_COURSES)

    def _best_tee_match(self, tee_options: list[str], garmin_tee: str | None) -> str | None:
        """Pick the best tee from GHIN dropdown options based on Garmin tee name.

        Garmin tee names are like "White Tees", "Blue Tees", "Copper/White Tees".
        GHIN options look like "Copper/White  69.5 / 123 / 72" or "Blue  71.2 / 130 / 72".
        """
        if not tee_options:
            return None
        if len(tee_options) == 1:
            return tee_options[0]

        if garmin_tee:
            # Extract color words from Garmin tee name (e.g. "Copper/White Tees" -> ["copper", "white"])
            garmin_colors = [w.lower().rstrip("s") for w in
                            garmin_tee.replace("/", " ").replace("Tees", "").replace("Tee", "").split()
                            if w.lower() not in ("tees", "tee", "men's", "women's")]

            # Score each GHIN option by how many color words match
            best_score = -1
            best_option = None
            for opt in tee_options:
                opt_lower = opt.lower()
                score = sum(1 for color in garmin_colors if color in opt_lower)
                if score > best_score:
                    best_score = score
                    best_option = opt

            if best_score > 0:
                return best_option

        # Fallback: return first option
        return tee_options[0]

    def post_score(self, scorecard: Scorecard, dry_run: bool = True) -> dict:
        """Navigate the GHIN post-score form and fill it out with scorecard data.

        Returns a dict summarizing all selections made for user review.
        If dry_run is True (default), stops before clicking POST SCORE.
        """
        driver = self._ensure_browser()
        self._login(driver)

        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By

        report = {
            "course": scorecard.course_name,
            "date": scorecard.date_played.isoformat(),
            "nine_played": scorecard.nine_played,
            "holes_count": scorecard.num_holes_played,
            "total_score": scorecard.computed_total,
            "garmin_tee": scorecard.garmin_tee_name or scorecard.tee_name,
            "selections": {},
            "issues": [],
        }

        # ── Step 1: Navigate to Post Score page ──
        logger.info("Navigating to Post Score page...")
        driver.get(GHIN_POST_SCORE_URL)
        time.sleep(4)
        self._dismiss_cookie_consent(driver)

        # ── Step 2: Click HOLE-BY-HOLE SCORE tab ──
        logger.info("Clicking HOLE-BY-HOLE SCORE tab...")
        hbh_clicked = False
        for attempt in range(3):
            try:
                links = driver.find_elements("css selector", "a, button, [role='tab'], div[class*='tab']")
                for el in links:
                    txt = el.text.strip().upper()
                    if "HOLE-BY-HOLE" in txt or "HOLE BY HOLE" in txt:
                        driver.execute_script("arguments[0].click();", el)
                        hbh_clicked = True
                        logger.info("Clicked HOLE-BY-HOLE SCORE tab")
                        break
                if hbh_clicked:
                    break
            except Exception as e:
                logger.debug("Attempt %d to find HBH tab: %s", attempt, e)
            time.sleep(2)

        if not hbh_clicked:
            report["issues"].append("Could not find HOLE-BY-HOLE SCORE tab")
            return report

        time.sleep(3)

        # ── Step 3: Select course ──
        logger.info("Selecting course: %s", scorecard.course_name)
        course_selected = False

        # First try: look for the course in the recently played / my courses list
        try:
            # Look for course rows — they typically contain the course name text
            body_text = driver.find_element("tag name", "body").text
            logger.debug("Post score page text (500): %s", body_text[:500])

            # Try clicking on course name text directly
            course_name = scorecard.course_name
            # GHIN uses "Desert Mountain" + "Apache" as separate elements
            # Try to find a clickable element containing the GHIN course name
            all_clickable = driver.find_elements("css selector",
                "a, button, tr, div[class*='course'], div[class*='row'], li")

            # Build search terms from the course name
            # e.g. "Desert Mountain Apache" -> try "Apache", "Desert Mountain"
            name_parts = course_name.split()
            # Try exact match first, then partial
            for el in all_clickable:
                el_text = el.text.strip()
                if not el_text:
                    continue
                el_lower = el_text.lower()
                # Check if this element's text matches our course
                if course_name.lower() in el_lower:
                    driver.execute_script("arguments[0].click();", el)
                    course_selected = True
                    logger.info("Selected course by full name: '%s'", el_text[:60])
                    break

            # If no exact match, try matching the distinguishing part (e.g. "Apache")
            if not course_selected:
                # For "Desert Mountain Apache", the GHIN list shows "Desert Mountain" on left, "Apache" on right
                # Try to find just the sub-name (last word or words after common prefix)
                ghin_parts = course_name.split()
                # Try the last word first (e.g. "Apache", "Seven", "Outlaw")
                for search_term in [ghin_parts[-1]] if len(ghin_parts) > 1 else [course_name]:
                    for el in all_clickable:
                        el_text = el.text.strip()
                        if search_term.lower() in el_text.lower() and len(el_text) < 100:
                            driver.execute_script("arguments[0].click();", el)
                            course_selected = True
                            logger.info("Selected course by partial match '%s': '%s'",
                                        search_term, el_text[:60])
                            break
                    if course_selected:
                        break

            # If still not found, try the search box
            if not course_selected:
                search_inputs = driver.find_elements("css selector",
                    "input[placeholder*='COURSE'], input[placeholder*='course'], input[type='search']")
                if search_inputs:
                    search_input = search_inputs[0]
                    search_input.clear()
                    search_input.send_keys(course_name)
                    logger.info("Typed course name in search box")
                    time.sleep(3)
                    # Click first result
                    results = driver.find_elements("css selector",
                        "div[class*='result'], div[class*='option'], li, tr")
                    for el in results:
                        el_text = el.text.strip()
                        if el_text and any(p.lower() in el_text.lower() for p in name_parts[-2:]):
                            driver.execute_script("arguments[0].click();", el)
                            course_selected = True
                            logger.info("Selected course from search results: '%s'", el_text[:60])
                            break

        except Exception as e:
            logger.error("Error selecting course: %s", e)
            report["issues"].append(f"Error selecting course: {e}")

        if not course_selected:
            report["issues"].append(f"Could not find course '{course_name}' in GHIN list")
            return report

        report["selections"]["course"] = course_name
        time.sleep(3)

        # ── Step 4: Select number of holes ──
        nine = scorecard.nine_played
        target_holes = "18" if nine == "both" else "9"
        logger.info("Selecting %s holes (nine_played=%s)...", target_holes, nine)

        holes_selected = False
        try:
            # Log all visible text on page for debugging
            page_text = driver.find_element("tag name", "body").text
            logger.debug("Page text after course selection (800): %s", page_text[:800])

            # Search broadly — React SPAs often use divs, spans, labels as buttons
            all_els = driver.find_elements("css selector",
                "button, div[role='button'], span, label, a, div[class*='toggle'], "
                "div[class*='button'], div[class*='option'], div[class*='pill']")
            for el in all_els:
                el_text = el.text.strip()
                if not el_text:
                    continue
                # Match "9 Holes" or "18 Holes"
                if el_text == f"{target_holes} Holes" or el_text == f"{target_holes} holes":
                    driver.execute_script("arguments[0].click();", el)
                    logger.info("Selected '%s' (tag=%s)", el_text, el.tag_name)
                    report["selections"]["holes"] = f"{target_holes} Holes"
                    holes_selected = True
                    break

            if not holes_selected:
                # Broader match — look for any element containing the target
                for el in all_els:
                    el_text = el.text.strip()
                    if f"{target_holes} Hole" in el_text:
                        driver.execute_script("arguments[0].click();", el)
                        logger.info("Selected '%s' via partial match (tag=%s)", el_text, el.tag_name)
                        report["selections"]["holes"] = el_text
                        holes_selected = True
                        break

            if not holes_selected:
                report["selections"]["holes"] = f"{target_holes} Holes (NOT FOUND — default assumed)"
                report["issues"].append(f"Could not find {target_holes} Holes button")
                logger.warning("Holes button not found. Visible button-like texts: %s",
                    [el.text.strip() for el in all_els if el.text.strip() and len(el.text.strip()) < 30][:20])
        except Exception as e:
            report["issues"].append(f"Error selecting holes: {e}")

        time.sleep(2)

        # ── Step 5: Select tees ──
        logger.info("Reading tee options from dropdown...")
        tee_options = []
        selected_tee = None

        try:
            # Find the tee dropdown/select
            selects = driver.find_elements("css selector", "select")
            tee_select = None
            for sel in selects:
                # Check if this select has tee-related options
                options = sel.find_elements("tag name", "option")
                for opt in options:
                    opt_text = opt.text.strip()
                    if "/" in opt_text and any(c.isdigit() for c in opt_text):
                        tee_select = sel
                        break
                if tee_select:
                    break

            if tee_select:
                options = tee_select.find_elements("tag name", "option")
                for opt in options:
                    opt_text = opt.text.strip()
                    if opt_text and opt_text != "Select Tees" and opt_text != "":
                        tee_options.append(opt_text)
                logger.info("Found %d tee options: %s", len(tee_options), tee_options)

                # Pick best match
                garmin_tee = scorecard.garmin_tee_name or scorecard.tee_name
                best = self._best_tee_match(tee_options, garmin_tee)

                if best:
                    # Click the matching option
                    for opt in options:
                        if opt.text.strip() == best:
                            opt.click()
                            selected_tee = best
                            logger.info("Selected tee: %s (matched from Garmin tee: %s)",
                                        best, garmin_tee)
                            break
            else:
                # Try clicking a dropdown that opens a custom select
                dropdowns = driver.find_elements("css selector",
                    "div[class*='select'], div[class*='dropdown'], div[class*='tee']")
                for dd in dropdowns:
                    dd_text = dd.text.strip()
                    if "/" in dd_text and any(c.isdigit() for c in dd_text):
                        # This looks like it already shows a tee — click to open
                        driver.execute_script("arguments[0].click();", dd)
                        time.sleep(1)
                        # Read options
                        option_els = driver.find_elements("css selector",
                            "div[class*='option'], li[class*='option'], div[class*='menu'] div")
                        for oel in option_els:
                            ot = oel.text.strip()
                            if ot and "/" in ot:
                                tee_options.append(ot)
                        logger.info("Custom dropdown tee options: %s", tee_options)

                        garmin_tee = scorecard.garmin_tee_name or scorecard.tee_name
                        best = self._best_tee_match(tee_options, garmin_tee)
                        if best:
                            for oel in option_els:
                                if oel.text.strip() == best:
                                    driver.execute_script("arguments[0].click();", oel)
                                    selected_tee = best
                                    break
                        break

        except Exception as e:
            report["issues"].append(f"Error selecting tees: {e}")
            logger.error("Error selecting tees: %s", e)

        report["selections"]["tee_options_available"] = tee_options
        report["selections"]["tee_selected"] = selected_tee or "UNKNOWN"
        if not selected_tee:
            report["issues"].append("Could not select tees — may need manual selection")

        time.sleep(1)

        # ── Step 6: Select Home / Away ──
        is_home = self._is_home_course(scorecard.course_name)
        target_type = "Home" if is_home else "Away"
        logger.info("Selecting score type: %s", target_type)

        try:
            all_els = driver.find_elements("css selector",
                "button, div[role='button'], span, label, a, "
                "div[class*='toggle'], div[class*='button'], div[class*='option'], div[class*='pill']")
            for el in all_els:
                el_text = el.text.strip()
                if el_text == target_type:
                    driver.execute_script("arguments[0].click();", el)
                    logger.info("Selected score type: %s (tag=%s)", target_type, el.tag_name)
                    report["selections"]["score_type"] = target_type
                    break
            else:
                report["selections"]["score_type"] = f"{target_type} (button not found, may be default)"
        except Exception as e:
            report["issues"].append(f"Error selecting Home/Away: {e}")

        time.sleep(1)

        # ── Step 7: Date played ──
        # GHIN defaults to today's date, which is correct for same-day posting.
        # Skip date manipulation to avoid triggering the calendar picker.
        target_date = scorecard.date_played.strftime("%m/%d/%Y")
        report["selections"]["date"] = f"{target_date} (using GHIN default — not modified)"
        logger.info("Date: %s — leaving GHIN default (same-day posting assumed)", target_date)

        time.sleep(1)

        # ── Step 8: Click "Enter Hole-by-Hole Score" button ──
        logger.info("Looking for Enter Hole-by-Hole Score button...")
        hbh_entered = False

        try:
            # Log all visible buttons/links for debugging
            all_els = driver.find_elements("css selector",
                "button, a, input[type='submit'], div[role='button'], "
                "span[class*='btn'], div[class*='btn'], div[class*='button']")
            visible_buttons = [(el.tag_name, el.text.strip()) for el in all_els
                               if el.text.strip() and el.is_displayed()]
            logger.info("Visible clickable elements: %s", visible_buttons[:30])

            # Search for the enter hole-by-hole button with broad matching
            search_terms = ["enter hole", "hole-by-hole", "hole by hole",
                            "enter scores", "enter score", "hole-by-hole score"]
            for el in all_els:
                el_text = el.text.strip().lower()
                if not el_text or not el.is_displayed():
                    continue
                for term in search_terms:
                    if term in el_text:
                        driver.execute_script("arguments[0].click();", el)
                        hbh_entered = True
                        logger.info("Clicked HBH button: '%s' (tag=%s, matched='%s')",
                                    el.text.strip(), el.tag_name, term)
                        break
                if hbh_entered:
                    break

            if not hbh_entered:
                # Try page text to find the exact button label
                page_text = driver.find_element("tag name", "body").text
                logger.info("Page text for HBH search (1000): %s", page_text[:1000])

                # Maybe it's just a generic "Continue" or "Next" button
                for el in all_els:
                    el_text = el.text.strip().lower()
                    if el_text in ("continue", "next", "submit"):
                        driver.execute_script("arguments[0].click();", el)
                        hbh_entered = True
                        logger.info("Clicked fallback button: '%s'", el.text.strip())
                        break

            if not hbh_entered:
                # Check if score input fields are already visible (no button needed)
                score_inputs = driver.find_elements("css selector",
                    "input[type='number'], input[type='tel'], td input, "
                    "input[class*='score'], input[aria-label*='score']")
                if len(score_inputs) >= 9:
                    hbh_entered = True
                    logger.info("Score input fields already visible (%d found)", len(score_inputs))

        except Exception as e:
            report["issues"].append(f"Error entering hole-by-hole: {e}")
            logger.error("Error in step 8: %s", e, exc_info=True)

        if not hbh_entered:
            report["issues"].append("Could not find Enter Hole-by-Hole button or score inputs")
            # Don't return yet — log page state for debugging
            try:
                page_text = driver.find_element("tag name", "body").text
                report["page_debug"] = page_text[:1500]
            except Exception:
                pass
            return report

        time.sleep(4)

        # ── Step 9: Fill in hole-by-hole scores ──
        logger.info("Filling in hole-by-hole scores...")
        played_holes = scorecard.played_holes
        scores_entered = {}

        try:
            # The GHIN scorecard grid has SCORE rows for Front 9 and Back 9
            # Each has input fields for holes 1-9 and 10-18
            # Find all score input fields in the grid

            # Try finding inputs in the SCORE row by looking at table structure
            score_inputs = driver.find_elements("css selector",
                "input[type='number'], input[type='tel'], "
                "td input, input[class*='score'], input[aria-label*='score'], "
                "input[aria-label*='Score'], input[name*='score']")

            if not score_inputs:
                # Broader search
                score_inputs = driver.find_elements("css selector", "table input, .scorecard input")

            if not score_inputs:
                # Even broader — find all text/number inputs on the page
                all_inputs = driver.find_elements("css selector", "input")
                score_inputs = [inp for inp in all_inputs
                                if inp.get_attribute("type") in ("text", "number", "tel", "")
                                and inp.is_displayed()
                                and inp.get_attribute("readonly") is None]

            logger.info("Found %d potential score input fields", len(score_inputs))

            # We expect 18 inputs for 18 holes (or 9 for 9 holes)
            # Filter to just the ones that appear to be in the score entry area
            # The GHIN form typically has Front 9 inputs then Back 9 inputs
            if len(score_inputs) >= 18:
                # Assume first 18 are the hole score inputs (may include summary fields)
                # Check: GHIN has SCORE row with 9 inputs + OUT summary, then 9 inputs + IN + TOTAL
                # The summary cells are usually read-only
                writable_inputs = []
                for inp in score_inputs:
                    readonly = inp.get_attribute("readonly")
                    disabled = inp.get_attribute("disabled")
                    tabindex = inp.get_attribute("tabindex")
                    if readonly or disabled:
                        continue
                    # Check if it looks like a score cell (not a summary cell)
                    writable_inputs.append(inp)

                logger.info("Found %d writable score inputs", len(writable_inputs))
                score_inputs = writable_inputs

            # Map holes to inputs
            # For 18-hole rounds: inputs 0-8 = holes 1-9, inputs 9-17 = holes 10-18
            # For 9-hole front: inputs 0-8 = holes 1-9
            # For 9-hole back: inputs 0-8 = holes 10-18 (on the Back 9 section)
            nine = scorecard.nine_played

            if nine == "both" and len(score_inputs) >= 18:
                # Fill all 18
                for i, h in enumerate(scorecard.holes):
                    if h.played and i < len(score_inputs):
                        inp = score_inputs[i]
                        inp.clear()
                        inp.send_keys(str(h.score))
                        scores_entered[h.hole_number] = h.score
                        time.sleep(0.15)
            elif nine == "front" and len(score_inputs) >= 9:
                for i in range(9):
                    h = scorecard.holes[i]
                    if h.played and i < len(score_inputs):
                        inp = score_inputs[i]
                        inp.clear()
                        inp.send_keys(str(h.score))
                        scores_entered[h.hole_number] = h.score
                        time.sleep(0.15)
            elif nine == "back" and len(score_inputs) >= 9:
                # For back 9: if 18 inputs exist, use indices 9-17
                # If only 9 inputs (9-hole mode), use indices 0-8
                offset = 9 if len(score_inputs) >= 18 else 0
                for i in range(9):
                    h = scorecard.holes[9 + i]  # holes 10-18
                    if h.played and (offset + i) < len(score_inputs):
                        inp = score_inputs[offset + i]
                        inp.clear()
                        inp.send_keys(str(h.score))
                        scores_entered[h.hole_number] = h.score
                        time.sleep(0.15)
            else:
                report["issues"].append(
                    f"Input count mismatch: found {len(score_inputs)} inputs for "
                    f"{nine} nine ({scorecard.num_holes_played} holes)")

            logger.info("Entered scores for %d holes: %s", len(scores_entered), scores_entered)

        except Exception as e:
            report["issues"].append(f"Error filling scores: {e}")
            logger.error("Error filling scores: %s", e, exc_info=True)

        report["selections"]["scores_entered"] = scores_entered
        report["scores_filled"] = len(scores_entered)

        # ── Step 10: Read back the page state for verification ──
        try:
            page_text = driver.find_element("tag name", "body").text
            # Try to extract the OUT/IN/TOTAL summary values
            for marker in ["OUT", "IN", "TOTAL"]:
                if marker in page_text:
                    logger.debug("Page contains '%s' marker", marker)
            report["page_summary"] = page_text[:500]
        except Exception:
            pass

        if dry_run:
            report["status"] = "READY_FOR_REVIEW"
            report["message"] = "Scores filled. Review in browser. POST SCORE button NOT clicked."
        else:
            # Actually click POST SCORE
            try:
                buttons = driver.find_elements("css selector", "button, input[type='submit']")
                for btn in buttons:
                    if "post score" in btn.text.strip().lower():
                        driver.execute_script("arguments[0].click();", btn)
                        report["status"] = "POSTED"
                        report["message"] = "POST SCORE button clicked!"
                        logger.info("Clicked POST SCORE button")
                        break
                else:
                    report["status"] = "POST_BUTTON_NOT_FOUND"
                    report["issues"].append("Could not find POST SCORE button")
            except Exception as e:
                report["status"] = "POST_ERROR"
                report["issues"].append(f"Error clicking POST SCORE: {e}")

        return report

    def close(self) -> None:
        if self._driver:
            self._driver.quit()
            self._driver = None
