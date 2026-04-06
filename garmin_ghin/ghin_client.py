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

    def _click_course_in_list(self, driver, search_terms: list[str]) -> bool:
        """Try to click a course from the visible list using XPath text search.

        Tries each search term, clicks the matching element, and walks up to
        parent/grandparent if the click doesn't navigate away from Select Course.
        Returns True if course was successfully selected.
        """
        for term in search_terms:
            try:
                xpath = f"//*[contains(text(), '{term}')]"
                matches = driver.find_elements("xpath", xpath)
                logger.info("XPath search for '%s': found %d matches", term, len(matches))

                for el in matches:
                    el_text = el.text.strip()
                    tag = el.tag_name
                    logger.debug("  Match: tag=%s text='%s'", tag, el_text[:80])

                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});", el)
                    time.sleep(0.5)

                    # Try clicking the element, then parent, then grandparent
                    for ancestor_label, target in self._element_and_ancestors(el):
                        try:
                            driver.execute_script("arguments[0].click();", target)
                            time.sleep(2)
                            new_text = driver.find_element("tag name", "body").text
                            if "select course" not in new_text.lower():
                                logger.info("Selected course via %s of '%s' (tag=%s)",
                                            ancestor_label, term, tag)
                                return True
                            logger.debug("%s click didn't navigate", ancestor_label)
                        except Exception:
                            pass

            except Exception as e:
                logger.debug("XPath search for '%s' failed: %s", term, e)

        return False

    @staticmethod
    def _find_score_inputs(driver) -> list:
        """Find the writable score input fields on the GHIN scorecard grid.

        Returns a fresh list of input elements (avoids stale references).
        """
        score_inputs = driver.find_elements("css selector",
            "input[type='number'], input[type='tel'], "
            "td input, input[class*='score'], input[aria-label*='score'], "
            "input[aria-label*='Score'], input[name*='score']")

        if not score_inputs:
            score_inputs = driver.find_elements("css selector", "table input")

        if not score_inputs:
            all_inputs = driver.find_elements("css selector", "input")
            score_inputs = [inp for inp in all_inputs
                            if inp.get_attribute("type") in ("text", "number", "tel", "")
                            and inp.is_displayed()
                            and inp.get_attribute("readonly") is None]

        # Filter out read-only / disabled / summary fields
        writable = []
        for inp in score_inputs:
            try:
                if inp.get_attribute("readonly") or inp.get_attribute("disabled"):
                    continue
                writable.append(inp)
            except Exception:
                continue

        return writable

    @staticmethod
    def _fill_input(inp, value):
        """Clear and fill a single input field, handling React SPA quirks."""
        try:
            inp.clear()
            inp.send_keys(str(value))
        except Exception:
            # Fallback: set via JS if send_keys fails
            from selenium.webdriver.remote.webelement import WebElement
            inp.parent.execute_script("""
                var el = arguments[0];
                var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                nativeInputValueSetter.call(el, arguments[1]);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            """, inp, str(value))

    @staticmethod
    def _element_and_ancestors(el):
        """Yield (label, element) for the element and its parent/grandparent."""
        yield ("element", el)
        try:
            parent = el.find_element("xpath", "./..")
            yield ("parent", parent)
        except Exception:
            pass
        try:
            grandparent = el.find_element("xpath", "./../..")
            yield ("grandparent", grandparent)
        except Exception:
            pass

    @staticmethod
    def _get_ghin_list_text(course_name: str) -> str | None:
        """Look up the GHIN course list display text from courses.json.

        Matches against both ghin_name and garmin_name, since the DB course_name
        could be either depending on whether fix-names has been run.
        """
        try:
            from .course_db import _load_db
            db = _load_db()
            for course in db.get("courses", []):
                if (course.get("ghin_name", "") == course_name
                        or course.get("garmin_name", "") == course_name):
                    return course.get("ghin_list_text")
        except Exception:
            pass
        return None

    @staticmethod
    def _is_home_course(course_name: str) -> bool:
        """Check if a course should be scored as Home."""
        name_lower = course_name.lower()
        return any(home in name_lower for home in HOME_COURSES)

    def _best_tee_match(self, tee_options: list[str], garmin_tee: str | None,
                        nine_played: str | None = None) -> str | None:
        """Pick the best tee from GHIN dropdown options based on Garmin tee name.

        Garmin tee names are like "White Tees", "Blue Tees", "Copper/White Tees".
        GHIN options look like "Copper/White  69.5 / 123 / 72" or for 9-hole rounds:
        "Copper/White (Front)  34.9 / 124 / 36" and "Copper/White (Back)  34.9 / 124 / 36".

        nine_played: "front", "back", or "both"/None — used to pick the right
        9-hole tee variant when options include (Front)/(Back).
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
            # Bonus point for matching the correct nine (Front/Back)
            best_score = -1
            best_option = None
            for opt in tee_options:
                opt_lower = opt.lower()
                score = sum(1 for color in garmin_colors if color in opt_lower)

                # Boost/penalize based on front/back match
                if nine_played == "back":
                    if "(back)" in opt_lower:
                        score += 10  # Strong preference
                    elif "(front)" in opt_lower:
                        score -= 10  # Wrong nine
                elif nine_played == "front":
                    if "(front)" in opt_lower:
                        score += 10
                    elif "(back)" in opt_lower:
                        score -= 10

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

        # ── Step 3: Select course from the on-page list ──
        course_name = scorecard.course_name
        logger.info("Selecting course: %s", course_name)
        course_selected = False

        # Load ghin_list_text mapping from courses.json
        ghin_list_text = self._get_ghin_list_text(course_name)
        # Build list of search terms to try, most specific first
        search_terms = []
        if ghin_list_text:
            search_terms.append(ghin_list_text)
        # Also try the last word (e.g. "Apache" from "Desert Mountain Apache")
        parts = course_name.split()
        if len(parts) > 1:
            search_terms.append(parts[-1])
        search_terms.append(course_name)
        logger.info("Course search terms: %s", search_terms)

        try:
            page_text = driver.find_element("tag name", "body").text
            logger.info("Course list page text (800): %s", page_text[:800])

            course_selected = self._click_course_in_list(driver, search_terms)

        except Exception as e:
            logger.error("Error selecting course: %s", e)
            report["issues"].append(f"Error selecting course: {e}")

        if not course_selected:
            report["issues"].append(
                f"Could not find course '{course_name}' in GHIN recently played list. "
                f"Search terms: {search_terms}")
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

        # Extra wait after holes selection — the tee dropdown refreshes
        # (e.g. 9 Holes shows Front/Back tee variants)
        time.sleep(4)

        # ── Step 5: Select tees ──
        logger.info("Reading tee options from dropdown (nine_played=%s, garmin_tee=%s)...",
                     nine, scorecard.garmin_tee_name or scorecard.tee_name)
        tee_options = []
        selected_tee = None
        garmin_tee = scorecard.garmin_tee_name or scorecard.tee_name

        try:
            # Strategy 1: Look for a native <select> element
            selects = driver.find_elements("css selector", "select")
            logger.info("Found %d <select> elements on page", len(selects))
            tee_select = None
            for idx, sel in enumerate(selects):
                options = sel.find_elements("tag name", "option")
                option_texts = [opt.text.strip() for opt in options if opt.text.strip()]
                logger.info("  <select> #%d options: %s", idx, option_texts[:10])
                # Tee selects typically have rating/slope numbers or tee-like words
                has_digits = any(any(c.isdigit() for c in ot) for ot in option_texts)
                has_tee_words = any(
                    any(w in ot.lower() for w in ("tee", "front", "back", "men", "women", "gold", "blue", "white", "red", "copper"))
                    for ot in option_texts
                )
                if has_digits or has_tee_words:
                    tee_select = sel
                    logger.info("  -> Matched as tee selector (digits=%s, tee_words=%s)", has_digits, has_tee_words)
                    break

            if tee_select:
                logger.info("Found native <select> for tees")
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", tee_select)
                time.sleep(0.5)

                options = tee_select.find_elements("tag name", "option")
                for opt in options:
                    opt_text = opt.text.strip()
                    if opt_text and opt_text.lower() not in ("", "select tees", "select"):
                        tee_options.append(opt_text)
                logger.info("Tee options (%d): %s", len(tee_options), tee_options)

                best = self._best_tee_match(tee_options, garmin_tee, nine_played=nine)
                if best:
                    from selenium.webdriver.support.ui import Select
                    select_helper = Select(tee_select)
                    select_helper.select_by_visible_text(best)
                    selected_tee = best
                    logger.info("Selected tee via native select: '%s' (Garmin: %s, nine: %s)",
                                best, garmin_tee, nine)
                    time.sleep(1)
            else:
                logger.info("No native <select> found, trying custom dropdown...")
                # Strategy 2: Look for elements with tee/rating text using targeted selectors
                # rather than iterating all elements
                tee_el = None

                # Try XPath: find elements containing rating/slope pattern text
                try:
                    xpath_candidates = driver.find_elements("xpath",
                        "//*[contains(text(), '/') and string-length(text()) < 80]")
                    logger.info("XPath candidates with '/': %d", len(xpath_candidates))
                    for el in xpath_candidates:
                        try:
                            el_text = el.text.strip()
                            if not el_text or len(el_text) > 80:
                                continue
                            if re.search(r'\d+\.?\d*\s*/\s*\d+', el_text):
                                tag = el.tag_name
                                if tag in ('select', 'option', 'script', 'style', 'head'):
                                    continue
                                tee_el = el
                                logger.info("Found tee display element: tag=%s text='%s'", tag, el_text)
                                break
                        except Exception:
                            continue
                except Exception as xe:
                    logger.debug("XPath tee search failed: %s", xe)

                # Strategy 3: Look for clickable dropdown triggers near tee labels
                if not tee_el:
                    logger.info("Trying to find tee dropdown by label...")
                    try:
                        # Look for "Tee" or "Tees" label and nearby clickable elements
                        labels = driver.find_elements("xpath",
                            "//*[contains(translate(text(), 'TEE', 'tee'), 'tee') and "
                            "string-length(text()) < 30]")
                        logger.info("Found %d tee-label elements", len(labels))
                        for lbl in labels[:5]:
                            try:
                                logger.info("  Tee label: tag=%s text='%s'", lbl.tag_name, lbl.text.strip())
                                # Try clicking the label's parent or sibling dropdown
                                parent = lbl.find_element("xpath", "..")
                                # Look for a clickable element in the parent container
                                clickables = parent.find_elements("css selector",
                                    "select, div[class*='select'], div[class*='dropdown'], "
                                    "div[role='listbox'], div[role='combobox'], "
                                    "[class*='react-select'], [class*='MuiSelect']")
                                if clickables:
                                    tee_el = clickables[0]
                                    logger.info("Found dropdown near tee label: tag=%s", tee_el.tag_name)
                                    break
                                # Also check grandparent
                                grandparent = parent.find_element("xpath", "..")
                                clickables = grandparent.find_elements("css selector",
                                    "select, div[class*='select'], div[class*='dropdown'], "
                                    "div[role='listbox'], div[role='combobox'], "
                                    "[class*='react-select'], [class*='MuiSelect']")
                                if clickables:
                                    tee_el = clickables[0]
                                    logger.info("Found dropdown near tee grandparent: tag=%s", tee_el.tag_name)
                                    break
                            except Exception:
                                continue
                    except Exception as le:
                        logger.debug("Label-based tee search failed: %s", le)

                if tee_el:
                    # Click to open the dropdown
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});", tee_el)
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", tee_el)
                    time.sleep(2)

                    # Read the dropdown options that appeared
                    option_els = driver.find_elements("css selector",
                        "option, li, div[class*='option'], div[role='option'], "
                        "div[class*='menu'] > div, ul > li")
                    for oel in option_els:
                        ot = oel.text.strip()
                        if ot and re.search(r'\d+\.?\d*\s*/\s*\d+', ot):
                            tee_options.append(ot)
                    logger.info("Custom dropdown tee options (%d): %s", len(tee_options), tee_options)

                    if tee_options:
                        best = self._best_tee_match(tee_options, garmin_tee, nine_played=nine)
                        if best:
                            for oel in option_els:
                                if oel.text.strip() == best:
                                    driver.execute_script("arguments[0].click();", oel)
                                    selected_tee = best
                                    logger.info("Selected tee via custom dropdown: '%s'", best)
                                    break
                else:
                    logger.warning("Could not find any tee selector element on page")
                    # Dump page text for debugging
                    try:
                        page_text = driver.find_element("tag name", "body").text
                        logger.info("Page text for tee debugging (600 chars): %s", page_text[:600])
                    except Exception:
                        pass

        except Exception as e:
            report["issues"].append(f"Error selecting tees: {e}")
            logger.error("Error selecting tees: %s", e, exc_info=True)

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

        # ── Step 8: Click "ENTER HOLE-BY-HOLE SCORE" button ──
        # This button is often below the fold, so scroll into view before clicking.
        logger.info("Looking for ENTER HOLE-BY-HOLE SCORE button...")
        hbh_entered = False

        try:
            # Scroll to bottom first to ensure the button is in the DOM
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            all_els = driver.find_elements("css selector",
                "button, a, input[type='submit'], div[role='button'], "
                "span[class*='btn'], div[class*='btn'], div[class*='button']")

            # IMPORTANT: Only match "enter hole" — NOT "hole-by-hole score",
            # because that also matches the HOLE-BY-HOLE SCORE *tab* at the top,
            # which navigates back to course selection.
            for el in all_els:
                el_text = el.text.strip().lower()
                if not el_text:
                    continue
                if "enter" in el_text and "hole" in el_text:
                    # Save text before click (element goes stale after page navigates)
                    saved_text = el_text
                    saved_tag = el.tag_name
                    # Scroll into view and click
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'}); "
                        "arguments[0].click();", el)
                    hbh_entered = True
                    logger.info("Clicked HBH button: '%s' (tag=%s)", saved_text, saved_tag)
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
        scores_entered = {}

        try:
            # Re-find inputs fresh to avoid stale element references
            # (the page may have re-rendered since earlier steps)
            score_inputs = self._find_score_inputs(driver)
            logger.info("Found %d score input fields", len(score_inputs))

            nine = scorecard.nine_played

            # For 9-hole courses (like No 7), GHIN always shows holes 1-9
            # in the grid regardless of whether front or back was played.
            # The scores go into inputs 0-8 either way.
            if nine == "both" and len(score_inputs) >= 18:
                # Full 18 holes
                for i, h in enumerate(scorecard.holes):
                    if h.played and i < len(score_inputs):
                        self._fill_input(score_inputs[i], h.score)
                        scores_entered[h.hole_number] = h.score
                        time.sleep(0.15)
            elif nine in ("front", "back") and len(score_inputs) >= 9:
                # 9-hole round: if GHIN shows 18 inputs (18-hole course, back 9),
                # use offset 9. If GHIN shows only 9 inputs (9-hole course like No 7),
                # always use offset 0 — the grid only has holes 1-9.
                played = scorecard.played_holes
                if len(score_inputs) >= 18 and nine == "back":
                    offset = 9
                else:
                    offset = 0

                for i, h in enumerate(played):
                    idx = offset + i
                    if idx < len(score_inputs):
                        self._fill_input(score_inputs[idx], h.score)
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
