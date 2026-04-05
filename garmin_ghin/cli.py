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
            sc_id = act.get("scorecardId", act.get("activityId", "?"))
            text = act.get("text", act.get("activityName", ""))
            print(f"  [{i+1}] {sc_id}  {text[:60]}")

        # Fetch the most recent
        latest = activities[0]
        sc_id = latest.get("scorecardId", latest.get("activityId"))

        if sc_id:
            print(f"\nFetching scorecard {sc_id}...\n")
            scorecard = client.get_scorecard(sc_id)
            print(scorecard.summary())

            issues = scorecard.validate()
            if not issues:
                print("\nScorecard looks good!")
        else:
            print("\nCould not determine scorecard ID from first result.")
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
