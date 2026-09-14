"""Tests for family discovery and member parsing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests.support.discovery_fixtures import (
    empty_family_id_payload,
    missing_family_id_payload,
    no_authenticated_member_payload,
    normal_discovery_payload,
    single_element_array_payload,
    two_family_array_payload,
)

from familywall_mcp.errors import MalformedPayloadError, UnsupportedConfigurationError
from familywall_mcp.familywall.discovery import (
    DiscoveredFamily,
    FamilyMember,
    build_discovery_fields,
    parse_discovery,
)


class TestBuildDiscoveryFields:
    def test_discovery_fields_match_contract(self) -> None:
        fields = build_discovery_fields()
        assert fields == {
            "partnerScope": "Family",
            "a01call": "prfgetProfiles",
        }


class TestNormalFamily:
    def test_single_family_object_parses(self) -> None:
        payload = normal_discovery_payload()
        family = parse_discovery(payload)

        assert family.family_id == "1234567"
        assert family.family_meta_id == "family/1234567"
        assert family.calendar_id == "calendar/1234567"
        assert family.name == "Test Family"
        assert len(family.members) == 2

    def test_calendar_id_derived_correctly(self) -> None:
        payload = normal_discovery_payload()
        family = parse_discovery(payload)

        assert family.calendar_id == f"calendar/{family.family_id}"

    def test_single_element_array_parses_identically(self) -> None:
        payload = single_element_array_payload()
        family = parse_discovery(payload)

        assert family.family_id == "1234567"
        assert family.family_meta_id == "family/1234567"
        assert family.calendar_id == "calendar/1234567"
        assert family.name == "Test Family"
        assert len(family.members) == 2


class TestMembers:
    def test_members_parse_with_all_fields(self) -> None:
        payload = normal_discovery_payload()
        family = parse_discovery(payload)

        alice = family.members[0]
        assert alice.account_id == "acc1"
        assert alice.display_name == "Alice Smith"
        assert alice.first_name == "Alice"
        assert alice.timezone == "Australia/Sydney"
        assert alice.is_authenticated_member is True

        bob = family.members[1]
        assert bob.account_id == "acc2"
        assert bob.display_name == "Bob Smith"
        assert bob.first_name == "Bob"
        assert bob.timezone == "Europe/London"
        assert bob.is_authenticated_member is False

    def test_absent_timezone_yields_none(self) -> None:
        payload = normal_discovery_payload()
        payload["members"][0].pop("timeZone", None)
        family = parse_discovery(payload)

        alice = family.members[0]
        assert alice.timezone is None

    def test_absent_firstname_yields_none(self) -> None:
        payload = normal_discovery_payload()
        payload["members"][0].pop("firstName", None)
        family = parse_discovery(payload)

        alice = family.members[0]
        assert alice.first_name is None

    def test_empty_string_timezone_yields_none(self) -> None:
        payload = normal_discovery_payload()
        payload["members"][0]["timeZone"] = ""
        family = parse_discovery(payload)

        alice = family.members[0]
        assert alice.timezone is None

    def test_empty_string_firstname_yields_none(self) -> None:
        payload = normal_discovery_payload()
        payload["members"][0]["firstName"] = ""
        family = parse_discovery(payload)

        alice = family.members[0]
        assert alice.first_name is None


class TestAuthenticatedMember:
    def test_exactly_one_authenticated_member(self) -> None:
        payload = normal_discovery_payload()
        family = parse_discovery(payload)

        authenticated = [m for m in family.members if m.is_authenticated_member]
        assert len(authenticated) == 1
        assert authenticated[0].account_id == "acc1"

    def test_to_context_uses_authenticated_member(self) -> None:
        payload = normal_discovery_payload()
        family = parse_discovery(payload)

        context = family.to_context()
        assert context.account_id == "acc1"
        assert context.family_id == "1234567"
        assert context.calendar_id == "calendar/1234567"
        assert context.verified is True


class TestMultiFamily:
    def test_two_element_array_raises_unsupported_configuration_error(self) -> None:
        payload = two_family_array_payload()

        with pytest.raises(UnsupportedConfigurationError):
            parse_discovery(payload)


class TestMissingAuthenticatedMember:
    def test_no_authenticated_member_raises_malformed_payload_error(self) -> None:
        payload = no_authenticated_member_payload()

        with pytest.raises(MalformedPayloadError):
            parse_discovery(payload)

    def test_to_context_fails_when_no_authenticated_member(self) -> None:
        # Manually override to create a family with no authenticated member
        family = DiscoveredFamily(
            family_id="1234567",
            family_meta_id="family/1234567",
            calendar_id="calendar/1234567",
            name="Test Family",
            members=(
                FamilyMember(
                    account_id="acc1",
                    display_name="Alice",
                    first_name="Alice",
                    timezone="Australia/Sydney",
                    is_authenticated_member=False,
                ),
            ),
        )

        with pytest.raises(MalformedPayloadError):
            family.to_context()


class TestMalformedPayloads:
    def test_missing_family_id_raises_malformed_payload_error(self) -> None:
        payload = missing_family_id_payload()

        with pytest.raises(MalformedPayloadError):
            parse_discovery(payload)

    def test_empty_family_id_raises_malformed_payload_error(self) -> None:
        payload = empty_family_id_payload()

        with pytest.raises(MalformedPayloadError):
            parse_discovery(payload)

    def test_empty_array_raises_malformed_payload_error(self) -> None:
        with pytest.raises(MalformedPayloadError):
            parse_discovery([])

    def test_non_dict_payload_raises_malformed_payload_error(self) -> None:
        with pytest.raises(MalformedPayloadError):
            parse_discovery("not a dict")

    def test_array_with_non_dict_element_raises_malformed_payload_error(self) -> None:
        with pytest.raises(MalformedPayloadError):
            parse_discovery(["not a dict"])

    def test_missing_name_raises_malformed_payload_error(self) -> None:
        payload = normal_discovery_payload()
        payload.pop("name")

        with pytest.raises(MalformedPayloadError):
            parse_discovery(payload)

    def test_missing_metaid_raises_malformed_payload_error(self) -> None:
        payload = normal_discovery_payload()
        payload.pop("metaId")

        with pytest.raises(MalformedPayloadError):
            parse_discovery(payload)

    def test_missing_members_list_raises_malformed_payload_error(self) -> None:
        payload = normal_discovery_payload()
        payload.pop("members")

        with pytest.raises(MalformedPayloadError):
            parse_discovery(payload)

    def test_members_not_list_raises_malformed_payload_error(self) -> None:
        payload = normal_discovery_payload()
        payload["members"] = "not a list"

        with pytest.raises(MalformedPayloadError):
            parse_discovery(payload)

    def test_family_id_as_integer_raises_malformed_payload_error(self) -> None:
        payload = normal_discovery_payload()
        payload["family_id"] = 1234567

        with pytest.raises(MalformedPayloadError):
            parse_discovery(payload)

    def test_meta_id_as_integer_raises_malformed_payload_error(self) -> None:
        payload = normal_discovery_payload()
        payload["metaId"] = 1234567

        with pytest.raises(MalformedPayloadError):
            parse_discovery(payload)

    def test_name_as_integer_raises_malformed_payload_error(self) -> None:
        payload = normal_discovery_payload()
        payload["name"] = 12345

        with pytest.raises(MalformedPayloadError):
            parse_discovery(payload)


class TestStringBooleanParsing:
    def test_string_true_parses_correctly(self) -> None:
        payload = normal_discovery_payload()
        family = parse_discovery(payload)

        authenticated = [m for m in family.members if m.is_authenticated_member]
        assert len(authenticated) == 1

    def test_string_false_parses_correctly(self) -> None:
        payload = normal_discovery_payload()
        family = parse_discovery(payload)

        non_authenticated = [m for m in family.members if not m.is_authenticated_member]
        assert len(non_authenticated) == 1


class TestIsolation:
    def test_concurrent_parse_discovery_independent(self) -> None:
        """Two concurrent discovery parses never share state."""
        payload1 = normal_discovery_payload()
        payload2 = normal_discovery_payload()
        payload2["family_id"] = "9999999"
        payload2["metaId"] = "family/9999999"

        family1 = parse_discovery(payload1)
        family2 = parse_discovery(payload2)

        assert family1.family_id == "1234567"
        assert family2.family_id == "9999999"
        assert family1.calendar_id == "calendar/1234567"
        assert family2.calendar_id == "calendar/9999999"

    def test_discovered_families_are_frozen(self) -> None:
        """DiscoveredFamily is immutable (frozen)."""
        payload = normal_discovery_payload()
        family = parse_discovery(payload)

        with pytest.raises(ValidationError):  # Pydantic frozen raises on assignment
            family.family_id = "999"  # type: ignore[misc]
