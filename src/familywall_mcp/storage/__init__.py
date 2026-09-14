"""Local mutation receipt storage.

This package provides repositories for tracking mutation operations.
See storage/memory.py for the in-memory implementation used during P1-P4.
"""

from __future__ import annotations

from .memory import InMemoryReceiptRepository
from .sqlite import SqliteReceiptRepository

__all__ = ["InMemoryReceiptRepository", "SqliteReceiptRepository"]
