"""Tests for MSR-Kit CLI commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Item,
    ItemKind,
    Manifest,
    Provenance,
    Query,
    RawItem,
    SourceManifestEntry,
)
from msrkit.provenance import save_manifest
from msrkit.storage import ItemStorage, RawStorage

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


@pytest.fixture
def sample_items() -> list[Item]:
    """Create sample items for CLI testing."""
    item1 = Item(
        id=Item.make_id("github", "101"),
        source="github",
        kind=ItemKind.REPO,
        url="https://github.com/org/rag-testing",  # type: ignore[arg-type]
        title="rag-testing framework",
        body="A comprehensive tool for RAG testing and LLM evaluation.",
        author_handle="testuser",
        created_at=datetime(2024, 1, 15, tzinfo=UTC),
        provenance=Provenance(
            run_id="test-run-cli",
            query_string="RAG testing",
            partition="q1",
            adapter="github",
            adapter_version="0.1.0",
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
            response_sha256="hash1",
            raw_ref="data/raw/github/test-run-cli/part1.jsonl.gz:0",
        ),
    )
    # Duplicate of item1 (same URL)
    item2 = Item(
        id=Item.make_id("github", "102"),
        source="github",
        kind=ItemKind.REPO,
        url="https://github.com/org/rag-testing?utm_source=twitter",  # type: ignore[arg-type]
        title="rag-testing framework",
        body="A comprehensive tool for RAG testing and LLM evaluation.",
        author_handle="testuser",
        created_at=datetime(2024, 1, 15, tzinfo=UTC),
        provenance=Provenance(
            run_id="test-run-cli",
            query_string="RAG testing",
            partition="q2",
            adapter="github",
            adapter_version="0.1.0",
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
            response_sha256="hash2",
            raw_ref="data/raw/github/test-run-cli/part2.jsonl.gz:0",
        ),
    )
    # StackExchange item (full_text_with_attribution)
    item3 = Item(
        id=Item.make_id("stackexchange", "201"),
        source="stackexchange",
        kind=ItemKind.THREAD,
        url="https://stackoverflow.com/q/201",  # type: ignore[arg-type]
        title="How to test RAG pipelines effectively?",
        body="What evaluation metrics and golden datasets are recommended?",
        author_handle="stackuser",
        created_at=datetime(2024, 2, 1, tzinfo=UTC),
        provenance=Provenance(
            run_id="test-run-cli",
            query_string="RAG testing",
            partition="q1",
            adapter="stackexchange",
            adapter_version="0.1.0",
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
            response_sha256="hash3",
            raw_ref="data/raw/stackexchange/test-run-cli/part1.jsonl.gz:0",
        ),
    )
    return [item1, item2, item3]


class TestCliCommands:
    """Test suite for CLI command execution."""

    def test_sources_table_output(self) -> None:
        """msrkit sources outputs table with registered adapters."""
        result = runner.invoke(app, ["sources"])
        assert result.exit_code == 0
        assert "MSR-Kit Sources" in result.stdout
        assert "github" in result.stdout
        assert "linkedin" in result.stdout

    def test_sources_markdown_output(self) -> None:
        """msrkit sources --md outputs markdown documentation."""
        result = runner.invoke(app, ["sources", "--md"])
        assert result.exit_code == 0
        assert "# MSR-Kit Sources" in result.stdout
        assert "## github" in result.stdout
        assert "## linkedin" in result.stdout

    def test_validate_valid_protocol(self) -> None:
        """msrkit validate succeeds on standard protocol."""
        result = runner.invoke(app, ["validate", "protocols/v0_rag_agents_testing.yaml"])
        assert result.exit_code == 0
        assert "Protocol 'v0_rag_agents_testing' v0 is valid" in result.stdout

    def test_validate_nonexistent_protocol(self) -> None:
        """msrkit validate fails on missing file."""
        result = runner.invoke(app, ["validate", "protocols/nonexistent.yaml"])
        assert result.exit_code != 0

    def test_plan_dry_run(self) -> None:
        """msrkit plan displays dry-run estimates without network calls."""
        result = runner.invoke(app, ["plan", "protocols/v0_rag_agents_testing.yaml"])
        assert result.exit_code == 0
        assert "Collection Plan (Dry Run)" in result.stdout
        assert "Total estimated requests:" in result.stdout

    def test_plan_with_source(self) -> None:
        """msrkit plan --source filters to single source."""
        result = runner.invoke(
            app, ["plan", "protocols/v0_rag_agents_testing.yaml", "--source", "hackernews"]
        )
        assert result.exit_code == 0
        assert "hackernews" in result.stdout
        assert "github" not in result.stdout

    def test_plan_with_invalid_source(self) -> None:
        """msrkit plan --source fails on unconfigured source."""
        result = runner.invoke(
            app, ["plan", "protocols/v0_rag_agents_testing.yaml", "--source", "nonexistent"]
        )
        assert result.exit_code == 1
        assert "not configured in protocol" in result.stdout

    def test_dedupe_command_registered_and_executes(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit dedupe deduplicates items and saves items_deduped.jsonl."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items, "run-dedupe-test")

        result = runner.invoke(app, ["dedupe", "--run", "run-dedupe-test"])
        assert result.exit_code == 0
        assert "Input: 3" in result.stdout
        assert "Unique: 2" in result.stdout
        assert "Duplicates removed: 1" in result.stdout

        deduped_file = tmp_path / "items" / "run-dedupe-test" / "items_deduped.jsonl"
        assert deduped_file.exists()

    def test_export_refuses_body_for_metadata_only_source(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit export --include-body rejects export when metadata_only sources exist."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items, "run-export-test")

        result = runner.invoke(app, ["export", "--run", "run-export-test", "--include-body"])
        assert result.exit_code == 1
        assert "Cannot export body" in result.stdout
        assert "metadata_only" in result.stdout

    def test_export_without_body_jsonl(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit export creates JSONL with body omitted by default."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items, "run-export-jsonl")

        out_file = tmp_path / "test_export.jsonl"
        result = runner.invoke(
            app, ["export", "--run", "run-export-jsonl", "--format", "jsonl", "-o", str(out_file)]
        )
        assert result.exit_code == 0
        assert out_file.exists()

        lines = out_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        first_item = json.loads(lines[0])
        assert "body" not in first_item

    def test_export_without_body_csv(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit export creates CSV without body column by default."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items, "run-export-csv")

        out_file = tmp_path / "test_export.csv"
        result = runner.invoke(
            app, ["export", "--run", "run-export-csv", "--format", "csv", "-o", str(out_file)]
        )
        assert result.exit_code == 0
        assert out_file.exists()

        content = out_file.read_text(encoding="utf-8-sig")
        header = content.splitlines()[0]
        assert "body" not in header.split(";")
        assert "id" in header.split(";")

    def test_export_csv_custom_delimiter(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit export respects custom delimiter like comma."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items, "run-export-delim")

        out_file = tmp_path / "test_delim.csv"
        result = runner.invoke(
            app,
            [
                "export",
                "--run",
                "run-export-delim",
                "--format",
                "csv",
                "--delimiter",
                ",",
                "-o",
                str(out_file),
            ],
        )
        assert result.exit_code == 0
        content = out_file.read_text(encoding="utf-8-sig")
        header = content.splitlines()[0]
        assert "id" in header.split(",")

    def test_export_duckdb(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit export creates duckdb database without body by default."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items, "run-export-duckdb")

        out_file = tmp_path / "test_export.duckdb"
        result = runner.invoke(
            app, ["export", "--run", "run-export-duckdb", "--format", "duckdb", "-o", str(out_file)]
        )
        assert result.exit_code == 0
        assert out_file.exists()

    def test_normalize_with_protocol_terms(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit normalize reprocesses raw items and performs term matching."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)

        run_id = "test-normalize-terms"
        raw_storage = RawStorage(tmp_path)

        # Save a raw Hackernews item
        raw = RawItem(
            source="hackernews",
            native_id="999",
            payload={
                "objectID": "999",
                "title": "A new tool for RAG testing and evaluation",
                "url": "https://news.ycombinator.com/item?id=999",
                "author": "hn_author",
                "created_at_i": 1700000000,
                "_tags": ["story"],
            },
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        raw_storage.save_raw(raw, run_id, "part1")

        # Save manifest
        manifest = Manifest(
            run_id=run_id,
            msrkit_version="0.1.0",
            protocol_path="protocols/v0_rag_agents_testing.yaml",
            protocol_sha256="abc",
            started_at=datetime.now(UTC),
            sources=[
                SourceManifestEntry(
                    name="hackernews",
                    adapter_version="0.1.0",
                    availability=Availability(status=AvailabilityStatus.OK, reason="OK"),
                )
            ],
        )
        save_manifest(manifest, tmp_path)

        # Run normalize
        result = runner.invoke(app, ["normalize", "--run", run_id])
        assert result.exit_code == 0

        # Read back normalized item
        item_storage = ItemStorage(tmp_path)
        items = item_storage.read_items(run_id)
        assert len(items) == 1
        assert len(items[0].matched_terms) > 0
        matched_term_names = [t.term for t in items[0].matched_terms]
        assert "RAG testing" in matched_term_names

    def test_normalize_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running normalize multiple times must overwrite, not append duplicate items."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        run_id = "test-normalize-idempotent"
        raw_storage = RawStorage(tmp_path)
        raw = RawItem(
            source="hackernews",
            native_id="111",
            payload={
                "objectID": "111",
                "title": "Item title",
                "created_at_i": 1700000000,
                "_tags": ["story"],
            },
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        raw_storage.save_raw(raw, run_id, "part1")
        manifest = Manifest(
            run_id=run_id,
            msrkit_version="0.1.0",
            protocol_path="protocols/v0_rag_agents_testing.yaml",
            protocol_sha256="abc",
            started_at=datetime.now(UTC),
            sources=[
                SourceManifestEntry(
                    name="hackernews",
                    adapter_version="0.1.0",
                    availability=Availability(status=AvailabilityStatus.OK, reason="OK"),
                )
            ],
        )
        save_manifest(manifest, tmp_path)

        res1 = runner.invoke(app, ["normalize", "--run", run_id])
        assert res1.exit_code == 0
        res2 = runner.invoke(app, ["normalize", "--run", run_id])
        assert res2.exit_code == 0

        item_storage = ItemStorage(tmp_path)
        items = item_storage.read_items(run_id)
        assert len(items) == 1

    def test_stats_command(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit stats displays run statistics."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        run_id = "test-stats-run"

        manifest = Manifest(
            run_id=run_id,
            msrkit_version="0.1.0",
            protocol_path="protocols/v0_rag_agents_testing.yaml",
            protocol_sha256="abc",
            started_at=datetime.now(UTC),
            sources=[
                SourceManifestEntry(
                    name="github",
                    adapter_version="0.1.0",
                    availability=Availability(status=AvailabilityStatus.OK, reason="OK"),
                )
            ],
        )
        save_manifest(manifest, tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items, run_id)

        result = runner.invoke(app, ["stats", "--run", run_id])
        assert result.exit_code == 0
        assert f"Run Statistics: {run_id}" in result.stdout
        assert "Total items:" in result.stdout

    def test_version_flag(self) -> None:
        """msrkit --version outputs version."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "msrkit" in result.stdout

    def test_run_command_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """msrkit run executes collection and creates manifest + raw + items."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)

        proto_file = tmp_path / "test_proto.yaml"
        proto_file.write_text(
            """
