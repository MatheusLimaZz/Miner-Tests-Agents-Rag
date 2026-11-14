"""Tests for domain models."""

from __future__ import annotations

from datetime import UTC, datetime

from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Item,
    ItemKind,
    Manifest,
    Provenance,
    Query,
    RateLimit,
    RawItem,
    RedistributionPolicy,
    SourceManifestEntry,
    SourcePolicy,
    SourceUnsupportedError,
)


class TestItem:
    """Tests for the canonical Item model."""

    def test_make_id_deterministic(self) -> None:
        """Same source + native_id always produce the same ID."""
        id1 = Item.make_id("github", "12345")
        id2 = Item.make_id("github", "12345")
        assert id1 == id2
        assert len(id1) == 16

    def test_make_id_different_sources(self) -> None:
        """Different sources produce different IDs."""
        id1 = Item.make_id("github", "12345")
        id2 = Item.make_id("devto", "12345")
        assert id1 != id2

    def test_body_hash_auto_computed(self) -> None:
        """body_hash is auto-computed from body when not provided."""
        item = _make_item(body="Hello world")
        assert item.body_hash is not None
        assert len(item.body_hash) == 64  # SHA-256 hex

    def test_body_hash_none_when_no_body(self) -> None:
        """body_hash is None when body is None."""
        item = _make_item(body=None)
        assert item.body_hash is None

    def test_body_hash_preserved_when_explicit(self) -> None:
        """Explicit body_hash is not overwritten."""
        item = _make_item(body="Hello", body_hash="explicit_hash")
        assert item.body_hash == "explicit_hash"

    def test_item_serialization_roundtrip(self) -> None:
        """Item can be serialized to JSON and deserialized back."""
        item = _make_item(body="Test body")
        json_str = item.model_dump_json()
        restored = Item.model_validate_json(json_str)
        assert restored.id == item.id
        assert restored.source == item.source
        assert restored.body_hash == item.body_hash


class TestRawItem:
    """Tests for RawItem."""

    def test_raw_item_creation(self) -> None:
        raw = RawItem(
            source="github",
            native_id="123",
            payload={"key": "value"},
            fetched_at=datetime.now(UTC),
        )
        assert raw.source == "github"
        assert raw.payload["key"] == "value"


class TestQuery:
    """Tests for Query model."""

    def test_query_with_defaults(self) -> None:
        q = Query(source="github", terms=["RAG testing"])
        assert q.source == "github"
        assert q.kind is None
        assert q.extra == {}
        assert q.limit is None

    def test_query_with_all_fields(self) -> None:
        from datetime import date

        q = Query(
            source="github",
            terms=["RAG testing", "LLM eval"],
            kind="repo",
            since=date(2023, 1, 1),
            until=date(2024, 12, 31),
            extra={"languages": ["Python"]},
            limit=1000,
        )
        assert len(q.terms) == 2
        assert q.kind == "repo"


class TestAvailability:
    """Tests for Availability model."""

    def test_ok_status(self) -> None:
        a = Availability(
            status=AvailabilityStatus.OK,
            reason="All good",
        )
        assert a.status == "OK"

    def test_unsupported_with_missing_env(self) -> None:
        a = Availability(
            status=AvailabilityStatus.UNSUPPORTED,
            reason="Missing credentials",
            missing_env=["TOKEN_X"],
        )
        assert "TOKEN_X" in a.missing_env


class TestSourcePolicy:
    """Tests for SourcePolicy model."""

    def test_policy_creation(self) -> None:
        policy = SourcePolicy(
            requires_auth=True,
            auth_env_vars=["GITHUB_TOKEN"],
            rate_limit=RateLimit(requests=30, per_seconds=60, burst=5),
            max_page_size=100,
            supports_full_text_search=True,
            supports_date_filter=True,
            redistribution=RedistributionPolicy.METADATA_ONLY,
            tos_url="https://example.com/tos",
            docs_url="https://example.com/docs",
        )
        assert policy.requires_auth is True
        assert policy.rate_limit.requests == 30


class TestManifest:
    """Tests for Manifest model."""

    def test_manifest_creation(self) -> None:
        m = Manifest(
            run_id="test-run",
            msrkit_version="0.1.0",
            protocol_path="test.yaml",
            protocol_sha256="abc123",
            started_at=datetime.now(UTC),
        )
        assert m.run_id == "test-run"
        assert m.finished_at is None

    def test_manifest_with_sources(self) -> None:
        m = Manifest(
            run_id="test-run",
            msrkit_version="0.1.0",
            protocol_path="test.yaml",
            protocol_sha256="abc123",
            started_at=datetime.now(UTC),
            sources=[
                SourceManifestEntry(
                    name="linkedin",
                    adapter_version="0.1.0",
                    availability=Availability(
                        status=AvailabilityStatus.UNSUPPORTED,
                        reason="Permanently unsupported",
                    ),
                    queries=[],
                )
            ],
        )
        assert len(m.sources) == 1
        assert m.sources[0].name == "linkedin"


