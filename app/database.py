import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "favorites.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bib TEXT NOT NULL,
        usedata TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS runner_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bib TEXT NOT NULL,
        usedata TEXT,
        official_time TEXT,
        last_point TEXT,
        raw_json TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


def add_favorite(bib: str, usedata: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM favorites WHERE bib = ? AND IFNULL(usedata, '') = IFNULL(?, '')",
        (bib, usedata),
    )
    existing = cur.fetchone()

    if not existing:
        cur.execute(
            "INSERT INTO favorites (bib, usedata) VALUES (?, ?)",
            (bib, usedata),
        )
        conn.commit()

    conn.close()


def delete_favorite(bib: str, usedata: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM favorites WHERE bib = ? AND IFNULL(usedata, '') = IFNULL(?, '')",
        (bib, usedata),
    )
    conn.commit()
    conn.close()


def get_favorites():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT bib, usedata FROM favorites ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_latest_snapshot(bib: str, usedata: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM runner_snapshots
        WHERE bib = ? AND IFNULL(usedata, '') = IFNULL(?, '')
        ORDER BY id DESC
        LIMIT 1
        """,
        (bib, usedata),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def save_snapshot(bib: str, usedata: str, official_time: str, last_point: str, raw_json: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO runner_snapshots (bib, usedata, official_time, last_point, raw_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (bib, usedata, official_time, last_point, raw_json),
    )
    conn.commit()
    conn.close()