"""Garmin Connect client for retrieving golf round data.

Uses browser-based auth (Playwright) to bypass Cloudflare TLS fingerprinting,
then makes API calls using requests with the captured session cookies.
"""

import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from .config import GarminConfig
from .scorecard import HoleScore, Scorecard

logger = logging.getLogger(__name__)

TOKEN_DIR = Path(__file__).resolve().parent.parent / "token_store"
AUTH_FILE = TOKEN_DIR / "browser_auth.json"

GARMIN_API = "https://connect.garmin.com"


class GarminClient:
    """Fetches golf data from Garmin Connect using browser-captured session."""

    GOLF_ACTIVITY_TYPE = "golf"

    def __init__(self, config: GarminConfig):
        self._config = config
        self._session: requests.Session | None = None

    def _ensure_connected(self) -> requests.Session:
        if self._session is not None:
            return self._session

        if not AUTH_FILE.exists():
            print(
                "No saved Garmin session found.\n"
                "Run browser login first:\n\n"
                "  python -m garmin_ghin.browser_login\n",
                file=sys.stderr,
            )
            raise SystemExit(1)

        auth_data = json.loads(AUTH_FILE.read_text())
        session = requests.Session()

        # Load cookies from browser capture
        for cookie in auth_data.get("cookies", []):
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain", ""),
                path=cookie.get("path", "/"),
            )

        # Set headers to look like a browser
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "NK": "NT",
            "Di-Backend": "connectapi.garmin.com",
        })

        # Verify session is valid
        resp = session.get(f"{GARMIN_API}/userprofile-service/usersocial/profile")
        if resp.status_code == 401 or resp.status_code == 403:
            print(
                "Saved session has expired. Re-run browser login:\n\n"
                "  python -m garmin_ghin.browser_login\n",
                file=sys.stderr,
            )
            raise SystemExit(1)
        resp.raise_for_status()

        profile = resp.json()
        display_name = profile.get("displayName", "Unknown")
        logger.info("Garmin: authenticated as %s", display_name)

        self._session = session
        return session

    def get_recent_golf_rounds(self, days_back: int = 30) -> list[dict]:
        """Fetch golf activities from the last N days."""
        session = self._ensure_connected()
        start = (date.today() - timedelta(days=days_back)).isoformat()
        end = date.today().isoformat()

        resp = session.get(
            f"{GARMIN_API}/activitylist-service/activities/search/activities",
            params={
                "activityType": self.GOLF_ACTIVITY_TYPE,
                "startDate": start,
                "endDate": end,
                "limit": 50,
            },
        )
        resp.raise_for_status()
        activities = resp.json()

        logger.info("Garmin: found %d golf activities in last %d days", len(activities), days_back)
        return activities

    def get_scorecard(self, activity_id: int) -> Scorecard:
        """Fetch full scorecard details for a golf activity."""
        session = self._ensure_connected()

        # Get activity summary
        resp = session.get(f"{GARMIN_API}/activity-service/activity/{activity_id}")
        resp.raise_for_status()
        activity = resp.json()

        # Get scorecard data
        resp = session.get(
            f"{GARMIN_API}/gcs-golfcommunity/api/v2/scorecard/activity/{activity_id}/details"
        )
        resp.raise_for_status()
        scorecard_data = resp.json()

        return self._build_scorecard(activity_id, activity, scorecard_data)

    def _build_scorecard(self, activity_id: int, activity: dict, scorecard_data: dict) -> Scorecard:
        """Reconstruct a Scorecard from Garmin API responses."""
        # Parse date
        start_time = activity.get("startTimeLocal") or activity.get("startTimeGMT", "")
        try:
            date_played = datetime.fromisoformat(start_time.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            date_played = date.today()

        course_name = (
            activity.get("locationName")
            or activity.get("activityName", "Unknown Course")
        )

        # Build scorecard from summary
        summary = activity.get("summaryDTO", {})
        scorecard = Scorecard(
            garmin_activity_id=activity_id,
            course_name=course_name,
            date_played=date_played,
            total_score=summary.get("totalScore") or summary.get("scoringTotalScore"),
            total_putts=summary.get("totalPutts"),
            score_vs_par=summary.get("scoreToPar"),
        )

        # Extract hole-by-hole from scorecard detail endpoint
        holes_data = scorecard_data.get("holes", [])
        if not holes_data:
            # Fallback: try the activity's embedded scorecard
            holes_data = (
                activity.get("scorecardSummaryDTO", {}).get("scorecardHoles", [])
            )

        for hole_data in holes_data:
            hole = HoleScore(
                hole_number=hole_data.get("holeNumber", hole_data.get("number", 0)),
                par=hole_data.get("par", 4),
                score=hole_data.get("strokes", hole_data.get("score", 0)),
                putts=hole_data.get("putts"),
                fairway_hit=hole_data.get("fairwayHit"),
                gir=hole_data.get("greenInRegulation", hole_data.get("gir")),
                penalties=hole_data.get("penalties", 0),
            )
            scorecard.holes.append(hole)

        scorecard.holes.sort(key=lambda h: h.hole_number)

        logger.info(
            "Built scorecard: %s on %s, %d holes, score %s",
            course_name,
            date_played,
            scorecard.num_holes,
            scorecard.computed_total if scorecard.holes else "N/A",
        )
        return scorecard
