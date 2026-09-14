"""Shopping list and item adapters for FamilyWall wire protocol."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import NamedTuple

from pydantic import Field

from familywall_mcp.errors import MalformedPayloadError
from familywall_mcp.models import DomainModel


class ListType(StrEnum):
    """Known shopping list types."""

    SHOPPING = "SHOPPING_LIST"
    TODOS = "TODOS"
    OTHER = "OTHER"


class ShoppingList(DomainModel):
    """A shopping list summary from the wire protocol."""

    list_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type_raw: str = Field(min_length=1)
    known_type: ListType | None
    total_items: int | None
    remaining_items: int | None
    color: str | None
    system_id: str | None


class ListItem(DomainModel):
    """An item in a shopping list."""

    item_id: str = Field(min_length=1)
    list_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    completed: bool
    description: str | None
    author_account_id: str | None
    category_names: tuple[str, ...]
    created_at: datetime | None
    completed_at: datetime | None


class ParsedItems(NamedTuple):
    """Result of parsing list items, including skipped count."""

    items: tuple[ListItem, ...]
    skipped: int


def _coerce_bool(value: object) -> bool:
    """Parse a boolean from wire format.

    Accepts the strings "true" or "false" (case-sensitive).
    Raises MalformedPayloadError for any other value.
    """
    if value == "true":
        return True
    if value == "false":
        return False
    raise MalformedPayloadError()


def _coerce_int(value: object) -> int | None:
    """Parse an integer from wire format (int or numeric string).

    Returns None if the value is missing (None), absent, or a non-numeric string.
    Raises MalformedPayloadError for wrong types (bool, list, dict, etc).
    Explicitly rejects bool before int check since isinstance(True, int) is True.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # Explicitly reject bool before checking int, since isinstance(True, int) is True
        raise MalformedPayloadError()
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            # Non-numeric string returns None for cosmetic fields
            return None
    raise MalformedPayloadError()


def _parse_iso8601_utc(value: object) -> datetime | None:
    """Parse an ISO-8601 UTC datetime with Z suffix.

    Returns None if the value cannot be parsed.
    Always returns timezone-aware UTC datetime, never naive.
    """
    if not isinstance(value, str):
        return None
    with contextlib.suppress(ValueError, TypeError):
        # fromisoformat in Python 3.11+ handles Z suffix directly
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Ensure it's UTC
        return dt.astimezone(UTC) if dt.tzinfo else None
    return None


def _extract_list_array(payload: object) -> list[object]:
    """Extract a list array from the payload.

    Accepts:
    - A bare array
    - An object with 'lists', 'taskLists', or 'results' key (all as arrays)

    Raises MalformedPayloadError for invalid shapes.
    """
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        # Try each expected wrapper key
        for key in ("lists", "taskLists", "results"):
            if key in payload:
                value = payload[key]
                if isinstance(value, list):
                    return value
                raise MalformedPayloadError()

    raise MalformedPayloadError()


def _extract_items_array(payload: object) -> list[object]:
    """Extract an items array from the payload.

    Accepts:
    - A bare array
    - An object with 'items', 'tasks', or 'listItems' key (all as arrays)

    Raises MalformedPayloadError for invalid shapes.
    """
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        # Try each expected wrapper key
        for key in ("items", "tasks", "listItems"):
            if key in payload:
                value = payload[key]
                if isinstance(value, list):
                    return value
                raise MalformedPayloadError()

    raise MalformedPayloadError()


def _validate_prefix(id_str: str, expected_prefix: str) -> None:
    """Validate that an ID has the expected prefix.

    Raises MalformedPayloadError if the prefix is wrong.
    """
    if not id_str.startswith(expected_prefix):
        raise MalformedPayloadError()


def parse_list_summaries(payload: object) -> tuple[ShoppingList, ...]:
    """Parse a list of shopping list summaries from wire format.

    Accepts a bare array or an object wrapping the array under 'lists',
    'taskLists', or 'results'.

    Raises MalformedPayloadError if:
    - The payload shape is invalid
    - A required field is missing or invalid
    - An ID has the wrong prefix
    """
    array = _extract_list_array(payload)

    lists = []
    for item in array:
        if not isinstance(item, dict):
            raise MalformedPayloadError()

        # Required fields
        meta_id = item.get("metaId")
        name = item.get("name")

        if not isinstance(meta_id, str) or not meta_id:
            raise MalformedPayloadError()
        if not isinstance(name, str) or not name:
            raise MalformedPayloadError()

        _validate_prefix(meta_id, "taskList/")

        # Type handling: preserve raw, compute known
        type_raw = item.get("taskListType", "")
        if not isinstance(type_raw, str) or not type_raw:
            raise MalformedPayloadError()

        known_type = None
        with contextlib.suppress(ValueError):
            known_type = ListType(type_raw)

        # Numeric fields (can be int or string, non-numeric strings yield None)
        total_items = _coerce_int(item.get("totalTaskNumber"))
        remaining_items = _coerce_int(item.get("remainingTaskNumber"))

        # Optional fields
        color = item.get("color")
        if not isinstance(color, str):
            color = None

        system_id = item.get("systemId")
        if not isinstance(system_id, str):
            system_id = None

        lists.append(
            ShoppingList(
                list_id=meta_id,
                name=name,
                type_raw=type_raw,
                known_type=known_type,
                total_items=total_items,
                remaining_items=remaining_items,
                color=color,
                system_id=system_id,
            )
        )

    return tuple(lists)


