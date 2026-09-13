"""OMI import and lookup. The fixture is a verbatim slice of a real export —
header and rows exactly as the Agenzia publishes them, Italian decimals and
all — because the parsing details are the whole job."""
import pytest

from retirement.core import db
from retirement.modules.property import omi

HEADER = ("Area_territoriale,Regione,Prov,Comune_ISTAT,Comune_cat,Sez,Comune_amm,"
          "Comune_descrizione,Fascia,Zona,LinkZona,Cod_Tip,Descr_Tipologia,Stato,"
          "Stato_prev,Compr_min,Compr_max,Sup_NL_compr,Loc_min,Loc_max,Sup_NL_loc")

REAL = (HEADER + "\n"
  'NORD-OVEST,PIEMONTE,AL,1006003,A2AA, ,A182,ALESSANDRIA,B,B1,AL00000001,20,Abitazioni civili,NORMALE,P,810,1000,L,4,"5,1",L\n'
  'NORD-OVEST,PIEMONTE,AL,1006003,A2AA, ,A182,ALESSANDRIA,B,B1,AL00000001,13,Box,NORMALE,P,1100,1600,L,"4,4","6,2",L\n'
  'SUD,PUGLIA,BA,1072031,E645, ,E645,LOCOROTONDO,B,B1,BA00000001,20,Abitazioni civili,NORMALE,P,1150,1700,L,3,"4,5",L\n'
  'SUD,PUGLIA,BA,1072031,E645, ,E645,LOCOROTONDO,C,C1,BA00000002,20,Abitazioni civili,NORMALE,P,900,1300,L,2,"3,5",L\n'
  'SUD,PUGLIA,BA,1072031,E645, ,E645,LOCOROTONDO,B,B1,BA00000001,21,Ville e Villini,NORMALE,P,1400,2000,L,4,"5,5",L\n'
  'SUD,PUGLIA,BA,1072031,E645, ,E645,LOCOROTONDO,B,B1,BA00000001,14,Posti auto coperti,NORMALE,P,700,1000,L,"2,7","3,8",L\n'
).encode("utf-8")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "omi.sqlite3")
    omi.migrate(c)
    return c


def test_only_residential_rows_are_kept(conn):
    rows = omi.parse(REAL, "QI_294577_1_20182_VALORI_utf8.csv")
    kinds = {r["tipologia"] for r in rows}
    assert kinds == {"Abitazioni civili", "Ville e Villini"}
    # Garages and parking would drag every benchmark down if averaged in.
    assert "Box" not in kinds and "Posti auto coperti" not in kinds


def test_semester_comes_from_the_filename(conn):
    assert omi.semester_from("QI_294577_1_20182_VALORI_utf8.csv") == "2018-2"
    assert omi.semester_from("QI_294582_1_20171_VALORI_utf8.csv") == "2017-1"
    assert omi.semester_from("something-else.csv") == ""


def test_lookup_takes_the_median_of_a_comune_zones(conn):
    omi.import_file(conn, REAL, "QI_1_20182_VALORI.csv")
    found = omi.lookup(conn, "Locorotondo")
    # zone mids: 1425 (B1 civili), 1100 (C1 civili), 1700 (B1 ville)
    assert found["per_sqm"] == 1425
    assert found["low"] == 900 and found["high"] == 2000
    assert found["zones"] == 2 and found["semester"] == "2018-2"
    assert found["source"] == "omi"


@pytest.mark.parametrize("spelling", [
    "Locorotondo", "LOCOROTONDO", "locorotondo", "Locorotondo (BA)", " Locorotondo ",
])
def test_the_portals_spelling_still_finds_the_comune(conn, spelling):
    omi.import_file(conn, REAL, "QI_1_20182_VALORI.csv")
    assert omi.lookup(conn, spelling)["per_sqm"] == 1425


def test_accents_and_abbreviations_normalise():
    assert omi.normalise("Forlì") == "FORLI"
    assert omi.normalise("S. Vito dei Normanni") == "SAN VITO DEI NORMANNI"
    assert omi.normalise("Reggio nell'Emilia") == "REGGIO NELL EMILIA"


def test_an_unknown_comune_is_simply_absent(conn):
    omi.import_file(conn, REAL, "QI_1_20182_VALORI.csv")
    assert omi.lookup(conn, "Padenghe sul Garda") is None
    assert omi.lookup(conn, "") is None


def test_lookup_before_any_import_does_not_explode(tmp_path):
    assert omi.lookup(db.connect(tmp_path / "empty.sqlite3"), "Locorotondo") is None


def test_a_newer_semester_supersedes_an_older_one(conn):
    omi.import_file(conn, REAL, "QI_1_20171_VALORI.csv")
    newer = REAL.replace(b"1150,1700", b"1500,2100").replace(b"900,1300", b"1200,1600")
    omi.import_file(conn, newer, "QI_1_20182_VALORI.csv")
    found = omi.lookup(conn, "Locorotondo")
    assert found["semester"] == "2018-2"
    assert found["per_sqm"] == 1700          # from the 2018 figures, not 2017


def test_semicolon_delimited_distributions_parse_too(conn):
    semi = REAL.decode().replace('"', "").replace(",", ";")
    # put the Italian decimals back the way that distribution writes them
    rows = omi.parse(semi.encode(), "QI_1_20182_VALORI.csv")
    assert any(r["comune"] == "LOCOROTONDO" for r in rows)


def test_a_file_that_is_not_an_omi_export_says_so(conn):
    with pytest.raises(ValueError, match="VALORI"):
        omi.parse(b"Account,Balance\nChecking,100\n", "networth.csv")


def test_status_reports_what_has_been_loaded(conn):
    assert omi.status(conn)["imported"] is False
    omi.import_file(conn, REAL, "QI_1_20182_VALORI.csv")
    state = omi.status(conn)
    assert state["imported"] is True
    assert state["files"][0]["comuni"] == 2
