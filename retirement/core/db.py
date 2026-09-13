"""SQLite store. One file, no server, easy to back up and to hand to a notebook."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from retirement.core.config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    summary     TEXT
);

-- Client-side guard against burning a small monthly API allowance.
CREATE TABLE IF NOT EXISTS api_usage (
    source  TEXT NOT NULL,
    month   TEXT NOT NULL,          -- YYYY-MM
    calls   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, month)
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open(path: Path, wal: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA journal_mode={'WAL' if wal else 'DELETE'}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connect(path: Path | None = None) -> sqlite3.Connection:
    """WAL where the filesystem supports it, rollback journal where it does not.

    WAL needs a shared-memory file alongside the database, which network and
    bind-mounted filesystems do not provide -- there the first write fails with
    a bare "disk I/O error" that looks like a corrupt database and is not. The
    probe below is the cheapest way to tell the two apart: if creating the
    schema fails under WAL, reopen in DELETE mode (which also resets the
    journal mode persisted in the file) and try once more. A real I/O problem
    fails the second time too, and then the error is worth raising.
    """
    target = path or db_path()
    conn = _open(target, wal=True)
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError:
        conn.close()
        conn = _open(target, wal=False)
        conn.executescript(SCHEMA)
    return conn


def start_run(conn: sqlite3.Connection, module: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs (module, started_at) VALUES (?, ?)", (module, utcnow())
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, summary: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, summary = ? WHERE id = ?",
        (utcnow(), json.dumps(summary, default=str), run_id),
    )
    conn.commit()


def month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def usage_this_month(conn: sqlite3.Connection, source: str) -> int:
    row = conn.execute(
        "SELECT calls FROM api_usage WHERE source = ? AND month = ?",
        (source, month_key()),
    ).fetchone()
    return int(row["calls"]) if row else 0


def record_api_call(conn: sqlite3.Connection, source: str, n: int = 1) -> int:
    conn.execute(
        """INSERT INTO api_usage (source, month, calls) VALUES (?, ?, ?)
           ON CONFLICT(source, month) DO UPDATE SET calls = calls + excluded.calls""",
        (source, month_key(), n),
    )
    conn.commit()
    return usage_this_month(conn, source)
