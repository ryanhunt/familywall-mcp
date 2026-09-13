"""Tests for FamilyWall wire protocol envelope parsing and encoding."""

from __future__ import annotations

import json

import pytest

from familywall_mcp.errors import (
    AuthenticationError,
    InvalidEnvelopeError,
    MalformedPayloadError,
    SessionExpiredError,
    UpstreamRejectedError,
)
from familywall_mcp.familywall.wire import coerce_bool, parse_envelope


class TestParseEnvelope:
    """Test parse_envelope function."""

    def test_success_envelope(self) -> None:
        """Test parsing a successful response envelope."""
        envelope = {
            "a00": {
                "r": {
                    "r": {
                        "accountId": "123",
                        "tokenCsrf": "abcd" * 8,
                    }
                },
                "cn": "log2in",
            }
        }
        result = parse_envelope(json.dumps(envelope), endpoint="log2in")
        assert result == {"accountId": "123", "tokenCsrf": "abcd" * 8}

    def test_success_envelope_bytes(self) -> None:
        """Test parsing a successful response envelope from bytes."""
        envelope = {
            "a00": {
                "r": {
                    "r": {"id": "test-123"},
                },
                "cn": "calget",
            }
        }
        result = parse_envelope(json.dumps(envelope).encode("utf-8"), endpoint="calget")
        assert result == {"id": "test-123"}

    def test_invalid_json(self) -> None:
        """Test that invalid JSON raises InvalidEnvelopeError."""
        with pytest.raises(InvalidEnvelopeError) as exc_info:
            parse_envelope("not valid json", endpoint="test")
        assert exc_info.value.info.endpoint == "test"

    def test_html_response(self) -> None:
        """Test that HTML body raises SessionExpiredError."""
        html_body = "<html><body>Login required</body></html>"
        with pytest.raises(SessionExpiredError) as exc_info:
            parse_envelope(html_body, endpoint="test")
        assert exc_info.value.info.endpoint == "test"

    def test_html_response_whitespace_prefix(self) -> None:
        """Test HTML detection with leading whitespace."""
        html_body = "  \n  <html><body>Login</body></html>"
        with pytest.raises(SessionExpiredError):
            parse_envelope(html_body, endpoint="test")

    def test_missing_key(self) -> None:
        """Test that missing envelope key raises InvalidEnvelopeError."""
        envelope = {"a01": {"r": {"r": {}}}}
        with pytest.raises(InvalidEnvelopeError) as exc_info:
            parse_envelope(json.dumps(envelope), endpoint="test")
        assert exc_info.value.info.endpoint == "test"

    def test_non_dict_body(self) -> None:
        """Test that non-dict top-level raises InvalidEnvelopeError."""
        with pytest.raises(InvalidEnvelopeError):
            parse_envelope(json.dumps([1, 2, 3]), endpoint="test")

    def test_non_dict_envelope(self) -> None:
        """Test that non-dict envelope raises InvalidEnvelopeError."""
        envelope = {"a00": "not a dict"}
        with pytest.raises(InvalidEnvelopeError):
            parse_envelope(json.dumps(envelope), endpoint="test")

    def test_un_envelope_501_noauthent(self) -> None:
        """Test that un/501/NOAUTHENT raises SessionExpiredError."""
        envelope = {
            "a00": {
                "un": {
                    "un": {
                        "FiZClassId": "501",
                        "message": "Api test is not allowed by ruleset NOAUTHENT",
                    }
                },
                "cn": "test",
            }
        }
        with pytest.raises(SessionExpiredError) as exc_info:
            parse_envelope(json.dumps(envelope), endpoint="test")
        assert exc_info.value.info.endpoint == "test"

    def test_un_envelope_501_without_noauthent(self) -> None:
        """Test that un/501 without NOAUTHENT still raises SessionExpiredError."""
        envelope = {
            "a00": {
                "un": {
                    "un": {
                        "FiZClassId": "501",
                        "message": "Authentication failed",
                    }
                },
                "cn": "test",
            }
        }
        with pytest.raises(SessionExpiredError) as exc_info:
            parse_envelope(json.dumps(envelope), endpoint="test")
        assert exc_info.value.info.endpoint == "test"

    def test_un_envelope_502(self) -> None:
        """Test that un/502 raises UpstreamRejectedError."""
        envelope = {
            "a00": {
                "un": {
                    "un": {
                        "FiZClassId": "502",
                        "message": "Bad request identifier",
                    }
                },
                "cn": "test",
            }
        }
        with pytest.raises(UpstreamRejectedError) as exc_info:
            parse_envelope(json.dumps(envelope), endpoint="test")
        assert exc_info.value.info.endpoint == "test"

    def test_ex_envelope_log2in(self) -> None:
        """Test that ex envelope on log2in raises AuthenticationError."""
        envelope = {
            "a00": {
                "ex": {
                    "ex": {
                        "FiZClassId": "3",
                        "message": "bad password",
                    }
                },
                "cn": "log2in",
            }
        }
        with pytest.raises(AuthenticationError) as exc_info:
            parse_envelope(json.dumps(envelope), endpoint="log2in")
        assert exc_info.value.info.endpoint == "log2in"

    def test_ex_envelope_other_endpoint(self) -> None:
        """Test that ex envelope on non-login raises UpstreamRejectedError."""
        envelope = {
            "a00": {
                "ex": {
                    "ex": {
                        "FiZClassId": "3",
                        "message": "error",
                    }
                },
                "cn": "calget",
            }
        }
        with pytest.raises(UpstreamRejectedError) as exc_info:
            parse_envelope(json.dumps(envelope), endpoint="calget")
        assert exc_info.value.info.endpoint == "calget"

    def test_malformed_r_envelope(self) -> None:
        """Test that malformed r envelope raises InvalidEnvelopeError."""
        envelope = {
            "a00": {
                "r": "not a dict",
            }
        }
        with pytest.raises(InvalidEnvelopeError):
            parse_envelope(json.dumps(envelope), endpoint="test")

    def test_r_envelope_missing_inner_r(self) -> None:
        """Test that r envelope without inner r raises InvalidEnvelopeError."""
        envelope = {
            "a00": {
                "r": {
                    "data": {},
                }
            }
        }
        with pytest.raises(InvalidEnvelopeError):
            parse_envelope(json.dumps(envelope), endpoint="test")

    def test_unknown_envelope_type(self) -> None:
        """Test that unknown envelope type raises InvalidEnvelopeError."""
        envelope = {
            "a00": {
                "unknown": {"data": {}},
            }
        }
        with pytest.raises(InvalidEnvelopeError):
            parse_envelope(json.dumps(envelope), endpoint="test")

    def test_custom_envelope_key(self) -> None:
        """Test parsing with custom envelope key."""
        envelope = {
            "a01": {
                "r": {
                    "r": {"data": "value"},
                },
            }
        }
        result = parse_envelope(json.dumps(envelope), endpoint="test", key="a01")
        assert result == {"data": "value"}

    def test_error_message_not_in_exception(self) -> None:
        """Test that upstream error messages are not in the raised exception."""
        envelope = {
            "a00": {
                "un": {
                    "un": {
                        "FiZClassId": "502",
                        "message": "This is a secret upstream error message",
                    }
                },
            }
        }
        with pytest.raises(UpstreamRejectedError) as exc_info:
            parse_envelope(json.dumps(envelope), endpoint="test")

        # Check that the upstream message is not in the error
        error_dict = exc_info.value.as_dict()
        error_str = str(exc_info.value)

        assert "This is a secret upstream error message" not in error_dict.get("message", "")
        assert "This is a secret upstream error message" not in error_str

    def test_un_envelope_with_null_message(self) -> None:
        """Test that un envelope with null message field is handled safely."""
        envelope = {
            "a00": {
                "un": {
                    "un": {
                        "FiZClassId": "501",
                        "message": None,  # JSON null becomes None
                    }
                },
                "cn": "test",
            }
        }
        with pytest.raises(SessionExpiredError) as exc_info:
            parse_envelope(json.dumps(envelope), endpoint="test")
        assert exc_info.value.info.endpoint == "test"


