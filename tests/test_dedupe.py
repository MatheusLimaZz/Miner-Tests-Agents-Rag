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

    def test_root_slash_equivalence(self) -> None:
        """Root URLs with and without trailing slash produce identical canonical URLs."""
        assert canonicalize_url("https://example.com") == canonicalize_url("https://example.com/")
        assert canonicalize_url("https://example.com") == "https://example.com/"

    def test_preserve_meaningful_params(self) -> None:
        url = "https://example.com/search?q=test&page=2"
        result = canonicalize_url(url)
        assert "q=" in result
        assert "page=" in result

    def test_sort_params(self) -> None:
        url1 = "https://example.com/search?page=2&q=test"
        url2 = "https://example.com/search?q=test&page=2"
        assert canonicalize_url(url1) == canonicalize_url(url2)

    def test_scheme_normalization_and_www(self) -> None:
        assert canonicalize_url("http://www.example.com/path") == "https://example.com/path"

    def test_git_suffix_stripping(self) -> None:
        assert (
            canonicalize_url("https://github.com/owner/repo.git") == "https://github.com/owner/repo"
        )

    def test_git_forge_case_insensitivity(self) -> None:
        assert (
            canonicalize_url("https://GitHub.com/LangChain-AI/LangChain.git")
            == "https://github.com/langchain-ai/langchain"
        )

    def test_huggingface_dataset_canonicalization(self) -> None:
        assert (
            canonicalize_url("https://HuggingFace.co/datasets/Owner/MyDataset.git")
            == "https://huggingface.co/datasets/owner/mydataset"
        )
        assert (
            canonicalize_url("https://huggingface.co/spaces/Owner/MySpace")
            == "https://huggingface.co/spaces/owner/myspace"
        )


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

    def test_code_files_with_same_filename_different_repos_not_colliding(self) -> None:
        """Code files in different repos sharing the same filename must NOT be treated as dupes."""
        item1 = _make_item(
            "1",
            "https://github.com/org1/repo1/blob/main/test_rag.py",
            title="test_rag.py",
            body=None,
        )
        item2 = _make_item(
            "2",
            "https://github.com/org2/repo2/blob/main/test_rag.py",
            title="test_rag.py",
            body=None,
        )
        unique, dupes = deduplicate([item1, item2])
        assert len(unique) == 2
        assert len(dupes) == 0

    def test_posts_with_same_title_no_body_different_urls_not_colliding(self) -> None:
        """Posts with the same generic title but different URLs and no body must not collide."""
        item1 = _make_item(
            "1",
            "https://news.ycombinator.com/item?id=101",
            title="RAG Evaluation",
            body=None,
        )
        item2 = _make_item(
            "2",
            "https://www.reddit.com/r/LocalLLaMA/comments/202",
            title="RAG Evaluation",
            body=None,
        )
        unique, dupes = deduplicate([item1, item2])
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


class TestNearDuplicates:
    """Pass 3: MinHash/LSH near-duplicates (Phase 2, T1)."""

    BASE = (
        "We evaluate our retrieval augmented generation pipeline with a golden dataset of "
        "two hundred questions, measure faithfulness and context recall with an LLM judge, "
        "and fail the CI build when the score drops below the threshold agreed by the team. "
        "The retriever is tested separately with recall at k over a fixed index snapshot."
    )

    def _item(self, n: int, body: str, url: str | None = None) -> Item:
        from datetime import UTC, datetime

        from msrkit.models import ItemKind, Provenance

        return Item(
            id=f"n{n}",
            source="devto",
            kind=ItemKind.ARTICLE,
            url=url or f"https://dev.to/a/{n}",
            title="RAG testing",
            body=body,  # type: ignore[arg-type]
            provenance=Provenance(
                run_id="r",
                query_string="q",
                partition="p",
                adapter="devto",
                adapter_version="1",
                fetched_at=datetime.now(UTC),
                response_sha256="",
                raw_ref="",
            ),
        )

    def test_repost_with_small_edits_is_removed_with_reason(self) -> None:
        from msrkit.dedupe import deduplicate_report

        original = self._item(1, self.BASE)
        repost = self._item(2, self.BASE.replace("two hundred", "200") + " Originally on Medium.")
        different = self._item(3, "Completely different text about agents " * 12)
        report = deduplicate_report([original, repost, different], near_threshold=0.8)
        assert [i.id for i in report.unique] == ["n1", "n3"]
        reason = report.reasons["n2"]
        assert reason.startswith("near:") and reason.endswith(":n1")
        assert report.counts() == {"url": 0, "content": 0, "near": 1}

    def test_disabled_and_short_texts_are_untouched(self) -> None:
        from msrkit.dedupe import deduplicate_report, minhash_signature

        a, b = self._item(1, self.BASE), self._item(2, self.BASE + " Updated.")
        assert len(deduplicate_report([a, b], near_threshold=None).unique) == 2
        assert minhash_signature("too short to compare") is None

    def test_signatures_are_deterministic(self) -> None:
        from msrkit.dedupe import minhash_signature

        assert minhash_signature(self.BASE) == minhash_signature(self.BASE)

    def test_exact_reasons_are_reported(self) -> None:
        from msrkit.dedupe import deduplicate_report

        a = self._item(1, self.BASE)
        same_url = self._item(2, "other body", url="https://dev.to/a/1/")
        report = deduplicate_report([a, same_url], near_threshold=None)
        assert report.reasons == {"n2": "url"}

    def test_duplicate_items_with_same_id_count_accurately(self) -> None:
        from msrkit.dedupe import deduplicate_report

        a = self._item(1, self.BASE)
        # Itens com mesmo ID que compartilham URL ou hash
        dup1 = self._item(2, "other body 1", url="https://dev.to/a/1/")
        dup2 = self._item(2, "other body 2", url="https://dev.to/a/1/")
        report = deduplicate_report([a, dup1, dup2], near_threshold=None)
        assert len(report.unique) == 1
        assert len(report.duplicates) == 2
        # A contagem deve somar exatamente 2, sem ser diminuída por colisão de chave
        assert sum(report.counts().values()) == 2
        assert report.counts()["url"] == 2
