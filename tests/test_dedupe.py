"""Tests for deduplication: URL canonicalization and content hashing."""

from __future__ import annotations

from datetime import UTC, datetime

from msrkit.dedupe import canonicalize_url, content_hash, deduplicate
from msrkit.models import (
    Item,
    ItemKind,
    Provenance,
)


class TestCanonicalizeUrl:
    """Tests for URL canonicalization."""

    def test_lowercase_host(self) -> None:
        assert "example.com" in canonicalize_url("https://EXAMPLE.COM/path")

    def test_remove_utm_params(self) -> None:
        url = "https://example.com/article?utm_source=twitter&utm_medium=social"
        result = canonicalize_url(url)
        assert "utm_" not in result

    def test_remove_ref_param(self) -> None:
        url = "https://example.com/page?ref=homepage"
        result = canonicalize_url(url)
        assert "ref=" not in result

    def test_remove_trailing_slash(self) -> None:
        url = "https://example.com/path/"
        result = canonicalize_url(url)
        assert result.endswith("/path")

    def test_keep_root_slash(self) -> None:
        url = "https://example.com/"
        result = canonicalize_url(url)
        assert result.endswith("/")

    def test_preserve_meaningful_params(self) -> None:
        url = "https://example.com/search?q=test&page=2"
        result = canonicalize_url(url)
        assert "q=" in result
        assert "page=" in result

    def test_sort_params(self) -> None:
        url1 = "https://example.com/search?page=2&q=test"
        url2 = "https://example.com/search?q=test&page=2"
        assert canonicalize_url(url1) == canonicalize_url(url2)


class TestContentHash:
    """Tests for content-based hashing."""

    def test_same_content_same_hash(self) -> None:
        h1 = content_hash("Title", "Body text")
        h2 = content_hash("Title", "Body text")
        assert h1 == h2

    def test_different_content_different_hash(self) -> None:
        h1 = content_hash("Title A", "Body A")
        h2 = content_hash("Title B", "Body B")
        assert h1 != h2

    def test_whitespace_normalization(self) -> None:
        h1 = content_hash("Title", "Body   with   spaces")
        h2 = content_hash("Title", "Body with spaces")
        assert h1 == h2

    def test_none_values(self) -> None:
        h = content_hash(None, None)
        assert len(h) == 64  # Still produces a valid hash


class TestDeduplicate:
    """Tests for the deduplicate function."""

    def test_no_duplicates(self) -> None:
        items = [
            _make_item("1", "https://a.com", title="Article A"),
            _make_item("2", "https://b.com", title="Article B"),
        ]
        unique, dupes = deduplicate(items)
        assert len(unique) == 2
        assert len(dupes) == 0

    def test_url_duplicate(self) -> None:
        items = [
            _make_item("1", "https://example.com/page"),
            _make_item("2", "https://example.com/page?utm_source=twitter"),
        ]
        unique, dupes = deduplicate(items)
        assert len(unique) == 1
        assert len(dupes) == 1

    def test_content_duplicate(self) -> None:
        items = [
            _make_item("1", "https://a.com", title="Same Title", body="Same Body"),
            _make_item("2", "https://b.com", title="Same Title", body="Same Body"),
        ]
        unique, dupes = deduplicate(items)
        assert len(unique) == 1
        assert len(dupes) == 1

    def test_idempotent(self) -> None:
        """Deduplicating twice gives the same result (spec §15)."""
        items = [
            _make_item("1", "https://a.com", title="T1", body="B1"),
            _make_item("2", "https://b.com", title="T1", body="B1"),  # dup
            _make_item("3", "https://c.com", title="T2", body="B2"),
        ]
        unique1, _ = deduplicate(items)
        unique2, dupes2 = deduplicate(unique1)
        assert len(unique1) == len(unique2)
        assert len(dupes2) == 0

    def test_dedupe_items_without_content_not_colliding(self) -> None:
        """Items with no title and no body must not collide on empty string content hash."""
        items = [
            _make_item("1", "https://github.com/org1/file1.py", title="", body=None),
            _make_item("2", "https://github.com/org2/file2.py", title="", body=None),
        ]
        unique, dupes = deduplicate(items)
        assert len(unique) == 2
        assert len(dupes) == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    native_id: str,
    url: str,
    title: str = "Test",
    body: str | None = None,
) -> Item:
    return Item(
        id=Item.make_id("test", native_id),
        source="test",
        kind=ItemKind.ARTICLE,
        url=url,  # type: ignore[arg-type]
        title=title,
        body=body,
        provenance=Provenance(
            run_id="test-run",
            query_string="test",
            partition="p1",
            adapter="test",
            adapter_version="0.1.0",
            fetched_at=datetime.now(UTC),
            response_sha256="abc",
            raw_ref="test:0",
        ),
    )