def parse_list_items(payload: object) -> ParsedItems:
    """Parse a list of items from wire format.

    Accepts a bare array or an object wrapping the array under 'items',
    'tasks', or 'listItems'.

    Returns ParsedItems with parsed items and a count of skipped entries.
    Skipped entries are those that fail validation but don't halt parsing.

    Raises MalformedPayloadError if:
    - The payload shape is invalid
    - An item is missing a required field with wrong prefix (metaId or taskListId)
      These are hard errors that stop parsing; other per-item errors are skipped.
    """
    array = _extract_items_array(payload)

    items = []
    skipped = 0

    for entry in array:
        if not isinstance(entry, dict):
            skipped += 1
            continue

        try:
            # Required fields with validation
            meta_id = entry.get("metaId")
            list_id = entry.get("taskListId")
            text = entry.get("text")

            if not isinstance(meta_id, str) or not meta_id:
                skipped += 1
                continue
            if not isinstance(list_id, str) or not list_id:
                skipped += 1
                continue
            if not isinstance(text, str) or not text:
                skipped += 1
                continue

            _validate_prefix(meta_id, "task/")
            _validate_prefix(list_id, "taskList/")

            # Boolean field
            complete = _coerce_bool(entry.get("complete"))

            # Optional fields with type checking
            description = entry.get("description")
            if not isinstance(description, str):
                description = None

            author_account_id = entry.get("accountId")
            if not isinstance(author_account_id, str):
                author_account_id = None

            # Categories
            categories = entry.get("categories", [])
            if not isinstance(categories, list):
                categories = []
            category_names = tuple(
                cat.get("name", "")
                for cat in categories
                if isinstance(cat, dict) and isinstance(cat.get("name"), str)
            )

            # Dates
            created_at = _parse_iso8601_utc(entry.get("creationDate"))
            completed_at = _parse_iso8601_utc(entry.get("completedDate"))

            items.append(
                ListItem(
                    item_id=meta_id,
                    list_id=list_id,
                    text=text,
                    completed=complete,
                    description=description,
                    author_account_id=author_account_id,
                    category_names=category_names,
                    created_at=created_at,
                    completed_at=completed_at,
                )
            )
        except MalformedPayloadError:
            skipped += 1
            continue

    return ParsedItems(items=tuple(items), skipped=skipped)


def build_get_lists_fields() -> dict[str, str]:
    """Build request fields for get-lists operation.

    Returns:
        Dict with partnerScope field for FamilyWall wire protocol.
    """
    return {"partnerScope": "Family"}


def build_get_list_fields(list_id: str) -> dict[str, str]:
    """Build request fields for get-list operation.

    Args:
        list_id: The list metaId (must have taskList/ prefix)

    Returns:
        Dict with partnerScope and a00listId fields.

    Raises:
        MalformedPayloadError if list_id has wrong prefix.
    """
    _validate_prefix(list_id, "taskList/")
    return {"partnerScope": "Family", "a00listId": list_id}


def build_create_item_fields(text: str) -> dict[str, str]:
    """Build request fields for create-item operation.

    Creates an item in the default list. Use taskmove to place it in a specific list.

    Args:
        text: Item text (will be used as-is)

    Returns:
        Dict with partnerScope and a00text fields only.
    """
    return {
        "partnerScope": "Family",
        "a00text": text,
    }


def build_move_item_fields(item_id: str, list_id: str) -> dict[str, str]:
    """Build request fields for move-item operation.

    Args:
        item_id: The item's metaId (must have task/ prefix)
        list_id: The destination list's metaId (must have taskList/ prefix)

    Returns:
        Dict with partnerScope, a00taskId, and a00taskListId fields.
        Note: prevTaskId and taskCategoryId exist in the real signature but are
        not sent by v1 — see ADR 0002.

    Raises:
        MalformedPayloadError if item_id or list_id have wrong prefixes.
    """
    _validate_prefix(item_id, "task/")
    _validate_prefix(list_id, "taskList/")

    return {
        "partnerScope": "Family",
        "a00taskId": item_id,
        "a00taskListId": list_id,
    }


def build_mark_item_fields(item_id: str, completed: bool) -> dict[str, str]:
    """Build request fields for mark-item operation.

    Args:
        item_id: The item's metaId (must have task/ prefix)
        completed: Whether the item is completed

    Returns:
        Dict with partnerScope, a00taskId, and a00complete fields (as the string "true" or "false").
        Note: this operation carries no list ID, so membership must be verified before
        calling this function.

    Raises:
        MalformedPayloadError if item_id has wrong prefix.
    """
    _validate_prefix(item_id, "task/")
    return {
        "partnerScope": "Family",
        "a00taskId": item_id,
        "a00complete": "true" if completed else "false",
    }
