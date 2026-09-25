"""Tests for local member-name resolution (services/members.py)."""

from __future__ import annotations

import pytest

from familywall_mcp.familywall.discovery import DiscoveredFamily, FamilyMember
from familywall_mcp.services.members import (
    MemberSelectionError,
    describe_assignment,
    resolve_members,
)


def _member(account_id: str, display_name: str, first_name: str | None) -> FamilyMember:
    """A synthetic family member."""
    return FamilyMember(
        account_id=account_id,
        display_name=display_name,
        first_name=first_name,
        timezone="Australia/Sydney",
        is_authenticated_member=False,
    )


def _family(*members: FamilyMember) -> DiscoveredFamily:
    """A synthetic discovered family with the given members, in order."""
    return DiscoveredFamily(
        family_id="family/1",
        family_meta_id="family/1",
        calendar_id="calendar/1",
        name="Test Family",
        members=members,
    )


def _fullwidth(text: str) -> str:
    """Render ASCII letters and spaces in their fullwidth Unicode forms.

    NFKC-normalising the result collapses it back to ``text``, so this is
    used to build a spelling that only matches after normalisation.
    """
    rendered = []
    for char in text:
        if char == " ":
            rendered.append("　")  # fullwidth space
        elif char.isalpha():
            offset = ord(char.upper()) - ord("A")
            base = 0xFF21 if char.isupper() else 0xFF41
            rendered.append(chr(base + offset))
        else:
            rendered.append(char)
    return "".join(rendered)


def test_t1_normalisation_matches_across_case_whitespace_and_fullwidth_unicode() -> None:
    """T1: messy whitespace/case and a fullwidth-Unicode spelling both match "Robin Smith"."""
    robin = _member("acc-robin", "Robin Smith", "Robin")
    family = _family(robin, _member("acc-other", "Jamie Lee", "Jamie"))

    messy = resolve_members([" rObIn  SMITH "], family)
    fullwidth = resolve_members([_fullwidth("Robin Smith")], family)

    assert messy.account_ids == ("acc-robin",)
    assert fullwidth.account_ids == ("acc-robin",)


def test_t2_exact_full_name_wins_over_first_name_match() -> None:
    """T2: "Sam" resolves to the member displayed as "Sam", not ambiguous with "Sam Lee"."""
    sam = _member("acc-sam", "Sam", "Sam")
    sam_lee = _member("acc-sam-lee", "Sam Lee", "Sam")
    family = _family(sam, sam_lee)

    result = resolve_members(["Sam"], family)

    assert result.account_ids == ("acc-sam",)
    assert result.display_names == ("Sam",)


def test_t3_unique_first_name_resolves() -> None:
    """T3: A unique first name resolves to that member."""
    alice = _member("acc-alice", "Alice Smith", "Alice")
    family = _family(alice, _member("acc-bob", "Bob Jones", "Bob"))

    result = resolve_members(["Alice"], family)

    assert result.account_ids == ("acc-alice",)
    assert result.display_names == ("Alice Smith",)


def test_t4_duplicate_first_name_is_ambiguous() -> None:
    """T4: Two members sharing a first name make that first name ambiguous_member."""
    family = _family(
        _member("acc-1", "Sam Lee", "Sam"),
        _member("acc-2", "Sam Patel", "Sam"),
    )

    with pytest.raises(MemberSelectionError) as exc_info:
        resolve_members(["Sam"], family)

    assert exc_info.value.info.code == "ambiguous_member"


def test_t5_duplicate_display_name_is_ambiguous() -> None:
    """T5: Two members sharing a display name make the full name ambiguous_member."""
    family = _family(
        _member("acc-1", "Jordan Kim", "Jordan"),
        _member("acc-2", "Jordan Kim", "Jordan"),
    )

    with pytest.raises(MemberSelectionError) as exc_info:
        resolve_members(["Jordan Kim"], family)

    assert exc_info.value.info.code == "ambiguous_member"


def test_t6_unknown_name_is_unknown_member() -> None:
    """T6: An unknown name is unknown_member."""
    family = _family(_member("acc-1", "Alice Smith", "Alice"))

    with pytest.raises(MemberSelectionError) as exc_info:
        resolve_members(["Zach"], family)

    assert exc_info.value.info.code == "unknown_member"


def test_t7_whitespace_only_entry_is_invalid_member_name() -> None:
    """T7: A whitespace-only entry is invalid_member_name."""
    family = _family(_member("acc-1", "Alice Smith", "Alice"))

    with pytest.raises(MemberSelectionError) as exc_info:
        resolve_members(["   "], family)

    assert exc_info.value.info.code == "invalid_member_name"


