"""Golden set validation: verify extraction accuracy against known-good data.

This captures the 57 rounds from the initial bulk extraction. Any code change
that alters par, score, nine_played, or course_name for these rounds is a
regression.
"""

import sqlite3
from pathlib import Path

# Golden set: (id, date, course_name, total_score, nine_played)
GOLDEN = [
    (1, "2026-04-05", "Desert Mountain Apache", 47, "front"),
    (2, "2026-04-04", "Desert Mountain No 7", 33, "front"),
    (3, "2026-04-02", "Desert Mountain - Outlaw", 49, "front"),
    (4, "2026-04-01", "Desert Mountain No 7", 39, "front"),
    (5, "2026-03-14", "Desert Mountain - Renegade White", 44, "front"),
    (6, "2026-03-13", "Desert Mountain No 7", 34, "back"),
    (7, "2026-03-12", "Desert Mountain - Outlaw", 51, "front"),
    (8, "2026-03-11", "Desert Mountain No 7", 38, "back"),
    (9, "2026-03-09", "Desert Mountain No 7", 37, "back"),
    (10, "2026-02-25", "Desert Mountain No 7", 35, "front"),
    (11, "2026-02-23", "Desert Mountain No 7", 64, "both"),
    (12, "2026-02-20", "Desert Mountain - Renegade White", 46, "front"),
    (13, "2026-02-15", "Desert Mountain No 7", 41, "back"),
    (14, "2026-01-18", "The Hay", 30, "front"),
    (15, "2026-01-16", "The Hay", 33, "front"),
    (16, "2026-01-08", "The Hay", 26, "front"),
    (17, "2026-01-07", "The Hay", 35, "front"),
    (18, "2025-12-11", "The Hay", 36, "front"),
    (19, "2025-12-10", "The Hay", 37, "front"),
    (20, "2025-12-09", "The Hay", 29, "front"),
    (21, "2025-11-21", "The Hay", 29, "front"),
    (22, "2025-11-20", "The Hay", 32, "front"),
    (23, "2025-11-18", "The Hay", 37, "front"),
    (24, "2025-11-03", "Lake Wildwood Golf Course", 51, "front"),
    (25, "2025-11-02", "Lake Wildwood Golf Course", 51, "front"),
    (26, "2025-11-01", "Lake Wildwood Golf Course", 55, "front"),
    (27, "2025-10-27", "Poppy Hills Golf Course", 54, "front"),
    (28, "2025-10-26", "The Hay", 34, "front"),
    (29, "2025-10-15", "Lake Wildwood Golf Course", 54, "front"),
    (30, "2025-10-11", "Lake Wildwood Golf Course", 51, "front"),
    (31, "2025-10-01", "The Hay", 35, "front"),
    (32, "2025-09-30", "The Hay", 38, "front"),
    (33, "2025-09-29", "Poppy Hills Golf Course", 53, "front"),
    (34, "2025-09-24", "Poppy Hills Golf Course", 54, "front"),
    (35, "2025-09-19", "Del Monte Golf Course", 99, "both"),
    (36, "2025-09-16", "The Links at Spanish Bay", 103, "both"),
    (37, "2025-09-15", "The Hay", 35, "front"),
    (38, "2025-09-07", "Lake Wildwood Golf Course", 51, "front"),
    (39, "2025-08-27", "Lake Wildwood Golf Course", 48, "front"),
    (40, "2025-08-24", "Lake Wildwood Golf Course", 51, "front"),
    (41, "2025-08-20", "Lake Wildwood Golf Course", 46, "front"),
    (42, "2025-08-18", "Lake Wildwood Golf Course", 53, "back"),
    (43, "2025-08-14", "Poppy Hills Golf Course", 50, "front"),
    (44, "2025-08-12", "The Hay", 30, "front"),
    (45, "2025-08-04", "Lake Wildwood Golf Course", 50, "front"),
    (46, "2025-08-01", "Lake Wildwood Golf Course", 47, "front"),
    (47, "2025-07-26", "Lake Wildwood Golf Course", 47, "front"),
    (48, "2025-07-25", "Lake Wildwood Golf Course", 49, "front"),
    (49, "2025-07-24", "Lake Wildwood Golf Course", 48, "back"),
    (50, "2025-07-22", "Lake Wildwood Golf Course", 48, "front"),
    (51, "2025-07-20", "Lake Wildwood Golf Course", 51, "front"),
    (52, "2025-07-19", "Lake Wildwood Golf Course", 103, "both"),
    (53, "2025-07-18", "Lake Wildwood Golf Course", 43, "front"),
    (54, "2025-07-16", "Lake Wildwood Golf Course", 51, "front"),
    (55, "2025-06-21", "Lake Wildwood Golf Course", 51, "front"),
    (56, "2025-06-10", "Lake Wildwood Golf Course", 52, "front"),
    (57, "2025-06-03", "Lake Wildwood Golf Course", 52, "front"),
]


def test_golden_set():
    """Verify current database matches golden set."""
    db_path = Path.home() / ".garmin_ghin" / "scorecards.db"
    assert db_path.exists(), f"Database not found at {db_path}"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT id, date_played, course_name, total_score, nine_played
        FROM scorecards ORDER BY date_played DESC, id DESC
    """).fetchall()
    conn.close()

    assert len(rows) >= len(GOLDEN), (
        f"Expected at least {len(GOLDEN)} rows, got {len(rows)}"
    )

    mismatches = []
    for expected in GOLDEN:
        exp_id, exp_date, exp_course, exp_score, exp_nine = expected
        # Find by ID
        row = next((r for r in rows if r["id"] == exp_id), None)
        if not row:
            mismatches.append(f"ID {exp_id}: not found in database")
            continue

        if row["date_played"] != exp_date:
            mismatches.append(f"ID {exp_id}: date {row['date_played']} != {exp_date}")
        if row["course_name"] != exp_course:
            mismatches.append(f"ID {exp_id}: course '{row['course_name']}' != '{exp_course}'")
        if row["total_score"] != exp_score:
            mismatches.append(f"ID {exp_id}: score {row['total_score']} != {exp_score}")
        if row["nine_played"] != exp_nine:
            mismatches.append(f"ID {exp_id}: nine '{row['nine_played']}' != '{exp_nine}'")

    assert not mismatches, "Golden set mismatches:\n" + "\n".join(mismatches)


if __name__ == "__main__":
    test_golden_set()
    print(f"Golden set OK — all {len(GOLDEN)} rounds match.")
