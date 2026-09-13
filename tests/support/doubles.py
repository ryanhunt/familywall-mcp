from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from familywall_mcp.errors import UnexpectedRequestError
from familywall_mcp.models import ResponseEnvelope


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class FakeCookies:
    """Small cookie jar with deterministic replacement semantics."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def snapshot(self) -> dict[str, str]:
        return dict(self._values)


class RequestCapture:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> ResponseEnvelope:
        self.requests.append((method, url, kwargs))
        return ResponseEnvelope(ok=True, data={})


class FakeUpstream(RequestCapture):
    """Allow-list fake upstream; an unregistered call fails loudly."""

    def __init__(self, responses: dict[tuple[str, str], ResponseEnvelope] | None = None) -> None:
        super().__init__()
        self.responses = responses or {}

    async def request(self, method: str, url: str, **kwargs: Any) -> ResponseEnvelope:
        key = (method.upper(), url)
        self.requests.append((method.upper(), url, kwargs))
        if key not in self.responses:
            raise UnexpectedRequestError()
        return self.responses[key]


def synthetic_success(data: object) -> ResponseEnvelope:
    return ResponseEnvelope(ok=True, data=data)


def synthetic_failure(code: str = "unauthorised") -> ResponseEnvelope:
    return ResponseEnvelope(ok=False, error_code=code, error_message="synthetic failure")


FIXED_INSTANT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
