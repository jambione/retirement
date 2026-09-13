"""OMI at zone grain: the sale band, the rent band, and the zone polygons.

`property/omi.py` already imports the same export at comune grain and answers
"what is a square metre worth in this town". This reads it finer and keeps two
things that one drops:

  * **Loc_min / Loc_max** — the rent band, €/m² per MONTH. Without it there is
    no yield component, and with it misread as €/m²/year every yield in the app
    is out by twelve.
  * **LinkZona** — the zone id, which is what joins a quotation to a polygon
    and therefore what lets a map pin pick its own band.

GETTING THE FILES. Both are free and neither can be fetched unattended:

  * `QI_<n>_<semester>_VALORI_<enc>.csv` and `..._ZONE_....csv` come from
    telematici.agenziaentrate.gov.it behind a Fisconline/Entratel login.
  * The zone GEOMETRY is published through Geopoi as KML/shapefile; the public
    consultation at geopoi.it serves it a comune at a time.

So this module imports files you downloaded, from `data/omi/`. `./retire value
omi --scan` reads whatever is in that folder; DATA_SOURCES.md has the
click-path for each file. Attribution — "Agenzia Entrate – OMI" — is a
condition of use and is printed next to every band this produces.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Iterable

from retirement.modules.property.omi import RESIDENTIAL, normalise, semester_from
from retirement.modules.value import store
from retirement.modules.value.geo import bounds_of

# Column spellings differ between the Fisconline export, the "open data"
# distribution and the community mirrors. Try each in order.
COLUMNS = {
    "comune": ("Comune_descrizione", "Comune_amm", "COMUNE_DESCRIZIONE"),
    "istat": ("Comune_ISTAT", "Comune_Istat", "COMUNE_ISTAT", "Codice_Istat"),
    "prov": ("Prov", "PROV", "Provincia"),
    "regione": ("Regione", "REGIONE", "Regione_descrizione"),
    "zona": ("Zona", "ZONA", "Zona_Descr"),
    "linkzona": ("LinkZona", "Linkzona", "LINKZONA", "Link_Zona"),
    "fascia": ("Fascia", "FASCIA"),
    "tipologia": ("Descr_Tipologia", "DESCR_TIPOLOGIA", "Tipologia"),
    "stato": ("Stato", "STATO", "Stato_conservativo"),
    "compr_min": ("Compr_min", "COMPR_MIN"),
    "compr_max": ("Compr_max", "COMPR_MAX"),
    "loc_min": ("Loc_min", "LOC_MIN"),
    "loc_max": ("Loc_max", "LOC_MAX"),
}


def istat_code(raw: str) -> str:
    """OMI writes `Comune_ISTAT` as the region code followed by the comune code
    — 16074005 is Puglia (16) plus Cisternino (074005), and Alessandria appears
    as 1006003 because region 1 is not zero-padded. Every other source in this
    project keys on the six-digit comune code alone, so the join silently finds
    nothing unless the region is trimmed here.
    """
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if not digits:
        return ""
    return digits[-6:].zfill(6)


def _cell(row: dict[str, str], field: str) -> str:
    for name in COLUMNS[field]:
        if name in row and (row[name] or "").strip():
            return row[name].strip()
    return ""


def _number(raw: str) -> float | None:
    """OMI writes 1.234,56. Some mirrors write 1234.56. Accept both, and treat
    0 as absent — a zero band is a hole in the file, not a free house."""
    cell = (raw or "").strip()
    if not cell:
        return None
    if "," in cell:
        cell = cell.replace(".", "").replace(",", ".")
    try:
        value = float(cell)
    except ValueError:
        return None
    return value if value > 0 else None


def _table(data: bytes, marker: str) -> csv.DictReader:
    """Skip the preamble line the Agenzia puts above the header, and work out
    the delimiter from the body rather than assuming semicolons."""
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines[:12]) if marker in line), None)
    if start is None:
        raise ValueError(
            f"No {marker} column in this file — that is the marker for the export "
            "this parser reads. Check you picked the right one of the pair."
        )
    body = "\n".join(lines[start:])
    delimiter = ";" if body.count(";") > body.count(",") else ","
    return csv.DictReader(io.StringIO(body), delimiter=delimiter)


# ── VALORI ─────────────────────────────────────────────────────────────────
def parse_values(data: bytes, filename: str = "") -> list[dict[str, Any]]:
    semester = semester_from(filename)
    rows: list[dict[str, Any]] = []
    for row in _table(data, "Comune_descrizione"):
        tipologia = _cell(row, "tipologia")
        if tipologia.lower() not in RESIDENTIAL:
            continue                      # garages and shops would drag the band down
        comune = _cell(row, "comune")
        if not comune:
            continue
        low, high = _number(_cell(row, "compr_min")), _number(_cell(row, "compr_max"))
        rent_low, rent_high = _number(_cell(row, "loc_min")), _number(_cell(row, "loc_max"))
        if low is None and rent_low is None:
            continue                      # a row with neither band says nothing
        rows.append({
            "semester": semester,
            "istat": istat_code(_cell(row, "istat")),
            "comune_key": normalise(comune),
            "comune": comune,
            "prov": _cell(row, "prov"),
            # The region is in the file and is what picks the index area for the
            # carry-forward. Without it only the 497 comuni ISPRA registered
            # could be aged, and the other 7,400 would silently stay in 2018.
            "regione": _cell(row, "regione").title(),
            "linkzona": _cell(row, "linkzona"),
            "zona": _cell(row, "zona"),
            "fascia": _cell(row, "fascia"),
            "tipologia": tipologia,
            "stato": _cell(row, "stato") or "NORMALE",
            "compr_min": min(low, high) if low and high else low,
            "compr_max": max(low, high) if low and high else high,
            "loc_min": min(rent_low, rent_high) if rent_low and rent_high else rent_low,
            "loc_max": max(rent_low, rent_high) if rent_low and rent_high else rent_high,
        })
    if not rows:
        raise ValueError("Read the file but found no residential rows in it.")
    return rows


def import_values(conn: sqlite3.Connection, data: bytes, filename: str) -> dict[str, Any]:
    store.migrate(conn)
    rows = parse_values(data, filename)
    conn.executemany(
        """INSERT OR REPLACE INTO omi_zone_values
             (semester, istat, comune_key, comune, prov, regione, linkzona, zona,
              fascia, tipologia, stato, compr_min, compr_max, loc_min, loc_max)
           VALUES (:semester,:istat,:comune_key,:comune,:prov,:regione,:linkzona,:zona,
                   :fascia,:tipologia,:stato,:compr_min,:compr_max,:loc_min,:loc_max)""",
        rows,
    )
    conn.commit()
    summary = {
        "rows": len(rows),
        "comuni": len({r["comune_key"] for r in rows}),
        "zones": len({r["linkzona"] for r in rows if r["linkzona"]}),
        "with_rent": sum(1 for r in rows if r["loc_min"]),
        "semester": rows[0]["semester"],
    }
    store.record_import(conn, "omi_values", Path(filename).name, len(rows),
                        json.dumps(summary))
    return summary


# ── zone geometry (GeoJSON or KML) ─────────────────────────────────────────
KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
ZONE_ID_KEYS = ("linkzona", "link_zona", "LINKZONA", "LinkZona", "cod_zona", "zona")


def _zone_id(props: dict[str, Any]) -> str:
    lowered = {str(k).lower(): v for k, v in props.items()}
    for key in ZONE_ID_KEYS:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _polygons_from_geojson(geometry: dict[str, Any]) -> list[list[list[list[float]]]]:
    kind = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates") or []
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return list(coords)
    return []


def parse_geojson(data: bytes) -> list[dict[str, Any]]:
    payload = json.loads(data.decode("utf-8-sig", errors="replace"))
    features = payload.get("features") if isinstance(payload, dict) else None
    if features is None:
        features = [payload]
    out = []
    for feature in features:
        props = feature.get("properties") or {}
        for rings in _polygons_from_geojson(feature.get("geometry") or {}):
            if not rings:
                continue
            out.append({"linkzona": _zone_id(props), "props": props, "rings": rings})
    return out


def _kml_coords(text: str) -> list[list[float]]:
    points = []
    for chunk in (text or "").replace("\n", " ").split():
        parts = chunk.split(",")
        if len(parts) >= 2:
            try:
                points.append([float(parts[0]), float(parts[1])])   # lng, lat
            except ValueError:
                continue
    return points


def parse_kml(data: bytes) -> list[dict[str, Any]]:
    """Geopoi hands out KML. Zone ids live in ExtendedData/SimpleData or, on
    older exports, in the Placemark name."""
    root = ET.fromstring(data)
    out = []
    for placemark in root.iter():
        if not placemark.tag.endswith("Placemark"):
            continue
        props: dict[str, Any] = {}
        for node in placemark.iter():
            tag = node.tag.split("}")[-1]
            if tag in ("SimpleData", "Data", "value") and node.text:
                key = node.get("name") or node.get("key") or tag
                props[key] = node.text.strip()
            elif tag == "name" and node.text:
                props.setdefault("name", node.text.strip())
        for polygon in placemark.iter():
            if not polygon.tag.endswith("Polygon"):
                continue
            rings: list[list[list[float]]] = []
            outer = polygon.find(".//kml:outerBoundaryIs//kml:coordinates", KML_NS)
            if outer is None:                       # namespace-free exports
                outer = next((n for n in polygon.iter()
                              if n.tag.endswith("coordinates")), None)
            if outer is None or not outer.text:
                continue
            rings.append(_kml_coords(outer.text))
            for inner in polygon.findall(".//kml:innerBoundaryIs//kml:coordinates", KML_NS):
                if inner.text:
                    rings.append(_kml_coords(inner.text))
            zone = _zone_id(props) or re.sub(r"\s+", "", props.get("name", ""))
            out.append({"linkzona": zone, "props": props, "rings": rings})
    return out


def import_geometry(conn: sqlite3.Connection, data: bytes, filename: str,
                    semester: str = "") -> dict[str, Any]:
    """Accepts .geojson/.json, .kml, or a .kmz/.zip holding either."""
    store.migrate(conn)
    name = Path(filename).name.lower()
    semester = semester or semester_from(filename) or store.latest_semester(conn)

    if name.endswith((".kmz", ".zip")):
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            inner = next((n for n in bundle.namelist()
                          if n.lower().endswith((".kml", ".geojson", ".json"))), None)
            if inner is None:
                raise ValueError("That archive holds no .kml or .geojson.")
            data, name = bundle.read(inner), inner.lower()

    shapes = parse_kml(data) if name.endswith(".kml") else parse_geojson(data)
    rows, skipped = [], 0
    for shape in shapes:
        if not shape["rings"] or not shape["rings"][0]:
            skipped += 1
            continue
        if not shape["linkzona"]:
            skipped += 1               # a polygon with no zone id cannot be joined
            continue
        min_lat, min_lng, max_lat, max_lng = bounds_of(shape["rings"])
        props = {str(k).lower(): v for k, v in shape["props"].items()}
        rows.append((
            semester, shape["linkzona"],
            str(props.get("comune_istat") or props.get("istat") or ""),
            str(props.get("comune") or props.get("comune_descrizione") or ""),
            str(props.get("zona") or props.get("zona_descr") or ""),
            min_lat, min_lng, max_lat, max_lng, json.dumps(shape["rings"]),
        ))
    if not rows:
        raise ValueError(
            "No usable polygons in that file. Every shape needs a zone id "
            "(LinkZona) to be joinable to a quotation; "
            f"{skipped} shape(s) had none or had no coordinates."
        )
    conn.executemany(
        """INSERT OR REPLACE INTO omi_zones
             (semester, linkzona, istat, comune, zona,
              min_lat, min_lng, max_lat, max_lng, rings)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    summary = {"zones": len(rows), "skipped": skipped, "semester": semester}
    store.record_import(conn, "omi_zones", Path(filename).name, len(rows),
                        json.dumps(summary))
    return summary


