"""Property tables and the upsert logic that makes 'what is new' answerable."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from retirement.core.db import utcnow
from retirement.modules.property.models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    url           TEXT,
    title         TEXT,
    description   TEXT,
    price         REAL,
    currency      TEXT,
    size_sqm      REAL,
    rooms         INTEGER,
    bathrooms     INTEGER,
    lat           REAL,
    lng           REAL,
    municipality  TEXT,
    province      TEXT,
    region        TEXT,
    area_id       TEXT,
    property_type TEXT,
    condition     TEXT,
    photos        INTEGER,
    thumbnail     TEXT,
    raw           TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    first_price   REAL,
    active        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_listings_area ON listings(area_id, active);

CREATE TABLE IF NOT EXISTS price_history (
    listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    seen_at    TEXT NOT NULL,
    price      REAL,
    PRIMARY KEY (listing_id, seen_at)
);

CREATE TABLE IF NOT EXISTS listing_scores (
    listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    run_id     INTEGER,
    scored_at  TEXT NOT NULL,
    total      REAL,
    detail     TEXT,
    rejected   TEXT,
    PRIMARY KEY (listing_id, scored_at)
);
CREATE INDEX IF NOT EXISTS idx_scores_total ON listing_scores(total DESC);

-- Your verdicts, so the spring trip can be planned from the shortlist.
CREATE TABLE IF NOT EXISTS decisions (
    listing_id TEXT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    verdict    TEXT NOT NULL,          -- shortlist | visit | reject
    note       TEXT,
    updated_at TEXT NOT NULL
);
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert(conn: sqlite3.Connection, listing: Listing) -> dict[str, Any]:
    """Insert or refresh. Returns {'is_new': bool, 'price_change': float|None}."""
    now = utcnow()
    row = conn.execute(
        "SELECT price, first_price FROM listings WHERE id = ?", (listing.id,)
    ).fetchone()

    if row is None:
        conn.execute(
            """INSERT INTO listings (
                 id, source, external_id, url, title, description, price, currency,
                 size_sqm, rooms, bathrooms, lat, lng, municipality, province, region,
                 area_id, property_type, condition, photos, thumbnail, raw,
                 first_seen, last_seen, first_price, active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (
                listing.id, listing.source, listing.external_id, listing.url,
                listing.title, listing.description, listing.price, listing.currency,
                listing.size_sqm, listing.rooms, listing.bathrooms, listing.lat,
                listing.lng, listing.municipality, listing.province, listing.region,
                listing.area_id, listing.property_type, listing.condition,
                listing.photos, listing.thumbnail, json.dumps(listing.raw, default=str),
                now, now, listing.price,
            ),
        )
        result = {"is_new": True, "price_change": None}
    else:
        previous = row["price"]
        change = (
            listing.price - previous
            if listing.price is not None and previous is not None and listing.price != previous
            else None
        )
        conn.execute(
            """UPDATE listings SET url=?, title=?, description=?, price=?, size_sqm=?,
                 rooms=?, bathrooms=?, lat=?, lng=?, municipality=?, province=?,
                 region=?, area_id=?, condition=?, photos=?, thumbnail=?, raw=?,
                 last_seen=?, active=1
               WHERE id=?""",
            (
                listing.url, listing.title, listing.description, listing.price,
                listing.size_sqm, listing.rooms, listing.bathrooms, listing.lat,
                listing.lng, listing.municipality, listing.province, listing.region,
                listing.area_id, listing.condition, listing.photos, listing.thumbnail,
                json.dumps(listing.raw, default=str), now, listing.id,
            ),
        )
        result = {"is_new": False, "price_change": change}

    conn.execute(
        "INSERT OR REPLACE INTO price_history (listing_id, seen_at, price) VALUES (?,?,?)",
        (listing.id, now, listing.price),
    )
    return result


def mark_gone(conn: sqlite3.Connection, seen_ids: set[str], area_ids: list[str]) -> int:
    """Anything in a searched area we did not see this run has come off market."""
    if not area_ids:
        return 0
    placeholders = ",".join("?" * len(area_ids))
    rows = conn.execute(
        f"SELECT id FROM listings WHERE active = 1 AND area_id IN ({placeholders})",
        area_ids,
    ).fetchall()
    gone = [r["id"] for r in rows if r["id"] not in seen_ids]
    for listing_id in gone:
        conn.execute("UPDATE listings SET active = 0 WHERE id = ?", (listing_id,))
    return len(gone)


def save_score(
    conn: sqlite3.Connection,
    listing_id: str,
    run_id: int | None,
    result: dict[str, Any] | None,
    rejected: str | None = None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO listing_scores
             (listing_id, run_id, scored_at, total, detail, rejected)
           VALUES (?,?,?,?,?,?)""",
        (
            listing_id,
            run_id,
            utcnow(),
            (result or {}).get("total"),
            json.dumps(result or {}, default=str),
            rejected,
        ),
    )


def set_decision(conn: sqlite3.Connection, listing_id: str, verdict: str, note: str = "") -> None:
    conn.execute(
        """INSERT INTO decisions (listing_id, verdict, note, updated_at) VALUES (?,?,?,?)
           ON CONFLICT(listing_id) DO UPDATE SET
             verdict=excluded.verdict, note=excluded.note, updated_at=excluded.updated_at""",
        (listing_id, verdict, note, utcnow()),
    )
    conn.commit()


def latest_shortlist(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT l.*, s.total, s.detail, s.rejected, d.verdict, d.note AS decision_note
           FROM listings l
           JOIN (SELECT listing_id, MAX(scored_at) AS scored_at FROM listing_scores
                 GROUP BY listing_id) latest ON latest.listing_id = l.id
           JOIN listing_scores s
             ON s.listing_id = latest.listing_id AND s.scored_at = latest.scored_at
           LEFT JOIN decisions d ON d.listing_id = l.id
           WHERE l.active = 1 AND s.rejected IS NULL
           ORDER BY s.total DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["detail"] = json.loads(item["detail"]) if item["detail"] else {}
        out.append(item)
    return out