class TestCoerceBool:
    """Test coerce_bool function."""

    def test_coerce_bool_true_string(self) -> None:
        """Test coercing 'true' string to True."""
        assert coerce_bool("true") is True

    def test_coerce_bool_false_string(self) -> None:
        """Test coercing 'false' string to False."""
        assert coerce_bool("false") is False

    def test_coerce_bool_true_bool(self) -> None:
        """Test coercing True boolean to True."""
        assert coerce_bool(True) is True

    def test_coerce_bool_false_bool(self) -> None:
        """Test coercing False boolean to False."""
        assert coerce_bool(False) is False

    def test_coerce_bool_invalid_string(self) -> None:
        """Test that invalid string raises MalformedPayloadError."""
        with pytest.raises(MalformedPayloadError):
            coerce_bool("yes")

    def test_coerce_bool_integer(self) -> None:
        """Test that integer raises MalformedPayloadError."""
        with pytest.raises(MalformedPayloadError):
            coerce_bool(1)

    def test_coerce_bool_none(self) -> None:
        """Test that None raises MalformedPayloadError."""
        with pytest.raises(MalformedPayloadError):
            coerce_bool(None)

    def test_coerce_bool_case_sensitive(self) -> None:
        """Test that 'True' (capitalized) is rejected."""
        with pytest.raises(MalformedPayloadError):
            coerce_bool("True")


class TestParseEnvelopeUnicode:
    """Test Unicode handling in envelope parsing."""

    def test_unicode_in_result(self) -> None:
        """Test that Unicode in result is preserved."""
        envelope = {
            "a00": {
                "r": {
                    "r": {
                        "name": "Müsli",
                        "emoji": "🎉",
                    }
                },
            }
        }
        result = parse_envelope(json.dumps(envelope, ensure_ascii=False), endpoint="test")
        assert result == {"name": "Müsli", "emoji": "🎉"}

    def test_utf8_bytes(self) -> None:
        """Test parsing UTF-8 encoded bytes."""
        envelope = {
            "a00": {
                "r": {
                    "r": {
                        "name": "Müsli",
                    }
                },
            }
        }
        body_bytes = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        result = parse_envelope(body_bytes, endpoint="test")
        assert result == {"name": "Müsli"}
