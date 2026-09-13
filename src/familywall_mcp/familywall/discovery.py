"""Family discovery and context creation from FamilyWall API responses."""

from __future__ import annotations

from familywall_mcp.errors import (
    ErrorInfo,
    MalformedPayloadError,
    UnsupportedConfigurationError,
)
from familywall_mcp.familywall.wire import coerce_bool
from familywall_mcp.models import DomainModel, FamilyContext


class FamilyMember(DomainModel):
    """A family member as returned from the discovery endpoint."""

    account_id: str
    display_name: str
    first_name: str | None
    timezone: str | None
    is_authenticated_member: bool


class DiscoveredFamily(DomainModel):
    """A complete family structure discovered from FamilyWall API."""

    family_id: str
    family_meta_id: str
    calendar_id: str
    name: str
    members: tuple[FamilyMember, ...]

    def to_context(self) -> FamilyContext:
        """Convert to a verified FamilyContext using the authenticated member's account ID.

        Returns:
            A verified FamilyContext with the authenticated member's account ID.

        Raises:
            MalformedPayloadError: If no member is marked as authenticated.
        """
        authenticated_member = next(
            (m for m in self.members if m.is_authenticated_member), None
        )
        if authenticated_member is None:
            raise MalformedPayloadError(
                ErrorInfo(
                    code="malformed_upstream_payload",
                    message="FamilyWall returned data this server could not interpret.",
                    recovery="Report the endpoint; the upstream contract may have changed.",
                    endpoint="accgetallfamily",
                )
            )

        return FamilyContext(
            account_id=authenticated_member.account_id,
            family_id=self.family_id,
            calendar_id=self.calendar_id,
        )


def build_discovery_fields() -> dict[str, str]:
    """Build the form fields for a discovery call.

    Returns:
        A dictionary with the required fields for the accgetallfamily endpoint.
    """
    return {
        "partnerScope": "Family",
        "a01call": "prfgetProfiles",
    }


def parse_discovery(payload: object) -> DiscoveredFamily:
    """Parse a discovery payload into a verified DiscoveredFamily.

    Enforces the single-family rule: exactly one family is required. An array
    with one family is accepted; an array with multiple families is rejected.

    Args:
        payload: The parsed a00.r.r result from accgetallfamily.

    Returns:
        A DiscoveredFamily with verified structure and single-family constraint.

    Raises:
        UnsupportedConfigurationError: If multiple families are present.
        MalformedPayloadError: If the structure is invalid or no family is found.
    """
    family_obj = payload

    # If payload is a single-element array, extract and treat as single family
    if isinstance(payload, list):
        if len(payload) == 0:
            raise MalformedPayloadError(
                ErrorInfo(
                    code="malformed_upstream_payload",
                    message="FamilyWall returned data this server could not interpret.",
                    recovery="Report the endpoint; the upstream contract may have changed.",
                    endpoint="accgetallfamily",
                )
            )
        if len(payload) > 1:
            raise UnsupportedConfigurationError(
                ErrorInfo(
                    code="unsupported_configuration",
                    message="This account is in a configuration this server does not support.",
                    recovery="See the documented limitations; no action was taken.",
                    endpoint="accgetallfamily",
                )
            )
        family_obj = payload[0]

    # Validate the family object is a dict
    if not isinstance(family_obj, dict):
        raise MalformedPayloadError(
            ErrorInfo(
                code="malformed_upstream_payload",
                message="FamilyWall returned data this server could not interpret.",
                recovery="Report the endpoint; the upstream contract may have changed.",
                endpoint="accgetallfamily",
            )
        )

    # Extract family_id
    family_id = family_obj.get("family_id")
    if not isinstance(family_id, str) or not family_id:
        raise MalformedPayloadError(
            ErrorInfo(
                code="malformed_upstream_payload",
                message="FamilyWall returned data this server could not interpret.",
                recovery="Report the endpoint; the upstream contract may have changed.",
                endpoint="accgetallfamily",
            )
        )

    # Extract family_meta_id (metaId in wire format)
    family_meta_id = family_obj.get("metaId")
    if not isinstance(family_meta_id, str) or not family_meta_id:
        raise MalformedPayloadError(
            ErrorInfo(
                code="malformed_upstream_payload",
                message="FamilyWall returned data this server could not interpret.",
                recovery="Report the endpoint; the upstream contract may have changed.",
                endpoint="accgetallfamily",
            )
        )

    # Extract name
    name = family_obj.get("name")
    if not isinstance(name, str) or not name:
        raise MalformedPayloadError(
            ErrorInfo(
                code="malformed_upstream_payload",
                message="FamilyWall returned data this server could not interpret.",
                recovery="Report the endpoint; the upstream contract may have changed.",
                endpoint="accgetallfamily",
            )
        )

    # Derive calendar_id
    calendar_id = f"calendar/{family_id}"

    # Parse members array
    members_data = family_obj.get("members")
    if not isinstance(members_data, list):
        raise MalformedPayloadError(
            ErrorInfo(
                code="malformed_upstream_payload",
                message="FamilyWall returned data this server could not interpret.",
                recovery="Report the endpoint; the upstream contract may have changed.",
                endpoint="accgetallfamily",
            )
        )

    members: list[FamilyMember] = []
    for member_obj in members_data:
        if not isinstance(member_obj, dict):
            raise MalformedPayloadError(
                ErrorInfo(
                    code="malformed_upstream_payload",
                    message="FamilyWall returned data this server could not interpret.",
                    recovery="Report the endpoint; the upstream contract may have changed.",
                    endpoint="accgetallfamily",
                )
            )

        account_id = member_obj.get("accountId")
        if not isinstance(account_id, str) or not account_id:
            raise MalformedPayloadError(
                ErrorInfo(
                    code="malformed_upstream_payload",
                    message="FamilyWall returned data this server could not interpret.",
                    recovery="Report the endpoint; the upstream contract may have changed.",
                    endpoint="accgetallfamily",
                )
            )

        # Display name: prefer 'name' field, fall back to 'firstName'
        display_name = member_obj.get("name")
        if not isinstance(display_name, str) or not display_name:
            display_name = member_obj.get("firstName", "")
        if not isinstance(display_name, str):
            display_name = ""

        first_name = member_obj.get("firstName")
        if not isinstance(first_name, str):
            first_name = None

        # Timezone is optional, can be None if absent
        timezone = member_obj.get("timeZone")
        if not isinstance(timezone, (str, type(None))):
            timezone = None

        # Parse isloggedaccount boolean
        is_authenticated = coerce_bool(member_obj.get("isloggedaccount", "false"))

        member = FamilyMember(
            account_id=account_id,
            display_name=display_name,
            first_name=first_name,
            timezone=timezone,
            is_authenticated_member=is_authenticated,
        )
        members.append(member)

    return DiscoveredFamily(
        family_id=family_id,
        family_meta_id=family_meta_id,
        calendar_id=calendar_id,
        name=name,
        members=tuple(members),
    )
