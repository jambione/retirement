"""ISPRA IdroGEO — flood and landslide hazard, per comune, free and open.

This is the one source in the whole set that answers unattended: no login, no
key, no scraping. One GET per comune returns the national hazard mosaics and
risk indicators already reduced to that comune's boundary, plus census
population — which is why the demographic half of the demand score can exist
before any ISTAT file has been downloaded.

    GET https://idrogeo.isprambiente.it/api/pir/comuni/{istat}

Verified against Cisternino (074005) on 2026-09-13: 134 fields, of which the
ones below are the ones the score uses. Licence CC-BY 4.0, attribution
"ISPRA — IdroGEO"; the API and its OpenAPI description live at
https://idrogeo.isprambiente.it/openapi/ .

A note on what P3/P4 mean, because the score leans on them: for floods (idr)
P3 is *elevata* — the high-probability scenario, roughly a 20-50 year return
period — and P2 is *media*, the 100-200 year one. For landslides (fr) the
scale is P1..P4 with P4 *molto elevata*; P3+P4 together is the number the
national reports use, and the one taken here.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

import httpx

from retirement.modules.value import store

BASE = "https://idrogeo.isprambiente.it/api/pir/comuni"
SOURCE = "ISPRA — IdroGEO"

# field in the payload -> (metric we store, what it means)
FIELDS: dict[str, tuple[str, str]] = {
    "aridp3_p":    ("flood_area_p3_pct", "% of comune area, high flood hazard (P3)"),
    "aridp2_p":    ("flood_area_p2_pct", "% of comune area, medium flood hazard (P2)"),
    "popidp3_p":   ("flood_pop_p3_pct", "% of residents in a high flood hazard area"),
    "edidp3_p":    ("flood_build_p3_pct", "% of buildings in a high flood hazard area"),
    "ar_frp3p4p":  ("landslide_area_p3p4_pct", "% of comune area, high/very high landslide hazard"),
    "popfrp3p4p":  ("landslide_pop_p3p4_pct", "% of residents in a high/very high landslide area"),
    "edfrp3p4p":   ("landslide_build_p3p4_pct", "% of buildings in a high/very high landslide area"),
    "pop_res021":  ("population", "resident population, 2021 census"),
    "pop_res011":  ("population_2011", "resident population, 2011 census"),
    "pop_gio_p":   ("pop_young_pct", "% aged under 15"),
    "pop_anz_p":   ("pop_elderly_pct", "% aged 65 and over"),
    "fam_tot":     ("households", "households"),
    "ed_tot":      ("buildings", "buildings"),
    "ar_kmq":      ("area_kmq", "comune area, km²"),
}

YEARS = {"population": "2021", "population_2011": "2011",
         "pop_young_pct": "2021", "pop_elderly_pct": "2021", "households": "2021"}


def fetch_comune(istat: str, timeout: float = 30.0) -> dict[str, Any]:
    """Raw payload for one comune. Raises on anything but a 200 with a body:
    IdroGEO answers an unknown code with a JSON error object, and storing that
    as zeros would read as "no hazard here"."""
    code = str(istat).strip().zfill(6)
    response = httpx.get(f"{BASE}/{code}", timeout=timeout,
                         headers={"Accept": "application/json"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or "nome" not in payload:
        message = (payload or {}).get("message") if isinstance(payload, dict) else None
        raise ValueError(f"IdroGEO has no comune {code}" + (f": {message}" if message else ""))
    return payload


def to_stats(payload: dict[str, Any], istat: str) -> list[dict[str, Any]]:
    rows = []
    for field, (metric, note) in FIELDS.items():
        value = payload.get(field)
        if value is None:
            continue
        rows.append({"istat": istat, "metric": metric, "year": YEARS.get(metric, ""),
                     "value": float(value), "source": SOURCE, "note": note})
    return rows


def fetch_province(cod_prov: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Every comune in a province: code, name, region and bounding box.

    This is the free registry. ISTAT's own comune list is a download behind a
    click-path, but IdroGEO will hand over the same identifying facts a
    province at a time for nothing, which is what lets a listing that says only
    "Locorotondo" find its ISTAT code — and therefore its hazard figures —
    without anyone downloading a file first.
    """
    response = httpx.get(f"{BASE}?cod_prov={str(cod_prov).lstrip('0')}", timeout=timeout,
                         headers={"Accept": "application/json"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"IdroGEO returned no comune list for province {cod_prov}")
    return payload


def ingest_province(conn: sqlite3.Connection, cod_prov: str, with_stats: bool = False,
                    timeout: float = 30.0) -> dict[str, Any]:
    """Register a whole province, and optionally pull each comune's figures.

    Without `with_stats` this is one HTTP call and gives every comune a name, a
    code and a bounding box — enough to resolve a listing to its comune. With
    it, one further call per comune, spaced out, because a public API that
    answers for nothing deserves not to be hammered.
    """
    store.migrate(conn)
    comuni = fetch_province(cod_prov, timeout=timeout)
    registered = 0
    for entry in comuni:
        code = str(entry.get("pro_com") or "").zfill(6)
        if not code or code == "000000":
            continue
        _remember_name(conn, code, entry.get("nome", ""), _breadcrumb(entry))
        store.put_extent(conn, code, entry.get("extent"), SOURCE)
        registered += 1

    result: dict[str, Any] = {"province": cod_prov, "comuni": registered, "source": SOURCE}
    if with_stats:
        codes = [str(e.get("pro_com") or "").zfill(6) for e in comuni]
        stats = ingest(conn, [c for c in codes if c and c != "000000"], timeout=timeout,
                       pace=0.3)
        result["metrics_written"] = stats["metrics_written"]
        result["failed"] = stats["failed"]
    store.record_import(conn, "ispra_province", f"prov {cod_prov}", registered)
    return result


def ingest(conn: sqlite3.Connection, istat_codes: list[str],
           timeout: float = 30.0, pace: float = 0.0) -> dict[str, Any]:
    """One call per comune. The list is short by design — this project follows
    three search areas, not twenty provinces — and each comune is asked for
    once because the answer only changes when ISPRA publishes a new mosaic."""
    store.migrate(conn)
    done, failed, written = [], {}, 0
    for index, istat in enumerate(istat_codes):
        if pace and index:
            time.sleep(pace)
        code = str(istat).strip().zfill(6)
        try:
            payload = fetch_comune(code, timeout=timeout)
        except Exception as exc:
            failed[code] = str(exc)
            continue
        rows = to_stats(payload, code)
        written += store.put_stats(conn, rows)
        _remember_name(conn, code, payload.get("nome", ""),
                       _breadcrumb(payload))
        store.put_extent(conn, code, payload.get("extent"), SOURCE)
        done.append({"istat": code, "nome": payload.get("nome", ""), "metrics": len(rows)})
    store.record_import(conn, "ispra", f"{len(done)} comuni", written)
    return {"comuni": done, "failed": failed, "metrics_written": written, "source": SOURCE}


def _breadcrumb(payload: dict[str, Any]) -> tuple[str, str]:
    """IdroGEO nests the comune under province and region: ('BR', 'Puglia')."""
    crumbs = payload.get("breadcrumb") or []
    prov = next((c.get("name", "") for c in crumbs if c.get("t") == "p"), "")
    region = next((c.get("name", "") for c in crumbs if c.get("t") == "r"), "")
    return prov, region


def _remember_name(conn: sqlite3.Connection, istat: str, name: str,
                   place: tuple[str, str] = ("", "")) -> None:
    """IdroGEO knows the comune's name, so the registry gets seeded here too.

    Without it a comune only ever appears as its six-digit code until somebody
    downloads the ISTAT registry, and a code is a poor thing to read in a
    score. A later registry import overwrites this with the official spelling.
    """
    if not name:
        return
    from retirement.modules.property.omi import normalise

    prov, region = place
    conn.execute(
        """INSERT INTO comuni (istat, name, name_key, prov, region) VALUES (?,?,?,?,?)
           ON CONFLICT(istat) DO UPDATE SET
             name = CASE WHEN comuni.name = '' THEN excluded.name ELSE comuni.name END,
             name_key = CASE WHEN comuni.name_key = '' THEN excluded.name_key
                             ELSE comuni.name_key END,
             prov = CASE WHEN comuni.prov = '' THEN excluded.prov ELSE comuni.prov END,
             region = CASE WHEN comuni.region = '' THEN excluded.region
                           ELSE comuni.region END""",
        (istat, name, normalise(name), prov, region),
    )
    conn.commit()
