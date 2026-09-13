"""The ingress edit touches the file keeping trading.jbrasfield.com up, so it
gets the same scrutiny as anything else that can take a site down."""
import importlib.util
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "cloudflare_ingress",
    Path(__file__).resolve().parents[1] / "scripts" / "cloudflare_ingress.py",
)
ci = importlib.util.module_from_spec(spec)
sys.modules["cloudflare_ingress"] = ci
spec.loader.exec_module(ci)

LIVE = """# the tunnel that serves the trading dashboard
tunnel: 56c84116-0ef0-47c7-bbea-25634d765487
credentials-file: /Users/jambimac/.cloudflared/56c84116.json

ingress:
  - hostname: trading.jbrasfield.com
    service: http://localhost:8888

  - service: http_status:404
"""


def test_new_hostname_is_inserted_above_the_catch_all():
    out = ci.insert(LIVE, "retirement.jbrasfield.com", 8891)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    assert lines.index("- hostname: retirement.jbrasfield.com") < lines.index("- service: http_status:404")
    assert "service: http://localhost:8891" in out


def test_the_existing_rule_is_left_exactly_as_it_was():
    out = ci.insert(LIVE, "retirement.jbrasfield.com", 8891)
    assert "- hostname: trading.jbrasfield.com\n    service: http://localhost:8888" in out
    assert "# the tunnel that serves the trading dashboard" in out   # comments survive
    assert out.count("http_status:404") == 1


def test_indentation_matches_the_file_it_is_editing():
    out = ci.insert(LIVE, "retirement.jbrasfield.com", 8891)
    assert "  - hostname: retirement.jbrasfield.com\n" in out
    assert "    service: http://localhost:8891\n" in out


def test_running_it_twice_refuses_rather_than_duplicating():
    once = ci.insert(LIVE, "retirement.jbrasfield.com", 8891)
    with pytest.raises(ci.AlreadyPresent):
        ci.insert(once, "retirement.jbrasfield.com", 8891)


def test_a_file_with_no_catch_all_is_refused_untouched():
    with pytest.raises(ValueError, match="catch-all"):
        ci.insert("tunnel: abc\ningress:\n  - hostname: a.example.com\n    service: http://x\n",
                  "retirement.jbrasfield.com", 8891)


def test_a_completely_unrelated_file_is_refused():
    with pytest.raises(ValueError):
        ci.insert("just: some\nother: yaml\n", "retirement.jbrasfield.com", 8891)


def test_the_catch_all_may_carry_a_space_after_the_colon():
    text = LIVE.replace("http_status:404", "http_status: 404")
    out = ci.insert(text, "retirement.jbrasfield.com", 8891)
    assert "- hostname: retirement.jbrasfield.com" in out
