"""Cards. One table, one shape, ordered within a column."""
from __future__ import annotations

import sqlite3
from typing import Any

from retirement.core.db import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '',
    column_id  TEXT NOT NULL,
    tag        TEXT NOT NULL DEFAULT '',
    due        TEXT,                     -- YYYY-MM-DD, or NULL
    position   REAL NOT NULL DEFAULT 0,  -- sort key within the column
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    done_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_cards_column ON cards(column_id, position);

-- Cards can point at a property, so "go and see the trullo" and the listing
-- it came from are the same thought rather than two lists to reconcile.
CREATE TABLE IF NOT EXISTS card_links (
    card_id    INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL,
    PRIMARY KEY (card_id, listing_id)
);
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _next_position(conn: sqlite3.Connection, column_id: str) -> float:
    row = conn.execute(
        "SELECT MAX(position) AS p FROM cards WHERE column_id = ?", (column_id,)
    ).fetchone()
    return float((row["p"] or 0)) + 1024.0


def add(
    conn: sqlite3.Connection,
    title: str,
    column_id: str = "someday",
    tag: str = "",
    due: str | None = None,
    notes: str = "",
) -> int:
    now = utcnow()
    cur = conn.execute(
        """INSERT INTO cards (title, notes, column_id, tag, due, position, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (title.strip(), notes.strip(), column_id, tag, due or None,
         _next_position(conn, column_id), now, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def move(conn: sqlite3.Connection, card_id: int, column_id: str, before_id: int | None = None,
         terminal_columns: tuple[str, ...] = ("done",)) -> None:
    """Move a card to a column, optionally above `before_id` in that column.

    Position is a float between neighbours rather than a reindex of the whole
    column: a drag then costs one UPDATE regardless of how long the column is,
    and two people dragging at once cannot renumber each other's cards.
    """
    if before_id:
        row = conn.execute(
            "SELECT position FROM cards WHERE id = ? AND column_id = ?", (before_id, column_id)
        ).fetchone()
        if row:
            target = float(row["position"])
            prev = conn.execute(
                """SELECT MAX(position) AS p FROM cards
                   WHERE column_id = ? AND position < ?""",
                (column_id, target),
            ).fetchone()
            position = ((float(prev["p"]) if prev and prev["p"] is not None else target - 2048.0)
                        + target) / 2
        else:
            position = _next_position(conn, column_id)
    else:
        position = _next_position(conn, column_id)

    done_at = utcnow() if column_id in terminal_columns else None
    conn.execute(
        """UPDATE cards SET column_id = ?, position = ?, updated_at = ?,
             done_at = CASE WHEN ? IS NULL THEN NULL ELSE COALESCE(done_at, ?) END
           WHERE id = ?""",
        (column_id, position, utcnow(), done_at, done_at, card_id),
    )
    conn.commit()


def update(conn: sqlite3.Connection, card_id: int, **fields: Any) -> None:
    allowed = {"title", "notes", "tag", "due"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    clause = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(
        f"UPDATE cards SET {clause}, updated_at = ? WHERE id = ?",
        (*sets.values(), utcnow(), card_id),
    )
    conn.commit()


def delete(conn: sqlite3.Connection, card_id: int) -> None:
    conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    conn.commit()


def by_column(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in conn.execute("SELECT * FROM cards ORDER BY column_id, position"):
        out.setdefault(row["column_id"], []).append(dict(row))
    return out


def counts(conn: sqlite3.Connection, terminal: str = "done") -> dict[str, int]:
    rows = conn.execute(
        "SELECT column_id, COUNT(*) AS n FROM cards GROUP BY column_id"
    ).fetchall()
    per = {row["column_id"]: int(row["n"]) for row in rows}
    return {
        "open": sum(n for col, n in per.items() if col != terminal),
        "done": per.get(terminal, 0),
        **per,
    }
