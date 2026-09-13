"""Everything structured goes through the subscription CLI layer, not an API key."""
import pytest

from retirement.core import ask, llm


@pytest.fixture
def routed(monkeypatch):
    """Pretend a CLI is installed, and capture what would be sent to it."""
    calls = []

    def fake_ask(prompt, provider=None, timeout=ask.DEFAULT_TIMEOUT):
        calls.append({"prompt": prompt, "provider": provider, "timeout": timeout})
        return {"answer": fake_ask.answer, "provider": "claude_cli",
                "provider_label": "Claude", "seconds": 1.0}

    fake_ask.answer = "{}"
    monkeypatch.setattr(ask, "default_provider", lambda: "claude_cli")
    monkeypatch.setattr(ask, "ask", fake_ask)
    return calls, fake_ask


def test_unavailable_when_no_backend_is_installed(monkeypatch):
    monkeypatch.setattr(ask, "default_provider", lambda: None)
    assert llm.available() is False
    assert llm.parse_prompt("a small town") == {}
    assert llm.assess_listings("brief", [{"id": "x"}]) == {}


def test_available_follows_the_provider_layer(routed):
    assert llm.available() is True
    assert llm.backend() == "claude_cli"


def test_the_system_framing_is_folded_into_one_prompt(routed):
    calls, fake = routed
    fake.answer = '{"character": "hilltop"}'
    llm.parse_prompt("a small town with a strong sense of community")
    assert len(calls) == 1
    prompt = calls[0]["prompt"]
    assert "You convert a house-hunting brief" in prompt      # the system half
    assert "strong sense of community" in prompt              # the user half


def test_assessment_gets_a_longer_timeout_than_parsing(routed):
    calls, fake = routed
    fake.answer = '{"assessments": []}'
    llm.parse_prompt("x")
    llm.assess_listings("brief", [{"id": "a"}])
    assert calls[0]["timeout"] == llm.PARSE_TIMEOUT
    assert calls[1]["timeout"] == llm.ASSESS_TIMEOUT
    assert llm.ASSESS_TIMEOUT > llm.PARSE_TIMEOUT


def test_json_is_recovered_from_a_chatty_cli_answer(routed):
    calls, fake = routed
    # A CLI is an agentic tool; it editorialises where the API would not.
    fake.answer = (
        "Sure — here is the assessment you asked for:\n\n"
        '{"assessments": [{"id": "idealista:1", "character_score": 92, '
        '"condition_score": 88, "rental_score": 86, "note": "Walkable old town.", '
        '"concerns": ["septic tank"]}]}\n\n'
        "Let me know if you want these weighted differently."
    )
    out = llm.assess_listings("brief", [{"id": "idealista:1", "title": "Trullo"}])
    assert out["idealista:1"]["character_score"] == 92
    assert out["idealista:1"]["concerns"] == ["septic tank"]


def test_unparseable_output_is_not_fatal(routed):
    _calls, fake = routed
    fake.answer = "I could not read those listings, sorry."
    assert llm.assess_listings("brief", [{"id": "a"}]) == {}


def test_a_failing_backend_is_not_fatal(monkeypatch):
    monkeypatch.setattr(ask, "default_provider", lambda: "claude_cli")

    def boom(*_args, **_kwargs):
        raise RuntimeError("claude CLI timed out after 300s")

    monkeypatch.setattr(ask, "ask", boom)
    assert llm.assess_listings("brief", [{"id": "a"}]) == {}
    assert llm.parse_prompt("brief") == {}


def test_assessment_never_sends_more_than_the_fields_it_needs(routed):
    calls, fake = routed
    fake.answer = '{"assessments": []}'
    llm.assess_listings("brief", [{
        "id": "a", "title": "Trullo", "description": "Bello", "price": 268000,
        "raw": {"agency_phone": "should not travel"},
    }])
    assert "should not travel" not in calls[0]["prompt"]


def test_subscription_clis_are_preferred_over_the_paid_api():
    order = [p.id for p in ask.PROVIDERS]
    assert order.index("claude_cli") < order.index("anthropic_api")
    assert order.index("grok") < order.index("anthropic_api")
    assert order.index("agy") < order.index("anthropic_api")


