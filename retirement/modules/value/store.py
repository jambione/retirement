"""Tables for the value finder, and the reads the scorer makes against them.

Four things live here:

  * **omi_zone_values** — the OMI quotation grid at ZONE grain, with the rent
    band as well as the sale band. The property module already imports OMI at
    comune grain (`property/omi.py`); this is the same file read finer, because
    "cheap for Cisternino" and "cheap for the historic centre of Cisternino"
    are different questions and only the second one is answerable from a map
    pin.
  * **omi_zones** — the zone polygons, so a lat/lon can be resolved to a zone
    without asking anyone. Stored as rings of coordinates plus a bounding box;
    the bbox is the index, the rings are the answer.
  * **comuni / comune_stats** — one row per (comune, metric, year) with the
    source recorded next to the number. Attribution is a licence condition for
    most of these sources, so provenance is a column, not a comment.
  * **value_scores** — the last computed breakdown per listing, so the UI and
    `/api/value/listing/{id}` do not re-derive it on every page load.

Everything is SQLite because the rest of this project is: one file, no server,
and a copy is a backup. Point-in-polygon is done in Python (`geo.py`). If this
ever grows past a few provinces of zones, the migration is PostGIS and the
shape of these tables is deliberately close to what that would need.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from retirement.core.db import utcnow

SCHEMA = """
-- ── OMI: Agenzia delle Entrate, Quotazioni Immobiliari ────────────────────
-- Bands, not appraisals. Sale is €/m²; rent is €/m² per MONTH, which is how
-- the source publishes it and the single easiest thing to get wrong.
CREATE TABLE IF NOT EXISTS omi_zone_values (
    semester   TEXT NOT NULL,              -- '2025-1'
    istat      TEXT NOT NULL DEFAULT '',   -- ISTAT comune code, when the file carries it
    comune_key TEXT NOT NULL,              -- normalised name, the fallback join
    comune     TEXT NOT NULL DEFAULT '',
    prov       TEXT NOT NULL DEFAULT '',
    linkzona   TEXT NOT NULL DEFAULT '',   -- OMI zone id; joins to omi_zones
    zona       TEXT NOT NULL DEFAULT '',
    fascia     TEXT NOT NULL DEFAULT '',   -- centrale / semicentrale / periferica / ...
    tipologia  TEXT NOT NULL DEFAULT '',
    stato      TEXT NOT NULL DEFAULT '',   -- normale / ottimo / scadente
    compr_min  REAL, compr_max REAL,       -- €/m² purchase
    loc_min    REAL, loc_max  REAL,        -- €/m²/month rent
    PRIMARY KEY (semester, comune_key, linkzona, tipologia, stato)
);
CREATE INDEX IF NOT EXISTS idx_ozv_istat ON omi_zone_values(istat, semester);
CREATE INDEX IF NOT EXISTS idx_ozv_link  ON omi_zone_values(linkzona, semester);

CREATE TABLE IF NOT EXISTS omi_zones (
    semester TEXT NOT NULL,
    linkzona TEXT NOT NULL,
    istat    TEXT NOT NULL DEFAULT '',
    comune   TEXT NOT NULL DEFAULT '',
    zona     TEXT NOT NULL DEFAULT '',
    min_lat  REAL NOT NULL, min_lng REAL NOT NULL,
    max_lat  REAL NOT NULL, max_lng REAL NOT NULL,
    rings    TEXT NOT NULL,                -- JSON: [[[lng,lat], ...], ...]
    PRIMARY KEY (semester, linkzona)
);
CREATE INDEX IF NOT EXISTS idx_zones_bbox ON omi_zones(min_lat, max_lat, min_lng, max_lng);

