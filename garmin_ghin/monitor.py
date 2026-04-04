"""Main monitor: polls Garmin for new golf rounds and emails scorecards."""

import json
import logging
import time
from pathlib import Path

from .config import AppConfig, load_config
from .garmin_client import GarminClient
from .notifier import send_scorecard_email

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).resolve().parent.parent / ".processed_rounds.json"


def _load_processed_ids() -> set[int]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def _save_processed_ids(ids: set[int]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(ids)))


def run_once(config: AppConfig) -> int:
    """Check for new rounds, email scorecards. Returns count of new rounds found."""
    garmin = GarminClient(config.garmin)
    processed = _load_processed_ids()

    activities = garmin.get_recent_golf_rounds(days_back=7)
    new_count = 0

    for activity in activities:
        activity_id = activity.get("activityId")
        if not activity_id or activity_id in processed:
            continue

        logger.info("New golf round found: activity %s", activity_id)
        try:
            scorecard = garmin.get_scorecard(activity_id)
            issues = scorecard.validate()
            if issues:
                logger.warning("Scorecard issues: %s", issues)

            send_scorecard_email(config.smtp, scorecard)
            processed.add(activity_id)
            _save_processed_ids(processed)
            new_count += 1
            logger.info("Processed activity %s successfully", activity_id)

        except Exception:
            logger.exception("Failed to process activity %s", activity_id)

    if new_count == 0:
        logger.info("No new golf rounds found")

    return new_count


def run_loop(config: AppConfig) -> None:
    """Run the monitor in a continuous polling loop."""
    interval = config.poll_interval_minutes * 60
    logger.info("Starting monitor loop (polling every %d minutes)", config.poll_interval_minutes)

    while True:
        try:
            run_once(config)
        except Exception:
            logger.exception("Error in monitor loop")
        time.sleep(interval)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    config = load_config()
    run_loop(config)


if __name__ == "__main__":
    main()
