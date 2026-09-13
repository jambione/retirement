"""The importer is the part most likely to meet a file it has not seen, so the
fixtures here are deliberately messy: title rows, section headers, the export's
own totals, and the two different conventions for signing a mortgage."""
import pytest

from retirement.modules.finance import importer

CONFIG = {
    "currency": "USD",
    "liability_hints": ["mortgage", "loan", "credit card", "heloc", "liability"],
    "categories": {
        "retirement": ["401k", "ira", "roth"],
        "investments": ["brokerage", "taxable"],
        "cash": ["checking", "savings", "money market"],
        "property": ["residence", "real estate"],
        "liabilities": ["mortgage", "loan", "credit card"],
    },
}

EMONEY = b"""Net Worth Statement
Prepared for Jonathan and Sarah
Balances as of 08/31/2026

Account,Institution,Type,Current Value
Joint Checking,First National,Checking,"$24,310.00"
Emergency Savings,First National,Savings,"$61,400.00"
Jonathan 401(k),Fidelity,401k,"$742,905.00"
Sarah Roth IRA,Vanguard,Roth IRA,"$188,220.00"
Joint Brokerage,Schwab,Taxable Brokerage,"$415,780.00"
Primary Residence,,Real Estate,"$685,000.00"
Mortgage - Primary Residence,First National,Mortgage,"$311,250.00"
Visa,Chase,Credit Card,"$4,180.00"
Total Assets,,,"$2,117,615.00"
Total Liabilities,,,"$315,430.00"
Net Worth,,,"$1,802,185.00"
"""

SIGNED_NEGATIVE = b"""Account,Type,Balance
Checking,Cash,"12,000.00"
Mortgage,Real Estate Loan,"(250,000.00)"
"""


def test_reads_a_realistic_export():
    parsed = importer.parse(EMONEY, "networth.csv", CONFIG)
    assert parsed["as_of"] == "2026-08-31"
    # The export's own Total / Net Worth rows are ignored; we add up the accounts.
    assert len(parsed["accounts"]) == 8
    assert parsed["assets"] == pytest.approx(2117615.0)
    assert parsed["liabilities"] == pytest.approx(315430.0)
    assert parsed["net_worth"] == pytest.approx(1802185.0)


def test_buckets_accounts_by_type():
    parsed = importer.parse(EMONEY, "networth.csv", CONFIG)
    buckets = {a["name"]: a["category"] for a in parsed["accounts"]}
    assert buckets["Jonathan 401(k)"] == "retirement"
    assert buckets["Sarah Roth IRA"] == "retirement"
    assert buckets["Joint Brokerage"] == "investments"
    assert buckets["Joint Checking"] == "cash"
    assert buckets["Primary Residence"] == "property"
    assert buckets["Mortgage - Primary Residence"] == "liabilities"


def test_liabilities_are_positive_amounts_flagged_as_debt():
    parsed = importer.parse(EMONEY, "networth.csv", CONFIG)
    mortgage = next(a for a in parsed["accounts"] if a["name"].startswith("Mortgage"))
    assert mortgage["is_liability"] is True
    assert mortgage["balance"] > 0        # stored positive, subtracted once


def test_a_negative_balance_is_a_liability_not_a_negative_asset():
    parsed = importer.parse(SIGNED_NEGATIVE, "export.csv", CONFIG)
    assert parsed["assets"] == pytest.approx(12000.0)
    assert parsed["liabilities"] == pytest.approx(250000.0)
    assert parsed["net_worth"] == pytest.approx(-238000.0)


def test_missing_date_falls_back_to_today_and_warns():
    parsed = importer.parse(SIGNED_NEGATIVE, "export.csv", CONFIG)
    assert any("as of" in w.lower() for w in parsed["warnings"])


def test_semicolon_delimited_export():
    data = b"Account;Type;Value\nChecking;Cash;1.000,00\n".replace(b"1.000,00", b"1000.00")
    parsed = importer.parse(data, "eu.csv", CONFIG)
    assert parsed["assets"] == pytest.approx(1000.0)


def test_a_file_with_no_table_is_refused_with_a_useful_message():
    with pytest.raises(importer.ImportError_) as err:
        importer.parse(b"just some prose\nno table here\n", "notes.txt", CONFIG)
    assert "header row" in str(err.value).lower()


def test_an_empty_file_is_refused():
    with pytest.raises(importer.ImportError_):
        importer.parse(b"", "empty.csv", CONFIG)


@pytest.mark.parametrize("raw,expected", [
    ("$1,234.56", 1234.56), ("(2,000)", -2000.0), ("1234", 1234.0),
    ("", None), ("n/a", None), ("--", None),
])
def test_money_parsing(raw, expected):
    assert importer.parse_money(raw) == expected


def test_xlsx_round_trip(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "export.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Net Worth Statement"])
    sheet.append(["Balances as of 2026-08-31"])
    sheet.append(["Account", "Type", "Balance"])
    sheet.append(["Joint Checking", "Checking", "24310"])
    sheet.append(["Mortgage", "Mortgage", "311250"])
    book.save(path)

    parsed = importer.parse(path.read_bytes(), "export.xlsx", CONFIG)
    assert parsed["as_of"] == "2026-08-31"
    assert parsed["assets"] == pytest.approx(24310.0)
    assert parsed["liabilities"] == pytest.approx(311250.0)