def test_t8_none_and_empty_list_mean_everyone_in_discovery_order() -> None:
    """T8: None and [] both give to_all=True with every member, in discovery order."""
    alice = _member("acc-alice", "Alice Smith", "Alice")
    bob = _member("acc-bob", "Bob Jones", "Bob")
    family = _family(alice, bob)

    none_result = resolve_members(None, family)
    empty_result = resolve_members([], family)

    for result in (none_result, empty_result):
        assert result.to_all is True
        assert result.account_ids == ("acc-alice", "acc-bob")
        assert result.display_names == ("Alice Smith", "Bob Jones")


def test_t9_duplicate_reference_dedupes_and_distinct_order_is_preserved() -> None:
    """T9: ["Robin", "robin smith"] gives one ID; several distinct names keep their order."""
    robin = _member("acc-robin", "Robin Smith", "Robin")
    family = _family(robin, _member("acc-other", "Jamie Lee", "Jamie"))

    dedup_result = resolve_members(["Robin", "robin smith"], family)
    assert dedup_result.account_ids == ("acc-robin",)

    order_family = _family(
        _member("acc-a", "Anna Bell", "Anna"),
        _member("acc-b", "Ben Cole", "Ben"),
        _member("acc-c", "Cara Diaz", "Cara"),
    )
    ordered_result = resolve_members(["Cara", "Anna", "Ben"], order_family)
    assert ordered_result.account_ids == ("acc-c", "acc-a", "acc-b")


def test_t10_naming_every_member_individually_gives_to_all_false() -> None:
    """T10: Naming every member individually still gives to_all=False."""
    alice = _member("acc-alice", "Alice Smith", "Alice")
    bob = _member("acc-bob", "Bob Jones", "Bob")
    family = _family(alice, bob)

    result = resolve_members(["Alice Smith", "Bob Jones"], family)

    assert result.to_all is False
    assert result.account_ids == ("acc-alice", "acc-bob")


def test_t11_name_from_a_different_family_is_unknown_member() -> None:
    """T11: A name that only exists in a different DiscoveredFamily is unknown_member."""
    _family(_member("acc-other", "Riley Fox", "Riley"))  # only in a sibling family
    this_family = _family(_member("acc-1", "Alice Smith", "Alice"))

    with pytest.raises(MemberSelectionError) as exc_info:
        resolve_members(["Riley"], this_family)

    assert exc_info.value.info.code == "unknown_member"


def test_t12_no_substring_matching() -> None:
    """T12: "Rob" does not substring-match "Robin Smith"; unknown_member."""
    family = _family(_member("acc-robin", "Robin Smith", "Robin"))

    with pytest.raises(MemberSelectionError) as exc_info:
        resolve_members(["Rob"], family)

    assert exc_info.value.info.code == "unknown_member"


def test_t13_error_text_is_static_and_never_echoes_input_or_member_names() -> None:
    """T13: Error message/recovery text never include the input or any member name."""
    family = _family(
        _member("acc-1", "Alice Wonderland", "Alice"),
        _member("acc-2", "Bob Marleyton", "Bob"),
    )
    sensitive_input = "Zzyzx Nobody"

    with pytest.raises(MemberSelectionError) as exc_info:
        resolve_members([sensitive_input], family)

    text = f"{exc_info.value.info.message} {exc_info.value.info.recovery}"
    assert sensitive_input not in text
    assert "Alice" not in text
    assert "Wonderland" not in text
    assert "Bob" not in text
    assert "Marleyton" not in text

    # Also true for the other two error codes.
    with pytest.raises(MemberSelectionError) as ambiguous_info:
        resolve_members(["   "], family)
    ambiguous_text = f"{ambiguous_info.value.info.message} {ambiguous_info.value.info.recovery}"
    assert "Alice" not in ambiguous_text
    assert "Bob" not in ambiguous_text

    twins = _family(
        _member("acc-1", "Alice Wonderland", "Alice"),
        _member("acc-3", "Alice Liddell", "Alice"),
    )
    with pytest.raises(MemberSelectionError) as twin_info:
        resolve_members(["alice"], twins)
    assert twin_info.value.info.code == "ambiguous_member"
    twin_text = f"{twin_info.value.info.message} {twin_info.value.info.recovery}"
    assert "Alice" not in twin_text
    assert "Liddell" not in twin_text


def test_t14_describe_assignment_reports_names_unresolved_and_everyone_passthrough() -> None:
    """T14: describe_assignment maps known IDs to names, counts unknown IDs, and
    passes to_all through unchanged, including None."""
    family = _family(
        _member("acc-alice", "Alice Smith", "Alice"),
        _member("acc-bob", "Bob Jones", "Bob"),
    )

    view = describe_assignment(("acc-alice", "acc-unknown", "acc-bob"), False, family)
    assert view.names == ("Alice Smith", "Bob Jones")
    assert view.unresolved == 1
    assert view.everyone is False

    view_true = describe_assignment(("acc-alice",), True, family)
    assert view_true.everyone is True

    view_none = describe_assignment(("acc-alice",), None, family)
    assert view_none.everyone is None
