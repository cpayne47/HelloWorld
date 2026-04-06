"""SQLite database for storing scorecards."""

import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

from .scorecard import HoleScore, Scorecard

logger = logging.getLogger(__name__)

DB_DIR = Path.home() / ".garmin_ghin"
DB_PATH = DB_DIR / "scorecards.db"


def _get_conn() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def rename_course(old_name: str, new_name: str) -> int:
    """Rename a course in all existing scorecards. Returns number of rows updated."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE scorecards SET course_name = ? WHERE course_name = ?",
            (new_name, old_name)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scorecards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            garmin_activity_id TEXT UNIQUE,
            course_name TEXT NOT NULL,
            date_played TEXT NOT NULL,
            tee_name TEXT,
            garmin_tee_name TEXT,
            nine_played TEXT,
            num_holes_played INTEGER,
            total_score INTEGER,
            total_putts INTEGER,
            score_vs_par INTEGER,
            eagles_or_better INTEGER DEFAULT 0,
            birdies INTEGER DEFAULT 0,
            pars INTEGER DEFAULT 0,
            bogeys INTEGER DEFAULT 0,
            double_bogeys_or_worse INTEGER DEFAULT 0,
            fairways_hit TEXT,
            gir_summary TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS hole_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scorecard_id INTEGER NOT NULL REFERENCES scorecards(id) ON DELETE CASCADE,
            hole_number INTEGER NOT NULL,
            par INTEGER NOT NULL,
            score INTEGER,
            putts INTEGER,
            gir INTEGER,
            fairway_hit INTEGER,
            penalties INTEGER DEFAULT 0,
            UNIQUE(scorecard_id, hole_number)
        );

        CREATE INDEX IF NOT EXISTS idx_scorecards_date ON scorecards(date_played);
        CREATE INDEX IF NOT EXISTS idx_scorecards_course ON scorecards(course_name);
    """)
    conn.commit()


def save_scorecard(sc: Scorecard) -> int:
    """Save a scorecard to the database. Returns the database ID.

    If a scorecard with the same garmin_activity_id already exists, it is updated.
    """
    conn = _get_conn()
    try:
        played = sc.played_holes

        # Check if already exists
        existing = conn.execute(
            "SELECT id FROM scorecards WHERE garmin_activity_id = ?",
            (sc.garmin_activity_id,)
        ).fetchone()

        if existing:
            db_id = existing["id"]
            conn.execute("""
                UPDATE scorecards SET
                    course_name = ?, date_played = ?, tee_name = ?,
                    garmin_tee_name = ?, nine_played = ?, num_holes_played = ?,
                    total_score = ?, total_putts = ?, score_vs_par = ?,
                    eagles_or_better = ?, birdies = ?, pars = ?, bogeys = ?,
                    double_bogeys_or_worse = ?, fairways_hit = ?, gir_summary = ?
                WHERE id = ?
            """, (
                sc.course_name, sc.date_played.isoformat(), sc.tee_name,
                sc.garmin_tee_name, sc.nine_played, sc.num_holes_played,
                sc.computed_total if played else None,
                sc.total_putts,
                sc.computed_total - sc.computed_par if played else None,
                sc.eagles_or_better, sc.birdies, sc.pars, sc.bogeys,
                sc.double_bogeys_or_worse, sc.fairways_hit, sc.gir_summary,
                db_id,
            ))
            conn.execute("DELETE FROM hole_scores WHERE scorecard_id = ?", (db_id,))
            logger.info("Updated existing scorecard %d (garmin_id=%s)", db_id, sc.garmin_activity_id)
        else:
            cursor = conn.execute("""
                INSERT INTO scorecards (
                    garmin_activity_id, course_name, date_played, tee_name,
                    garmin_tee_name, nine_played, num_holes_played,
                    total_score, total_putts, score_vs_par,
                    eagles_or_better, birdies, pars, bogeys,
                    double_bogeys_or_worse, fairways_hit, gir_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sc.garmin_activity_id, sc.course_name, sc.date_played.isoformat(),
                sc.tee_name, sc.garmin_tee_name, sc.nine_played, sc.num_holes_played,
                sc.computed_total if played else None,
                sc.total_putts,
                sc.computed_total - sc.computed_par if played else None,
                sc.eagles_or_better, sc.birdies, sc.pars, sc.bogeys,
                sc.double_bogeys_or_worse, sc.fairways_hit, sc.gir_summary,
            ))
            db_id = cursor.lastrowid
            logger.info("Saved new scorecard %d (garmin_id=%s)", db_id, sc.garmin_activity_id)

        # Insert all 18 holes
        for h in sc.holes:
            conn.execute("""
                INSERT INTO hole_scores (
                    scorecard_id, hole_number, par, score, putts,
                    gir, fairway_hit, penalties
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                db_id, h.hole_number, h.par, h.score, h.putts,
                1 if h.gir is True else (0 if h.gir is False else None),
                1 if h.fairway_hit is True else (0 if h.fairway_hit is False else None),
                h.penalties,
            ))

        conn.commit()
        return db_id
    finally:
        conn.close()


