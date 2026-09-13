from fastapi.testclient import TestClient


def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RETIREMENT_DB", str(tmp_path / "web.sqlite3"))
    from retirement.web.app import app

    return TestClient(app)


def test_healthz(tmp_path, monkeypatch):
    assert client(tmp_path, monkeypatch).get("/healthz").json() == {"ok": True}


def test_property_page_renders_with_an_empty_database(tmp_path, monkeypatch):
    page = client(tmp_path, monkeypatch).get("/")
    assert page.status_code == 200
    assert "Worth your time" in page.text
    assert "Nothing scored yet" in page.text        # empty state, not a crash
    assert "Valle d&#39;Itria" in page.text         # areas came from the YAML


def test_board_page_renders_and_accepts_a_card(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    page = c.get("/board")
    assert page.status_code == 200
    assert "Empty board" in page.text

    added = c.post("/board/card", data={"title": "Apply for a codice fiscale",
                                        "column": "soon", "tag": "paperwork", "due": ""})
    assert added.status_code == 200                 # followed the redirect
    assert "Apply for a codice fiscale" in c.get("/board").text


def test_board_move_over_json_returns_ok(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    c.post("/board/card", data={"title": "book flights", "column": "soon", "tag": "trip", "due": ""})
    from retirement.core import db
    from retirement.modules.board import store

    card_id = store.by_column(db.connect())["soon"][0]["id"]
    moved = c.post("/board/move", json={"card_id": card_id, "column": "done", "before_id": None})
    assert moved.json() == {"ok": True}
    assert store.by_column(db.connect())["done"][0]["done_at"] is not None


def test_finance_page_explains_itself_when_empty(tmp_path, monkeypatch):
    page = client(tmp_path, monkeypatch).get("/finance")
    assert page.status_code == 200
    assert "Export the balance sheet" in page.text
    assert "licensed to advisory" in page.text      # says why it is not live


def test_finance_import_round_trip(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    csv = (b"Net Worth Statement\nBalances as of 08/31/2026\n\n"
           b"Account,Institution,Type,Current Value\n"
           b'Joint Checking,First National,Checking,"$24,310.00"\n'
           b'Mortgage,First National,Mortgage,"$311,250.00"\n')
    posted = c.post("/finance/import", files={"file": ("networth.csv", csv, "text/csv")})
    assert posted.status_code == 200

    page = c.get("/finance")
    assert "Joint Checking" in page.text
    assert "2026-08-31" in page.text
    assert "-286,940" in page.text or "−286,940" in page.text   # 24,310 - 311,250


def test_a_junk_upload_reports_the_reason_rather_than_500ing(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    posted = c.post("/finance/import",
                    files={"file": ("notes.txt", b"no table in here at all", "text/plain")})
    assert posted.status_code == 200
    assert "import failed" in posted.text


def test_ask_context_says_what_it_can_see(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    data = c.get("/ask/context?scope=brief").json()
    assert data["title"] == "The brief"
    assert "search areas" in data["explains"]
    assert "WHAT WE ARE LOOKING FOR" in data["context"]
    assert isinstance(data["providers"], list)


def test_ask_refuses_an_empty_question(tmp_path, monkeypatch):
    r = client(tmp_path, monkeypatch).post("/ask", json={"scope": "brief", "question": "  "})
    assert r.status_code == 400


def test_price_check_page_shows_only_what_you_checked_by_hand(tmp_path, monkeypatch):
    """Two pages, two jobs. The nightly scan's listings are scored too, but they
    belong on the Property page — repeating them here made both pages show the
    same table and neither say why."""
    c = client(tmp_path, monkeypatch)
    from retirement.core import db
    from retirement.modules.property import store as listing_store
    from retirement.modules.property.models import Listing
    from retirement.modules.value import store as value_store

    conn = db.connect()
    listing_store.migrate(conn)
    value_store.migrate(conn)

    for source, ident in (("upload", "mine"), ("idealista", "theirs")):
        listing_store.upsert(conn, Listing(
            id=f"{source}:{ident}", source=source, external_id=ident,
            title=f"House {ident}", price=200000, size_sqm=100,
            municipality="Cisternino", province="BR", area_id="x",
        ))
    hand = value_store.save_score(conn, "upload:mine", {"score": 61.0}, comune="Cisternino")
    scan = value_store.save_score(conn, "idealista:theirs", {"score": 55.0}, comune="Cisternino")
    typed = value_store.save_score(conn, "adhoc:abc123", {"score": 48.0}, comune="Ostuni")

    page = c.get("/value")
    assert page.status_code == 200
    assert hand in page.text and typed in page.text     # yours
    assert scan not in page.text                        # the scan's, which lives on /
    assert "carry a price check" in page.text           # pointed at, not duplicated
    assert "Price check" in page.text                   # the nav says what it does
