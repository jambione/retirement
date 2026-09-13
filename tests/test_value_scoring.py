"""The value finder, exercised where it is most likely to lie.

Most of these are about absence: no zone, no rent band, no size, no statistics
at all. A scorer that answers confidently when its inputs are missing is worse
than one that refuses, so the assertions here are mostly "returns None and says
why" rather than "returns a number".
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from retirement.modules.value import geo, store
from retirement.modules.value.ingestion import omi as omi_in
from retirement.modules.value.ingestion import tabular
from retirement.modules.value import scoring
from retirement.modules.value.scoring import bands, components, composite


@pytest.fixture()
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    store.migrate(connection)
    return connection


# A square around Cisternino, and one OMI zone inside it.
SQUARE = [[[17.40, 40.72], [17.46, 40.72], [17.46, 40.76], [17.40, 40.76], [17.40, 40.72]]]


def seed_omi(conn: sqlite3.Connection, *, rent: bool = True, zone: bool = True) -> None:
    if zone:
        min_lat, min_lng, max_lat, max_lng = geo.bounds_of(SQUARE)
        conn.execute(
            """INSERT INTO omi_zones (semester, linkzona, istat, comune, zona,
                                      min_lat, min_lng, max_lat, max_lng, rings)
               VALUES ('2025-1','B1','074005','Cisternino','Centro',?,?,?,?,?)""",
            (min_lat, min_lng, max_lat, max_lng, json.dumps(SQUARE)),
        )
    conn.execute(
        """INSERT INTO omi_zone_values
             (semester, istat, comune_key, comune, prov, linkzona, zona, fascia,
              tipologia, stato, compr_min, compr_max, loc_min, loc_max)
           VALUES ('2025-1','074005','CISTERNINO','Cisternino','BR','B1','Centro',
                   'centrale','abitazioni civili','NORMALE',1200,1800,?,?)""",
        (4.0 if rent else None, 6.0 if rent else None),
    )
    conn.commit()


def seed_stats(conn: sqlite3.Connection) -> None:
    store.put_stats(conn, [
        {"istat": "074005", "metric": "population", "year": "2021", "value": 11231,
         "source": "ISPRA — IdroGEO"},
        {"istat": "074005", "metric": "population_2011", "year": "2011", "value": 11745,
         "source": "ISPRA — IdroGEO"},
        {"istat": "074005", "metric": "pop_young_pct", "year": "2021", "value": 10.5,
         "source": "ISPRA — IdroGEO"},
        {"istat": "074005", "metric": "flood_area_p3_pct", "year": "", "value": 1.477,
         "source": "ISPRA — IdroGEO"},
        {"istat": "074005", "metric": "landslide_area_p3p4_pct", "year": "", "value": 0.266,
         "source": "ISPRA — IdroGEO"},
    ])
    conn.execute("INSERT INTO comuni (istat, name, name_key, prov) "
                 "VALUES ('074005','Cisternino','CISTERNINO','BR')")
    conn.commit()


# ── geometry ───────────────────────────────────────────────────────────────
def test_point_in_polygon_and_holes():
    outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    assert geo.point_in_rings(1, 1, [outer])
    assert not geo.point_in_rings(11, 1, [outer])
    assert not geo.point_in_rings(5, 5, [outer, hole])   # inside the hole is outside


def test_zone_lookup_uses_the_bbox_index(conn):
    seed_omi(conn)
    assert geo.zone_at(conn, 40.74, 17.43)["linkzona"] == "B1"
    assert geo.zone_at(conn, 45.00, 9.00) is None        # Lombardy is not in the square


# ── band selection ─────────────────────────────────────────────────────────
def test_band_falls_back_to_the_comune_and_says_so(conn):
    seed_omi(conn, zone=False)
    band = bands.band_for(conn, 40.74, 17.43, municipality="Cisternino",
                          typology="homes", condition="good")
    assert band["available"] and band["grain"] == "comune"
    assert any("whole comune" in note for note in composite.standing_caveats(band))


def test_band_absent_when_nothing_is_imported(conn):
    band = bands.band_for(conn, 40.74, 17.43, municipality="Cisternino")
    assert band["available"] is False
    assert "No OMI quotation" in band["reason"]


def test_condition_falls_back_to_normale(conn):
    seed_omi(conn)
    band = bands.band_for(conn, 40.74, 17.43, typology="homes", condition="newdevelopment")
    assert band["match"] == "typology, condition→normale"


# ── components ─────────────────────────────────────────────────────────────
def test_price_needs_a_size(conn):
    seed_omi(conn)
    band = bands.band_for(conn, 40.74, 17.43, typology="homes")
    assert components.price(200_000, None, band).score is None
    assert components.price(200_000, 0, band).score is None      # zero, not a division


def test_price_flags_a_listing_under_the_band(conn):
    seed_omi(conn)
    band = bands.band_for(conn, 40.74, 17.43, typology="homes")
    cheap = components.price(100_000, 120, band)                 # €833/m² vs 1200-1800
    assert "below_band" in cheap.flags and cheap.score > 80
    dear = components.price(300_000, 120, band)                  # €2500/m²
    assert "above_band" in dear.flags and dear.score < 20


def test_yield_uses_a_monthly_rent_band(conn):
    seed_omi(conn)
    band = bands.band_for(conn, 40.74, 17.43, typology="homes")
    component = components.gross_yield(180_000, 120, band)
    # 5 €/m²/month * 120 m² * 12 = €7,200 a year on €180,000 = 4.0%
    assert component.value["gross_yield_pct"] == pytest.approx(4.0, abs=0.05)


def test_yield_is_absent_without_a_rent_band(conn):
    seed_omi(conn, rent=False)
    band = bands.band_for(conn, 40.74, 17.43, typology="homes")
    assert components.gross_yield(180_000, 120, band).score is None


def test_listing_rent_overrides_the_band(conn):
    seed_omi(conn)
    band = bands.band_for(conn, 40.74, 17.43, typology="homes")
    component = components.gross_yield(180_000, 120, band, asking_rent_month=900)
    assert component.value["gross_yield_pct"] == pytest.approx(6.0, abs=0.05)
    assert "listing" in component.value["basis"]


def test_liquidity_refuses_to_proxy_population(conn):
    seed_stats(conn)
    stats = store.stats_for(conn, "074005")
    component = components.liquidity(stats)
    assert component.score is None and "NTN" in component.detail


def test_risk_is_absent_not_zero_when_nothing_is_loaded():
    assert components.risk_penalty({}).score is None


def test_risk_penalty_is_capped():
    stats = {
        "flood_area_p3_pct": {"value": 90.0, "source": "ISPRA"},
        "landslide_area_p3p4_pct": {"value": 90.0, "source": "ISPRA"},
        "seismic_zone": {"value": 1.0, "source": "DPC"},
    }
    assert components.risk_penalty(stats, cap=25.0).score == 25.0


# ── composite ──────────────────────────────────────────────────────────────
def test_missing_components_redistribute_their_weight(conn):
    seed_omi(conn)
    seed_stats(conn)
    result = scoring.score(conn, lat=40.74, lng=17.43, price=180_000, size_m2=120,
                           typology="homes", condition="good")
    assert result["score"] is not None
    assert sum(result["weights_used"].values()) == pytest.approx(100.0, abs=0.2)
    assert result["confidence"] < 1.0                     # liquidity and OSM are missing
    assert any("redistributed" in note for note in result["caveats"])


def test_no_data_at_all_scores_nothing(conn):
    result = scoring.score(conn, lat=41.9, lng=12.5, price=200_000, size_m2=100)
    assert result["score"] is None and result["band"] is None
    assert any("Nothing could be scored" in note for note in result["caveats"])


def test_attribution_travels_with_the_score(conn):
    seed_omi(conn)
    seed_stats(conn)
    result = scoring.score(conn, lat=40.74, lng=17.43, price=180_000, size_m2=120,
                           typology="homes")
    assert "Agenzia Entrate — OMI" in result["sources"]
    assert any("OMI semester 2025-1" in note for note in result["caveats"])


def test_merged_comune_resolves_to_its_successor(conn):
    conn.executescript(
        """INSERT INTO comuni (istat,name,name_key,prov,superseded_by)
             VALUES ('022999','Vecchio','VECCHIO','TN','022111');
           INSERT INTO comuni (istat,name,name_key,prov)
             VALUES ('022111','Nuovo','NUOVO','TN');"""
    )
    conn.commit()
    assert store.resolve_comune(conn, istat="022999")["name"] == "Nuovo"


def test_tavolare_provinces_carry_their_own_caveat(conn):
    seed_omi(conn)
    band = bands.band_for(conn, 40.74, 17.43, typology="homes")
    assert any("tavolare" in note for note in composite.standing_caveats(band, "BZ"))
    assert not any("tavolare" in note for note in composite.standing_caveats(band, "BR"))


# ── ingestion ──────────────────────────────────────────────────────────────
VALORI = (
    "Area_territoriale;Regione;Prov;Comune_ISTAT;Comune_descrizione;Fascia;Zona;LinkZona;"
    "Descr_Tipologia;Stato;Compr_min;Compr_max;Loc_min;Loc_max\n"
    "SUD;PUGLIA;BR;074005;CISTERNINO;centrale;Centro;B1;Abitazioni civili;NORMALE;"
    "1.200,00;1.800,00;4,00;6,00\n"
    "SUD;PUGLIA;BR;074005;CISTERNINO;centrale;Centro;B1;Box;NORMALE;500,00;700,00;2,00;3,00\n"
)


def test_valori_import_keeps_rent_and_drops_garages(conn):
    summary = omi_in.import_values(conn, VALORI.encode(), "QI_1_20251_VALORI_utf8.csv")
    assert summary == {"rows": 1, "comuni": 1, "zones": 1, "with_rent": 1, "semester": "2025-1"}
    row = conn.execute("SELECT * FROM omi_zone_values").fetchone()
    assert (row["compr_min"], row["compr_max"]) == (1200.0, 1800.0)
    assert (row["loc_min"], row["loc_max"]) == (4.0, 6.0)        # €/m²/MONTH


def test_geojson_zone_import_needs_a_zone_id(conn):
    payload = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"LinkZona": "B1", "Comune": "Cisternino"},
         "geometry": {"type": "Polygon", "coordinates": SQUARE}},
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Polygon", "coordinates": SQUARE}},
    ]}
    summary = omi_in.import_geometry(conn, json.dumps(payload).encode(), "zones.geojson",
                                     semester="2025-1")
    assert summary["zones"] == 1 and summary["skipped"] == 1


def test_italian_numbers_and_suppressed_cells():
    assert tabular.number("1.234,56") == 1234.56
    assert tabular.number("1234.56") == 1234.56
    assert tabular.number("..") is None                  # suppressed, not zero
    assert tabular.number("") is None
    assert tabular.istat_code(74005) == "074005"         # the leading zero a spreadsheet ate


# ── Ask B sees the same data, and the same holes ───────────────────────────
def test_ask_b_value_scope_names_its_sources_and_its_gaps(conn):
    from retirement.core import context

    seed_omi(conn)
    seed_stats(conn)
    built = context.build("value", conn, {})

    assert built["scope"] == "value" and built["chars"] > 200
    body = built["context"]
    # what is loaded, with the source against each number
    assert "WHAT OFFICIAL DATA IS LOADED" in body
    assert "ISPRA — IdroGEO" in body and "Agenzia Entrate — OMI" in body
    assert "CISTERNINO" in body
    assert "per m² per MONTH" in body          # the units B must not get wrong
    # and the instruction that stops a model reading absence as a bad score
    assert "'no data'" in body and "not a zero" in body


def test_ask_b_value_scope_says_when_omi_is_missing(conn):
    from retirement.core import context

    seed_stats(conn)                            # stats but no OMI at all
    body = context.build("value", conn, {})["context"]
    assert "No OMI quotation is loaded for this comune" in body
    assert "no price or yield component" in body


def test_pin_finds_its_comune_by_bbox_and_admits_the_approximation(conn):
    seed_stats(conn)
    store.put_extent(conn, "074005", [[17.349, 40.713], [17.497, 40.799]], "ISPRA — IdroGEO")
    result = scoring.score(conn, lat=40.7430, lng=17.4260, price=180_000, size_m2=120)
    assert result["comune"]["name"] == "Cisternino"
    assert any("bounding box" in note for note in result["caveats"])


def test_bbox_fallback_prefers_the_smaller_box(conn):
    store.put_extent(conn, "074001", [[17.0, 40.0], [18.0, 41.0]], "test")   # province-sized
    store.put_extent(conn, "074005", [[17.3, 40.7], [17.5, 40.8]], "test")   # the town
    assert store.comune_at(conn, 40.74, 17.42)["istat"] == "074005"


def test_province_registry_gives_names_and_boxes_without_a_download(conn, monkeypatch):
    """One free call registers a province, which is what lets a listing that
    says only 'Cisternino' find its ISTAT code and therefore its figures."""
    from retirement.modules.value.ingestion import ispra

    monkeypatch.setattr(ispra, "fetch_province", lambda prov, timeout=30.0: [
        {"pro_com": 74005, "nome": "Cisternino",
         "extent": [[17.349, 40.713], [17.497, 40.799]],
         "breadcrumb": [{"name": "Puglia", "t": "r"}, {"name": "BR", "t": "p"}]},
        {"pro_com": 74001, "nome": "Brindisi",
         "extent": [[17.731, 40.478], [18.045, 40.708]],
         "breadcrumb": [{"name": "Puglia", "t": "r"}, {"name": "BR", "t": "p"}]},
    ])
    result = ispra.ingest_province(conn, "074")

    assert result["comuni"] == 2
    found = store.resolve_comune(conn, name_key="CISTERNINO")
    assert found["istat"] == "074005" and found["prov"] == "BR" and found["region"] == "Puglia"
    assert store.comune_at(conn, 40.743, 17.426)["istat"] == "074005"


# ── property references ────────────────────────────────────────────────────
def test_reference_reads_as_a_place_and_survives_rescoring(conn):
    seed_omi(conn)
    seed_stats(conn)
    first = scoring.score(conn, lat=40.74, lng=17.43, price=180_000, size_m2=120,
                          typology="homes", persist=True)
    assert first["ref"].startswith("CIS") and first["ref"][3:].isdigit()

    # the same property scored again keeps its number, even as the answer moves
    again = scoring.score(conn, lat=40.74, lng=17.43, price=180_000, size_m2=120,
                          typology="homes", persist=True)
    assert again["ref"] == first["ref"]

    # a different property in the same town gets the next one
    other = scoring.score(conn, lat=40.74, lng=17.43, price=260_000, size_m2=120,
                          typology="homes", persist=True)
    assert other["ref"] != first["ref"] and other["ref"][:3] == "CIS"


def test_lookup_is_forgiving_about_how_you_type_it(conn):
    seed_omi(conn)
    seed_stats(conn)
    ref = scoring.score(conn, lat=40.74, lng=17.43, price=180_000, size_m2=120,
                        typology="homes", persist=True)["ref"]
    for typed in (ref, ref.lower(), f"{ref[:3]}-{ref[3:]}", f" {ref[:3]} {ref[3:]} "):
        assert store.by_ref(conn, typed)["ref"] == ref
    assert store.by_ref(conn, "ZZZ9999") is None


def test_reference_falls_back_to_the_province_then_to_italy(conn):
    assert store.ref_prefix("Cisternino") == "CIS"
    assert store.ref_prefix("Bì", "BR") == "BRX"     # too short a name
    assert store.ref_prefix("", "") == "ITA"


def test_a_listing_keeps_one_reference_across_runs(conn):
    from retirement.modules.property.models import Listing

    seed_omi(conn)
    seed_stats(conn)
    listing = Listing(id="idealista:99", source="idealista", external_id="99",
                      price=180_000, size_sqm=120, lat=40.74, lng=17.43,
                      municipality="Cisternino", province="BR", property_type="homes")
    one = scoring.score_listing(conn, listing)["ref"]
    two = scoring.score_listing(conn, listing)["ref"]
    assert one == two
    # and the portal's own code finds it too
    assert store.by_ref(conn, "99") is None or store.by_ref(conn, "99")["ref"] == one


# ── carrying a stale band forward ──────────────────────────────────────────
def seed_index(conn: sqlite3.Connection, base: float = 100.0, now: float = 111.3) -> None:
    store.put_index(conn, [
        {"series": "hpi_existing", "area": "ITFG", "period": "2018-Q4",
         "value": base, "source": "ISTAT — IPAB"},
        {"series": "hpi_existing", "area": "ITFG", "period": "2025-Q4",
         "value": now, "source": "ISTAT — IPAB"},
    ])


def test_semester_maps_to_the_quarter_it_ends_in():
    assert store.semester_quarter("2018-2") == "2018-Q4"
    assert store.semester_quarter("2026-1") == "2026-Q2"
    assert store.semester_quarter("nonsense") == ""


def test_index_factor_declines_to_adjust_when_it_should(conn):
    seed_index(conn)
    assert store.index_factor(conn, "ITFG", "") is None            # no semester
    assert store.index_factor(conn, "ITC", "2018-2") is None       # no index for that area
    store.put_index(conn, [
        {"series": "hpi_existing", "area": "ITE", "period": "2018-Q4", "value": 100.0,
         "source": "ISTAT — IPAB"},
        {"series": "hpi_existing", "area": "ITE", "period": "2019-Q2", "value": 100.4,
         "source": "ISTAT — IPAB"},
    ])
    assert store.index_factor(conn, "ITE", "2018-2") is None       # under 1% is noise


def test_band_is_carried_forward_and_keeps_the_original(conn):
    seed_omi(conn)                       # 2025-1 band, 1200-1800, rent 4.0-6.0
    conn.execute("UPDATE omi_zone_values SET semester = '2018-2'")
    store.put_index(conn, [
        {"series": "hpi_existing", "area": "ITFG", "period": "2018-Q4", "value": 100.0,
         "source": "ISTAT — IPAB"},
        {"series": "hpi_existing", "area": "ITFG", "period": "2025-Q4", "value": 111.3,
         "source": "ISTAT — IPAB"},
    ])
    conn.commit()

    band = bands.band_for(conn, None, None, municipality="Cisternino",
                          typology="homes", region="Puglia")
    assert band["adjusted"]["pct"] == pytest.approx(11.3, abs=0.1)
    assert band["as_published"]["sale_min"] == 1200
    assert band["sale_min"] == round(1200 * 1.113)
    # rents are NOT carried forward: IPAB measures purchases
    assert band["rent_min"] == 4.0 and band["rent_max"] == 6.0
    assert "NOT carried forward" in components.gross_yield(180_000, 120, band).detail


def test_the_adjustment_is_in_the_caveats_not_just_the_arithmetic(conn):
    seed_omi(conn)
    conn.execute("UPDATE omi_zone_values SET semester = '2018-2'")
    seed_index(conn)
    seed_stats(conn)
    conn.execute("UPDATE comuni SET region = 'Puglia' WHERE istat = '074005'")
    conn.commit()
    result = scoring.score(conn, municipality="Cisternino", price=180_000, size_m2=120,
                           typology="homes")
    assert any("carried forward" in note for note in result["caveats"])
    assert any("current OMI semester removes the adjustment" in note for note in result["caveats"])


def test_region_maps_to_the_index_area():
    from retirement.modules.value.ingestion.istat_hpi import area_for

    assert area_for("Puglia") == "ITFG" and area_for("Abruzzo") == "ITFG"
    assert area_for("Lombardia") == "ITC" and area_for("Veneto") == "ITD"
    assert area_for("Toscana") == "ITE"
    assert area_for("") == "IT"            # national is a fallback, not a wrong answer


def test_index_parser_keeps_levels_and_drops_changes():
    from retirement.modules.value.ingestion import istat_hpi

    csv_text = (
        "DATAFLOW,FREQ,REF_AREA,DATA_TYPE,MEASURE,PURCHASES_DWELLINGS,TIME_PERIOD,OBS_VALUE\n"
        "x,Q,ITFG,59,4,EXST_DW,2025-Q4,107.1\n"      # the level we want
        "x,Q,ITFG,59,7,EXST_DW,2025-Q4,3.0\n"        # a year-on-year change
        "x,Q,ITFG,59,4,NEW_DW,2025-Q4,120.4\n"
    )
    rows = istat_hpi.parse(csv_text)
    assert len(rows) == 2
    assert {r["series"] for r in rows} == {"hpi_existing", "hpi_new"}
    assert rows[0]["value"] == 107.1


# ── the towns view: answers before any listing exists ──────────────────────
def seed_town(conn: sqlite3.Connection, name: str, key: str, istat: str,
              low: float, high: float, prov: str = "BR", zone: str = "B1") -> None:
    conn.execute(
        """INSERT OR REPLACE INTO omi_zone_values
             (semester, istat, comune_key, comune, prov, regione, linkzona, zona, fascia,
              tipologia, stato, compr_min, compr_max, loc_min, loc_max)
           VALUES ('2018-2',?,?,?,?,'Puglia',?,?,'centrale',
                   'abitazioni civili','NORMALE',?,?,4.0,6.0)""",
        (istat, key, name, prov, f"{prov}{zone}", zone, low, high),
    )
    conn.commit()


def test_town_rows_rank_by_price_and_carry_the_index(conn):
    seed_town(conn, "CISTERNINO", "CISTERNINO", "074005", 1000, 1200)
    seed_town(conn, "OSTUNI", "OSTUNI", "074010", 1400, 1800)
    store.put_index(conn, [
        {"series": "hpi_existing", "area": "ITFG", "period": "2018-Q4", "value": 100.0,
         "source": "ISTAT — IPAB"},
        {"series": "hpi_existing", "area": "ITFG", "period": "2025-Q4", "value": 110.0,
         "source": "ISTAT — IPAB"},
    ])
    rows = store.town_rows(conn)
    assert [r["name"] for r in rows] == ["CISTERNINO", "OSTUNI"]     # cheapest first
    assert rows[0]["sale_mid"] == round(1100 * 1.10)                 # index applied
    assert rows[0]["adjusted"]["pct"] == pytest.approx(10.0, abs=0.1)
    assert rows[0]["yield_pct"] == pytest.approx(5.0 * 12 / 1210 * 100, abs=0.1)


def test_town_rows_filter_by_province_and_name(conn):
    seed_town(conn, "CISTERNINO", "CISTERNINO", "074005", 1000, 1200, prov="BR")
    seed_town(conn, "DESENZANO DEL GARDA", "DESENZANO DEL GARDA", "017067", 3000, 4000, prov="BS")
    assert len(store.town_rows(conn, prov="BR")) == 1
    assert len(store.town_rows(conn, prov="bs")) == 1                # case does not matter
    assert store.town_rows(conn, query="cistern")[0]["name"] == "CISTERNINO"
    assert store.town_rows(conn, query="zzz") == []


def test_town_detail_groups_the_zones_and_carries_the_figures(conn):
    seed_town(conn, "CISTERNINO", "CISTERNINO", "074005", 1000, 1200, zone="B1")
    seed_town(conn, "CISTERNINO", "CISTERNINO", "074005", 600, 800, zone="C1")
    seed_stats(conn)
    detail = store.town_detail(conn, istat="074005")
    assert detail["name"] == "CISTERNINO" and len(detail["zones"]) == 2
    assert detail["sale_min"] == 600 and detail["sale_max"] == 1200
    assert detail["stats"]["population"]["value"] == 11231
    assert store.town_detail(conn, istat="999999") is None
