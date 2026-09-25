"""Local resolution of family-member names to FamilyWall account IDs.

Assigning tools take a list of member names (or none, meaning everyone)
rather than raw account IDs, and account IDs never cross the tool boundary in
either direction. This module is the seam: :func:`resolve_members` turns
names into IDs using only the family's own cached discovery, and
:func:`describe_assignment` turns IDs back into names for tool output.

No hosted discovery refresh happens here. A name unknown to the cached
discovery simply resolves as ``unknown_member``; refreshing discovery on an
unknown name is a later slice's job, once names are first used for a write.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from pydantic import model_validator

from familywall_mcp.errors import ErrorInfo, FamilyWallError
from familywall_mcp.familywall.discovery import DiscoveredFamily, FamilyMember
from familywall_mcp.models import DomainModel

_RECOVERY = "Call list_family_members for the exact names."

INVALID_MEMBER_NAME = ErrorInfo(
    code="invalid_member_name",
    message="A member name was empty once normalised.",
    recovery=_RECOVERY,
)

UNKNOWN_MEMBER = ErrorInfo(
    code="unknown_member",
    message="A member name did not match anyone in this family.",
    recovery=_RECOVERY,
)

AMBIGUOUS_MEMBER = ErrorInfo(
    code="ambiguous_member",
    message="A member name matched more than one person in this family.",
    recovery=_RECOVERY,
)


class MemberSelectionError(FamilyWallError):
    """A name in an assignment request could not be resolved to one member.

    Messages and recovery text are static: they never include the input
    string or any member's name, so a resolution failure cannot leak family
    data into logs, prompts or error text.
    """

    default = INVALID_MEMBER_NAME


class ResolvedAssignment(DomainModel):
    """Local names resolved to FamilyWall account IDs, ready for a write."""

    to_all: bool
    """Whether every family member is meant, as opposed to a named subset."""

    account_ids: tuple[str, ...]
    """Account IDs to assign, de-duplicated and in first-seen order. Never empty."""

    display_names: tuple[str, ...]
    """Display names for ``account_ids``: same length and order."""

    @model_validator(mode="after")
    def _validate_shape(self) -> ResolvedAssignment:
        if not self.account_ids:
            raise ValueError("account_ids must not be empty")
        if len(self.account_ids) != len(self.display_names):
            raise ValueError("account_ids and display_names must have the same length")
        return self


class AssignmentView(DomainModel):
    """A safe, name-only view of an assignment for tool output. Never IDs."""

    names: tuple[str, ...]
    """Display names for the IDs found in the family, in the given order."""

    everyone: bool | None
    """The upstream "assigned to everyone" flag, passed through unchanged."""

    unresolved: int
    """Count of IDs that were not found in the family."""


def _normalise(name: str) -> str:
    """NFKC-normalise, casefold, collapse internal whitespace, and strip."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return " ".join(folded.split())


def _first_name(member: FamilyMember) -> str:
    """The member's first name: ``first_name`` if set, else display_name's first token."""
    if member.first_name:
        return member.first_name
    tokens = member.display_name.split()
    return tokens[0] if tokens else ""


def resolve_members(names: Sequence[str] | None, family: DiscoveredFamily) -> ResolvedAssignment:
    """Resolve a list of member names (or none) to FamilyWall account IDs.

    ``None`` or an empty sequence means everyone: every member of ``family``,
    in discovery order. A named list is always ``to_all=False``, even if it
    happens to name every member.

    Each name is normalised with NFKC + casefold, its internal whitespace
    collapsed to one space, and stripped. Matching tries an exact normalised
    ``display_name`` first (one match wins, more than one is
    ``ambiguous_member``); only if there is no full-name match does it fall
    back to an exact normalised first name (``first_name`` if set, else
    ``display_name``'s first token), with the same one-match-wins,
    more-than-one-is-ambiguous rule, and no match at all is
    ``unknown_member``. There is no substring, prefix or fuzzy matching.
    Repeated references to the same member are de-duplicated by account ID,
    keeping first-seen order.

    Args:
        names: Names to resolve, or ``None``/``[]`` for everyone.
        family: The discovered family to resolve names against.

    Returns:
        A ResolvedAssignment.

    Raises:
        MemberSelectionError: ``invalid_member_name`` for a name that is
            empty once normalised, ``unknown_member`` for a name matching
            nobody, and ``ambiguous_member`` for a name matching more than
            one member. Messages and recovery text are static.
    """
    if not names:
        return ResolvedAssignment(
            to_all=True,
            account_ids=tuple(member.account_id for member in family.members),
            display_names=tuple(member.display_name for member in family.members),
        )

    account_ids: list[str] = []
    display_names: list[str] = []
    seen_account_ids: set[str] = set()

    for raw_name in names:
        normalised = _normalise(raw_name)
        if not normalised:
            raise MemberSelectionError(INVALID_MEMBER_NAME)

        member = _match_member(normalised, family)

        if member.account_id not in seen_account_ids:
            seen_account_ids.add(member.account_id)
            account_ids.append(member.account_id)
            display_names.append(member.display_name)

    return ResolvedAssignment(
        to_all=False,
        account_ids=tuple(account_ids),
        display_names=tuple(display_names),
    )


def _match_member(normalised_name: str, family: DiscoveredFamily) -> FamilyMember:
    """Match one normalised name to exactly one member of ``family``, or raise."""
    full_matches = [
        member for member in family.members if _normalise(member.display_name) == normalised_name
    ]
    if len(full_matches) == 1:
        return full_matches[0]
    if len(full_matches) > 1:
        raise MemberSelectionError(AMBIGUOUS_MEMBER)

    first_name_matches = [
        member for member in family.members if _normalise(_first_name(member)) == normalised_name
    ]
    if len(first_name_matches) == 1:
        return first_name_matches[0]
    if len(first_name_matches) > 1:
        raise MemberSelectionError(AMBIGUOUS_MEMBER)

    raise MemberSelectionError(UNKNOWN_MEMBER)


def describe_assignment(
    account_ids: Sequence[str], to_all: bool | None, family: DiscoveredFamily
) -> AssignmentView:
    """Describe a resolved or wire-read assignment as names only, never IDs.

    Args:
        account_ids: Account IDs to describe, in order.
        to_all: The upstream "assigned to everyone" flag, passed through
            unchanged (including ``None`` when the field itself was absent).
        family: The discovered family to resolve names against.

    Returns:
        An AssignmentView with names for the IDs found in ``family`` (in the
        given order), ``everyone`` equal to ``to_all``, and ``unresolved``
        counting the IDs not found in ``family``. Never returns account IDs.
    """
    display_name_by_id = {member.account_id: member.display_name for member in family.members}
    known_ids = [account_id for account_id in account_ids if account_id in display_name_by_id]
    names = tuple(display_name_by_id[account_id] for account_id in known_ids)
    unresolved = len(account_ids) - len(known_ids)
    return AssignmentView(names=names, everyone=to_all, unresolved=unresolved)
