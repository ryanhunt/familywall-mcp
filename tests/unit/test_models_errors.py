from __future__ import annotations

import pytest
from pydantic import ValidationError

from familywall_mcp.errors import InvalidEnvelopeError, UnknownFamilyContextError
from familywall_mcp.models import (
    FamilyContext,
    FamilyWallCredentials,
    Principal,
    ResponseEnvelope,
    UnknownFamilyContext,
)


def test_principal_and_verified_context_are_typed() -> None:
    principal = Principal(subject="synthetic-user", scopes=frozenset({"familywall:read"}))
    context = FamilyContext(account_id="a1", family_id="f1", calendar_id="c1")
    assert principal.subject == "synthetic-user"
    assert context.verified is True


def test_unknown_context_cannot_be_constructed_as_verified() -> None:
    unknown = UnknownFamilyContext(family_id="unverified")
    assert unknown.verified is False
    with pytest.raises(ValidationError):
        FamilyContext(account_id="a1", family_id="f1", calendar_id="c1", verified=False)
    error = UnknownFamilyContextError()
    assert error.as_dict()["code"] == "unknown_family_context"


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True, "error_code": "unexpected"},
        {"ok": False},
        {"ok": False, "error_code": "bad", "error_message": ""},
        {"ok": True, "data": {}, "unexpected": True},
    ],
)
def test_malformed_envelopes_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResponseEnvelope.model_validate(payload)


def test_safe_error_has_no_raw_body() -> None:
    error = InvalidEnvelopeError()
    assert error.as_dict() == {
        "code": "invalid_upstream_envelope",
        "message": "The upstream response was not a recognised envelope.",
        "recovery": "Reconnect the account; if it persists, inspect the endpoint contract.",
    }


def test_credentials_are_hidden_from_repr() -> None:
    credentials = FamilyWallCredentials(username="email@example.com", password="synthetic-secret")
    assert "synthetic-secret" not in repr(credentials)


def test_version_has_a_single_source() -> None:
    """The package, its installed metadata and the CLI report one version."""
    import contextlib
    import io
    from importlib.metadata import version

    from familywall_mcp import __version__
    from familywall_mcp.cli import build_parser

    assert __version__ == version("familywall-mcp")

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.suppress(SystemExit):
        build_parser().parse_args(["--version"])
    assert buffer.getvalue().strip() == f"familywall-mcp {__version__}"
