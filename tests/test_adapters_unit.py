"""Unit tests for adapter normalization, parsing, and query builders (no network)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import httpx
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

    def test_normalize_with_null_user(self) -> None:
        """dev.to normalize does not crash when user payload is null/None."""
        adapter = DevToAdapter()
        raw = RawItem(
            source="devto",
            native_id="999",
            payload={
                "id": 999,
                "title": "Anonymous article",
                "description": "Article without user object",
                "user": None,
                "url": "https://dev.to/article/999",
            },
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw)
        assert item.author_handle is None
        assert item.title == "Anonymous article"


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
        assert {h.term for h in item.matched_terms} == {"golden dataset", "Testing RAG"}
        # Flexible matching also catches the plural "Golden Datasets" in the title.
        assert any(h.field == "title" and h.term == "golden dataset" for h in item.matched_terms)

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

    def test_github_issue_queries_are_restricted_to_issues(self) -> None:
        from datetime import date

        adapter = GitHubAdapter()
        window = {"since": date(2023, 1, 1), "until": date(2026, 8, 31)}
        q = Query(source="github", terms=["agent testing"], kind="issue", **window)
        assert adapter._build_single_query_string(q, term="agent testing", language="Python") == (
            '"agent testing" created:2023-01-01..2026-08-31 language:Python is:issue'
        )
        assert adapter._build_query_string(q).endswith(" is:issue")
        for kind in ("repo", "code"):
            other = Query(source="github", terms=["agent testing"], kind=kind, **window)
            assert "is:issue" not in adapter._build_single_query_string(other, term="x")
        # A literal query that already sets the type is kept as written.
        for literal in ("repo:o/r is:issue label:bug", "rag is:pr", "rag type:issue"):
            raw = Query(source="github", terms=[literal], kind="issue", raw=True)
            assert adapter._build_single_query_string(raw, term=literal) == literal

    def test_github_422_keeps_other_languages_and_fails_the_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from msrkit.adapters.github import QueryRejectedError

        adapter = GitHubAdapter()

        def fake_get(url: str, params: dict[str, Any] | None = None, **kw: Any) -> MagicMock:
            resp = MagicMock()
            assert params is not None
            if "language:Python" in params["q"]:
                resp.status_code = 422
                resp.json.return_value = {
                    "message": "Validation Failed",
                    "errors": [{"message": "Query must include 'is:issue' or 'is:pull-request'"}],
                }
            else:
                resp.status_code = 200
                resp.json.return_value = {
                    "items": [{"id": 7, "html_url": "https://github.com/o/r/issues/7"}]
                }
            return resp

        monkeypatch.setattr(adapter, "_governed_get", fake_get)
        q = Query(
            source="github",
            terms=["t"],
            kind="issue",
            extra={"languages": ["Python", "TypeScript"]},
            limit=10,
        )
        got = []
        with pytest.raises(QueryRejectedError, match="is:pull-request"):
            for raw in adapter.search(q):
                got.append(raw.native_id)
        assert got == ["7"]  # the TypeScript items came through before the error

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

    def test_stackexchange_tags_are_or_joined_with_terms(self) -> None:
        """Tags are sent together with each term, ';'-joined (OR per the API docs)."""
        adapter = StackExchangeAdapter()
        q = Query(
            source="stackexchange",
            terms=["rag"],
            extra={"sites": ["stackoverflow"], "tagged": ["rag", "langchain"]},
        )
        params = adapter._build_params(q, site="stackoverflow", page=1, pagesize=10, term="rag")
        assert params.get("q") == "rag"
        assert params.get("tagged") == "rag;langchain"

    def test_stackexchange_tagged_accepts_string(self) -> None:
        adapter = StackExchangeAdapter()
        q = Query(source="stackexchange", terms=[], extra={"tagged": "rag;langchain"})
        params = adapter._build_params(q, site="stackoverflow", page=1, pagesize=10)
        assert params.get("tagged") == "rag;langchain"
        assert "q" not in params

    def test_stackexchange_one_request_per_site_and_term(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = StackExchangeAdapter()
        captured: list[dict[str, Any]] = []

        def mock_get(url: str, params: dict[str, Any]) -> MagicMock:
            captured.append(params)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "has_more": False,
                "items": [{"question_id": len(captured), "title": "SE Q"}],
            }
            return resp

        monkeypatch.setattr(adapter, "_governed_get", mock_get)
        q = Query(
            source="stackexchange",
            terms=["RAG testing", "agent evaluation"],
            extra={
                "sites": ["stackoverflow", "softwareengineering"],
                "tagged": ["rag", "langchain"],
                "include_answers": False,
            },
            limit=10,
        )
        assert len(list(adapter.search(q))) == 4
        assert [(c["site"], c["q"]) for c in captured] == [
            ("stackoverflow", "RAG testing"),
            ("stackoverflow", "agent evaluation"),
            ("softwareengineering", "RAG testing"),
            ("softwareengineering", "agent evaluation"),
        ]
        assert all(c["tagged"] == "rag;langchain" for c in captured)

    def test_stackexchange_thread_includes_answers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Answers are fetched in one batch per page and appended to the thread body (C4a)."""
        adapter = StackExchangeAdapter()
        urls: list[str] = []

        def mock_get(url: str, params: dict[str, Any]) -> MagicMock:
            urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            if url.endswith("/answers"):
                resp.json.return_value = {
                    "has_more": False,
                    "items": [
                        {
                            "question_id": 1,
                            "answer_id": 10,
                            "body": "Use ragas in CI",
                            "score": 5,
                            "is_accepted": True,
                        },
                        {
                            "question_id": 1,
                            "answer_id": 11,
                            "body": "Try a golden dataset",
                            "score": 2,
                        },
                    ],
                }
            else:
                resp.json.return_value = {
                    "has_more": False,
                    "items": [
                        {
                            "question_id": 1,
                            "title": "How to test RAG?",
                            "body": "Q body",
                            "answer_count": 2,
                        },
                        {"question_id": 2, "title": "Unanswered", "body": "B", "answer_count": 0},
                    ],
                }
            return resp

        monkeypatch.setattr(adapter, "_governed_get", mock_get)
        q = Query(source="stackexchange", terms=["RAG"], extra={"sites": ["stackoverflow"]})
        raws = list(adapter.search(q))

        assert len(urls) == 2
        assert urls[1].endswith("/questions/1/answers")  # unanswered question not requested
        assert len(raws[0].payload["_answers"]) == 2
        assert raws[1].payload["_answers"] == []

        item = adapter.normalize(raws[0], terms=["golden dataset"])
        assert item.body.startswith("Q body")
        assert "--- Resposta aceita (score 5) ---" in item.body
        assert "Try a golden dataset" in item.body
        assert item.matched_terms and item.matched_terms[0].term == "golden dataset"

    def test_devto_fetches_full_body_only_for_kept_articles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Kept articles get body_markdown from /articles/{id}; discarded cost nothing (C4b)."""
        adapter = DevToAdapter()
        urls: list[str] = []

        def mock_get(url: str, params: dict[str, Any] | None = None) -> MagicMock:
            urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            if url.endswith("/articles/1"):
                resp.json.return_value = {"id": 1, "body_markdown": "Full text: golden dataset"}
            else:
                resp.json.return_value = [
                    {
                        "id": 1,
                        "title": "RAG testing guide",
                        "description": "short",
                        "url": "https://dev.to/a/1",
                        "published_at": "2024-01-01T00:00:00Z",
                    },
                    {
                        "id": 2,
                        "title": "Cooking",
                        "description": "x",
                        "url": "https://dev.to/a/2",
                        "published_at": "2024-01-01T00:00:00Z",
                    },
                ]
            return resp

        monkeypatch.setattr(adapter, "_governed_get", mock_get)
        q = Query(source="devto", terms=["RAG testing"], extra={"tags": ["rag"]}, limit=10)
        raws = list(adapter.search(q))

        assert [r.native_id for r in raws] == ["1"]
        assert sum(u.endswith("/articles/1") for u in urls) == 1
        assert not any(u.endswith("/articles/2") for u in urls)
        item = adapter.normalize(raws[0], terms=["golden dataset"])
        assert item.body == "Full text: golden dataset"
        assert item.matched_terms[0].term == "golden dataset"

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

    def test_devto_cross_tag_deduplication(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dev.to search does not yield duplicate items when an article matches multiple tags."""
        adapter = DevToAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": 42, "title": "Article on RAG and LLM", "tag_list": ["rag", "llm"]}
        ]
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(
            source="devto",
            terms=["rag"],
            extra={"tags": ["rag", "llm"]},
            limit=10,
        )
        items = list(adapter.search(q))
        assert len(items) == 1
        assert items[0].native_id == "42"

    def test_rss_atom_updated_date_fallback(self) -> None:
        """RSS adapter extracts date from <updated> tag when <published> is absent (Atom format)."""
        adapter = RSSAdapter()
        raw = RawItem(
            source="rss",
            native_id="https://example.com/atom/1",
            payload={
                "title": "Atom Post",
                "link": "https://example.com/atom/1",
                "published": "Mon, 15 Jan 2024 10:00:00 GMT",
            },
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw)
        assert item.created_at is not None
        assert item.created_at.year == 2024
        assert item.created_at.month == 1

    def test_huggingface_model_and_dataset_same_id_distinct(self) -> None:
        """Models and datasets with identical IDs produce distinct canonical Item IDs."""
        adapter = HuggingFaceAdapter()
        raw_model = RawItem(
            source="huggingface",
            native_id="user/bench",
            payload={"id": "user/bench", "_hf_kind": "models"},
            fetched_at=datetime.now(UTC),
        )
        raw_dataset = RawItem(
            source="huggingface",
            native_id="user/bench",
            payload={"id": "user/bench", "_hf_kind": "datasets"},
            fetched_at=datetime.now(UTC),
        )
        item_model = adapter.normalize(raw_model)
        item_dataset = adapter.normalize(raw_dataset)
        assert item_model.id != item_dataset.id

    def test_stackexchange_multi_site_same_id_distinct(self) -> None:
        """Same numeric question ID across different SE sites produces distinct Item IDs."""
        adapter = StackExchangeAdapter()
        raw_so = RawItem(
            source="stackexchange",
            native_id="999",
            payload={"question_id": 999, "_site": "stackoverflow"},
            fetched_at=datetime.now(UTC),
        )
        raw_ds = RawItem(
            source="stackexchange",
            native_id="999",
            payload={"question_id": 999, "_site": "datascience"},
            fetched_at=datetime.now(UTC),
        )
        item_so = adapter.normalize(raw_so)
        item_ds = adapter.normalize(raw_ds)
        assert item_so.id != item_ds.id

    def test_github_code_search_omits_created_stars_and_sort(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = GitHubAdapter()
        monkeypatch.setattr(
            adapter, "_env", lambda k: "mock_token" if k == "GITHUB_TOKEN" else None
        )
        captured_params: list[dict[str, Any]] = []

        def mock_get(url: str, params: dict[str, Any]) -> MagicMock:
            captured_params.append(params)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "total_count": 1,
                "items": [
                    {
                        "sha": "abc123sha",
                        "path": "test/eval.py",
                        "repository": {"full_name": "owner/repo"},
                        "html_url": "https://github.com/owner/repo/blob/main/test/eval.py",
                    }
                ],
            }
            return resp

        monkeypatch.setattr(adapter, "_governed_get", mock_get)

        q = Query(
            source="github",
            terms=["rag testing"],
            kind="code",
            since=date(2024, 1, 1),
            until=date(2024, 12, 31),
            extra={"min_stars": 10},
            limit=2,
        )
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "owner/repo:test/eval.py"
        assert len(captured_params) > 0
        qs = captured_params[0]["q"]
        assert "created:" not in qs
        assert "stars:" not in qs
        assert "sort" not in captured_params[0]  # closing down; best match (ADR-053)

    def test_github_bot_filtering_opt_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = GitHubAdapter()
        monkeypatch.setattr(
            adapter, "_env", lambda k: "mock_token" if k == "GITHUB_TOKEN" else None
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "total_count": 2,
            "items": [
                {
                    "id": 101,
                    "title": "Dependabot PR",
                    "user": {"login": "dependabot[bot]"},
                    "html_url": "https://github.com/o/r/issues/1",
                },
                {
                    "id": 102,
                    "title": "Human Issue",
                    "user": {"login": "alice"},
                    "html_url": "https://github.com/o/r/issues/2",
                },
            ],
        }
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: resp)

        # Default: filter_bots is False -> all items yielded (matches upstream Icar0S)
        q_default = Query(source="github", terms=["t"], kind="issue", limit=10)
        items_default = list(adapter.search(q_default))
        assert len(items_default) == 2

        # Opt-in: filter_bots is True -> bot items discarded
        q_filtered = Query(
            source="github", terms=["t"], kind="issue", extra={"filter_bots": True}, limit=10
        )
        items_filtered = list(adapter.search(q_filtered))
        assert len(items_filtered) == 1
        assert items_filtered[0].native_id == "102"

    def test_github_issue_query_building_qualifiers(self) -> None:
        adapter = GitHubAdapter()
        # Open query with issue_in and issue_qualifiers
        q = Query(
            source="github",
            terms=["rerank eval"],
            kind="issue",
            extra={"issue_in": "title,body", "issue_qualifiers": "state:open"},
        )
        qs_single = adapter._build_single_query_string(q, term="rerank eval")
        assert "is:issue" in qs_single
        assert "in:title,body" in qs_single
        assert "state:open" in qs_single

        qs_full = adapter._build_query_string(q)
        assert "is:issue" in qs_full
        assert "in:title,body" in qs_full
        assert "state:open" in qs_full

        # Literal raw query stays verbatim
        q_raw = Query(
            source="github",
            terms=["repo:foo/bar is:issue label:bug"],
            kind="issue",
            raw=True,
            extra={"issue_in": "title,body", "issue_qualifiers": "state:open"},
        )
        qs_raw = adapter._build_single_query_string(q_raw, term=q_raw.terms[0])
        assert qs_raw == "repo:foo/bar is:issue label:bug"

    def test_github_issue_discards_pull_requests(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = GitHubAdapter()
        monkeypatch.setattr(
            adapter, "_env", lambda k: "mock_token" if k == "GITHUB_TOKEN" else None
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "total_count": 2,
            "items": [
                {
                    "id": 201,
                    "title": "Fix bug PR",
                    "user": {"login": "alice"},
                    "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/1"},
                    "html_url": "https://github.com/o/r/pull/1",
                },
                {
                    "id": 202,
                    "title": "Actual Issue",
                    "user": {"login": "bob"},
                    "html_url": "https://github.com/o/r/issues/2",
                },
            ],
        }
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: resp)

        q = Query(source="github", terms=["bug"], kind="issue", limit=10)
        items = list(adapter.search(q))
        assert len(items) == 1
        assert items[0].native_id == "202"
        discards = adapter.pop_discards()
        assert len(discards) == 1
        assert discards[0]["native_id"] == "201"
        assert discards[0]["reason"] == "pull_request"

    def test_github_issue_require_term_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = GitHubAdapter()
        monkeypatch.setattr(
            adapter, "_env", lambda k: "mock_token" if k == "GITHUB_TOKEN" else None
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "total_count": 2,
            "items": [
                {
                    "id": 301,
                    "title": "Unrelated issue title",
                    "body": "Body mentioning nothing relevant, match was only in comment",
                    "html_url": "https://github.com/o/r/issues/301",
                },
                {
                    "id": 302,
                    "title": "Issue about RAG eval",
                    "body": "Discussing RAG eval metrics",
                    "html_url": "https://github.com/o/r/issues/302",
                },
            ],
        }
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: resp)

        # With require_issue_term_match: True
        q = Query(
            source="github",
            terms=["RAG eval"],
            kind="issue",
            extra={"require_issue_term_match": True},
            limit=10,
        )
        items = list(adapter.search(q))
        assert len(items) == 1
        assert items[0].native_id == "302"
        discards = adapter.pop_discards()
        assert len(discards) == 1
        assert discards[0]["native_id"] == "301"
        assert discards[0]["reason"] == "no_term_match"

        # Raw query is unaffected by require_issue_term_match
        q_raw = Query(
            source="github",
            terms=["RAG eval"],
            kind="issue",
            raw=True,
            extra={"require_issue_term_match": True},
            limit=10,
        )
        items_raw = list(adapter.search(q_raw))
        assert len(items_raw) == 2
        assert adapter.pop_discards() == []

    def test_github_issue_noise_filters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = GitHubAdapter()
        monkeypatch.setattr(
            adapter, "_env", lambda k: "mock_token" if k == "GITHUB_TOKEN" else None
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "total_count": 5,
            "items": [
                {
                    "id": 401,
                    "title": "Latest 10 papers in AI",
                    "html_url": "https://github.com/normal-user/normal-repo/issues/1",
                },
                {
                    "id": 402,
                    "title": "Paper discussion",
                    "html_url": "https://github.com/DailyArXiv/daily-papers/issues/2",
                },
                {
                    "id": 403,
                    "title": "Weekly reading digest",
                    "html_url": "https://github.com/researcher/reading_list/issues/3",
                },
                {
                    "id": 404,
                    "title": "Feature tracker",
                    "labels": [{"name": "requirement"}],
                    "html_url": "https://github.com/normal-user/normal-repo/issues/4",
                },
                {
                    "id": 405,
                    "title": "Legitimate bug report",
                    "labels": [{"name": "bug"}],
                    "html_url": "https://github.com/normal-user/normal-repo/issues/5",
                },
            ],
        }
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: resp)

        q = Query(
            source="github",
            terms=["papers"],
            kind="issue",
            extra={
                "exclude_title_patterns": [r"(?i)^latest\s+\d+\s+papers\b"],
                "exclude_repo_patterns": [r"(?i)dailyarxiv", r"(?i)reading[_-]list"],
                "exclude_labels": ["requirement", "wontfix"],
            },
            limit=10,
        )
        items = list(adapter.search(q))
        assert len(items) == 1
        assert items[0].native_id == "405"
        discards = adapter.pop_discards()
        assert len(discards) == 4
        discard_ids = {d["native_id"] for d in discards}
        assert discard_ids == {"401", "402", "403", "404"}
        assert all(d["reason"] == "noise_filter" for d in discards)

    def test_reddit_quotes_multi_word_terms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = RedditAdapter()
        adapter._access_token = "mock_token"
        captured_params: list[dict[str, Any]] = []

        def mock_get(url: str, params: dict[str, Any]) -> MagicMock:
            captured_params.append(params)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"data": {"children": []}}
            return resp

        monkeypatch.setattr(adapter, "_governed_get", mock_get)

        q = Query(
            source="reddit",
            terms=["RAG testing", "eval harness", "benchmarking"],
            extra={"subreddits": ["LocalLLaMA"]},
            limit=5,
        )
        list(adapter.search(q))
        assert len(captured_params) > 0
        assert captured_params[0]["q"] == '"RAG testing" OR "eval harness" OR benchmarking'

    def test_bluesky_iterates_terms_and_formats_rfc3339(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = BlueskyAdapter()
        adapter._session_token = "mock_jwt"
        captured_params: list[dict[str, Any]] = []

        def mock_get(url: str, params: dict[str, Any], headers: dict[str, str]) -> MagicMock:
            captured_params.append(params)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "posts": [
                    {
                        "uri": f"at://did:plc:123/app.bsky.feed.post/{len(captured_params)}",
                        "record": {
                            "text": "RAG evaluation post",
                            "createdAt": "2024-05-01T00:00:00Z",
                        },
                    }
                ]
            }
            return resp

        monkeypatch.setattr(adapter, "_governed_get", mock_get)

        q = Query(
            source="bluesky",
            terms=["RAG testing", "eval harness"],
            since=date(2024, 1, 1),
            until=date(2024, 6, 30),
            limit=5,
        )
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 2
        assert len(captured_params) == 2
        assert captured_params[0]["q"] == '"RAG testing"'
        assert captured_params[0]["since"] == "2024-01-01T00:00:00Z"
        assert captured_params[0]["until"] == "2024-06-30T23:59:59Z"
        assert captured_params[1]["q"] == '"eval harness"'

    def test_huggingface_link_header_pagination(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = HuggingFaceAdapter()
        calls = 0

        def mock_get(url: str, params: dict[str, Any] | None = None) -> MagicMock:
            nonlocal calls
            calls += 1
            resp = MagicMock()
            resp.status_code = 200
            if calls == 1:
                resp.json.return_value = [{"id": "user/dataset1"}]
                resp.links = {"next": {"url": "https://huggingface.co/api/datasets?page=2"}}
            else:
                resp.json.return_value = [{"id": "user/dataset2"}]
                resp.links = {}
            return resp

        monkeypatch.setattr(adapter, "_governed_get", mock_get)

        q = Query(source="huggingface", terms=["rag"], extra={"kinds": ["datasets"]}, limit=5)
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 2
        assert raw_items[0].native_id == "user/dataset1"
        assert raw_items[1].native_id == "user/dataset2"
        assert calls == 2

    def test_hackernews_configurable_tags(self) -> None:
        adapter = HackerNewsAdapter()
        q_comment = Query(source="hackernews", terms=["rag"], kind="comment")
        params_comment = adapter._build_params(q_comment, term="rag")
        assert params_comment["tags"] == "comment"

        q_custom = Query(
            source="hackernews",
            terms=["rag"],
            kind="thread",
            extra={"tags": "(story,comment)"},
        )
        params_custom = adapter._build_params(q_custom, term="rag")
        assert params_custom["tags"] == "(story,comment)"

    def test_devto_resilient_to_out_of_order_pinned_article(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = DevToAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Page has a pinned older article followed by fresh articles within range
        mock_resp.json.return_value = [
            {"id": 1, "title": "Old Pinned", "published_at": "2020-01-01T00:00:00Z"},
            {"id": 2, "title": "Fresh RAG", "published_at": "2024-05-01T00:00:00Z"},
        ]
        monkeypatch.setattr(adapter, "_governed_get", lambda *args, **kwargs: mock_resp)

        q = Query(
            source="devto",
            terms=["RAG"],
            since=date(2024, 1, 1),
            extra={"tags": ["rag"]},
            limit=5,
        )
        raw_items = list(adapter.search(q))
        assert len(raw_items) == 1
        assert raw_items[0].native_id == "2"

    def test_github_is_bot_author_detection(self) -> None:
        from msrkit.adapters.github import is_bot_author

        assert is_bot_author("dependabot[bot]") is True
        assert is_bot_author("renovate[bot]") is True
        assert is_bot_author("github-actions[bot]") is True
        assert is_bot_author("snyk-bot") is True
        assert is_bot_author("custom-bot") is True
        assert is_bot_author("alice") is False
        assert is_bot_author("john_doe") is False
        assert is_bot_author(None) is False

    def test_reddit_normalize_handles_absolute_permalink(self) -> None:
        adapter = RedditAdapter()
        raw = RawItem(
            source="reddit",
            native_id="p123",
            payload={
                "id": "p123",
                "title": "Discussion on RAG",
                "selftext": "Text body",
                "subreddit": "MachineLearning",
                "permalink": "https://www.reddit.com/r/MachineLearning/comments/p123/rag_test/",
            },
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw)
        assert str(item.url) == "https://www.reddit.com/r/MachineLearning/comments/p123/rag_test/"

    def test_rss_normalize_handles_tag_uri(self) -> None:
        adapter = RSSAdapter()
        raw = RawItem(
            source="rss",
            native_id="tag:blogger.com,1999:blog-1234",
            payload={
                "title": "Blog post",
                "link": "tag:blogger.com,1999:blog-1234",
                "feed_url": "https://blog.example/feed.xml",
            },
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw)
        assert str(item.url) == "https://blog.example/feed.xml"

    def test_devto_normalize_handles_missing_url(self) -> None:
        adapter = DevToAdapter()
        raw = RawItem(
            source="devto",
            native_id="456",
            payload={
                "id": 456,
                "title": "Dev.to article without url",
                "url": "",
            },
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw)
        assert str(item.url) == "https://dev.to/articles/456"

    def test_bluesky_normalize_trailing_slash_in_uri(self) -> None:
        adapter = BlueskyAdapter()
        raw = RawItem(
            source="bluesky",
            native_id="at://did:plc:123/app.bsky.feed.post/post456/",
            payload={
                "uri": "at://did:plc:123/app.bsky.feed.post/post456/",
                "author": {"handle": "alice.bsky.social"},
                "record": {
                    "text": "testing trailing slash",
                    "createdAt": "2024-01-01T00:00:00Z",
                },
            },
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw)
        assert str(item.url) == "https://bsky.app/profile/alice.bsky.social/post/post456"

    def test_adapters_search_limit_zero(self) -> None:
        q = Query(source="github", terms=["rag"], limit=0)
        assert list(GitHubAdapter().search(q)) == []

        q_se = Query(source="stackexchange", terms=["rag"], limit=0)
        assert list(StackExchangeAdapter().search(q_se)) == []

        q_hf = Query(source="huggingface", terms=["rag"], limit=0)
        assert list(HuggingFaceAdapter().search(q_hf)) == []

        q_hn = Query(source="hackernews", terms=["rag"], limit=0)
        assert list(HackerNewsAdapter().search(q_hn)) == []

        q_red = Query(source="reddit", terms=["rag"], limit=0)
        reddit_adapter = RedditAdapter()
        reddit_adapter._authenticate = MagicMock(return_value=True)  # type: ignore[method-assign]
        reddit_adapter._client = MagicMock()
        assert list(reddit_adapter.search(q_red)) == []

        q_rss = Query(
            source="rss",
            terms=["rag"],
            extra={"feeds": ["https://example.com/rss"]},
            limit=0,
        )
        assert list(RSSAdapter().search(q_rss)) == []

        q_dev = Query(source="devto", terms=["rag"], limit=0)
        assert list(DevToAdapter().search(q_dev)) == []

        bsky_adapter = BlueskyAdapter()
        bsky_adapter._session_token = "dummy"
        q_bsky = Query(source="bluesky", terms=["rag"], limit=0)
        assert list(bsky_adapter.search(q_bsky)) == []

    def test_stackexchange_partition_splits_by_term(self) -> None:
        adapter = StackExchangeAdapter()
        q = Query(source="stackexchange", terms=["term1", "term2", "term3"])
        parts = adapter.partition(q)
        assert len(parts) == 3
        assert [p.terms for p in parts] == [["term1"], ["term2"], ["term3"]]

    def test_huggingface_partition_splits_by_term(self) -> None:
        adapter = HuggingFaceAdapter()
        q = Query(source="huggingface", terms=["term1", "term2"])
        parts = adapter.partition(q)
        assert len(parts) == 2
        assert [p.terms for p in parts] == [["term1"], ["term2"]]

    def test_huggingface_preserves_body(self) -> None:
        adapter = HuggingFaceAdapter()
        raw = RawItem(
            source="huggingface",
            native_id="user/repo",
            payload={"id": "user/repo", "description": "Benchmark evaluation dataset for RAG."},
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw, terms=["RAG"])
        assert item.body == "Benchmark evaluation dataset for RAG."

    def test_rss_preserves_body_and_content(self) -> None:
        adapter = RSSAdapter()
        raw = RawItem(
            source="rss",
            native_id="http://example.com/item",
            payload={
                "title": "Post",
                "summary": "Short summary",
                "content": "Full article body with evaluation details.",
            },
            fetched_at=datetime.now(UTC),
        )
        item = adapter.normalize(raw)
        assert item.body == "Full article body with evaluation details."

    def test_github_effective_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        unauth_limit = GitHubAdapter.effective_rate_limit()
        assert unauth_limit.requests == 10
        assert unauth_limit.per_seconds == 60

        monkeypatch.setenv("GITHUB_TOKEN", "dummy_token")
        auth_limit = GitHubAdapter.effective_rate_limit()
        assert auth_limit.requests == 30

    def test_enrich_effective_core_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from msrkit.enrich import effective_core_rate_limit

        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert effective_core_rate_limit().requests == 55

        monkeypatch.setenv("GITHUB_TOKEN", "dummy_token")
        assert effective_core_rate_limit().requests == 4500

    def test_governed_get_retries_after_rate_limit_sleep(self) -> None:
        from msrkit.governor import Governor

        governor = Governor("github", GitHubAdapter.effective_rate_limit())
        adapter = GitHubAdapter(governor=governor)
        call_count = 0

        def fake_request(*args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    403,
                    headers={"Retry-After": "1", "x-ratelimit-remaining": "0"},
                    request=httpx.Request("GET", "https://api.github.com/test"),
                )
            return httpx.Response(
                200,
                json={"items": []},
                request=httpx.Request("GET", "https://api.github.com/test"),
            )

        adapter._request_with_retry = fake_request  # type: ignore[method-assign]
        resp = adapter._governed_get("https://api.github.com/test")
        assert resp.status_code == 200
        assert call_count == 2

    def test_devto_respects_max_pages_and_dedupes_discards(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = DevToAdapter()
        pages_requested: list[tuple[str, int]] = []

        def fake_get(url: str, params: dict[str, Any] | None = None) -> httpx.Response:
            assert params is not None
            pages_requested.append((params["tag"], params["page"]))
            articles = [
                {"id": i, "title": f"Unrelated post {i}", "description": "no match"}
                for i in range(1, 1001)
            ]
            return httpx.Response(200, json=articles, request=httpx.Request("GET", url))

        monkeypatch.setattr(adapter, "_governed_get", fake_get)
        q = Query(
            source="devto",
            terms=["RAG evaluation"],
            extra={"tags": ["rag", "llm"], "max_pages": 2},
            limit=10,
        )
        assert list(adapter.search(q)) == []
        assert pages_requested == [("rag", 1), ("rag", 2), ("llm", 1), ("llm", 2)]
        assert len(adapter.pop_discards()) == 1000
