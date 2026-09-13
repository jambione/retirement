"""Dated snapshots, not a running balance.

Every import is a new snapshot rather than an update, so the trend survives an
export that is missing an account, and a bad import can be deleted without
taking the history with it.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from retirement.core.db import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS net_worth_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of        TEXT NOT NULL,          -- YYYY-MM-DD, from the export if it says
    source       TEXT NOT NULL,          -- filename it came from
    currency     TEXT NOT NULL DEFAULT 'USD',
    assets       REAL NOT NULL DEFAULT 0,
    liabilities  REAL NOT NULL DEFAULT 0,
    net_worth    REAL NOT NULL DEFAULT 0,
    imported_at  TEXT NOT NULL,
    warnings     TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_snapshots_asof ON net_worth_snapshots(as_of);

CREATE TABLE IF NOT EXISTS accounts (
    snapshot_id INTEGER NOT NULL REFERENCES net_worth_snapshots(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    institution TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'other',
    balance     REAL NOT NULL DEFAULT 0,
    is_liability INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_accounts_snapshot ON accounts(snapshot_id);
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def save_snapshot(conn: sqlite3.Connection, parsed: dict[str, Any]) -> int:
    cur = conn.execute(
        """INSERT INTO net_worth_snapshots
             (as_of, source, currency, assets, liabilities, net_worth, imported_at, warnings)
           VALUES (?,?,?,?,?,?,?,?)""",
        (parsed["as_of"], parsed["source"], parsed.get("currency", "USD"),
         parsed["assets"], parsed["liabilities"], parsed["net_worth"],
         utcnow(), json.dumps(parsed.get("warnings", []))),
    )
    snapshot_id = int(cur.lastrowid)
    conn.executemany(
        """INSERT INTO accounts (snapshot_id, name, institution, category, balance, is_liability)
           VALUES (?,?,?,?,?,?)""",
        [(snapshot_id, a["name"], a.get("institution", ""), a.get("category", "other"),
          a["balance"], int(a.get("is_liability", False)))
         for a in parsed.get("accounts", [])],
    )
    conn.commit()
    return snapshot_id


def latest(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM net_worth_snapshots ORDER BY as_of DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    snapshot = dict(row)
    snapshot["warnings"] = json.loads(snapshot["warnings"] or "[]")
    snapshot["accounts"] = [
        dict(a) for a in conn.execute(
            "SELECT * FROM accounts WHERE snapshot_id = ? ORDER BY is_liability, balance DESC",
            (snapshot["id"],),
        )
    ]
    return snapshot


def previous(conn: sqlite3.Connection, before_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT * FROM net_worth_snapshots WHERE id != ?
           ORDER BY as_of DESC, id DESC LIMIT 1 OFFSET 0""",
        (before_id,),
    ).fetchone()
    return dict(row) if row else None


def history(conn: sqlite3.Connection, limit: int = 36) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, as_of, assets, liabilities, net_worth FROM net_worth_snapshots
           ORDER BY as_of DESC, id DESC LIMIT ?""", (limit,)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def delete_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> None:
    conn.execute("DELETE FROM net_worth_snapshots WHERE id = ?", (snapshot_id,))
    conn.commit()


def by_category(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    buckets: dict[str, float] = {}
    for account in snapshot.get("accounts", []):
        amount = abs(float(account["balance"]))
        if account["is_liability"]:
            continue
        buckets[account["category"]] = buckets.get(account["category"], 0.0) + amount
    total = sum(buckets.values()) or 1.0
    return sorted(
        [{"category": k, "amount": v, "share": v / total} for k, v in buckets.items()],
        key=lambda b: b["amount"], reverse=True,
    )
