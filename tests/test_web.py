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