# ── drop folder ────────────────────────────────────────────────────────────
def drop_folder(root: Path) -> Path:
    path = root / "data" / "omi"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scan(conn: sqlite3.Connection, root: Path) -> list[dict[str, Any]]:
    """Import everything sitting in data/omi/. Idempotent: every write is an
    INSERT OR REPLACE keyed on (semester, zone, typology, condition), so
    running it twice changes nothing and re-running after a new semester lands
    adds that semester beside the old one rather than over it."""
    results = []
    for path in sorted(drop_folder(root).iterdir()):
        if path.name.startswith(".") or path.is_dir():
            continue
        data = path.read_bytes()
        try:
            if path.suffix.lower() in (".csv", ".txt"):
                if "_ZONE_" in path.name.upper():
                    results.append({"file": path.name, "skipped":
                                    "ZONE metadata export — geometry comes from Geopoi"})
                    continue
                results.append({"file": path.name, **import_values(conn, data, path.name)})
            elif path.suffix.lower() in (".geojson", ".json", ".kml", ".kmz", ".zip"):
                results.append({"file": path.name, **import_geometry(conn, data, path.name)})
            else:
                results.append({"file": path.name, "skipped": "unrecognised extension"})
        except Exception as exc:                      # one bad file must not stop the rest
            results.append({"file": path.name, "error": str(exc)})
    return results
