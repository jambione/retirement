import json

import pytest

from retirement.core import notify, secrets


@pytest.fixture
def secrets_file(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    monkeypatch.setenv("RETIREMENT_SECRETS", str(path))
    return path


def test_secrets_json_wins_over_the_environment(secrets_file, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "from-env.example.com")
    secrets_file.write_text(json.dumps({"smtp_host": "from-json.example.com"}))
    assert secrets.get("smtp_host") == "from-json.example.com"


def test_the_environment_fills_in_what_the_file_omits(secrets_file, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "me@example.com")
    secrets_file.write_text(json.dumps({"smtp_host": "mail.example.com"}))
    assert secrets.get("smtp_user") == "me@example.com"


def test_a_missing_or_broken_file_is_not_fatal(secrets_file, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "mail.example.com")
    assert secrets.get("smtp_host") == "mail.example.com"     # file absent
    secrets_file.write_text("{ not json at all")
    assert secrets.get("smtp_host") == "mail.example.com"     # file unreadable


def test_status_reports_readiness_without_the_password(secrets_file, monkeypatch):
    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_PASS", "DIGEST_TO"):
        monkeypatch.delenv(key, raising=False)
    secrets_file.write_text(json.dumps({
        "smtp_host": "mail.example.com", "smtp_user": "me@example.com",
        "smtp_pass": "hunter2", "digest_to": "me@example.com, spouse@example.com",
    }))
    status = notify.status()
    assert status["configured"] is True
    assert status["has_password"] is True
    assert status["digest_to"] == ["me@example.com", "spouse@example.com"]
    assert "hunter2" not in json.dumps(status)


def test_send_refuses_clearly_when_nothing_is_configured(secrets_file, monkeypatch):
    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_PASSWORD", "DIGEST_TO", "NOTIFY_TO"):
        monkeypatch.delenv(key, raising=False)
    secrets_file.write_text("{}")
    assert notify.configured() is False
    with pytest.raises(notify.EmailNotConfigured):
        notify.send("subject", "<p>body</p>")


def test_credentials_without_a_recipient_are_not_configured(secrets_file, monkeypatch):
    for key in ("DIGEST_TO", "NOTIFY_TO"):
        monkeypatch.delenv(key, raising=False)
    secrets_file.write_text(json.dumps({
        "smtp_host": "mail.example.com", "smtp_user": "me", "smtp_pass": "x",
    }))
    assert notify.configured() is False
