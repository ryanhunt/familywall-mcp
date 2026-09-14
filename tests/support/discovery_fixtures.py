"""Synthetic discovery payloads for testing."""

from __future__ import annotations


def normal_discovery_payload() -> dict[str, object]:
    """A normal single-family discovery payload (dict form)."""
    return {
        "family_id": "1234567",
        "metaId": "family/1234567",
        "name": "Test Family",
        "members": [
            {
                "accountId": "acc1",
                "firstName": "Alice",
                "name": "Alice Smith",
                "role": "admin",
                "right": "full",
                "color": "#FF0000",
                "timeZone": "Australia/Sydney",
                "familyId": "1234567",
                "isloggedaccount": "true",
            },
            {
                "accountId": "acc2",
                "firstName": "Bob",
                "name": "Bob Smith",
                "role": "member",
                "right": "read",
                "color": "#00FF00",
                "timeZone": "Europe/London",
                "familyId": "1234567",
                "isloggedaccount": "false",
            },
        ],
    }


def single_element_array_payload() -> list[object]:
    """A single-family discovery payload in single-element array form."""
    return [normal_discovery_payload()]


def two_family_array_payload() -> list[object]:
    """A two-family array payload (should raise UnsupportedConfigurationError)."""
    family1 = normal_discovery_payload()
    family2 = {
        "family_id": "7654321",
        "metaId": "family/7654321",
        "name": "Other Family",
        "members": [
            {
                "accountId": "acc3",
                "firstName": "Charlie",
                "name": "Charlie Brown",
                "role": "admin",
                "right": "full",
                "color": "#0000FF",
                "timeZone": "America/New_York",
                "familyId": "7654321",
                "isloggedaccount": "true",
            },
        ],
    }
    return [family1, family2]


def no_authenticated_member_payload() -> dict[str, object]:
    """A payload with no member marked as authenticated."""
    return {
        "family_id": "1234567",
        "metaId": "family/1234567",
        "name": "Test Family",
        "members": [
            {
                "accountId": "acc1",
                "firstName": "Alice",
                "name": "Alice Smith",
                "role": "admin",
                "right": "full",
                "color": "#FF0000",
                "timeZone": "Australia/Sydney",
                "familyId": "1234567",
                "isloggedaccount": "false",
            },
        ],
    }


def missing_family_id_payload() -> dict[str, object]:
    """A payload with missing family_id."""
    return {
        "metaId": "family/1234567",
        "name": "Test Family",
        "members": [
            {
                "accountId": "acc1",
                "firstName": "Alice",
                "name": "Alice Smith",
                "role": "admin",
                "right": "full",
                "color": "#FF0000",
                "timeZone": "Australia/Sydney",
                "familyId": "1234567",
                "isloggedaccount": "true",
            },
        ],
    }


def empty_family_id_payload() -> dict[str, object]:
    """A payload with empty string family_id."""
    return {
        "family_id": "",
        "metaId": "family/1234567",
        "name": "Test Family",
        "members": [
            {
                "accountId": "acc1",
                "firstName": "Alice",
                "name": "Alice Smith",
                "role": "admin",
                "right": "full",
                "color": "#FF0000",
                "timeZone": "Australia/Sydney",
                "familyId": "1234567",
                "isloggedaccount": "true",
            },
        ],
    }