# ── the Antigravity envelope ───────────────────────────────────────────────
def test_agy_success_envelope_is_unwrapped():
    assert ask._unwrap_agy('{"status":"SUCCESS","response":"the answer"}') == "the answer"


def test_agy_logged_out_says_what_to_do_rather_than_returning_silence():
    # A logged-out agy returns a WELL-FORMED envelope with the error inside, so
    # generic extraction reports "returned nothing" for a five-second fix.
    with pytest.raises(RuntimeError, match="not logged in"):
        ask._unwrap_agy('{"status":"ERROR","error":"Authentication required"}')


def test_agy_other_errors_keep_their_reason():
    with pytest.raises(RuntimeError, match="model overloaded"):
        ask._unwrap_agy('{"status":"ERROR","error":"model overloaded"}')


def test_agy_plain_text_passes_through():
    assert ask._unwrap_agy("not json at all") == "not json at all"


def test_antigravity_is_preferred_over_the_other_subscriptions():
    order = [p.id for p in ask.PROVIDERS]
    assert order[0] == "agy"


# ── pinning, and noticing when the pin cannot be honoured ──────────────────
def test_a_pinned_backend_wins_over_preference_order(monkeypatch, no_real_model):
    monkeypatch.setattr(ask, "default_provider", no_real_model)
    monkeypatch.setenv("ASK_DEFAULT_PROVIDER", "grok")
    monkeypatch.setattr(ask, "PROVIDERS", [
        ask.Provider("agy", "Antigravity", "", lambda p, t: "", lambda: True),
        ask.Provider("grok", "Grok", "", lambda p, t: "", lambda: True),
    ])
    assert ask.default_provider() == "grok"
    assert ask.preference_note() is None


def test_a_pinned_backend_that_is_missing_is_reported_not_silently_replaced(
    monkeypatch, no_real_model
):
    monkeypatch.setattr(ask, "default_provider", no_real_model)
    monkeypatch.setenv("ASK_DEFAULT_PROVIDER", "agy")
    monkeypatch.setattr(ask, "PROVIDERS", [
        ask.Provider("agy", "Antigravity", "", lambda p, t: "", lambda: False),
        ask.Provider("claude_cli", "Claude", "", lambda p, t: "", lambda: True),
    ])
    assert ask.default_provider() == "claude_cli"       # still works
    note = ask.preference_note()
    assert note and "agy" in note and "claude_cli" in note   # but says so


def test_an_unknown_pin_is_reported(monkeypatch, no_real_model):
    monkeypatch.setattr(ask, "default_provider", no_real_model)
    monkeypatch.setenv("ASK_DEFAULT_PROVIDER", "gpt5")
    monkeypatch.setattr(ask, "PROVIDERS", [
        ask.Provider("agy", "Antigravity", "", lambda p, t: "", lambda: True),
    ])
    assert "not a known backend" in (ask.preference_note() or "")


def test_agy_is_invoked_with_a_print_timeout_matching_the_call(monkeypatch):
    captured = {}

    def fake_run(cmd, timeout, env_overrides=None):
        captured["cmd"] = cmd
        return '{"status":"SUCCESS","response":"ok"}'

    monkeypatch.setattr(ask.shutil, "which", lambda _n: "/opt/homebrew/bin/agy")
    monkeypatch.setattr(ask, "_run", fake_run)
    assert ask._agy_cli("a prompt", 300.0) == "ok"
    cmd = captured["cmd"]
    assert "--print-timeout" in cmd and "300s" in cmd
    assert "--output-format" in cmd and "json" in cmd


# ── finding the binaries at all ────────────────────────────────────────────
def test_an_absolute_pin_is_taken_as_given(tmp_path):
    binary = tmp_path / "agy"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    assert ask.resolve_binary(str(binary)) == str(binary)


def test_an_absolute_pin_that_is_not_executable_is_not_accepted(tmp_path):
    binary = tmp_path / "agy"
    binary.write_text("not executable")
    assert ask.resolve_binary(str(binary)) is None


