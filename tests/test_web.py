from fastapi.testclient import TestClient


def test_index_renders_with_an_empty_database(tmp_path, monkeypatch):
    monkeypatch.setenv("RETIREMENT_DB", str(tmp_path / "web.sqlite3"))
    from retirement.web.app import app

    client = TestClient(app)
    assert client.get("/healthz").json() == {"ok": True}

    page = client.get("/")
    assert page.status_code == 200
    assert "Retirement project" in page.text
    assert "Nothing scored yet" in page.text       # empty state, not a crash
    assert "Valle d&#39;Itria" in page.text        # areas came from the YAML (escaped)
