"""Tests for FamilyWall client and session management."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from familywall_mcp.errors import (
    AuthenticationError,
    RateLimitedError,
    SessionExpiredError,
    TransportError,
    UpstreamRejectedError,
)
from familywall_mcp.familywall.client import FamilyWallSession


@pytest.fixture
def base_url() -> str:
    """Base URL for test API."""
    return "https://api.familywall.com"


@pytest.fixture
async def http_client() -> httpx.AsyncClient:
    """Create a test HTTP client with respx mocking."""
    async with httpx.AsyncClient() as client:
        yield client


def make_response_with_cookies(
    request: httpx.Request, body: dict, cookies: dict[str, str]
) -> httpx.Response:
    """Helper to create a response with cookies."""
    # Build headers list with Set-Cookie entries
    header_list = [("set-cookie", f"{name}={value}; Path=/") for name, value in cookies.items()]

    response = httpx.Response(
        200,
        text=json.dumps(body),
        headers=header_list,
    )
    # Manually set request so cookies can be extracted
    response._request = request
    # Force cookie parsing
    _ = response.cookies
    return response


@pytest.mark.asyncio
class TestFamilyWallSessionLogin:
    """Test FamilyWallSession.login method."""

    async def test_login_success_stores_session(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that successful login stores JSESSIONID and tokenCsrf."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "abcd" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-token-12345"}
                )

            route = respx.post(f"{base_url}/api/log2in").mock(side_effect=callback)

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            assert session._jsessionid == "session-token-12345"
            assert session._token_csrf == "abcd" * 8

            # Verify the request was made
            assert route.called
            request = route.calls.last.request
            assert request.method == "POST"
            assert "partnerScope=Family" in request.content.decode()
            assert "a00identifier=test%40example.com" in request.content.decode()

    async def test_login_bad_password_raises_authentication_error(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that bad password raises AuthenticationError."""
        with respx.mock:
            login_response = {
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

            respx.post(f"{base_url}/api/log2in").mock(
                return_value=httpx.Response(200, text=json.dumps(login_response))
            )

            session = FamilyWallSession(base_url, http_client)
            with pytest.raises(AuthenticationError):
                await session.login("test@example.com", "wrongpassword")

    async def test_login_multiple_set_cookie_headers(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that multiple Set-Cookie headers still yields JSESSIONID."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "abcd" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request,
                    login_response,
                    {
                        "AWSALB": "cookie1",
                        "JSESSIONID": "session-token-12345",
                        "AWSALBCORS": "cookie2",
                    },
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=callback)

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            assert session._jsessionid == "session-token-12345"

    async def test_login_no_jsessionid_raises_authentication_error(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that missing JSESSIONID in response raises AuthenticationError."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "abcd" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            respx.post(f"{base_url}/api/log2in").mock(
                return_value=httpx.Response(200, text=json.dumps(login_response))
            )

            session = FamilyWallSession(base_url, http_client)
            with pytest.raises(AuthenticationError):
                await session.login("test@example.com", "password123")


@pytest.mark.asyncio
class TestFamilyWallSessionCall:
    """Test FamilyWallSession.call method."""

    async def test_call_without_login_raises_authentication_error(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that calling without login raises AuthenticationError."""
        session = FamilyWallSession(base_url, http_client)
        with pytest.raises(AuthenticationError):
            await session.call("calget", {})

    async def test_call_sends_session_and_csrf_headers(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that authenticated call sends JSESSIONID and tokencsrf headers."""
        with respx.mock:
            # First, login
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            # Then, make an authenticated call
            call_response = {
                "a00": {
                    "r": {
                        "r": {
                            "events": [],
                        }
                    },
                    "cn": "calget",
                }
            }

            route = respx.post(f"{base_url}/api/calget").mock(
                return_value=httpx.Response(200, text=json.dumps(call_response))
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")
            result = await session.call("calget", {"familyId": "fam-123"})

            assert result == {"events": []}

            # Verify the request had correct headers
            request = route.calls.last.request
            assert request.headers.get("tokencsrf") == "aaaa" * 8
            # JSESSIONID is required; ALB cookies may also be present (harmless)
            cookie_header = request.headers.get("cookie", "")
            assert cookie_header == "JSESSIONID=session-123" or cookie_header.startswith(
                "JSESSIONID=session-123"
            )
            assert "partnerScope=Family" in request.content.decode()

    async def test_call_unicode_roundtrip(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that Unicode field values round-trip in form body."""
        with respx.mock:
            # Login
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            # Authenticated call
            call_response = {
                "a00": {
                    "r": {
                        "r": {"result": "ok"},
                    },
                    "cn": "test",
                }
            }

            route = respx.post(f"{base_url}/api/test").mock(
                return_value=httpx.Response(200, text=json.dumps(call_response))
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")
            await session.call("test", {"name": "Müsli", "emoji": "🎉"})

            # Verify form encoding
            request = route.calls.last.request
            content = request.content.decode("utf-8")
            assert "M%C3%BCsli" in content or "Müsli" in content
            assert "%F0%9F%8E%89" in content or "🎉" in content

    async def test_call_http_401_raises_transport_error(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that HTTP 401 raises TransportError."""
        with respx.mock:
            # Login first
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            respx.post(f"{base_url}/api/test").mock(
                return_value=httpx.Response(401, text="Unauthorized")
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            with pytest.raises(TransportError):
                await session.call("test", {})

    async def test_call_http_403_raises_transport_error(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that HTTP 403 raises TransportError."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            respx.post(f"{base_url}/api/test").mock(
                return_value=httpx.Response(403, text="Forbidden")
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            with pytest.raises(TransportError):
                await session.call("test", {})

    async def test_call_http_500_raises_transport_error(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that HTTP 500 raises TransportError."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            respx.post(f"{base_url}/api/test").mock(
                return_value=httpx.Response(500, text="Internal Server Error")
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            with pytest.raises(TransportError):
                await session.call("test", {})

    async def test_call_http_429_raises_rate_limited_error(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that HTTP 429 raises RateLimitedError."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            respx.post(f"{base_url}/api/test").mock(
                return_value=httpx.Response(429, text="Too Many Requests")
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            with pytest.raises(RateLimitedError):
                await session.call("test", {})

    async def test_call_session_expired_raises_session_expired_error(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that 501/NOAUTHENT raises SessionExpiredError."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            expired_response = {
                "a00": {
                    "un": {
                        "un": {
                            "FiZClassId": "501",
                            "message": "Api calget is not allowed by ruleset NOAUTHENT",
                        }
                    },
                    "cn": "calget",
                }
            }

            respx.post(f"{base_url}/api/calget").mock(
                return_value=httpx.Response(200, text=json.dumps(expired_response))
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            with pytest.raises(SessionExpiredError):
                await session.call("calget", {})

    async def test_call_batched_response_with_valid_a01(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that extra_keys extracts valid a01 envelope."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            batched_response = {
                "a00": {
                    "r": {
                        "r": {
                            "families": [],
                        }
                    },
                    "cn": "accgetallfamily",
                },
                "a01": {
                    "r": {
                        "r": {
                            "profiles": [{"id": "prof-123"}],
                        }
                    },
                    "cn": "prfgetProfiles",
                },
            }

            respx.post(f"{base_url}/api/accgetallfamily").mock(
                return_value=httpx.Response(200, text=json.dumps(batched_response))
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")
            result = await session.call(
                "accgetallfamily",
                {"a01call": "prfgetProfiles"},
                extra_keys=["a01"],
            )

            assert result == {
                "a00": {"families": []},
                "a01": {"profiles": [{"id": "prof-123"}]},
            }

    async def test_call_batched_response_with_absent_a01(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that missing extra_keys are omitted from result."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            # Response without a01 envelope
            response_body = {
                "a00": {
                    "r": {
                        "r": {
                            "families": [],
                        }
                    },
                    "cn": "accgetallfamily",
                }
            }

            respx.post(f"{base_url}/api/accgetallfamily").mock(
                return_value=httpx.Response(200, text=json.dumps(response_body))
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")
            result = await session.call(
                "accgetallfamily",
                {"a01call": "prfgetProfiles"},
                extra_keys=["a01"],
            )

            # a01 is absent, so it should not be in result
            assert result == {"a00": {"families": []}}
            assert "a01" not in result

    async def test_call_batched_response_with_error_a01(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that malformed a01 raises SessionExpiredError (un/501)."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            # a01 carries an error envelope
            batched_response = {
                "a00": {
                    "r": {
                        "r": {
                            "families": [],
                        }
                    },
                    "cn": "accgetallfamily",
                },
                "a01": {
                    "un": {
                        "un": {
                            "FiZClassId": "501",
                            "message": "Api prfgetProfiles is not allowed by ruleset NOAUTHENT",
                        }
                    },
                    "cn": "prfgetProfiles",
                },
            }

            respx.post(f"{base_url}/api/accgetallfamily").mock(
                return_value=httpx.Response(200, text=json.dumps(batched_response))
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            with pytest.raises(SessionExpiredError):
                await session.call(
                    "accgetallfamily",
                    {"a01call": "prfgetProfiles"},
                    extra_keys=["a01"],
                )

    async def test_call_batched_response_with_malformed_a01(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that structurally invalid a01 raises InvalidEnvelopeError."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            # a01 is malformed (missing nested r)
            batched_response = {
                "a00": {
                    "r": {
                        "r": {
                            "families": [],
                        }
                    },
                    "cn": "accgetallfamily",
                },
                "a01": {
                    "r": {
                        "data": {},  # Missing nested 'r' key
                    },
                    "cn": "prfgetProfiles",
                },
            }

            respx.post(f"{base_url}/api/accgetallfamily").mock(
                return_value=httpx.Response(200, text=json.dumps(batched_response))
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            from familywall_mcp.errors import InvalidEnvelopeError

            with pytest.raises(InvalidEnvelopeError):
                await session.call(
                    "accgetallfamily",
                    {"a01call": "prfgetProfiles"},
                    extra_keys=["a01"],
                )


@pytest.mark.asyncio
class TestFamilyWallSessionRepr:
    """Test FamilyWallSession repr and safe error handling."""

    async def test_repr_does_not_expose_credentials(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that __repr__ does not expose session credentials."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "abcd" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "secret-session-token"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            repr_str = repr(session)
            assert "abcdabcdabcdabcdabcdabcdabcdabcd" not in repr_str
            assert "secret-session-token" not in repr_str
            assert "password123" not in repr_str
            assert "authenticated=True" in repr_str

    async def test_error_does_not_contain_upstream_message(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that errors do not contain upstream message text."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "aaaa" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            error_response = {
                "a00": {
                    "un": {
                        "un": {
                            "FiZClassId": "502",
                            "message": "User 'test@example.com' not found in database",
                        }
                    },
                    "cn": "test",
                }
            }

            respx.post(f"{base_url}/api/test").mock(
                return_value=httpx.Response(200, text=json.dumps(error_response))
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            with pytest.raises(UpstreamRejectedError) as exc_info:
                await session.call("test", {})

            # Verify the upstream message is not in the error
            error_dict = exc_info.value.as_dict()
            error_str = str(exc_info.value)

            assert "User 'test@example.com' not found in database" not in error_dict.get(
                "message", ""
            )
            assert "User 'test@example.com' not found in database" not in error_str

    async def test_error_does_not_contain_password(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that errors do not contain password."""
        with respx.mock:
            error_response = {
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

            respx.post(f"{base_url}/api/log2in").mock(
                return_value=httpx.Response(200, text=json.dumps(error_response))
            )

            session = FamilyWallSession(base_url, http_client)

            with pytest.raises(AuthenticationError) as exc_info:
                await session.login("test@example.com", "secretpassword123")

            # Verify password is not in error
            error_dict = exc_info.value.as_dict()
            error_str = str(exc_info.value)

            assert "secretpassword123" not in error_dict.get("message", "")
            assert "secretpassword123" not in error_str

    async def test_error_does_not_contain_csrf_token(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that errors do not contain CSRF token."""
        with respx.mock:
            login_response = {
                "a00": {
                    "r": {
                        "r": {
                            "accountId": "user-123",
                            "tokenCsrf": "abcd" * 8,
                        }
                    },
                    "cn": "log2in",
                }
            }

            def login_callback(request: httpx.Request) -> httpx.Response:
                return make_response_with_cookies(
                    request, login_response, {"JSESSIONID": "session-123"}
                )

            respx.post(f"{base_url}/api/log2in").mock(side_effect=login_callback)

            error_response = {
                "a00": {
                    "un": {
                        "un": {
                            "FiZClassId": "501",
                            "message": "Wrong anti csrf token=null",
                        }
                    },
                    "cn": "test",
                }
            }

            respx.post(f"{base_url}/api/test").mock(
                return_value=httpx.Response(200, text=json.dumps(error_response))
            )

            session = FamilyWallSession(base_url, http_client)
            await session.login("test@example.com", "password123")

            with pytest.raises(SessionExpiredError) as exc_info:
                await session.call("test", {})

            # Verify CSRF token is not in error
            error_dict = exc_info.value.as_dict()
            error_str = str(exc_info.value)

            assert "abcdabcdabcdabcdabcdabcdabcdabcd" not in error_dict.get("message", "")
            assert "abcdabcdabcdabcdabcdabcdabcdabcd" not in error_str
