"""CLI for Garmin golf scorecard extraction."""

import argparse
import logging
import sys


def cmd_scorecard(args) -> None:
    """Fetch and display the most recent golf scorecard."""
    from .config import load_config
    from .garmin_client import GarminClient

    config = load_config()
    client = GarminClient(config.garmin)

    try:
        print("Launching Chrome and connecting to Garmin Connect...")
        activities = client.get_recent_golf_rounds(days_back=args.days)

        if not activities:
            print(f"No golf rounds found in the last {args.days} days.")
            sys.exit(0)

        print(f"Found {len(activities)} golf round(s).\n")

        # Show what we found
        for i, act in enumerate(activities[:5]):
            act_id = act.get("activityId")
            text = act.get("text", act.get("activityName", ""))
            href = act.get("href", "")
            print(f"  [{i+1}] ID: {act_id}  {text}  {href}")

        # Fetch the most recent
        latest = activities[0]
        activity_id = latest.get("activityId")

        if activity_id:
            print(f"\nFetching scorecard for activity {activity_id}...\n")
            scorecard = client.get_scorecard(activity_id)
            print(scorecard.summary())

            issues = scorecard.validate()
            if not issues:
                print("\nScorecard looks good!")
        else:
            print("\nCould not determine activity ID from first result.")
            print(f"Raw data: {latest}")

    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Garmin golf scorecard tools")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    subparsers = parser.add_subparsers(dest="command")

    # scorecard subcommand
    sc_parser = subparsers.add_parser("scorecard", help="Fetch and display most recent scorecard")
    sc_parser.add_argument(
        "-n", "--days", type=int, default=30,
        help="Look back this many days for rounds (default: 30)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "scorecard":
        cmd_scorecard(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
