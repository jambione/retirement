"""Read a balance-sheet export and turn it into a snapshot.

Written to be tolerant rather than exact. Portal exports move their columns
between releases, wrap the table in title and total rows, and disagree about
whether a mortgage is a negative asset or a positive liability. So nothing here
depends on a fixed layout: it finds the header row by looking for the columns
it knows, reads what it recognises, and reports in `warnings` anything it had
to guess or skip -- which the import screen shows you before you keep the
snapshot.

Works on CSV, TSV and XLSX, from eMoney or anywhere else with the same shape.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Any, Iterable

NAME_HEADERS = ("account", "description", "name", "account name", "holding")
INSTITUTION_HEADERS = ("institution", "custodian", "firm", "owner", "provider", "company")
CATEGORY_HEADERS = ("type", "category", "classification", "asset type", "account type", "class")
BALANCE_HEADERS = ("balance", "value", "current value", "market value", "amount",
                   "current balance", "total value")

TOTAL_ROW = re.compile(
    r"^\s*(grand\s+)?(total|net\s*worth|subtotal|sum)\b", re.I
)
MONEY = re.compile(r"^\(?-?\s*[$€£]?\s*[\d,.\s]+\)?$")
DATE_IN_TEXT = re.compile(
    r"(?:as of|as at|report date|balances? as of)\D{0,12}"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"[A-Z][a-z]+ \d{1,2},? \d{4})", re.I
)


class ImportError_(ValueError):
    """Raised when the file has no table we can read at all."""


# ── reading the file ───────────────────────────────────────────────────────
def read_rows(data: bytes, filename: str) -> list[list[str]]:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        return _read_xlsx(data)
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [[str(cell).strip() for cell in row] for row in csv.reader(io.StringIO(text), dialect)]


def _read_xlsx(data: bytes) -> list[list[str]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ImportError_(
            "Reading .xlsx needs openpyxl (./retire setup installs it). "
            "Exporting as CSV instead also works."
        ) from exc
    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = book.active
    return [
        ["" if cell is None else str(cell).strip() for cell in row]
        for row in sheet.iter_rows(values_only=True)
    ]


# ── finding the table ──────────────────────────────────────────────────────
def _header_score(row: Iterable[str]) -> int:
    cells = [c.strip().lower() for c in row]
    score = 0
    for group in (NAME_HEADERS, BALANCE_HEADERS, CATEGORY_HEADERS, INSTITUTION_HEADERS):
        if any(cell in group for cell in cells):
            score += 1
    return score


def _find_header(rows: list[list[str]]) -> int:
    best_index, best_score = -1, 0
    for index, row in enumerate(rows[:40]):
        score = _header_score(row)
        # A header must at least name a thing and a number.
        if score > best_score and score >= 2:
            best_index, best_score = index, score
    return best_index


def _column_map(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, raw in enumerate(header):
        cell = raw.strip().lower()
        for role, options in (("name", NAME_HEADERS), ("institution", INSTITUTION_HEADERS),
                              ("category", CATEGORY_HEADERS), ("balance", BALANCE_HEADERS)):
            if role not in mapping and cell in options:
                mapping[role] = index
    return mapping


# ── values ─────────────────────────────────────────────────────────────────
def parse_money(raw: str) -> float | None:
    cell = (raw or "").strip()
    if not cell or not MONEY.match(cell):
        return None
    negative = cell.startswith("(") and cell.endswith(")")
    cleaned = re.sub(r"[^\d.\-]", "", cell.replace(",", ""))
    if cleaned in ("", "-", "."):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def find_as_of(rows: list[list[str]]) -> str | None:
    for row in rows[:25]:
        match = DATE_IN_TEXT.search(" ".join(row))
        if match:
            return _normalise_date(match.group(1))
    return None


def _normalise_date(raw: str) -> str:
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


def classify(name: str, category: str, config: dict[str, Any]) -> tuple[str, bool]:
    """Returns (bucket, is_liability)."""
    blob = f"{name} {category}".lower()
    is_liability = any(
        hint.lower() in blob for hint in config.get("liability_hints", [])
    )
    # Debt wins over the asset it is secured against. "Mortgage - Primary
    # Residence" matches both "mortgage" and "residence", and bucketing it as
    # property would add the house to the balance sheet twice -- once as the
    # asset, once as the loan against it.
    if is_liability:
        return "liabilities", True
    for bucket, hints in (config.get("categories") or {}).items():
        if any(str(hint).lower() in blob for hint in hints):
            return bucket, is_liability or bucket == "liabilities"
    return ("liabilities" if is_liability else "other"), is_liability


# ── the import ─────────────────────────────────────────────────────────────
def parse(data: bytes, filename: str, config: dict[str, Any]) -> dict[str, Any]:
    rows = read_rows(data, filename)
    if not rows:
        raise ImportError_("That file is empty.")

    header_index = _find_header(rows)
    if header_index < 0:
        raise ImportError_(
            "Could not find a table in that file. It needs a header row naming an "
            "account column and a balance column — an eMoney balance-sheet or "
            "net-worth export has both."
        )
    columns = _column_map(rows[header_index])
    if "name" not in columns or "balance" not in columns:
        raise ImportError_(
            "Found a header row but not both an account column and a balance column."
        )

    warnings: list[str] = []
    if "category" not in columns:
        warnings.append("No account-type column — everything is bucketed by name alone.")

    accounts: list[dict[str, Any]] = []
    skipped = 0
    for row in rows[header_index + 1:]:
        if not any(cell.strip() for cell in row):
            continue
        name = row[columns["name"]].strip() if columns["name"] < len(row) else ""
        if not name or TOTAL_ROW.match(name):
            continue  # the export's own totals; we compute our own
        balance = parse_money(row[columns["balance"]]) if columns["balance"] < len(row) else None
        if balance is None:
            skipped += 1
            continue
        category_raw = (
            row[columns["category"]].strip()
            if "category" in columns and columns["category"] < len(row) else ""
        )
        bucket, is_liability = classify(name, category_raw, config)
        # An export that already signs liabilities negative must not be counted twice.
        if balance < 0:
            is_liability = True
        accounts.append({
            "name": name,
            "institution": (row[columns["institution"]].strip()
                            if "institution" in columns and columns["institution"] < len(row)
                            else ""),
            "category": bucket,
            "balance": abs(balance),
            "is_liability": is_liability,
        })

    if not accounts:
        raise ImportError_("Found the table but no rows with a readable balance in it.")
    if skipped:
        warnings.append(f"{skipped} row(s) had no readable balance and were skipped.")

    as_of = find_as_of(rows)
    if as_of is None:
        as_of = date.today().isoformat()
        warnings.append(f"No 'as of' date in the file — dated today ({as_of}).")

    assets = sum(a["balance"] for a in accounts if not a["is_liability"])
    liabilities = sum(a["balance"] for a in accounts if a["is_liability"])
    return {
        "as_of": as_of,
        "source": filename,
        "currency": config.get("currency", "USD"),
        "accounts": accounts,
        "assets": round(assets, 2),
        "liabilities": round(liabilities, 2),
        "net_worth": round(assets - liabilities, 2),
        "warnings": warnings,
    }
