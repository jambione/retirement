"""The board: ordering, moving, completion stamps, and the summary line."""
import pytest

from retirement.core import db
from retirement.modules.board import store, summary

CONFIG = {
    "target": {"label": "March scouting trip", "date": "2027-03-14"},
    "columns": [
        {"id": "someday", "label": "Someday"},
        {"id": "soon", "label": "Before the trip", "accent": True},
        {"id": "doing", "label": "In progress"},
        {"id": "done", "label": "Done", "terminal": True},
    ],
    "tags": ["property", "finance"],
    "summary": {"due_soon_days": 45},
}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "b.sqlite3")
    store.migrate(c)
    return c


def test_cards_keep_the_order_they_were_added(conn):
    for title in ("first", "second", "third"):
        store.add(conn, title, "someday")
    assert [c["title"] for c in store.by_column(conn)["someday"]] == ["first", "second", "third"]


def test_move_to_another_column(conn):
    card = store.add(conn, "codice fiscale", "someday", "paperwork")
    store.move(conn, card, "doing")
    cards = store.by_column(conn)
    assert "someday" not in cards
    assert cards["doing"][0]["title"] == "codice fiscale"


def test_move_above_a_specific_card(conn):
    a = store.add(conn, "a", "soon")
    store.add(conn, "b", "soon")
    c = store.add(conn, "c", "soon")
    store.move(conn, c, "soon", before_id=a)          # c jumps to the top
    assert [x["title"] for x in store.by_column(conn)["soon"]] == ["c", "a", "b"]


def test_done_column_stamps_a_completion_date(conn):
    card = store.add(conn, "book flights", "soon", "trip")
    store.move(conn, card, "done")
    done = store.by_column(conn)["done"][0]
    assert done["done_at"] is not None
    # Moving it back out clears the stamp rather than leaving a lie behind.
    store.move(conn, card, "doing")
    assert store.by_column(conn)["doing"][0]["done_at"] is None


def test_counts_separate_open_from_done(conn):
    store.add(conn, "one", "someday")
    store.add(conn, "two", "soon")
    done = store.add(conn, "three", "soon")
    store.move(conn, done, "done")
    counts = store.counts(conn)
    assert counts["open"] == 2 and counts["done"] == 1


def test_due_soon_ignores_finished_cards(conn):
    from datetime import date, timedelta

    near = (date.today() + timedelta(days=10)).isoformat()
    store.add(conn, "book flights", "soon", "trip", due=near)
    finished = store.add(conn, "already done", "soon", "trip", due=near)
    store.move(conn, finished, "done")
    soon = summary.due_soon(conn, 45)
    assert [c["title"] for c in soon] == ["book flights"]
    assert soon[0]["days_left"] == 10


def test_summary_without_an_llm_names_the_nearest_deadline(conn):
    from datetime import date, timedelta

    store.add(conn, "narrow to four towns", "soon", "property",
              due=(date.today() + timedelta(days=5)).isoformat())
    data = summary.build(conn, CONFIG)
    assert "narrow to four towns" in data["sentence"]
    assert data["counts"]["open"] == 1


def test_summary_of_an_empty_board_does_not_invent_work(conn):
    data = summary.build(conn, CONFIG)
    assert data["counts"]["open"] == 0
    assert "Nothing is blocked" in data["sentence"]


def test_module_run_reports_the_board(conn):
    from retirement.modules.board.pipeline import BoardModule

    store.add(conn, "one", "soon")
    result = BoardModule(CONFIG, conn).run()
    assert result["open"] == 1 and result["done"] == 0
    assert result["summary"]
