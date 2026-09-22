"""Tests for infrastructure modules: provenance, registry, and storage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Item,
    ItemKind,
    Manifest,
    Provenance,
    RawItem,
    SourceManifestEntry,
)
from msrkit.provenance import (
    generate_run_id,
    hash_response,
    hash_string,
    load_manifest,
    save_manifest,
)
from msrkit.registry import all_adapters, discover_adapters, get_adapter
from msrkit.storage import DuckDBStorage, RawStorage

if TYPE_CHECKING:
    from pathlib import Path


class TestProvenance:
    """Test provenance tracking and manifest persistence."""

    def test_generate_run_id_format(self) -> None:
        run_id = generate_run_id()
        assert len(run_id) > 10
        assert "T" in run_id
        assert "Z" in run_id

    def test_hash_helpers(self) -> None:
        h1 = hash_response(b"payload content")
        assert len(h1) == 64
        h2 = hash_string("string content")
        assert len(h2) == 64

    def test_save_and_load_manifest(self, tmp_path: Path) -> None:
        manifest = Manifest(
            run_id="run-infra-1",
            msrkit_version="0.1.0",
            protocol_path="proto.yaml",
            protocol_sha256="sha123",
            started_at=datetime.now(UTC),
            sources=[
                SourceManifestEntry(
                    name="hackernews",
                    adapter_version="0.1.0",
                    availability=Availability(status=AvailabilityStatus.OK, reason="OK"),
                )
            ],
        )
        path = save_manifest(manifest, tmp_path)
        assert path.exists()

        loaded = load_manifest(tmp_path, "run-infra-1")
        assert loaded.run_id == "run-infra-1"
        assert len(loaded.sources) == 1

    def test_load_manifest_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path, "nonexistent-run-id")


class TestRegistry:
    """Test adapter registry functions."""

    def test_get_adapter(self) -> None:
        discover_adapters()
        cls = get_adapter("github")
        assert cls.name == "github"

    def test_get_adapter_unknown(self) -> None:
        with pytest.raises(KeyError):
            get_adapter("unknown_provider_xyz")

    def test_all_adapters(self) -> None:
        adapters = all_adapters()
        assert "github" in adapters
        assert "hackernews" in adapters
        assert "stackexchange" in adapters


class TestStorageExtras:
    """Test extra storage functionality."""

    def test_duckdb_stats_and_query(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.duckdb"
        storage = DuckDBStorage(db_path)

        item = Item(
            id=Item.make_id("github", "1"),
            source="github",
            kind=ItemKind.REPO,
            url="https://github.com/org/repo",  # type: ignore[arg-type]
            title="test repo",
            provenance=Provenance(
                run_id="r1",
                query_string="q",
                partition="p",
                adapter="github",
                adapter_version="0.1",
                fetched_at=datetime.now(UTC),
                response_sha256="h",
                raw_ref="ref",
            ),
        )
        storage.ingest_items([item])

        stats_all = storage.stats()
        assert stats_all.get("github") == 1

        stats_r1 = storage.stats(run_id="r1")
        assert stats_r1.get("github") == 1

        stats_r2 = storage.stats(run_id="r2")
        assert stats_r2.get("github") is None
        storage.close()

    def test_duckdb_ingest_duplicates_count(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_dup.duckdb"
        storage = DuckDBStorage(db_path)
        item = Item(
            id=Item.make_id("github", "1"),
            source="github",
            kind=ItemKind.REPO,
            url="https://github.com/org/repo",  # type: ignore[arg-type]
            title="test repo",
            provenance=Provenance(
                run_id="r1",
                query_string="q",
                partition="p",
                adapter="github",
                adapter_version="0.1",
                fetched_at=datetime.now(UTC),
                response_sha256="h",
                raw_ref="ref",
            ),
        )
        inserted_first = storage.ingest_items([item])
        assert inserted_first == 1

        # Ingesting the exact same item again should return 0 inserted (INSERT OR IGNORE)
        inserted_second = storage.ingest_items([item])
        assert inserted_second == 0
        storage.close()

    def test_raw_storage_partitions_and_read(self, tmp_path: Path) -> None:
        raw_storage = RawStorage(tmp_path)
        raw = RawItem(
            source="github",
            native_id="10",
            payload={"id": 10},
            fetched_at=datetime.now(UTC),
        )
        ref = raw_storage.save_raw(raw, "run_alpha", "p1")
        assert "p1.jsonl.gz" in ref

        partitions = raw_storage.list_partitions("github", "run_alpha")
        assert "p1" in partitions

        items = raw_storage.read_raw("github", "run_alpha", "p1")
        assert len(items) == 1
        assert items[0].native_id == "10"

    def test_raw_storage_offset_caching(self, tmp_path: Path) -> None:
        """RawStorage caches offsets in memory to avoid O(N^2) decompression."""
        raw_storage = RawStorage(tmp_path)
        for i in range(3):
            raw = RawItem(
                source="hackernews",
                native_id=str(i),
                payload={"id": i},
                fetched_at=datetime.now(UTC),
            )
            ref = raw_storage.save_raw(raw, "run_beta", "p2")
            assert ref.endswith(f":{i}")

        items = raw_storage.read_raw("hackernews", "run_beta", "p2")
        assert len(items) == 3
