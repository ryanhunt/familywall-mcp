"""Synthetic test payloads for shopping list adapters."""

from __future__ import annotations


def list_summaries_bare_array() -> list[dict[str, object]]:
    """Bare array of four lists: shopping, todos, other, and unknown type."""
    return [
        {
            "metaId": "taskList/101",
            "name": "Weekly shop",
            "taskListType": "SHOPPING_LIST",
            "totalTaskNumber": 12,
            "remainingTaskNumber": 8,
            "color": "#FF5733",
            "systemId": None,
            "accountId": "acc1",
            "familyId": "fam1",
            "creationDate": "2026-09-01T10:00:00.000Z",
            "lastActionDate": "2026-09-13T15:30:00.000Z",
            "lastActionAuthor": "user1",
            "comments": 0,
            "medias": [],
            "moodMap": {},
            "emoji": "🛒",
            "alexa": False,
            "bestMoment": None,
            "clientOpId": None,
            "completedHidden": False,
            "moodStarShortcut": None,
            "rights": "OWNER",
            "sharedMemberIds": ["user2"],
            "sharedToAll": False,
            "sortingIndex": 0,
            "taskCategoriesHidden": False,
            "taskSorting": "CUSTOM",
            "pinSortingIndex": None,
        },
        {
            "metaId": "taskList/102",
            "name": "Hardware",
            "taskListType": "TODOS",
            "totalTaskNumber": 5,
            "remainingTaskNumber": 3,
            "color": "#3366FF",
            "systemId": None,
            "accountId": "acc1",
            "familyId": "fam1",
            "creationDate": "2026-08-15T12:00:00.000Z",
            "lastActionDate": "2026-09-10T09:15:00.000Z",
            "lastActionAuthor": "user1",
            "comments": 1,
            "medias": [],
            "moodMap": {},
            "emoji": "🔨",
            "alexa": False,
            "bestMoment": None,
            "clientOpId": None,
            "completedHidden": False,
            "moodStarShortcut": None,
            "rights": "OWNER",
            "sharedMemberIds": [],
            "sharedToAll": False,
            "sortingIndex": 1,
            "taskCategoriesHidden": False,
            "taskSorting": "CUSTOM",
            "pinSortingIndex": None,
        },
        {
            "metaId": "taskList/103",
            "name": "Müsli",
            "taskListType": "OTHER",
            "totalTaskNumber": 2,
            "remainingTaskNumber": 1,
            "color": "#33FF66",
            "systemId": None,
            "accountId": "acc1",
            "familyId": "fam1",
            "creationDate": "2026-09-05T08:30:00.000Z",
            "lastActionDate": "2026-09-12T16:45:00.000Z",
            "lastActionAuthor": "user2",
            "comments": 0,
            "medias": [],
            "moodMap": {},
            "emoji": "🥣",
            "alexa": False,
            "bestMoment": None,
            "clientOpId": None,
            "completedHidden": False,
            "moodStarShortcut": None,
            "rights": "EDITOR",
            "sharedMemberIds": ["user1"],
            "sharedToAll": False,
            "sortingIndex": 2,
            "taskCategoriesHidden": False,
            "taskSorting": "CUSTOM",
            "pinSortingIndex": None,
        },
        {
            "metaId": "taskList/104",
            "name": "Meal plan",
            "taskListType": "MEALPLAN",  # Unknown type
            "totalTaskNumber": 7,
            "remainingTaskNumber": 7,
            "color": "#FF66FF",
            "systemId": None,
            "accountId": "acc1",
            "familyId": "fam1",
            "creationDate": "2026-09-10T14:20:00.000Z",
            "lastActionDate": "2026-09-13T11:00:00.000Z",
            "lastActionAuthor": "user1",
            "comments": 2,
            "medias": [],
            "moodMap": {},
            "emoji": "🍽️",
            "alexa": False,
            "bestMoment": None,
            "clientOpId": None,
            "completedHidden": False,
            "moodStarShortcut": None,
            "rights": "OWNER",
            "sharedMemberIds": [],
            "sharedToAll": False,
            "sortingIndex": 3,
            "taskCategoriesHidden": False,
            "taskSorting": "CUSTOM",
            "pinSortingIndex": None,
        },
    ]


def list_summaries_wrapped_lists() -> dict[str, object]:
    """Lists wrapped under 'lists' key."""
    return {"lists": list_summaries_bare_array()}


def list_summaries_wrapped_taskLists() -> dict[str, object]:
    """Lists wrapped under 'taskLists' key."""
    return {"taskLists": list_summaries_bare_array()}


def list_summaries_wrapped_results() -> dict[str, object]:
    """Lists wrapped under 'results' key."""
    return {"results": list_summaries_bare_array()}


