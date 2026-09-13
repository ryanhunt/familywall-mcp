from __future__ import annotations

import os
import socket

import pytest

from tests.support.doubles import (
    FIXED_INSTANT,
    FakeCookies,
    FakeUpstream,
    FixedClock,
    RequestCapture,
    synthetic_failure,
    synthetic_success,
)


class ExternalNetworkBlocked(AssertionError):
    pass


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make live calls an explicit opt-in instead of an accidental test side effect."""
    if os.environ.get("FAMILYWALL_ALLOW_LIVE") == "1":
        return

    def blocked_connect(self: socket.socket, address: object) -> None:
        raise ExternalNetworkBlocked(
            "external network is disabled; opt in explicitly for live tests"
        )

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)


@pytest.fixture
def json_envelopes() -> dict[str, object]:
    return {"success": synthetic_success({"id": "synthetic-1"}), "failure": synthetic_failure()}


@pytest.fixture
def fake_cookies() -> FakeCookies:
    return FakeCookies({"JSESSIONID": "synthetic-session"})


@pytest.fixture
def fixed_clock() -> FixedClock:
    return FixedClock(FIXED_INSTANT)


@pytest.fixture
def request_capture() -> RequestCapture:
    return RequestCapture()


@pytest.fixture
def fake_upstream() -> FakeUpstream:
    return FakeUpstream()