-- ── comuni and their statistics ───────────────────────────────────────────
-- Mergers are the reason `superseded_by` exists: Italy has retired hundreds of
-- comuni since 2014, and a 2019 income file keyed on a code that no longer
-- exists must still find its way onto today's map.
CREATE TABLE IF NOT EXISTS comuni (
    istat        TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    name_key     TEXT NOT NULL,
    prov         TEXT NOT NULL DEFAULT '',
    prov_name    TEXT NOT NULL DEFAULT '',
    region       TEXT NOT NULL DEFAULT '',
    cadastral    TEXT NOT NULL DEFAULT '',  -- Belfiore code
    superseded_by TEXT NOT NULL DEFAULT '',
    lat REAL, lng REAL
);
CREATE INDEX IF NOT EXISTS idx_comuni_key ON comuni(name_key);

-- One number, one year, one named source. Long rather than wide so a new
-- indicator is an INSERT and not a migration.
CREATE TABLE IF NOT EXISTS comune_stats (
    istat      TEXT NOT NULL,
    metric     TEXT NOT NULL,
    year       TEXT NOT NULL DEFAULT '',
    value      REAL,
    source     TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (istat, metric, year)
);

-- ── outputs and caches ────────────────────────────────────────────────────
-- A comune's bounding box, from whichever source knew it. NOT its boundary:
-- boxes overlap and a pin near an edge can land in the wrong one, so a match
-- made this way is reported as approximate and is only ever a fallback for
-- when no OMI zone polygon covers the point.
CREATE TABLE IF NOT EXISTS comune_extent (
    istat   TEXT PRIMARY KEY,
    min_lat REAL NOT NULL, min_lng REAL NOT NULL,
    max_lat REAL NOT NULL, max_lng REAL NOT NULL,
    source  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extent_bbox ON comune_extent(min_lat, max_lat, min_lng, max_lng);

-- A published index, by area and quarter. Not per comune and not per zone:
-- this exists to carry a semi-annual OMI band forward to today, and to say by
-- how much and on whose authority.
CREATE TABLE IF NOT EXISTS market_index (
    series  TEXT NOT NULL,           -- 'hpi_existing', 'hpi_all', 'hpi_new'
    area    TEXT NOT NULL,           -- NUTS: IT, ITC, ITD, ITE, ITFG
    period  TEXT NOT NULL,           -- '2025-Q4'
    value   REAL NOT NULL,
    source  TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (series, area, period)
);

CREATE TABLE IF NOT EXISTS value_scores (
    listing_id TEXT PRIMARY KEY,
    scored_at  TEXT NOT NULL,
    score      REAL,
    breakdown  TEXT NOT NULL,
    ref        TEXT                  -- CIS1001: three letters of the comune, then a run
);

CREATE TABLE IF NOT EXISTS geocode_cache (
    query      TEXT PRIMARY KEY,
    lat REAL, lng REAL,
    istat      TEXT NOT NULL DEFAULT '',
    display    TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL
);

-- Overpass answers are cached per rounded cell, not per listing: two houses on
-- the same street ask the same question and the endpoint is a shared resource.
CREATE TABLE IF NOT EXISTS osm_cache (
    cell       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    dist_km    REAL,
    name       TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (cell, kind)
);

CREATE TABLE IF NOT EXISTS value_imports (
    source     TEXT NOT NULL,
    filename   TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    rows       INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (source, filename)
);
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    # a column added after the first release needs its own idempotent step.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(value_scores)")}
    if "ref" not in columns:
        conn.execute("ALTER TABLE value_scores ADD COLUMN ref TEXT")
    # After the column exists in both cases, never inside SCHEMA: a database
    # made before `ref` would hit the index first and fail the whole script.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_value_ref "
                 "ON value_scores(ref) WHERE ref IS NOT NULL")
    conn.commit()


# ── writes ─────────────────────────────────────────────────────────────────
def record_import(conn: sqlite3.Connection, source: str, filename: str,
                  rows: int, detail: str = "") -> None:
    conn.execute(
        """INSERT OR REPLACE INTO value_imports (source, filename, detail, rows, imported_at)
           VALUES (?,?,?,?,?)""",
        (source, filename, detail, rows, utcnow()),
    )
    conn.commit()