def list_items_bare_array() -> list[dict[str, object]]:
    """Bare array of items with various states and data."""
    return [
        {
            "metaId": "task/201",
            "taskListId": "taskList/101",
            "taskId": 201,
            "text": "Milk",
            "complete": "true",
            "description": "Whole milk, 2L",
            "accountId": "acc2",
            "categories": [{"name": "Dairy", "system": "false"}],
            "creationDate": "2026-09-10T08:00:00.000Z",
            "completedDate": "2026-09-12T14:30:00.000Z",
            "modifDate": "2026-09-12T14:30:00.000Z",
            "assignee": "user1",
            "assigneeIds": ["user1"],
            "bestMoment": None,
            "clientOpId": None,
            "comments": 0,
            "editable": True,
            "familyId": "fam1",
            "lastAction": "COMPLETE",
            "lastActionAuthor": "user1",
            "lastActionDate": "2026-09-12T14:30:00.000Z",
            "medias": [],
            "moodMap": {},
            "moodStarShortcut": None,
            "recurrency": None,
            "recurrencyDeletedOccurence": None,
            "reminder": None,
            "sortingIndex": 0,
            "taskCategoryId": None,
            "toAll": False,
        },
        {
            "metaId": "task/202",
            "taskListId": "taskList/101",
            "taskId": 202,
            "text": "Bread",
            "complete": "false",
            "description": None,
            "accountId": "acc1",
            "categories": [],
            "creationDate": "2026-09-11T10:15:00.000Z",
            "completedDate": None,
            "modifDate": "2026-09-11T10:15:00.000Z",
            "assignee": None,
            "assigneeIds": [],
            "bestMoment": None,
            "clientOpId": None,
            "comments": 0,
            "editable": True,
            "familyId": "fam1",
            "lastAction": "CREATE",
            "lastActionAuthor": "user1",
            "lastActionDate": "2026-09-11T10:15:00.000Z",
            "medias": [],
            "moodMap": {},
            "moodStarShortcut": None,
            "recurrency": None,
            "recurrencyDeletedOccurence": None,
            "reminder": None,
            "sortingIndex": 1,
            "taskCategoryId": None,
            "toAll": False,
        },
        {
            "metaId": "task/203",
            "taskListId": "taskList/101",
            "taskId": 203,
            "text": "Café ☕",  # Unicode text
            "complete": "false",
            "description": "Ground coffee, arabica",
            "accountId": "acc3",
            "categories": [
                {"name": "Beverages", "system": "false"},
                {"name": "Premium", "system": "true"},
            ],
            "creationDate": "2026-09-13T06:00:00.000Z",
            "completedDate": None,
            "modifDate": "2026-09-13T06:00:00.000Z",
            "assignee": None,
            "assigneeIds": [],
            "bestMoment": None,
            "clientOpId": None,
            "comments": 0,
            "editable": True,
            "familyId": "fam1",
            "lastAction": "CREATE",
            "lastActionAuthor": "user2",
            "lastActionDate": "2026-09-13T06:00:00.000Z",
            "medias": [],
            "moodMap": {},
            "moodStarShortcut": None,
            "recurrency": None,
            "recurrencyDeletedOccurence": None,
            "reminder": None,
            "sortingIndex": 2,
            "taskCategoryId": None,
            "toAll": False,
        },
        {
            "metaId": "task/204",
            "taskListId": "taskList/101",
            "taskId": 204,
            "text": "Butter",
            "complete": "false",
            "description": None,
            "accountId": "acc1",
            "categories": [{"name": "Dairy", "system": "false"}],
            "creationDate": "2026-09-12T09:00:00.000Z",
            "completedDate": None,
            "modifDate": "2026-09-12T09:00:00.000Z",
            "assignee": "user2",
            "assigneeIds": ["user2"],
            "bestMoment": None,
            "clientOpId": None,
            "comments": 0,
            "editable": True,
            "familyId": "fam1",
            "lastAction": "CREATE",
            "lastActionAuthor": "user1",
            "lastActionDate": "2026-09-12T09:00:00.000Z",
            "medias": [],
            "moodMap": {},
            "moodStarShortcut": None,
            "recurrency": None,
            "recurrencyDeletedOccurence": None,
            "reminder": None,
            "sortingIndex": 3,
            "taskCategoryId": None,
            "toAll": False,
        },
    ]


def list_items_wrapped_items() -> dict[str, object]:
    """Items wrapped under 'items' key."""
    return {"items": list_items_bare_array()}


def list_items_wrapped_tasks() -> dict[str, object]:
    """Items wrapped under 'tasks' key."""
    return {"tasks": list_items_bare_array()}


def list_items_wrapped_listItems() -> dict[str, object]:
    """Items wrapped under 'listItems' key."""
    return {"listItems": list_items_bare_array()}


def list_items_with_malformed_entry() -> list[dict[str, object]]:
    """Array with one good item, one malformed, two more good items."""
    items = list_items_bare_array()
    # Keep first 3, add a malformed one, then add 4th
    good_items = items[:3]
    malformed = {"metaId": "task/999"}  # Missing required fields
    return good_items[:2] + [malformed] + good_items[2:]