class TestErrors:
    """Tests for custom exceptions."""

    def test_source_unsupported_error(self) -> None:
        err = SourceUnsupportedError("linkedin", "Not supported")
        assert "linkedin" in str(err)
        assert err.source == "linkedin"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    body: str | None = None,
    body_hash: str | None = None,
) -> Item:
    """Create a minimal Item for testing."""
    kwargs: dict = {
        "id": Item.make_id("test", "1"),
        "source": "test",
        "kind": ItemKind.ARTICLE,
        "url": "https://example.com/test",
        "body": body,
        "provenance": Provenance(
            run_id="test-run",
            query_string="test query",
            partition="p1",
            adapter="test",
            adapter_version="0.1.0",
            fetched_at=datetime.now(UTC),
            response_sha256="abc123",
            raw_ref="test:0",
        ),
    }
    if body_hash is not None:
        kwargs["body_hash"] = body_hash
    return Item(**kwargs)


class TestLanguageLexicons:
    """Protocol lexicons per language and the language stratum (C5)."""

    def _config(self, **overrides):
        from msrkit.config import ProtocolConfig

        data = {
            "version": 0,
            "name": "t",
            "description": "d",
            "window": {"since": "2023-01-01", "until": "2023-12-31"},
            "terms": ["RAG testing", "shared term"],
            "terms_by_language": {"pt": ["teste de RAG", "shared term"]},
            "languages": ["en", "pt"],
            "sources": {"hackernews": {"enabled": True}},
        }
        data.update(overrides)
        return ProtocolConfig.model_validate(data)

    def test_all_terms_merges_lexicons_without_duplicates(self) -> None:
        cfg = self._config()
        assert cfg.all_terms() == ["RAG testing", "shared term", "teste de RAG"]
        assert cfg.build_queries("hackernews")[0].terms == cfg.all_terms()

    def test_term_languages(self) -> None:
        langs = self._config().term_languages()
        assert langs == {"RAG testing": "en", "shared term": "en", "teste de RAG": "pt"}

    def test_undeclared_language_is_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="not listed"):
            self._config(languages=["en"])

    def test_portuguese_inflections_match(self) -> None:
        from msrkit.keywords import match_terms

        assert match_terms(["avaliação de RAG"], title="Avaliações de RAG em produção")
        assert match_terms(["teste de agentes"], title="Testes de agente com LangSmith")
        assert match_terms(["LLM como juiz"], title="Usando LLMs como juízes")
        assert not match_terms(["teste de RAG"], title="Receita de bolo")

    def test_sample_protocol_searches_english_only(self) -> None:
        from msrkit.config import load_protocol

        cfg = load_protocol("protocols/v0_rag_agents_testing.yaml")  # ADR-050
        assert cfg.languages == ["en"]
        assert not cfg.terms_by_language
        assert "pt.stackoverflow" not in cfg.sources["stackexchange"].extra["sites"]


class TestConcepts:
    """Concept groups label items and can generate combined query terms (A2)."""

    def _config(self, **overrides):
        from msrkit.config import ProtocolConfig

        data = {
            "version": 0,
            "name": "t",
            "description": "d",
            "window": {"since": "2023-01-01", "until": "2023-12-31"},
            "terms": ["base"],
            "concepts": {"rag": ["RAG", "retriever"], "agente": ["agent"], "teste": ["test"]},
            "sources": {"hackernews": {"enabled": True}},
        }
        data.update(overrides)
        return ProtocolConfig.model_validate(data)

    def test_concept_queries_add_product_terms(self) -> None:
        cfg = self._config(concept_queries=[["rag", "teste"]])
        assert cfg.all_terms() == ["base", "RAG test", "retriever test"]

    def test_undefined_concept_in_queries_is_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="undefined concepts"):
            self._config(concept_queries=[["rag", "nope"]])

    def test_items_get_every_matching_concept(self) -> None:
        from datetime import UTC, datetime

        from msrkit.cli import _tag_concepts
        from msrkit.models import Item, ItemKind, Provenance

        cfg = self._config()
        prov = Provenance(
            run_id="r",
            query_string="q",
            partition="p",
            adapter="a",
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        )

        def item(title: str) -> Item:
            return Item(
                id=title,
                source="hackernews",
                kind=ItemKind.THREAD,
                url="https://x.example/" + title.replace(" ", "-"),
                title=title,
                provenance=prov,
            )

        hybrid = item("Testing agents that use a retriever")
        rag_only = item("RAG pipelines in production")
        none = item("Cooking with cast iron")
        for it in (hybrid, rag_only, none):
            _tag_concepts(it, cfg.concepts)
        assert hybrid.concepts == ["rag", "agente", "teste"]
        assert rag_only.concepts == ["rag"]
        assert none.concepts == []

    def test_sample_protocol_defines_e2_concepts(self) -> None:
        from msrkit.config import load_protocol

        cfg = load_protocol("protocols/v0_rag_agents_testing.yaml")
        assert set(cfg.concepts) == {"rag", "agente", "teste"}