def put_stats(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    """Upsert (istat, metric, year) numbers. `source` is required by the
    schema and by every one of these licences."""
    payload = [
        (r["istat"], r["metric"], str(r.get("year") or ""), r.get("value"),
         r["source"], r.get("note", ""), utcnow())
        for r in rows
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO comune_stats
             (istat, metric, year, value, source, note, fetched_at)
           VALUES (?,?,?,?,?,?,?)""",
        payload,
    )
    conn.commit()
    return len(payload)


def ref_prefix(comune: str = "", province: str = "") -> str:
    """CIS for Cisternino, LOC for Locorotondo — the way an Italian agency
    writes a reference, so it reads as a place and not as a hash."""
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", comune or "")
    letters = "".join(c for c in text if c.isalpha() and not unicodedata.combining(c)).upper()
    letters = re.sub(r"[^A-Z]", "", letters)
    if len(letters) >= 3:
        return letters[:3]
    province = re.sub(r"[^A-Za-z]", "", province or "").upper()
    if len(province) >= 2:
        return (province + "X")[:3]
    return "ITA"


def mint_ref(conn: sqlite3.Connection, comune: str = "", province: str = "") -> str:
    """The next reference for this town. Numbers run from 1001 per prefix, so
    a reference is short enough to read down the phone and specific enough that
    two towns never collide."""
    prefix = ref_prefix(comune, province)
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(ref, ?) AS INTEGER)) AS n FROM value_scores "
        "WHERE ref LIKE ? || '%'",
        (len(prefix) + 1, prefix),
    ).fetchone()
    nxt = int((row["n"] or 1000)) + 1
    return f"{prefix}{nxt}"


def normalise_ref(raw: str) -> str:
    """Accept 'cis 1001', 'CIS-1001', ' CIS1001 ' as the same reference."""
    import re

    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def by_ref(conn: sqlite3.Connection, raw: str) -> dict[str, Any] | None:
    """A reference, a listing id, a portal's own code, or a URL — all four are
    things a person might paste into a box labelled 'look this up'."""
    needle = normalise_ref(raw)
    if not needle:
        return None
    row = conn.execute("SELECT * FROM value_scores WHERE ref = ?", (needle,)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM value_scores WHERE UPPER(REPLACE(listing_id, ':', '')) = ?",
            (needle,),
        ).fetchone()
    if row is None:
        try:
            hit = conn.execute(
                """SELECT id FROM listings
                   WHERE UPPER(external_id) = ? OR UPPER(REPLACE(id, ':', '')) = ?
                      OR UPPER(url) LIKE '%' || ? || '%' LIMIT 1""",
                (needle, needle, needle),
            ).fetchone()
        except sqlite3.OperationalError:
            hit = None
        if hit:
            row = conn.execute("SELECT * FROM value_scores WHERE listing_id = ?",
                               (hit["id"],)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["breakdown"] = json.loads(item["breakdown"]) if item["breakdown"] else {}
    try:
        listing = conn.execute("SELECT * FROM listings WHERE id = ?",
                               (item["listing_id"],)).fetchone()
        item["listing"] = dict(listing) if listing else None
    except sqlite3.OperationalError:
        item["listing"] = None
    return item


def save_score(conn: sqlite3.Connection, listing_id: str, breakdown: dict[str, Any],
               comune: str = "", province: str = "") -> str:
    """Stores the breakdown and returns the property's reference.

    A reference is minted once and then kept: re-scoring a property after new
    data lands must not renumber it, or the number you wrote down stops
    finding it."""
    existing = conn.execute(
        "SELECT ref FROM value_scores WHERE listing_id = ?", (listing_id,)
    ).fetchone()
    ref = (existing["ref"] if existing and existing["ref"]
           else mint_ref(conn, comune, province))
    for _ in range(5):                       # a race on MAX() retries, it does not fail
        try:
            conn.execute(
                """INSERT OR REPLACE INTO value_scores
                     (listing_id, scored_at, score, breakdown, ref) VALUES (?,?,?,?,?)""",
                (listing_id, utcnow(), breakdown.get("score"), json.dumps(breakdown), ref),
            )
            conn.commit()
            return ref
        except sqlite3.IntegrityError:
            ref = mint_ref(conn, comune, province)
    raise RuntimeError("could not mint a unique reference")


# ── reads ──────────────────────────────────────────────────────────────────
def latest_semester(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(semester) AS s FROM omi_zone_values").fetchone()
    return (row["s"] if row else "") or ""


def resolve_comune(conn: sqlite3.Connection, istat: str = "", name_key: str = "") -> dict | None:
    """Follow a merged comune to the one that replaced it, so a stale code in
    an old file still lands on a live town."""
    row = None
    if istat:
        row = conn.execute("SELECT * FROM comuni WHERE istat = ?", (istat,)).fetchone()
    if row is None and name_key:
        row = conn.execute("SELECT * FROM comuni WHERE name_key = ?", (name_key,)).fetchone()
    seen: set[str] = set()
    while row is not None and row["superseded_by"] and row["superseded_by"] not in seen:
        seen.add(row["superseded_by"])
        nxt = conn.execute("SELECT * FROM comuni WHERE istat = ?", (row["superseded_by"],)).fetchone()
        if nxt is None:
            break
        row = nxt
    return dict(row) if row is not None else None


def zone_bands(conn: sqlite3.Connection, linkzona: str, semester: str = "") -> list[dict]:
    semester = semester or latest_semester(conn)
    rows = conn.execute(
        """SELECT * FROM omi_zone_values WHERE linkzona = ? AND semester = ?""",
        (linkzona, semester),
    ).fetchall()
    return [dict(r) for r in rows]


def comune_bands(conn: sqlite3.Connection, istat: str = "", comune_key: str = "",
                 semester: str = "") -> list[dict]:
    """Every zone band for a comune — the fallback when a pin does not land in
    any polygon, and the source of the "this is a comune-wide band" caveat."""
    semester = semester or latest_semester(conn)
    if istat:
        rows = conn.execute(
            "SELECT * FROM omi_zone_values WHERE istat = ? AND semester = ?",
            (istat, semester),
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]
    if comune_key:
        rows = conn.execute(
            "SELECT * FROM omi_zone_values WHERE comune_key = ? AND semester = ?",
            (comune_key, semester),
        ).fetchall()
        return [dict(r) for r in rows]
    return []


def stats_for(conn: sqlite3.Connection, istat: str) -> dict[str, dict[str, Any]]:
    """The newest value of each metric for a comune, with its year and source
    kept alongside — the UI has to print both."""
    rows = conn.execute(
        """SELECT metric, year, value, source, note FROM comune_stats
           WHERE istat = ? ORDER BY metric, year""",
        (istat,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:                      # ordered by year, so the last wins
        out[row["metric"]] = {"value": row["value"], "year": row["year"],
                              "source": row["source"], "note": row["note"]}
    return out


def stats_series(conn: sqlite3.Connection, istat: str, metric: str) -> list[dict]:
    rows = conn.execute(
        """SELECT year, value, source FROM comune_stats
           WHERE istat = ? AND metric = ? AND value IS NOT NULL ORDER BY year""",
        (istat, metric),
    ).fetchall()
    return [dict(r) for r in rows]


def place_summary(conn: sqlite3.Connection, query: str = "",
                  istat: str = "") -> dict[str, Any]:
    """What is on file for one town, before anyone asks a question about it.

    The page calls this as soon as a town is chosen, because "we hold official
    values for Cisternino but no town figures" is something you want to know
    BEFORE you read a score, not inferred afterwards from which rows came back
    empty.
    """
    from retirement.modules.property.omi import normalise

    name_key = normalise(query)
    comune = resolve_comune(conn, istat=istat, name_key=name_key)
    code = (comune or {}).get("istat", istat)

    bands = comune_bands(conn, istat=code or "", comune_key=name_key)
    if not bands and name_key:
        bands = comune_bands(conn, comune_key=name_key)
    stats = stats_for(conn, code) if code else {}

    polygons = 0
    if bands:
        links = tuple({b["linkzona"] for b in bands if b["linkzona"]})
        if links:
            marks = ",".join("?" * len(links))
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM omi_zones WHERE linkzona IN ({marks})", links
            ).fetchone()
            polygons = row["n"] if row else 0

    known_names = [
        r["comune"] for r in conn.execute(
            "SELECT DISTINCT comune FROM omi_zone_values WHERE comune_key LIKE ? || '%' "
            "ORDER BY comune LIMIT 8", (name_key[:4],)
        ).fetchall()
    ] if name_key and not bands else []

    return {
        "query": query,
        "found": bool(bands or stats or comune),
        "name": (comune or {}).get("name") or (bands[0]["comune"] if bands else query),
        "istat": code,
        "prov": (comune or {}).get("prov") or (bands[0]["prov"] if bands else ""),
        "omi": {
            "zones": len({b["linkzona"] for b in bands if b["linkzona"]}) if bands else 0,
            "rows": len(bands),
            "semester": bands[0]["semester"] if bands else "",
            "rent": sum(1 for b in bands if b.get("loc_min")) if bands else 0,
        },
        "stats": {"count": len(stats), "metrics": sorted(stats),
                  "sources": sorted({v["source"] for v in stats.values()})},
        "polygons": polygons,
        "did_you_mean": known_names,
    }


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """What is actually loaded. `doctor` and the UI both need to be able to say
    "no data" rather than show an empty score."""
    def one(sql: str, *args: Any) -> Any:
        try:
            row = conn.execute(sql, args).fetchone()
        except sqlite3.OperationalError:
            return 0
        return (row[0] if row else 0) or 0

    return {
        "semester": latest_semester(conn),
        "omi_rows": one("SELECT COUNT(*) FROM omi_zone_values"),
        "omi_comuni": one("SELECT COUNT(DISTINCT comune_key) FROM omi_zone_values"),
        "omi_with_rent": one("SELECT COUNT(*) FROM omi_zone_values WHERE loc_min IS NOT NULL"),
        "zones": one("SELECT COUNT(*) FROM omi_zones"),
        "comuni": one("SELECT COUNT(*) FROM comuni"),
        "stats": one("SELECT COUNT(*) FROM comune_stats"),
        "stat_metrics": [
            r[0] for r in conn.execute(
                "SELECT DISTINCT metric FROM comune_stats ORDER BY metric"
            ).fetchall()
        ] if one("SELECT COUNT(*) FROM comune_stats") else [],
        "scored": one("SELECT COUNT(*) FROM value_scores"),
        # Which places the figures actually cover. A national OMI import and
        # six provinces of town figures are different kinds of "loaded", and
        # the page has to be able to say which is which.
        "stats_comuni": one("SELECT COUNT(DISTINCT istat) FROM comune_stats"),
        "stats_provs": [
            r[0] for r in conn.execute(
                """SELECT DISTINCT c.prov FROM comuni c
                   JOIN comune_stats s ON s.istat = c.istat
                   WHERE c.prov <> '' ORDER BY c.prov"""
            ).fetchall()
        ] if one("SELECT COUNT(*) FROM comune_stats") else [],
        "omi_provs": one("SELECT COUNT(DISTINCT prov) FROM omi_zone_values"),
        "imports": [
            dict(r) for r in conn.execute(
                "SELECT * FROM value_imports ORDER BY imported_at DESC LIMIT 20"
            ).fetchall()
        ],
    }


def comune_at(conn: sqlite3.Connection, lat: float, lng: float) -> dict | None:
    """The comune whose bounding box contains this point — the smallest one,
    when several do.

    A box is not a boundary. This exists so a pin still finds its town on a
    machine that has ISPRA figures but no OMI zone polygons, and every caller
    marks the result as approximate.
    """
    try:
        rows = conn.execute(
            """SELECT e.*, c.name, c.prov, c.region,
                      (e.max_lat - e.min_lat) * (e.max_lng - e.min_lng) AS area
               FROM comune_extent e LEFT JOIN comuni c ON c.istat = e.istat
               WHERE e.min_lat <= ? AND e.max_lat >= ? AND e.min_lng <= ? AND e.max_lng >= ?
               ORDER BY area LIMIT 1""",
            (lat, lat, lng, lng),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    return dict(rows[0]) if rows else None


def put_extent(conn: sqlite3.Connection, istat: str, extent: Any, source: str) -> bool:
    """`extent` as IdroGEO gives it: [[min_lng, min_lat], [max_lng, max_lat]]."""
    try:
        (min_lng, min_lat), (max_lng, max_lat) = extent
    except (TypeError, ValueError):
        return False
    conn.execute(
        """INSERT OR REPLACE INTO comune_extent
             (istat, min_lat, min_lng, max_lat, max_lng, source) VALUES (?,?,?,?,?,?)""",
        (istat, float(min_lat), float(min_lng), float(max_lat), float(max_lng), source),
    )
    conn.commit()
    return True


# ── a published index, and what it does to an old band ─────────────────────
def put_index(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    payload = [(r["series"], r["area"], r["period"], float(r["value"]), r["source"], utcnow())
               for r in rows]
    conn.executemany(
        """INSERT OR REPLACE INTO market_index (series, area, period, value, source, fetched_at)
           VALUES (?,?,?,?,?,?)""",
        payload,
    )
    conn.commit()
    return len(payload)


def index_at(conn: sqlite3.Connection, series: str, area: str,
             period: str = "") -> dict[str, Any] | None:
    """The index for a quarter, or the newest one when no quarter is named."""
    try:
        if period:
            row = conn.execute(
                "SELECT * FROM market_index WHERE series=? AND area=? AND period=?",
                (series, area, period),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM market_index WHERE series=? AND area=? "
                "ORDER BY period DESC LIMIT 1",
                (series, area),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def semester_quarter(semester: str) -> str:
    """OMI '2018-2' covers July-December, so the quarter it ends in is 2018-Q4.
    Carrying forward from the END of the semester is the conservative choice:
    it credits the band with the whole period it was measured over."""
    try:
        year, half = semester.split("-")
        return f"{int(year)}-Q{4 if half.strip() == '2' else 2}"
    except (ValueError, AttributeError):
        return ""


def index_factor(conn: sqlite3.Connection, area: str, semester: str,
                 series: str = "hpi_existing") -> dict[str, Any] | None:
    """How much an index says prices moved between an OMI semester and now.

    Returns None whenever the honest answer is "do not adjust": no index
    loaded, no reading for that quarter, or a gap too small to bother with.
    """
    base_period = semester_quarter(semester)
    if not base_period:
        return None
    base = index_at(conn, series, area, base_period) or index_at(conn, "hpi_all", area, base_period)
    latest = index_at(conn, series, area) or index_at(conn, "hpi_all", area)
    if not base or not latest or not base["value"]:
        return None
    if latest["period"] <= base_period:
        return None                       # the band is as new as the index
    factor = latest["value"] / base["value"]
    if abs(factor - 1) < 0.01:
        return None                       # under a percent is noise, not a correction
    return {"factor": round(factor, 4), "from": base_period, "to": latest["period"],
            "area": area, "series": series, "source": latest["source"],
            "pct": round((factor - 1) * 100, 1)}
