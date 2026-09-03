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
