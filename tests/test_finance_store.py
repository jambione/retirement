from retirement.core import db
from retirement.modules.finance import store
from retirement.modules.finance.pipeline import FinanceModule

CONFIG = {"currency": "USD",
          "purchase": {"budget_eur": 350000, "eur_usd": 1.08, "closing_cost_pct": 10}}


def snapshot(as_of, net, assets, liabilities, accounts=()):
    return {"as_of": as_of, "source": "e.csv", "currency": "USD", "assets": assets,
            "liabilities": liabilities, "net_worth": net, "warnings": [],
            "accounts": list(accounts)}


def conn(tmp_path):
    c = db.connect(tmp_path / "f.sqlite3")
    store.migrate(c)
    return c


def test_snapshots_are_dated_and_the_latest_wins(tmp_path):
    c = conn(tmp_path)
    store.save_snapshot(c, snapshot("2026-06-30", 1_700_000, 2_000_000, 300_000))
    store.save_snapshot(c, snapshot("2026-08-31", 1_802_185, 2_117_615, 315_430))
    latest = store.latest(c)
    assert latest["as_of"] == "2026-08-31"
    assert store.previous(c, latest["id"])["as_of"] == "2026-06-30"


def test_history_reads_oldest_first_for_the_trend_line(tmp_path):
    c = conn(tmp_path)
    for as_of, net in [("2026-04-30", 1_600_000), ("2026-06-30", 1_700_000),
                       ("2026-08-31", 1_802_185)]:
        store.save_snapshot(c, snapshot(as_of, net, net, 0))
    assert [h["as_of"] for h in store.history(c)] == ["2026-04-30", "2026-06-30", "2026-08-31"]


def test_deleting_a_snapshot_takes_its_accounts_and_leaves_the_rest(tmp_path):
    c = conn(tmp_path)
    first = store.save_snapshot(c, snapshot("2026-06-30", 1, 1, 0,
                                            [{"name": "Checking", "balance": 1}]))
    store.save_snapshot(c, snapshot("2026-08-31", 2, 2, 0, [{"name": "Checking", "balance": 2}]))
    store.delete_snapshot(c, first)
    assert len(store.history(c)) == 1
    assert c.execute("SELECT COUNT(*) n FROM accounts").fetchone()["n"] == 1


def test_category_breakdown_excludes_liabilities(tmp_path):
    c = conn(tmp_path)
    store.save_snapshot(c, snapshot("2026-08-31", 700, 1000, 300, [
        {"name": "401k", "category": "retirement", "balance": 600, "is_liability": False},
        {"name": "Checking", "category": "cash", "balance": 400, "is_liability": False},
        {"name": "Mortgage", "category": "liabilities", "balance": 300, "is_liability": True},
    ]))
    breakdown = store.by_category(store.latest(c))
    assert [b["category"] for b in breakdown] == ["retirement", "cash"]
    assert breakdown[0]["share"] == 0.6


def test_module_run_reports_the_move_and_the_purchase_cost(tmp_path):
    c = conn(tmp_path)
    store.save_snapshot(c, snapshot("2026-06-30", 1_700_000, 2_000_000, 300_000))
    store.save_snapshot(c, snapshot("2026-08-31", 1_802_185, 2_117_615, 315_430))
    result = FinanceModule(CONFIG, c).run()
    assert result["change"] == 102_185
    assert result["since"] == "2026-06-30"
    assert result["purchase_cost_usd"] == 415_800.0   # 350k EUR x 1.08 x 1.10


def test_module_run_on_an_empty_database_says_so(tmp_path):
    assert FinanceModule(CONFIG, conn(tmp_path)).run()["snapshots"] == 0
