"""Unit tests for adapter normalization, parsing, and query builders (no network)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from msrkit.adapters.base import BaseAdapter
from msrkit.adapters.bluesky import BlueskyAdapter
from msrkit.adapters.devto import DevToAdapter
from msrkit.adapters.discord import DiscordAdapter
from msrkit.adapters.github import GitHubAdapter
from msrkit.adapters.hackernews import HackerNewsAdapter
from msrkit.adapters.huggingface import HuggingFaceAdapter
from msrkit.adapters.linkedin import LinkedInAdapter
from msrkit.adapters.reddit import RedditAdapter
from msrkit.adapters.rss import RSSAdapter
from msrkit.adapters.stackexchange import StackExchangeAdapter
from msrkit.adapters.x_twitter import XTwitterAdapter
from msrkit.models import (
    AvailabilityStatus,
    ItemKind,
    Query,
    RawItem,
    SourceUnsupportedError,
)


class TestGitHubAdapterUnit:
    """Unit tests for GitHub adapter methods."""

    def test_normalize_code(self) -> None:
        raw = RawItem(
            source="github",
            native_id="code1",
            payload={
                "_search_kind": "code",
                "sha": "code123",
                "name": "test_rag.py",
                "path": "tests/test_rag.py",
                "html_url": "https://github.com/org/repo/blob/main/tests/test_rag.py",
                "repository": {
                    "owner": {"login": "ghuser"},
                    "language": "Python",
                },
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = GitHubAdapter()
        item = adapter.normalize(raw, terms=["test_rag"])
        assert item.kind == ItemKind.CODE
        assert item.tech.path == "tests/test_rag.py"
        assert item.tech.language == "Python"
        assert item.author_handle == "ghuser"

    def test_normalize_issue(self) -> None:
        raw = RawItem(
            source="github",
            native_id="issue1",
            payload={
                "_search_kind": "issue",
                "id": 888,
                "title": "Bug in RAG evaluation harness",
                "body": "The prompt regression check failed.",
                "html_url": "https://github.com/org/repo/issues/1",
                "user": {"login": "issueuser"},
                "created_at": "2024-02-10T10:00:00Z",
                "updated_at": "2024-02-11T12:00:00Z",
                "labels": [{"name": "bug"}, {"name": "testing"}],
                "comments": 4,
                "reactions": {"total_count": 2},
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = GitHubAdapter()
        item = adapter.normalize(raw, terms=["RAG evaluation", "prompt regression"])
        assert item.kind == ItemKind.ISSUE
        assert item.engagement.comments == 4
        assert item.engagement.reactions == 2
        assert "bug" in item.tech.tags
        assert len(item.matched_terms) == 2

    def test_estimate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = GitHubAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total_count": 420}
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(source="github", terms=["RAG testing"], kind="repo")
        est = adapter.estimate(q)
        assert est == 420

    def test_estimate_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = GitHubAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(source="github", terms=["RAG testing"])
        assert adapter.estimate(q) is None


class TestHackerNewsAdapterUnit:
    """Unit tests for HackerNews Algolia adapter."""

    def test_normalize_comment(self) -> None:
        raw = RawItem(
            source="hackernews",
            native_id="999",
            payload={
                "objectID": "999",
                "comment_text": "We use Ragas and TruLens for RAG testing.",
                "story_title": "State of RAG in 2024",
                "author": "commenter",
                "created_at_i": 1710000000,
                "points": 5,
                "num_comments": 0,
                "_tags": ["comment"],
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = HackerNewsAdapter()
        item = adapter.normalize(raw, terms=["RAG testing"])
        assert item.kind == ItemKind.COMMENT
        assert item.author_handle == "commenter"
        assert len(item.matched_terms) >= 1

    def test_estimate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = HackerNewsAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"nbHits": 150}
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(source="hackernews", terms=["RAG testing"])
        assert adapter.estimate(q) == 150

    def test_estimate_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = HackerNewsAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(source="hackernews", terms=["RAG testing"])
        assert adapter.estimate(q) is None


class TestStackExchangeAdapterUnit:
    """Unit tests for Stack Exchange adapter."""

    def test_estimate_with_total(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = StackExchangeAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total": 85, "items": []}
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(source="stackexchange", terms=["langchain", "testing"])
        assert adapter.estimate(q) == 85

    def test_estimate_without_total_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = StackExchangeAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"has_more": True, "items": [{}] * 10}
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(source="stackexchange", terms=["testing"])
        assert adapter.estimate(q) is None


class TestDevToAdapterUnit:
    """Unit tests for dev.to adapter."""

    def test_estimate_always_none(self) -> None:
        adapter = DevToAdapter()
        q = Query(source="devto", terms=["rag"])
        assert adapter.estimate(q) is None

    def test_search_with_local_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = DevToAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": 1, "title": "Intro to RAG testing", "description": "Details here"},
            {"id": 2, "title": "Cooking pasta recipe", "description": "Unrelated"},
        ]
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(source="devto", terms=["RAG testing"], extra={"tags": ["rag"]}, limit=5)
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "1"


class TestRedditAdapterUnit:
    """Unit tests for Reddit adapter."""

    def test_normalize(self) -> None:
        raw = RawItem(
            source="reddit",
            native_id="r123",
            payload={
                "id": "r123",
                "title": "Best practices for agent evaluation",
                "selftext": "How do you evaluate agentic workflows?",
                "author": "redditor",
                "score": 50,
                "num_comments": 12,
                "subreddit": "LocalLLaMA",
                "permalink": "/r/LocalLLaMA/comments/r123/best_practices/",
                "created_utc": 1705000000,
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = RedditAdapter()
        item = adapter.normalize(raw, terms=["agent evaluation"])
        assert item.kind == ItemKind.POST
        assert item.author_handle == "redditor"
        assert item.engagement.votes == 50
        assert item.engagement.comments == 12
        assert "LocalLLaMA" in item.tech.tags
        assert len(item.matched_terms) == 1

    def test_estimate_always_none(self) -> None:
        adapter = RedditAdapter()
        assert adapter.estimate(Query(source="reddit", terms=["rag"])) is None


class TestHuggingFaceAdapterUnit:
    """Unit tests for Hugging Face adapter."""

    def test_normalize_model_or_space(self) -> None:
        raw = RawItem(
            source="huggingface",
            native_id="eval-space",
            payload={
                "id": "org/eval-space",
                "description": "Evaluation leaderboard for RAG models",
                "tags": ["leaderboard", "rag"],
                "likes": 88,
                "downloads": 500,
                "pipeline_tag": "text-generation",
                "license": "apache-2.0",
                "createdAt": "2024-01-01T00:00:00.000Z",
                "lastModified": "2024-01-02T00:00:00.000Z",
                "_hf_kind": "spaces",
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = HuggingFaceAdapter()
        item = adapter.normalize(raw, terms=["RAG models"])
        assert item.kind == ItemKind.CARD
        assert item.engagement.stars == 88
        assert item.engagement.views == 500
        assert item.tech.license == "apache-2.0"
        assert len(item.matched_terms) >= 1

    def test_estimate_always_none(self) -> None:
        adapter = HuggingFaceAdapter()
        assert adapter.estimate(Query(source="huggingface", terms=["rag"])) is None


class TestRssAdapterUnit:
    """Unit tests for RSS adapter."""

    def test_normalize_rss_entry(self) -> None:
        raw = RawItem(
            source="rss",
            native_id="https://example.com/blog/rag-testing",
            payload={
                "title": "Testing RAG with Golden Datasets",
                "link": "https://example.com/blog/rag-testing",
                "summary": "We explain how to build a golden dataset for RAG.",
                "author": "techblogger",
                "published": "Mon, 15 Jan 2024 14:00:00 GMT",
                "tags": ["rag", "evaluation"],
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = RSSAdapter()
        item = adapter.normalize(raw, terms=["golden dataset", "Testing RAG"])
        assert item.kind == ItemKind.ARTICLE
        assert item.author_handle == "techblogger"
        assert item.created_at is not None
        assert len(item.matched_terms) == 2

    def test_estimate_always_none(self) -> None:
        adapter = RSSAdapter()
        assert adapter.estimate(Query(source="rss", terms=["rag"])) is None


class TestBlueskyAdapterUnit:
    """Unit tests for Bluesky adapter."""

    def test_normalize_bluesky_post(self) -> None:
        raw = RawItem(
            source="bluesky",
            native_id="at://did:plc:123/app.bsky.feed.post/456",
            payload={
                "uri": "at://did:plc:123/app.bsky.feed.post/456",
                "author": {"handle": "user.bsky.social"},
                "record": {
                    "text": "Check out this eval harness for testing RAG pipelines!",
                    "createdAt": "2024-03-01T12:00:00.000Z",
                },
                "likeCount": 15,
                "replyCount": 3,
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = BlueskyAdapter()
        item = adapter.normalize(raw, terms=["eval harness", "testing RAG"])
        assert item.kind == ItemKind.POST
        assert item.author_handle == "user.bsky.social"
        assert item.engagement.reactions == 15
        assert len(item.matched_terms) == 2

    def test_estimate_always_none(self) -> None:
        adapter = BlueskyAdapter()
        assert adapter.estimate(Query(source="bluesky", terms=["rag"])) is None


class TestGatedAndUnsupportedAdapters:
    """Unit tests for X, Discord, and LinkedIn adapters."""

    def test_linkedin_methods(self) -> None:
        adapter = LinkedInAdapter()
        assert adapter.available().status == AvailabilityStatus.UNSUPPORTED
        assert adapter.estimate(Query(source="linkedin", terms=["test"])) is None
        with pytest.raises(SourceUnsupportedError):
            list(adapter.search(Query(source="linkedin", terms=["test"])))
        with pytest.raises(SourceUnsupportedError):
            adapter.normalize(
                RawItem(source="linkedin", native_id="1", payload={}, fetched_at=datetime.now(UTC))
            )

    def test_discord_unsupported_by_default(self) -> None:
        adapter = DiscordAdapter()
        assert adapter.available().status == AvailabilityStatus.UNSUPPORTED
        assert adapter.estimate(Query(source="discord", terms=["test"])) is None
        with pytest.raises(SourceUnsupportedError):
            list(adapter.search(Query(source="discord", terms=["test"])))
        with pytest.raises(NotImplementedError):
            adapter.normalize(
                RawItem(source="discord", native_id="1", payload={}, fetched_at=datetime.now(UTC))
            )

    def test_x_twitter_unsupported_by_default(self) -> None:
        adapter = XTwitterAdapter()
        assert adapter.available().status == AvailabilityStatus.UNSUPPORTED
        assert adapter.estimate(Query(source="x_twitter", terms=["test"])) is None
        with pytest.raises(SourceUnsupportedError):
            list(adapter.search(Query(source="x_twitter", terms=["test"])))
        with pytest.raises(NotImplementedError):
            adapter.normalize(
                RawItem(source="x_twitter", native_id="1", payload={}, fetched_at=datetime.now(UTC))
            )


class TestBaseAdapterHelpers:
    """Unit tests for BaseAdapter helper methods."""

    def test_make_raw_item_and_hash(self) -> None:
        raw = BaseAdapter._make_raw_item("custom", "123", {"hello": "world"})
        assert raw.source == "custom"
        assert raw.native_id == "123"
        assert raw.payload == {"hello": "world"}

        h = BaseAdapter._hash_response(b"hello world")
        assert len(h) == 64


class TestSearchLoopsAndQueryBuilding:
    """Test search loops and query formatting with mocked responses."""

    def test_github_query_building(self) -> None:
        from datetime import date

        adapter = GitHubAdapter()
        q = Query(
            source="github",
            terms=["RAG", "testing"],
            since=date(2024, 1, 1),
            until=date(2024, 6, 30),
            extra={"languages": ["Python", "TypeScript"], "min_stars": 5},
        )
        qs = adapter._build_query_string(q)
        assert "RAG testing" in qs
        assert "created:2024-01-01..2024-06-30" in qs
        assert "language:Python" in qs
        assert "language:TypeScript" in qs
        assert "stars:>=5" in qs

    def test_github_search_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = GitHubAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "total_count": 1,
            "items": [
                {
                    "id": 12345,
                    "full_name": "owner/repo",
                    "html_url": "https://github.com/owner/repo",
                }
            ],
        }
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(source="github", terms=["RAG testing"], kind="repo", limit=1)
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "12345"

    def test_github_single_query_building(self) -> None:
        from datetime import date

        adapter = GitHubAdapter()
        q = Query(
            source="github",
            terms=["RAG testing", "eval harness"],
            since=date(2024, 1, 1),
            until=date(2024, 6, 30),
            extra={"languages": ["Python", "TypeScript"], "min_stars": 5},
        )
        qs = adapter._build_single_query_string(q, term="RAG testing", language="Python")
        assert '"RAG testing"' in qs
        assert "created:2024-01-01..2024-06-30" in qs
        assert "language:Python" in qs
        assert "stars:>=5" in qs

    def test_github_search_multiple_terms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = GitHubAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "total_count": 1,
            "items": [
                {
                    "id": 12345,
                    "full_name": "owner/repo",
                    "html_url": "https://github.com/owner/repo",
                }
            ],
        }
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(
            source="github",
            terms=["RAG testing", "eval harness"],
            kind="repo",
            extra={"languages": ["Python"]},
            limit=2,
        )
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "12345"

    def test_devto_early_stop_since(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import date

        adapter = DevToAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": 1, "title": "Old Article", "published_at": "2020-01-01T00:00:00Z"}
        ]
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(
            source="devto",
            terms=["rag"],
            since=date(2024, 1, 1),
            extra={"tags": ["rag"]},
            limit=5,
        )
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 0

    def test_stackexchange_search_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = StackExchangeAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "has_more": False,
            "items": [
                {
                    "question_id": 555,
                    "title": "SE Question",
                    "link": "https://stackoverflow.com/q/555",
                }
            ],
        }
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(
            source="stackexchange",
            terms=["rag"],
            extra={"sites": ["stackoverflow"], "tagged": ["rag"]},
            limit=5,
        )
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "555"

    def test_stackexchange_multi_tag_decoupled_params(self) -> None:
        """Multiple tags are not joined with ';' when terms are present to prevent 0-match AND."""
        adapter = StackExchangeAdapter()
        q = Query(
            source="stackexchange",
            terms=["rag"],
            extra={"sites": ["stackoverflow"], "tagged": ["rag", "langchain"]},
        )
        params = adapter._build_params(q, site="stackoverflow", page=1, pagesize=10, term="rag")
        assert params.get("q") == "rag"
        assert "tagged" not in params

    def test_stackexchange_tags_search_without_terms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When terms is empty, tags are queried individually."""
        adapter = StackExchangeAdapter()
        captured_params: list[dict[str, Any]] = []

        def mock_get(url: str, params: dict[str, Any]) -> MagicMock:
            captured_params.append(params)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "has_more": False,
                "items": [{"question_id": len(captured_params), "title": "SE Q"}],
            }
            return resp

        monkeypatch.setattr(adapter, "_governed_get", mock_get)

        q = Query(
            source="stackexchange",
            terms=[],
            extra={"sites": ["stackoverflow"], "tagged": ["rag", "langchain"]},
            limit=10,
        )
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 2
        assert len(captured_params) == 2
        assert captured_params[0].get("tagged") == "rag"
        assert captured_params[1].get("tagged") == "langchain"

    def test_stackexchange_explicit_tagged_mode_and(self) -> None:
        """When tagged_mode is 'and', multi-tags are joined with ';'."""
        adapter = StackExchangeAdapter()
        q = Query(
            source="stackexchange",
            terms=["rag"],
            extra={
                "sites": ["stackoverflow"],
                "tagged": ["rag", "langchain"],
                "tagged_mode": "and",
            },
        )
        params = adapter._build_params(q, site="stackoverflow", page=1, pagesize=10, term="rag")
        assert params.get("tagged") == "rag;langchain"

    def test_hackernews_search_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = HackerNewsAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "nbPages": 1,
            "hits": [
                {
                    "objectID": "777",
                    "title": "HN Story",
                    "url": "https://news.ycombinator.com/item?id=777",
                }
            ],
        }
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(source="hackernews", terms=["rag"], limit=5)
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "777"

    def test_huggingface_search_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = HuggingFaceAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"id": "user/rag-dataset"}]
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(source="huggingface", terms=["rag"], extra={"kinds": ["datasets"]}, limit=5)
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "user/rag-dataset"

    def test_rss_search_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = RSSAdapter()
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>RSS Article on RAG</title>
                    <link>https://example.com/item1</link>
                    <description>Summary of RAG testing.</description>
                    <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = xml_content
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(
            source="rss",
            terms=["rag"],
            extra={"feeds": ["https://example.com/feed"]},
            limit=5,
        )
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].payload["title"] == "RSS Article on RAG"

    def test_reddit_search_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = RedditAdapter()
        adapter._access_token = "mock_token"

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {
            "data": {
                "after": None,
                "children": [
                    {"data": {"id": "red1", "title": "Reddit Post", "permalink": "/r/test/red1"}}
                ],
            }
        }
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_get_resp)

        q = Query(source="reddit", terms=["rag"], extra={"subreddits": ["LocalLLaMA"]}, limit=5)
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "red1"

    def test_bluesky_search_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = BlueskyAdapter()
        adapter._session_token = "mock_jwt"

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {
            "cursor": None,
            "posts": [{"uri": "at://did:plc:1/app.bsky.feed.post/1", "record": {"text": "hello"}}],
        }
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_get_resp)

        q = Query(source="bluesky", terms=["rag"], limit=5)
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "at://did:plc:1/app.bsky.feed.post/1"

    def test_devto_until_upper_bound_filtering(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dev.to search skips articles newer than q.until."""
        from datetime import date

        adapter = DevToAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": 1, "published_at": "2026-05-01T00:00:00Z", "title": "RAG new"},
            {"id": 2, "published_at": "2023-06-01T00:00:00Z", "title": "RAG in-range"},
            {"id": 3, "published_at": "2020-01-01T00:00:00Z", "title": "RAG old"},
        ]
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(
            source="devto",
            terms=["rag"],
            since=date(2023, 1, 1),
            until=date(2023, 12, 31),
            extra={"tags": ["rag"]},
            limit=10,
        )
        raw_items = list(adapter.search(q))
        # Item 1 is skipped (too new), item 2 is kept, item 3 triggers early stop
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "2"

    def test_github_deleted_user_resilience(self) -> None:
        """GitHub normalization does not crash when user/owner is null (deleted accounts)."""
        adapter = GitHubAdapter()
        raw_issue = RawItem(
            source="github",
            native_id="123",
            payload={
                "_search_kind": "issue",
                "id": 123,
                "title": "Bug with deleted user",
                "html_url": "https://github.com/org/repo/issues/123",
                "user": None,  # Deleted user
                "reactions": None,
                "labels": None,
            },
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw_issue)
        assert item.author_handle is None
        assert item.engagement.reactions is None

    def test_rss_fallback_url_when_link_missing(self) -> None:
        """RSS normalization creates a valid HttpUrl when link is missing."""
        adapter = RSSAdapter()
        raw = RawItem(
            source="rss",
            native_id="entry-99",
            payload={"title": "Missing link", "link": "", "feed_url": "https://blog.com/feed"},
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw)
        assert str(item.url) == "https://blog.com/feed"

    def test_hackernews_inclusive_numeric_filters(self) -> None:
        """HackerNews numericFilters use inclusive >= and <= bounds."""
        from datetime import date

        adapter = HackerNewsAdapter()
        q = Query(
            source="hackernews",
            terms=["rag"],
            since=date(2024, 1, 1),
            until=date(2024, 1, 31),
        )
        params = adapter._build_params(q)
        filters = params.get("numericFilters", "")
        assert "created_at_i>=" in filters
        assert "created_at_i<=" in filters

    def test_huggingface_dataset_and_space_urls(self) -> None:
        """HuggingFace adapter formats URLs properly for datasets and spaces."""
        adapter = HuggingFaceAdapter()
        raw_dataset = RawItem(
            source="huggingface",
            native_id="user/rag-data",
            payload={"id": "user/rag-data", "_hf_kind": "datasets"},
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw_dataset)
        assert str(item.url) == "https://huggingface.co/datasets/user/rag-data"
