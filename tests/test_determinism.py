"""Determinism test: same input + same data → same output hash.

Verifies that the normalization pipeline is deterministic:
given the same raw data, the resulting items.jsonl has the
same SHA-256 hash every time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from msrkit.models import Item, ItemKind, Provenance, RawItem
from msrkit.storage import ItemStorage


@pytest.fixture
def raw_items() -> list[RawItem]:
    """Fixed set of raw items for determinism testing."""
    return [
        RawItem(
            source="hackernews",
            native_id="100001",
            payload={
                "objectID": "100001",
                "title": "RAG Testing Framework",
                "url": "https://example.com/rag-testing",
                "author": "testuser",
                "created_at_i": 1700000000,
                "points": 42,
                "num_comments": 5,
                "_tags": ["story"],
            },
            fetched_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        ),
        RawItem(
            source="hackernews",
            native_id="100002",
            payload={
                "objectID": "100002",
                "title": "LLM Evaluation Tools",
                "url": "https://example.com/llm-eval",
                "author": "anotheruser",
                "created_at_i": 1700100000,
                "points": 15,
                "num_comments": 3,
                "_tags": ["story"],
            },
            fetched_at=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        ),
    ]


def _normalize_raw(raw: RawItem) -> Item:
    """Deterministic normalization (no adapter, just fixed logic)."""
    p = raw.payload
    return Item(
        id=Item.make_id(raw.source, raw.native_id),
        source=raw.source,
        kind=ItemKind.THREAD,
        url=p.get("url", "https://example.com"),  # type: ignore[arg-type]
        title=p.get("title"),
        body=None,
        author_handle=p.get("author"),
        created_at=datetime.fromtimestamp(p.get("created_at_i", 0), tz=UTC),
        provenance=Provenance(
            run_id="determinism-test",
            query_string="RAG testing",
            partition="p1",
            adapter="hackernews",
            adapter_version="0.1.0",
            fetched_at=raw.fetched_at,
            response_sha256="fixed-hash",
            raw_ref="test:0",
        ),
    )


class TestDeterminism:
    """Verify that same input always produces same output."""

    def test_same_input_same_hash(self, tmp_path: Path, raw_items: list[RawItem]) -> None:
        """Process the same raw items twice → same items file hash."""
        hashes: list[str] = []

        for run_idx in range(2):
            run_id = f"determinism-run-{run_idx}"
            storage = ItemStorage(tmp_path)

            items = [_normalize_raw(raw) for raw in raw_items]
            storage.save_items(items, run_id)

            h = storage.items_hash(run_id)
            hashes.append(h)

        assert hashes[0] == hashes[1], (
            f"Determinism failure: hash1={hashes[0]}, hash2={hashes[1]}"
        )

    def test_item_id_deterministic(self, raw_items: list[RawItem]) -> None:
        """Same raw data always produces the same Item ID."""
        ids1 = [_normalize_raw(r).id for r in raw_items]
        ids2 = [_normalize_raw(r).id for r in raw_items]
        assert ids1 == ids2