def load_scorecard(db_id: int) -> Optional[Scorecard]:
    """Load a scorecard by database ID."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM scorecards WHERE id = ?", (db_id,)).fetchone()
        if not row:
            return None
        return _row_to_scorecard(conn, row)
    finally:
        conn.close()


def list_scorecards(limit: int = 50) -> list[dict]:
    """List recent scorecards (summary only)."""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT id, garmin_activity_id, course_name, date_played, tee_name,
                   garmin_tee_name, nine_played, num_holes_played, total_score,
                   score_vs_par, total_putts, gir_summary
            FROM scorecards ORDER BY date_played DESC, id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def scorecard_exists(garmin_activity_id: str) -> bool:
    """Check if a scorecard with this Garmin activity ID is already stored."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM scorecards WHERE garmin_activity_id = ?",
            (garmin_activity_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def round_exists(date_played: str, course_name: str, total_score: int) -> bool:
    """Check if a round with this date + course + score is already stored.

    Handles multiple rounds on the same day at the same course by matching
    the score as well.
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM scorecards WHERE date_played = ? AND course_name = ? AND total_score = ?",
            (date_played, course_name, total_score),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _row_to_scorecard(conn: sqlite3.Connection, row: sqlite3.Row) -> Scorecard:
    """Convert a database row + hole rows into a Scorecard object."""
    holes_rows = conn.execute(
        "SELECT * FROM hole_scores WHERE scorecard_id = ? ORDER BY hole_number",
        (row["id"],)
    ).fetchall()

    holes = []
    for hr in holes_rows:
        gir = True if hr["gir"] == 1 else (False if hr["gir"] == 0 else None)
        fh = True if hr["fairway_hit"] == 1 else (False if hr["fairway_hit"] == 0 else None)
        holes.append(HoleScore(
            hole_number=hr["hole_number"],
            par=hr["par"],
            score=hr["score"],
            putts=hr["putts"],
            gir=gir,
            fairway_hit=fh,
            penalties=hr["penalties"] or 0,
        ))

    sc = Scorecard(
        garmin_activity_id=row["garmin_activity_id"],
        course_name=row["course_name"],
        date_played=date.fromisoformat(row["date_played"]),
        holes=holes,
        tee_name=row["tee_name"],
        garmin_tee_name=row["garmin_tee_name"],
        eagles_or_better=row["eagles_or_better"] or 0,
        birdies=row["birdies"] or 0,
        pars=row["pars"] or 0,
        bogeys=row["bogeys"] or 0,
        double_bogeys_or_worse=row["double_bogeys_or_worse"] or 0,
        fairways_hit=row["fairways_hit"],
        gir_summary=row["gir_summary"],
    )
    return sc
