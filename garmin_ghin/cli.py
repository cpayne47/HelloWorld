"""CLI for testing Garmin golf scorecard extraction."""

import argparse
import logging
import sys

from .config import load_config
from .garmin_client import GarminClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and display Garmin golf scorecards")
    parser.add_argument(
        "-n", "--days", type=int, default=30,
        help="Look back this many days for rounds (default: 30)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = load_config()
    client = GarminClient(config.garmin)

    print("Connecting to Garmin Connect...")
    activities = client.get_recent_golf_rounds(days_back=args.days)

    if not activities:
        print(f"No golf rounds found in the last {args.days} days.")
        sys.exit(0)

    print(f"Found {len(activities)} golf round(s).\n")

    # Show the most recent round
    latest = activities[0]
    activity_id = latest.get("activityId")
    print(f"Fetching scorecard for activity {activity_id}...\n")

    scorecard = client.get_scorecard(activity_id)
    print(scorecard.summary())

    issues = scorecard.validate()
    if not issues:
        print("\nScorecard looks good!")


if __name__ == "__main__":
    main()
