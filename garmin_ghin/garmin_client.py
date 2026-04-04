"""Garmin Connect client for retrieving golf round data."""

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from garminconnect import Garmin

from .config import GarminConfig
from .scorecard import HoleScore, Scorecard

logger = logging.getLogger(__name__)

TOKEN_DIR = Path(__file__).resolve().parent.parent / "token_store"


class GarminClient:
    """Wraps garminconnect library to fetch golf activities."""

    GOLF_ACTIVITY_TYPE = "golf"

    def __init__(self, config: GarminConfig):
        self._config = config
        self._client: Garmin | None = None

    def _ensure_connected(self) -> Garmin:
        if self._client is not None:
            return self._client

        TOKEN_DIR.mkdir(exist_ok=True)
        tokenstore = str(TOKEN_DIR)

        # First try: load cached tokens (no credentials needed)
        try:
            client = Garmin()
            client.login(tokenstore)
            logger.info("Garmin: logged in with cached tokens")
            self._client = client
            return client
        except Exception as e:
            logger.info("Garmin: cached token login failed (%s), trying fresh login", e)

        # Second try: full login with credentials
        try:
            client = Garmin(self._config.email, self._config.password)
            client.login()
            client.garth.dump(tokenstore)
            logger.info("Garmin: fresh login succeeded, tokens cached to %s", tokenstore)
        except Exception as e:
            logger.error("Garmin login failed: %s", e)
            print(
                "\n--- Garmin Login Failed ---\n"
                f"Error: {e}\n\n"
                "Troubleshooting:\n"
                "  1. Verify your GARMIN_EMAIL and GARMIN_PASSWORD in .env\n"
                "  2. Try logging into connect.garmin.com in a browser first\n"
                "  3. If you have MFA/2FA enabled, that may cause issues\n"
                "     (try disabling it temporarily for first login)\n"
                "  4. Garmin may be rate-limiting logins — wait a few minutes\n"
                "  5. Check https://github.com/cyberjunky/python-garminconnect/issues\n"
                "     for known auth issues\n"
                f"  6. Try: pip install --upgrade garminconnect garth\n",
                file=sys.stderr,
            )
            raise

        self._client = client
        return client

    def get_recent_golf_rounds(self, days_back: int = 7) -> list[dict]:
        """Fetch golf activities from the last N days."""
        client = self._ensure_connected()
        start = (date.today() - timedelta(days=days_back)).isoformat()
        end = date.today().isoformat()

        activities = client.get_activities_by_date(start, end, self.GOLF_ACTIVITY_TYPE)
        logger.info("Garmin: found %d golf activities in last %d days", len(activities), days_back)
        return activities

    def get_scorecard(self, activity_id: int) -> Scorecard:
        """Fetch full scorecard details for a golf activity."""
        client = self._ensure_connected()

        # Get the activity summary
        activity = client.get_activity(activity_id)

        # Get detailed per-hole data
        details = client.get_activity_details(activity_id)

        return self._build_scorecard(activity_id, activity, details)

    def _build_scorecard(self, activity_id: int, activity: dict, details: dict) -> Scorecard:
        """Reconstruct a Scorecard from Garmin activity JSON."""
        # Parse date
        start_time = activity.get("startTimeLocal") or activity.get("startTimeGMT", "")
        try:
            date_played = datetime.fromisoformat(start_time.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            date_played = date.today()

        course_name = activity.get("locationName") or activity.get("activityName", "Unknown Course")

        scorecard = Scorecard(
            garmin_activity_id=activity_id,
            course_name=course_name,
            date_played=date_played,
            total_score=activity.get("scorecardSummaryDTO", {}).get("totalScore"),
            total_putts=activity.get("scorecardSummaryDTO", {}).get("totalPutts"),
            score_vs_par=activity.get("scorecardSummaryDTO", {}).get("scoreToPar"),
        )

        # Extract hole-by-hole data from scorecard DTO
        scorecard_holes = activity.get("scorecardSummaryDTO", {}).get("scorecardHoles", [])
        if not scorecard_holes:
            # Try alternate path in detailed data
            scorecard_holes = details.get("scorecardHoles", [])

        for hole_data in scorecard_holes:
            hole = HoleScore(
                hole_number=hole_data.get("holeNumber", 0),
                par=hole_data.get("par", 4),
                score=hole_data.get("strokes", 0),
                putts=hole_data.get("putts"),
                fairway_hit=hole_data.get("fairwayHit"),
                gir=hole_data.get("greenInRegulation"),
                penalties=hole_data.get("penalties", 0),
            )
            scorecard.holes.append(hole)

        # Sort holes by number
        scorecard.holes.sort(key=lambda h: h.hole_number)

        logger.info(
            "Built scorecard: %s on %s, %d holes, score %d",
            course_name,
            date_played,
            scorecard.num_holes,
            scorecard.computed_total,
        )
        return scorecard
