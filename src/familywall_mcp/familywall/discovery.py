"""Family discovery: extracting FamilyContext from authenticated login payloads."""

from __future__ import annotations

from typing import Any

from familywall_mcp.errors import MalformedPayloadError, UnsupportedConfigurationError
from familywall_mcp.familywall.wire import coerce_bool
from familywall_mcp.models import DomainModel, FamilyContext


class FamilyMember(DomainModel):
    """A family member as discovered during login."""

    account_id: str
    display_name: str
    first_name: str | None
    timezone: str | None
    is_authenticated_member: bool


class DiscoveredFamily(DomainModel):
    """A family and its members, discovered during login."""

    family_id: str
    family_meta_id: str
    calendar_id: str
    name: str
    members: tuple[FamilyMember, ...]

    def to_context(self) -> FamilyContext:
        """Build a verified FamilyContext from this discovery, finding the authenticated member.

        Returns:
            A verified FamilyContext with the authenticated principal's account_id.

        Raises:
            MalformedPayloadError: If no member is marked as authenticated.
        """
        authenticated = next(
            (m for m in self.members if m.is_authenticated_member),
            None,
        )
        if authenticated is None:
            raise MalformedPayloadError()

        return FamilyContext(
            account_id=authenticated.account_id,
            family_id=self.family_id,
            calendar_id=self.calendar_id,
        )


def build_discovery_fields() -> dict[str, str]:
    """Build the request fields for family discovery.

    Returns:
        A dict of form fields to send to accgetallfamily.
    """
    return {
        "partnerScope": "Family",
        "a01call": "prfgetProfiles",
    }


def parse_discovery(payload: object) -> DiscoveredFamily:
    """Parse a family discovery payload, enforcing the single-family rule.

    The payload is expected to be the unwrapped result from accgetallfamily,
    which is either:
    - A single family object
    - An array containing exactly one family object
    - (anything else raises UnsupportedConfigurationError or MalformedPayloadError)

    Args:
        payload: The unwrapped result from the discovery endpoint.

    Returns:
        A DiscoveredFamily with all members parsed.

    Raises:
        UnsupportedConfigurationError: If the payload contains more than one family.
        MalformedPayloadError: If family_id is missing, empty, or no authenticated member.
    """
    families = _normalize_families(payload)

    if len(families) != 1:
        raise UnsupportedConfigurationError()

    return _parse_family(families[0])


def _normalize_families(payload: object) -> list[Any]:
    """Normalize a discovery payload to a list of family dicts.

    Args:
        payload: Either a dict (single family) or a list (possibly multiple families).

    Returns:
        A list of family dicts.

    Raises:
        UnsupportedConfigurationError: If payload suggests multiple families.
        MalformedPayloadError: If payload structure is invalid.
    """
    if isinstance(payload, dict):
        return [payload]

    if isinstance(payload, list):
        if len(payload) == 0:
            raise MalformedPayloadError()
        if len(payload) > 1:
            raise UnsupportedConfigurationError()
        if not isinstance(payload[0], dict):
            raise MalformedPayloadError()
        return payload

    raise MalformedPayloadError()


def _parse_family(family: dict[str, Any]) -> DiscoveredFamily:
    """Parse a single family dict into DiscoveredFamily.

    Args:
        family: A family dict from the discovery payload.

    Returns:
        A DiscoveredFamily with all validation applied.

    Raises:
        MalformedPayloadError: If required fields are missing or invalid.
    """
    family_id_raw = family.get("family_id")
    if not isinstance(family_id_raw, str):
        raise MalformedPayloadError()
    family_id = family_id_raw.strip()
    if not family_id:
        raise MalformedPayloadError()

    family_meta_id_raw = family.get("metaId")
    if not isinstance(family_meta_id_raw, str):
        raise MalformedPayloadError()
    family_meta_id = family_meta_id_raw.strip()
    if not family_meta_id:
        raise MalformedPayloadError()

    name_raw = family.get("name")
    if not isinstance(name_raw, str):
        raise MalformedPayloadError()
    name = name_raw.strip()
    if not name:
        raise MalformedPayloadError()

    calendar_id = f"calendar/{family_id}"

    members_list = family.get("members")
    if not isinstance(members_list, list):
        raise MalformedPayloadError()

    members = tuple(_parse_member(m) for m in members_list)

    # Verify at least one member is authenticated
    if not any(m.is_authenticated_member for m in members):
        raise MalformedPayloadError()

    return DiscoveredFamily(
        family_id=family_id,
        family_meta_id=family_meta_id,
        calendar_id=calendar_id,
        name=name,
        members=members,
    )


def _parse_member(member: Any) -> FamilyMember:
    """Parse a single family member dict.

    Args:
        member: A member dict from the family.

    Returns:
        A FamilyMember with all fields parsed.

    Raises:
        MalformedPayloadError: If required fields are missing or invalid.
    """
    if not isinstance(member, dict):
        raise MalformedPayloadError()

    account_id = member.get("accountId", "").strip()
    if not account_id:
        raise MalformedPayloadError()

    display_name = member.get("name", "").strip()
    if not display_name:
        raise MalformedPayloadError()

    first_name = member.get("firstName")
    if isinstance(first_name, str):
        first_name = first_name.strip() or None
    elif first_name is not None:
        raise MalformedPayloadError()

    timezone = member.get("timeZone")
    if isinstance(timezone, str):
        timezone = timezone.strip() or None
    elif timezone is not None:
        raise MalformedPayloadError()

    is_authenticated_str = member.get("isloggedaccount")
    is_authenticated = coerce_bool(is_authenticated_str)

    return FamilyMember(
        account_id=account_id,
        display_name=display_name,
        first_name=first_name,
        timezone=timezone,
        is_authenticated_member=is_authenticated,
    )
