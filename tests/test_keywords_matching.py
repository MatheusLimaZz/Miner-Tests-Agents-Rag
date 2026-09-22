"""Tests for keywords matching and adapter term detection."""

from __future__ import annotations

from datetime import UTC, datetime

from msrkit.adapters.devto import DevToAdapter
from msrkit.adapters.github import GitHubAdapter
from msrkit.adapters.hackernews import HackerNewsAdapter
from msrkit.adapters.stackexchange import StackExchangeAdapter
from msrkit.keywords import match_terms
from msrkit.models import RawItem


class TestKeywordsMatching:
    """Test suite for keywords module."""

    def test_exact_phrase_matching(self) -> None:
        """Terms with spaces match exact multi-word phrases."""
        terms = ["RAG testing", "agent evaluation"]
        hits = match_terms(
            terms,
            title="A comprehensive benchmark for RAG testing tools",
            body="We propose agent evaluation frameworks for multi-agent loops.",
        )
        assert len(hits) == 2
        terms_hit = {h.term for h in hits}
        assert "RAG testing" in terms_hit
        assert "agent evaluation" in terms_hit

    def test_word_boundary_matching(self) -> None:
        """Matching respects word boundaries."""
        terms = ["rag"]
        hits_positive = match_terms(terms, title="A new rag tool")
        assert len(hits_positive) == 1

        hits_negative = match_terms(terms, title="Using courage and brag tactics")
        assert len(hits_negative) == 0

    def test_non_alphanumeric_boundaries(self) -> None:
        """Terms with symbols like C++, .NET, C# match properly without word-boundary failure."""
        terms = ["c++", ".NET", "c#"]
        hits = match_terms(terms, title="Building a C++ parser and a .NET library for C# agents")
        matched = {h.term for h in hits}
        assert matched == {"c++", ".NET", "c#"}

    def test_case_insensitive_matching(self) -> None:
        """Matching is case-insensitive."""
        terms = ["LLM evaluation"]
        hits = match_terms(terms, title="llm evaluation in production")
        assert len(hits) == 1

    def test_context_window(self) -> None:
        """Context window surrounds match with tokens."""
        terms = ["golden dataset"]
        body = (
            "alpha beta gamma delta epsilon zeta eta theta iota kappa "
            "golden dataset "
            "lambda mu nu xi omicron pi rho sigma tau upsilon"
        )
        hits = match_terms(terms, body=body, context_window=3)
        assert len(hits) == 1
        assert "golden dataset" in hits[0].context
        # Check context has preceding and trailing words
        assert "theta iota kappa golden dataset lambda mu nu" in hits[0].context


class TestAdapterNormalizeWithTerms:
    """Verify adapters populate matched_terms when terms are passed."""

    def test_github_normalize_terms(self) -> None:
        raw = RawItem(
            source="github",
            native_id="101",
            payload={
                "id": 101,
                "full_name": "owner/rag-testing-repo",
                "html_url": "https://github.com/owner/rag-testing-repo",
                "description": "Evaluation harness for RAG testing pipelines",
                "topics": ["llm-evaluation", "rag"],
                "_search_kind": "repo",
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = GitHubAdapter()
        item = adapter.normalize(raw, terms=["RAG testing", "eval harness"])
        assert len(item.matched_terms) >= 1
        matched_terms_names = {t.term for t in item.matched_terms}
        assert "RAG testing" in matched_terms_names

    def test_hackernews_normalize_terms(self) -> None:
        raw = RawItem(
            source="hackernews",
            native_id="202",
            payload={
                "objectID": "202",
                "title": "Prompt regression testing for agents",
                "story_text": "How do you detect prompt regression in LLM apps?",
                "_tags": ["story"],
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = HackerNewsAdapter()
        item = adapter.normalize(raw, terms=["prompt regression", "agent testing"])
        assert len(item.matched_terms) >= 1
        matched_terms_names = {t.term for t in item.matched_terms}
        assert "prompt regression" in matched_terms_names

    def test_stackexchange_normalize_terms(self) -> None:
        raw = RawItem(
            source="stackexchange",
            native_id="303",
            payload={
                "question_id": 303,
                "title": "How to avoid hallucination test failures?",
                "body": "We need a robust hallucination test framework.",
                "tags": ["testing", "rag"],
                "_site": "stackoverflow",
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = StackExchangeAdapter()
        item = adapter.normalize(raw, terms=["hallucination test"])
        assert len(item.matched_terms) >= 1
        assert item.matched_terms[0].term == "hallucination test"

    def test_devto_normalize_terms(self) -> None:
        raw = RawItem(
            source="devto",
            native_id="404",
            payload={
                "id": 404,
                "url": "https://dev.to/author/eval-harness",
                "title": "Building an eval harness for agents",
                "description": "Step by step guide to creating an eval harness.",
                "tag_list": ["rag", "ai"],
            },
            fetched_at=datetime.now(UTC),
        )
        adapter = DevToAdapter()
        item = adapter.normalize(raw, terms=["eval harness"])
        assert len(item.matched_terms) >= 1
        assert item.matched_terms[0].term == "eval harness"