def test_a_bare_name_is_looked_for_where_these_clis_actually_live(tmp_path, monkeypatch):
    # npm/bun/Homebrew installs are invisible to an ssh or launchd PATH, which
    # is what reported every backend as missing on a machine where all of them
    # work from a Terminal.
    nook = tmp_path / ".bun" / "bin"
    nook.mkdir(parents=True)
    binary = nook / "agy"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    monkeypatch.setattr(ask.shutil, "which", lambda _n: None)       # not on PATH
    monkeypatch.setattr(ask, "CLI_DIRS", (str(nook),))
    assert ask.resolve_binary("agy") == str(binary)


def test_nvm_style_version_directories_are_searched_newest_first(tmp_path, monkeypatch):
    for version in ("v18.0.0", "v22.1.0"):
        d = tmp_path / ".nvm" / "versions" / "node" / version / "bin"
        d.mkdir(parents=True)
        (d / "agy").write_text("#!/bin/sh\n")
        (d / "agy").chmod(0o755)

    monkeypatch.setattr(ask.shutil, "which", lambda _n: None)
    monkeypatch.setattr(ask, "CLI_DIRS", ())
    monkeypatch.setattr(ask, "CLI_GLOBS", (str(tmp_path / ".nvm/versions/node/*/bin"),))
    assert "v22.1.0" in ask.resolve_binary("agy")


def test_a_binary_that_is_nowhere_is_reported_as_absent(monkeypatch):
    monkeypatch.setattr(ask.shutil, "which", lambda _n: None)
    monkeypatch.setattr(ask, "CLI_DIRS", ())
    monkeypatch.setattr(ask, "CLI_GLOBS", ())
    assert ask.resolve_binary("definitely-not-installed") is None


def test_available_reports_where_each_backend_was_found(monkeypatch):
    monkeypatch.setattr(ask, "resolve_binary",
                        lambda name: "/opt/homebrew/bin/agy" if name == "agy" else None)
    entries = {e["id"]: e for e in ask.available()}
    assert entries["agy"]["ready"] is True
    assert entries["agy"]["path"] == "/opt/homebrew/bin/agy"
    assert entries["claude_cli"]["ready"] is False
    assert entries["claude_cli"]["path"] is None


# ── failures have to name themselves ───────────────────────────────────────
def test_the_command_is_shown_without_the_prompt():
    shown = ask.describe(["/opt/homebrew/bin/agy", "-p", "a very long prompt " * 50,
                          "--model", "gemini-3.7-flash-high"])
    assert shown == "/opt/homebrew/bin/agy -p <prompt> --model gemini-3.7-flash-high"


def test_a_failing_call_reports_the_command_and_the_stderr(monkeypatch, tmp_path):
    script = tmp_path / "agy"
    script.write_text("#!/bin/sh\necho 'unknown flag: --effort' >&2\nexit 2\n")
    script.chmod(0o755)
    monkeypatch.setattr(ask, "resolve_binary", lambda _n: str(script))
    monkeypatch.setenv("ASK_AGY_EFFORT", "high")

    with pytest.raises(RuntimeError) as err:
        ask._agy_cli("hello", 60.0)
    text = str(err.value)
    assert "exit 2" in text
    assert "--effort" in text          # the flag it choked on
    assert "<prompt>" in text          # argv shown, prompt elided
    assert "hello" not in text         # ...and not leaked into the error


def test_effort_is_only_passed_when_it_is_configured(monkeypatch):
    captured = {}
    monkeypatch.setattr(ask, "resolve_binary", lambda _n: "/x/agy")
    monkeypatch.setattr(ask, "_run",
                        lambda cmd, timeout, env_overrides=None:
                        captured.setdefault("cmd", cmd) or '{"status":"SUCCESS","response":"ok"}')

    monkeypatch.delenv("ASK_AGY_EFFORT", raising=False)
    ask._agy_cli("p", 60.0)
    assert "--effort" not in captured["cmd"]

    captured.clear()
    monkeypatch.setenv("ASK_AGY_EFFORT", "high")
    ask._agy_cli("p", 60.0)
    assert captured["cmd"][captured["cmd"].index("--effort") + 1] == "high"