version: 0
name: test_run
description: "Test run protocol"
window:
  since: "2024-01-01"
  until: "2024-01-31"
terms:
  - "RAG testing"
languages: [en]
sources:
  hackernews:
    enabled: true
limits:
  max_items_per_source: 10
  max_requests_per_source: 5
""",
            encoding="utf-8",
        )

        from msrkit.adapters.hackernews import HackerNewsAdapter

        def mock_search(self: HackerNewsAdapter, query: object) -> object:
            yield RawItem(
                source="hackernews",
                native_id="hn-run-1",
                payload={
                    "objectID": "hn-run-1",
                    "title": "A guide to RAG testing and validation",
                    "url": "https://news.ycombinator.com/item?id=123",
                    "created_at_i": 1705000000,
                    "_tags": ["story"],
                },
                fetched_at=datetime.now(UTC),
            )

        monkeypatch.setattr(HackerNewsAdapter, "search", mock_search)
        monkeypatch.setattr(HackerNewsAdapter, "estimate", lambda *args, **kwargs: 10)

        result = runner.invoke(app, ["run", str(proto_file)])
        assert result.exit_code == 0
        assert "Run complete" in result.stdout

        runs = [p.name for p in (tmp_path / "runs").iterdir() if p.is_dir()]
        assert len(runs) == 1
        run_id = runs[0]

        raw_storage = RawStorage(tmp_path)
        partitions = raw_storage.list_partitions("hackernews", run_id)
        assert len(partitions) == 1

        item_storage = ItemStorage(tmp_path)
        items = item_storage.read_items(run_id)
        assert len(items) == 1
        assert items[0].title == "A guide to RAG testing and validation"
        assert len(items[0].matched_terms) >= 1

    def test_run_with_source_and_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit run --source --limit executes collection for single source."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        from msrkit.adapters.hackernews import HackerNewsAdapter

        def mock_search(self: HackerNewsAdapter, query: object) -> object:
            for idx in range(5):
                yield RawItem(
                    source="hackernews",
                    native_id=f"hn-limit-{idx}",
                    payload={
                        "objectID": f"hn-limit-{idx}",
                        "title": f"RAG item {idx}",
                        "created_at_i": 1705000000,
                        "_tags": ["story"],
                    },
                    fetched_at=datetime.now(UTC),
                )

        monkeypatch.setattr(HackerNewsAdapter, "search", mock_search)
        monkeypatch.setattr(HackerNewsAdapter, "estimate", lambda *args, **kwargs: 10)

        result = runner.invoke(
            app,
            [
                "run",
                "protocols/v0_rag_agents_testing.yaml",
                "--source",
                "hackernews",
                "--limit",
                "2",
            ],
        )
        assert result.exit_code == 0
        assert "Source: hackernews" in result.stdout
        assert "Collected: 2 items" in result.stdout

    def test_run_with_invalid_source(self) -> None:
        """msrkit run --source fails when source is not in protocol."""
        result = runner.invoke(
            app,
            ["run", "protocols/v0_rag_agents_testing.yaml", "--source", "nonexistent"],
        )
        assert result.exit_code == 1
        assert "not configured in protocol" in result.stdout

    def test_run_resume_nonexistent_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit run --resume fails when run ID does not exist."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        result = runner.invoke(
            app,
            [
                "run",
                "protocols/v0_rag_agents_testing.yaml",
                "--resume",
                "nonexistent-run-id-999",
                "--source",
                "hackernews",
            ],
        )
        assert result.exit_code == 1
        assert "Cannot resume: Run 'nonexistent-run-id-999' not found" in result.stdout

    def test_run_resume_preserves_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit run --resume loads existing manifest and skips completed sources."""
        from datetime import UTC, datetime

        from msrkit.models import (
            Availability,
            AvailabilityStatus,
            Manifest,
            QueryManifestEntry,
            SourceManifestEntry,
        )
        from msrkit.provenance import load_manifest, save_manifest

        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        run_id = "resumed-run-1"
        initial_manifest = Manifest(
            run_id=run_id,
            msrkit_version="0.1.0",
            protocol_path="protocols/v0_rag_agents_testing.yaml",
            protocol_sha256="abc",
            started_at=datetime.now(UTC),
            sources=[
                SourceManifestEntry(
                    name="hackernews",
                    adapter_version="0.1.0",
                    availability=Availability(status=AvailabilityStatus.OK, reason="OK"),
                    queries=[
                        QueryManifestEntry(
                            query_string="q",
                            partitions=1,
                            requests=1,
                            items=5,
                            truncated=False,
                            response_sha256=[],
                        )
                    ],
                )
            ],
        )
        save_manifest(initial_manifest, tmp_path)
        from msrkit.adapters.hackernews import HackerNewsAdapter

        monkeypatch.setattr(HackerNewsAdapter, "search", lambda *args, **kwargs: iter([]))
        monkeypatch.setattr(HackerNewsAdapter, "estimate", lambda *args, **kwargs: 10)

        result = runner.invoke(
            app,
            [
                "run",
                "protocols/v0_rag_agents_testing.yaml",
                "--resume",
                run_id,
                "--source",
                "hackernews",
            ],
        )
        # Should succeed and manifest should still have hackernews
        assert result.exit_code == 0
        updated = load_manifest(tmp_path, run_id)
        assert len(updated.sources) >= 1
        assert any(s.name == "hackernews" for s in updated.sources)

    def test_toggle_source_in_protocol(self, tmp_path: Path) -> None:
        """_toggle_source_in_protocol toggles enabled state while preserving comments."""
        from msrkit.cli import _toggle_source_in_protocol

        proto_file = tmp_path / "test_proto.yaml"
        proto_file.write_text(
            """sources:
  hackernews:
    # Public Algolia API
    enabled: true
  devto:
    # dev.to API
    enabled: false
""",
            encoding="utf-8",
        )

        assert _toggle_source_in_protocol(str(proto_file), "hackernews", False) is True
        content = proto_file.read_text(encoding="utf-8")
        assert "hackernews:\n    # Public Algolia API\n    enabled: false" in content

        assert _toggle_source_in_protocol(str(proto_file), "devto", True) is True
        content = proto_file.read_text(encoding="utf-8")
        assert "devto:\n    # dev.to API\n    enabled: true" in content

    def test_export_all_runs(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit export --all consolidates items from multiple runs and deduplicates them."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items[:2], "run-1")
        storage.save_items(sample_items[1:], "run-2")

        out_file = tmp_path / "consolidated.csv"
        result = runner.invoke(app, ["export", "--all", "--format", "csv", "-o", str(out_file)])
        assert result.exit_code == 0
        assert "Consolidating items from 2 historical runs" in result.stdout
        assert out_file.exists()
        lines = out_file.read_text(encoding="utf-8-sig").strip().splitlines()
        # Header + 2 unique items
        assert len(lines) == 3

    def test_export_raw_flag(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit export --raw exports raw items even if deduped items exist."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        # sample_items[:2] has 2 items (one is duplicate of the other)
        storage.save_items(sample_items[:2], "run-raw-test")
        # Run dedupe to create items_deduped.jsonl (1 unique item)
        result_dedupe = runner.invoke(app, ["dedupe", "--run", "run-raw-test"])
        assert result_dedupe.exit_code == 0

        # Export with --raw should have both 2 items (header + 2 = 3 lines)
        raw_out = tmp_path / "raw.csv"
        res_raw = runner.invoke(
            app, ["export", "--run", "run-raw-test", "-f", "csv", "--raw", "-o", str(raw_out)]
        )
        assert res_raw.exit_code == 0
        raw_lines = raw_out.read_text(encoding="utf-8-sig").strip().splitlines()
        assert len(raw_lines) == 3

        # Default export (without --raw) should have 1 item (header + 1 = 2 lines)
        dedup_out = tmp_path / "dedup.csv"
        res_dedup = runner.invoke(
            app, ["export", "--run", "run-raw-test", "-f", "csv", "-o", str(dedup_out)]
        )
        assert res_dedup.exit_code == 0
        dedup_lines = dedup_out.read_text(encoding="utf-8-sig").strip().splitlines()
        assert len(dedup_lines) == 2

    def test_direct_python_run_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling run(...) directly from Python unwraps Typer defaults properly."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        from msrkit.adapters.hackernews import HackerNewsAdapter
        from msrkit.cli import run

        def mock_search(self: HackerNewsAdapter, query: object) -> object:
            return iter([])

        monkeypatch.setattr(HackerNewsAdapter, "search", mock_search)
        monkeypatch.setattr(HackerNewsAdapter, "estimate", lambda *args, **kwargs: 10)
        # Should execute cleanly without Manifest validation error for OptionInfo run_id
        run(protocol="protocols/v0_rag_agents_testing.yaml", source="hackernews", limit=1)

    def test_dedupe_all_runs(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit dedupe --all deduplicates items across all historical runs."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items[:2], "run-1")
        storage.save_items(sample_items[1:], "run-2")

        result = runner.invoke(app, ["dedupe", "--all"])
        assert result.exit_code == 0
        assert "Deduplicating across 2 historical runs" in result.stdout
        assert (tmp_path / "items" / "consolidated" / "items_deduped.jsonl").exists()

    def test_stats_with_historical_runs(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit stats displays run stats and consolidated historical corpus."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items[:2], "run-1")
        storage.save_items(sample_items[1:], "run-2")

        # Save manifest for run-2
        m = Manifest(
            run_id="run-2",
            msrkit_version="0.1.0",
            protocol_path="protocols/v0_rag_agents_testing.yaml",
            protocol_sha256="abc",
            started_at=datetime.now(UTC),
            sources=[
                SourceManifestEntry(
                    name="github",
                    adapter_version="0.1.0",
                    availability=Availability(status=AvailabilityStatus.OK, reason="OK"),
                )
            ],
        )
        save_manifest(m, tmp_path)

        result = runner.invoke(app, ["stats", "--run", "run-2"])
        assert result.exit_code == 0
        assert "Run Statistics: run-2" in result.stdout
        assert "Consolidated Historical Corpus (2 runs)" in result.stdout
        assert "Total Consolidated" in result.stdout

    def test_stats_all_flag(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit stats --all displays consolidated corpus overview directly."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items[:2], "run-1")
        storage.save_items(sample_items[1:], "run-2")

        result = runner.invoke(app, ["stats", "--all"])
        assert result.exit_code == 0
        assert "Consolidated Historical Corpus (2 runs)" in result.stdout
        assert "Total Consolidated" in result.stdout
        assert "duplicates removed" in result.stdout

    def test_stats_no_runs_exits_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit stats exits with error when no runs exist."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 1
        assert "No runs found" in result.stdout

    def test_export_permission_error_handled(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit export catches PermissionError and displays helpful message."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)
        storage.save_items(sample_items[:1], "run-1")

        import builtins

        real_open = builtins.open

        def mock_open(path: object, *args: object, **kwargs: object) -> object:
            if str(path).endswith("test.csv"):
                raise PermissionError("Access denied")
            return real_open(path, *args, **kwargs)  # type: ignore[call-overload]

        monkeypatch.setattr("builtins.open", mock_open)
        result = runner.invoke(app, ["export", "--run", "run-1", "-f", "csv", "-o", "test.csv"])
        assert result.exit_code == 1
        assert "Permissão negada" in result.stdout

    def test_run_enforces_source_level_limit_across_queries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit run --limit limits items at the source level, not per-query."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        from msrkit.adapters.github import GitHubAdapter

        def mock_search(self: GitHubAdapter, query: Query) -> Any:
            for i in range(5):
                yield RawItem(
                    source="github",
                    native_id=f"gh-{query.kind}-{i}",
                    payload={
                        "id": f"gh-{query.kind}-{i}",
                        "name": f"Repo {i}",
                        "full_name": f"owner/repo-{query.kind}-{i}",
                        "html_url": "https://github.com/owner/repo",
                        "created_at": "2024-01-01T00:00:00Z",
                    },
                    fetched_at=datetime.now(UTC),
                )

        monkeypatch.setattr(GitHubAdapter, "search", mock_search)
        monkeypatch.setattr(GitHubAdapter, "estimate", lambda *args, **kwargs: 10)

        result = runner.invoke(
            app,
            [
                "run",
                "protocols/v0_rag_agents_testing.yaml",
                "--source",
                "github",
                "--limit",
                "3",
            ],
        )
        assert result.exit_code == 0
        assert "Collected: 3 items" in result.stdout

        runs = [p.name for p in (tmp_path / "runs").iterdir() if p.is_dir()]
        assert len(runs) == 1
        item_storage = ItemStorage(tmp_path)
        items = item_storage.read_items(runs[0])
        assert len(items) == 3
        # Ensure provenance.partition matches the disk partition and is readable via RawStorage
        raw_storage = RawStorage(tmp_path)
        for item in items:
            raw_list = raw_storage.read_raw(item.source, runs[0], item.provenance.partition)
            assert len(raw_list) > 0

    def test_base_adapter_tracks_response_hashes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BaseAdapter tracks SHA-256 of HTTP responses for manifest provenance."""
        from unittest.mock import MagicMock

        from msrkit.adapters.devto import DevToAdapter

        adapter = DevToAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"hello": "world"}'
        mock_resp.json.return_value = []
        monkeypatch.setattr(adapter, "_request_with_retry", lambda *args, **kwargs: mock_resp)

        adapter._governed_get("https://dev.to/api/articles")
        expected_sha = adapter._hash_response(b'{"hello": "world"}')
        assert adapter.last_response_sha256 == expected_sha
        assert adapter.pop_response_hashes() == [expected_sha]
        assert adapter.pop_response_hashes() == []

    def test_normalize_preserves_provenance_raw_ref_and_partition(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """msrkit normalize populates raw_ref and partition in item provenance."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        run_id = "test-normalize-prov"
        raw_storage = RawStorage(tmp_path)
        raw = RawItem(
            source="hackernews",
            native_id="456",
            payload={
                "objectID": "456",
                "title": "RAG evaluation post",
                "created_at_i": 1700000000,
                "_tags": ["story"],
            },
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        raw_storage.save_raw(raw, run_id, "partA")

        manifest = Manifest(
            run_id=run_id,
            msrkit_version="0.1.0",
            protocol_path="protocols/v0_rag_agents_testing.yaml",
            protocol_sha256="abc",
            started_at=datetime.now(UTC),
            sources=[
                SourceManifestEntry(
                    name="hackernews",
                    adapter_version="0.1.0",
                    availability=Availability(status=AvailabilityStatus.OK, reason="OK"),
                )
            ],
        )
        save_manifest(manifest, tmp_path)

        res = runner.invoke(app, ["normalize", "--run", run_id])
        assert res.exit_code == 0

        item_storage = ItemStorage(tmp_path)
        items = item_storage.read_items(run_id)
        assert len(items) == 1
        assert items[0].provenance.partition == "partA"
        assert items[0].provenance.raw_ref.endswith("partA.jsonl.gz:0")


class TestTruncationManifest:
    """The run manifest records truncation and its reasons (C2)."""

    def _run_source(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> Any:
        from msrkit.provenance import load_manifest

        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        result = runner.invoke(
            app, ["run", "protocols/v0_rag_agents_testing.yaml", "--source", source, "--limit", "2"]
        )
        assert result.exit_code == 0, result.stdout
        run_id = next(p.name for p in (tmp_path / "runs").iterdir() if p.is_dir())
        entry = next(s for s in load_manifest(tmp_path, run_id).sources if s.name == source)
        return entry, run_id

    def test_source_cap_and_item_limit_are_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from msrkit.adapters.hackernews import HackerNewsAdapter

        def mock_partition(self: Any, q: Query) -> list[Query]:
            return [
                q.model_copy(
                    update={"terms": [q.terms[0]], "truncated": True, "estimated_total": 2500}
                )
            ]

        def mock_search(self: Any, query: Query) -> Any:
            for i in range(5):
                yield RawItem(
                    source="hackernews",
                    native_id=str(i),
                    payload={"objectID": str(i), "title": "LLM evaluation", "_tags": ["story"]},
                    fetched_at=datetime.now(UTC),
                )

        monkeypatch.setattr(HackerNewsAdapter, "partition", mock_partition)
        monkeypatch.setattr(HackerNewsAdapter, "search", mock_search)
        entry, run_id = self._run_source(tmp_path, monkeypatch, "hackernews")

        q = entry.queries[0]
        assert q.truncated is True
        assert q.truncation_reasons == ["source_cap", "item_limit"]
        assert q.estimated_total == 2500

        stats = runner.invoke(app, ["stats", "--run", run_id])
        assert "source_cap×1" in stats.stdout

    def test_rss_is_marked_without_historical_coverage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from msrkit.adapters.rss import RSSAdapter

        monkeypatch.setattr(RSSAdapter, "search", lambda *args, **kwargs: iter([]))
        entry, _ = self._run_source(tmp_path, monkeypatch, "rss")
        assert entry.queries
        assert all(q.truncated for q in entry.queries)
        assert all("no_historical_coverage" in q.truncation_reasons for q in entry.queries)


class TestLanguageStratum:
    """Term hits carry their lexicon language, exported as matched_languages (C5)."""

    def test_csv_exports_matched_languages(
        self, tmp_path: Path, sample_items: list[Item], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from msrkit.cli import _tag_term_languages
        from msrkit.models import TermHit

        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        item = sample_items[0]
        item.matched_terms = [
            TermHit(term="RAG testing", field="title", context="c"),
            TermHit(term="teste de RAG", field="body", context="c"),
        ]
        _tag_term_languages(item, {"RAG testing": "en", "teste de RAG": "pt"})
        assert [h.lang for h in item.matched_terms] == ["en", "pt"]

        ItemStorage(tmp_path).save_items([item], "run-lang")
        out = tmp_path / "out.csv"
        result = runner.invoke(
            app, ["export", "--run", "run-lang", "--format", "csv", "-o", str(out)]
        )
        assert result.exit_code == 0, result.stdout
        header, row = out.read_text(encoding="utf-8-sig").splitlines()[:2]
        cols = header.split(";")
        assert "matched_languages" in cols
        assert row.split(";")[cols.index("matched_languages")] == "en, pt"


class TestPlanEstimate:
    """`plan --estimate` partitions for real and budgets from the estimates (A4)."""

    def test_estimate_reports_partitions_truncation_and_items(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import timedelta

        from msrkit.adapters.hackernews import HackerNewsAdapter

        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)

        def fake_estimate(self: Any, q: Query) -> int:
            self._request_count += 1
            days = (q.until - q.since + timedelta(days=1)).days
            return 1500 if days > 400 else 400  # every term splits once

        monkeypatch.setattr(HackerNewsAdapter, "estimate", fake_estimate)
        result = runner.invoke(
            app,
            ["plan", "protocols/v0_rag_agents_testing.yaml", "-s", "hackernews", "--estimate"],
        )
        assert result.exit_code == 0, result.stdout
        assert "Collection Plan (Estimated)" in result.stdout
        assert "hackernews" in result.stdout
        assert "Dry run" not in result.stdout


class TestPartitionCheckpoint:
    """Checkpoint after every partition and exact resume (A5)."""

    def test_resume_skips_done_partitions_and_redoes_the_broken_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from msrkit.adapters.hackernews import HackerNewsAdapter
        from msrkit.provenance import load_manifest

        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)

        def two_partitions(self: Any, q: Query) -> list[Query]:
            return [q.model_copy(update={"terms": [t]}) for t in q.terms[:2]]

        calls: list[str] = []
        state = {"crash": True}
        proto = "protocols/v0_rag_agents_testing.yaml"
        from msrkit.config import load_protocol

        broken = load_protocol(proto).all_terms()[1]  # the second partition fails

        def search(self: Any, q: Query) -> Any:
            calls.append(q.terms[0])
            for i in range(3):
                if state["crash"] and q.terms[0] == broken and i == 1:
                    raise RuntimeError("connection reset")
                yield RawItem(
                    source="hackernews",
                    native_id=f"{q.terms[0]}-{i}",
                    payload={
                        "objectID": f"{q.terms[0]}-{i}",
                        "title": q.terms[0],
                        "_tags": ["story"],
                    },
                    fetched_at=datetime.now(UTC),
                )

        monkeypatch.setattr(HackerNewsAdapter, "partition", two_partitions)
        monkeypatch.setattr(HackerNewsAdapter, "search", search)
        monkeypatch.setattr(HackerNewsAdapter, "stratifies_by_date", lambda self, q: False)

        result = runner.invoke(app, ["run", proto, "--source", "hackernews"])
        assert result.exit_code == 0, result.stdout
        run_id = next(p.name for p in (tmp_path / "runs").iterdir())
        entry = load_manifest(tmp_path, run_id).sources[0]
        assert len(entry.planned) == 2
        assert [q.completed for q in entry.queries] == [True, False]
        assert len(ItemStorage(tmp_path).read_items(run_id)) == 4  # 3 + 1 partial

        state["crash"] = False
        calls.clear()
        result = runner.invoke(app, ["run", proto, "--source", "hackernews", "--resume", run_id])
        assert result.exit_code == 0, result.stdout
        assert calls == [broken]  # the completed partition is not searched again
        entry = load_manifest(tmp_path, run_id).sources[0]
        assert [q.completed for q in entry.queries] == [True, True]
        items = ItemStorage(tmp_path).read_items(run_id)
        assert len(items) == 6  # partial item of the broken partition was dropped
        assert len({it.id for it in items}) == 6


def test_module_entry_point_runs() -> None:
    """`python -m msrkit.cli` works (CI uses it; the CLI is a package since M1)."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "msrkit.cli", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "msrkit" in result.stdout


def test_extract_preserves_existing_detections_on_stripped_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """msrkit extract does not clobber detections if signals are stripped, unless --force."""
    import json
    from datetime import UTC, datetime

    from typer.testing import CliRunner

    from msrkit.cli import app
    from msrkit.models import Item, ItemKind, Provenance
    from msrkit.storage import ItemStorage

    monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
    runner = CliRunner()
    run_id = "run-test-extract-guard"

    item = Item(
        id="it1",
        source="github",
        kind=ItemKind.REPO,
        url="https://github.com/acme/plain",
        provenance=Provenance(
            run_id=run_id,
            query_string="q",
            partition="p",
            adapter="github",
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),
    )
    storage = ItemStorage(tmp_path)
    storage.save_items([item], run_id)

    enrich_dir = tmp_path / "enrich" / run_id
    enrich_dir.mkdir(parents=True, exist_ok=True)
    stripped_sig = {
        "repo": "acme/plain",
        "workflows": [".github/workflows/ci.yml"],
        "manifests": ["requirements.txt"],
    }
    (enrich_dir / "github_repos.jsonl").write_text(
        json.dumps(stripped_sig) + "\n", encoding="utf-8"
    )

    extract_dir = tmp_path / "extract" / run_id
    extract_dir.mkdir(parents=True, exist_ok=True)
    det_file = extract_dir / "detections.jsonl"
    fake_dets = (
        "\n".join(
            [f'{{"item_id": "it{i}", "entry_id": "tool", "signal": "doc"}}' for i in range(5)]
        )
        + "\n"
    )
    det_file.write_text(fake_dets, encoding="utf-8")

    res = runner.invoke(app, ["extract", "--run", run_id])
    assert res.exit_code == 0
    assert "Preserving existing" in res.stdout
    assert len(det_file.read_text(encoding="utf-8").strip().splitlines()) == 5

    res_force = runner.invoke(app, ["extract", "--run", run_id, "--force"])
    assert res_force.exit_code == 0
    assert "Preserving existing" not in res_force.stdout
    assert len(det_file.read_text(encoding="utf-8").strip().splitlines()) == 0
