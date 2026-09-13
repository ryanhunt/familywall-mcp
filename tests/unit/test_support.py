from __future__ import annotations

import pytest
from tests.support.doubles import (
    FIXED_INSTANT,
    FakeCookies,
    FakeUpstream,
    FixedClock,
    synthetic_success,
)

from familywall_mcp.errors import UnexpectedRequestError


def test_fixed_clock_and_cookies_are_deterministic() -> None:
    clock = FixedClock(FIXED_INSTANT)
    cookies = FakeCookies({"JSESSIONID": "synthetic-session"})
    cookies.set("XSRF", "synthetic-csrf")
    assert clock.now() == FIXED_INSTANT
    assert cookies.snapshot() == {"JSESSIONID": "synthetic-session", "XSRF": "synthetic-csrf"}


def test_pytest_support_fixtures_are_available(
    json_envelopes: dict[str, object], fixed_clock: FixedClock, fake_cookies: FakeCookies
) -> None:
    assert json_envelopes["success"] is not None
    assert fixed_clock.now() == FIXED_INSTANT
    assert fake_cookies.get("JSESSIONID") == "synthetic-session"


@pytest.mark.asyncio
async def test_fake_upstream_captures_expected_calls_and_rejects_others() -> None:
    upstream = FakeUpstream(
        {("GET", "https://upstream.example.invalid/status"): synthetic_success({})}
    )
    await upstream.request("GET", "https://upstream.example.invalid/status")
    assert upstream.requests[0][0:2] == ("GET", "https://upstream.example.invalid/status")
    with pytest.raises(UnexpectedRequestError):
        await upstream.request("POST", "https://upstream.example.invalid/write")
