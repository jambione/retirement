import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    """No test may reach a real model.

    Availability now depends on which CLIs are installed on the machine, not on
    an environment variable, so a developer with `claude` on their PATH would
    otherwise have the suite quietly shelling out to it -- slow, billable
    against their subscription, and non-deterministic. Tests that exercise the
    AI path opt in by patching the provider layer themselves.
    """
    from retirement.core import ask

    original = ask.default_provider
    monkeypatch.setattr(ask, "default_provider", lambda: None)
    # Handed back so a test that is ABOUT provider selection can put the real
    # one back: `monkeypatch.setattr(ask, "default_provider", no_real_model)`.
    return original
