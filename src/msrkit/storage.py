"""Storage: JSONL raw files + DuckDB query layer.

Raw responses are stored as immutable gzip-compressed JSONL files.
Normalized items are stored in plain JSONL. DuckDB provides fast
query access over the normalized data.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from msrkit.models import Item, RawItem

logger = logging.getLogger(__name__)


class RawStorage:
    """Immutable raw response storage using gzip-compressed JSONL.

    Files are stored at: data/raw/{source}/{run_id}/{partition_hash}.jsonl.gz
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._offsets: dict[str, int] = {}

    def save_raw(
        self,
        raw_item: RawItem,
        run_id: str,
        partition_hash: str,
    ) -> str:
        """Append a raw item to the partition file.

        Args:
            raw_item: The raw API response to store.
            run_id: Current run ID.
            partition_hash: Hash identifying the partition.

        Returns:
            Reference string (file path + line offset) for provenance.
        """
        raw_dir = self.data_dir / "raw" / raw_item.source / run_id
        raw_dir.mkdir(parents=True, exist_ok=True)
        file_path = raw_dir / f"{partition_hash}.jsonl.gz"

        line = raw_item.model_dump_json() + "\n"

        offset_key = f"{raw_item.source}:{run_id}:{partition_hash}"
        if offset_key in self._offsets:
            offset = self._offsets[offset_key]
            self._offsets[offset_key] += 1
        else:
            offset = 0
            if file_path.exists():
                with gzip.open(file_path, "rt", encoding="utf-8") as f:
                    offset = sum(1 for _ in f)
            self._offsets[offset_key] = offset + 1

        with gzip.open(file_path, "at", encoding="utf-8") as f:
            f.write(line)

        return f"{file_path}:{offset}"

    def read_raw(
        self,
        source: str,
        run_id: str,
        partition_hash: str,
    ) -> list[RawItem]:
        """Read all raw items from a partition file.

        Args:
            source: Source adapter name.
            run_id: Run ID.
            partition_hash: Partition hash.

        Returns:
            List of RawItem instances.
        """
        file_path = self.data_dir / "raw" / source / run_id / f"{partition_hash}.jsonl.gz"
        if not file_path.exists():
            return []

        items = []
        with gzip.open(file_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(RawItem.model_validate_json(line))
        return items

    def list_partitions(self, source: str, run_id: str) -> list[str]:
        """List all partition hashes for a source and run."""
        raw_dir = self.data_dir / "raw" / source / run_id
        if not raw_dir.exists():
            return []
        return [p.stem.replace(".jsonl", "") for p in raw_dir.glob("*.jsonl.gz")]


class ItemStorage:
    """Normalized item storage using JSONL.

    Files are stored at: data/items/{run_id}/items.jsonl
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def save_items(self, items: list[Item], run_id: str) -> Path:
        """Append normalized items to the run's item file.

        Args:
            items: List of normalized Item instances.
            run_id: Current run ID.

        Returns:
            Path to the items file.
        """
        items_dir = self.data_dir / "items" / run_id
        items_dir.mkdir(parents=True, exist_ok=True)
        file_path = items_dir / "items.jsonl"

        with open(file_path, "a", encoding="utf-8") as f:
            for item in items:
                f.write(item.model_dump_json() + "\n")

        return file_path

    def read_items(self, run_id: str, prefer_deduped: bool = False) -> list[Item]:
        """Read all normalized items for a run.

        Args:
            run_id: Run ID.
            prefer_deduped: If True and items_deduped.jsonl exists, read from it.

        Returns:
            List of Item instances.
        """
        items_dir = self.data_dir / "items" / run_id
        if prefer_deduped and (items_dir / "items_deduped.jsonl").exists():
            file_path = items_dir / "items_deduped.jsonl"
        else:
            file_path = items_dir / "items.jsonl"

        if not file_path.exists():
            return []

        items = []
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(Item.model_validate_json(line))
        return items

    def list_runs(self) -> list[str]:
        """List all run IDs that contain saved items."""
        items_dir = self.data_dir / "items"
        if not items_dir.exists():
            return []
        runs = [p.name for p in items_dir.iterdir() if p.is_dir() and (p / "items.jsonl").exists()]
        return sorted(runs)

    def items_hash(self, run_id: str) -> str:
        """Compute SHA-256 hash of the items file for determinism checks.

        Args:
            run_id: Run ID.

        Returns:
            SHA-256 hex digest, or empty string if file doesn't exist.
        """
        file_path = self.data_dir / "items" / run_id / "items.jsonl"
        if not file_path.exists():
            return ""
        content = file_path.read_bytes()
        return hashlib.sha256(content).hexdigest()


class DuckDBStorage:
    """DuckDB query layer over normalized items.

    Provides fast SQL access for stats, export, and analysis.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._con: object | None = None

    def _connect(self) -> object:
        """Lazy connection to DuckDB."""
        if self._con is None:
            import duckdb

            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._con = duckdb.connect(str(self.db_path))
            self._ensure_schema()
        return self._con  # type: ignore[return-value]

    def _ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        con = self._con
        assert con is not None
        con.execute("""  -- type: ignore[union-attr]
            CREATE TABLE IF NOT EXISTS items (
                id VARCHAR PRIMARY KEY,
                source VARCHAR NOT NULL,
                kind VARCHAR NOT NULL,
                url VARCHAR NOT NULL,
                title VARCHAR,
                body VARCHAR,
                body_hash VARCHAR,
                author_handle VARCHAR,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                run_id VARCHAR NOT NULL,
                adapter VARCHAR NOT NULL,
                query_string VARCHAR,
                fetched_at TIMESTAMP,
                data JSON
            )
        """)

    def ingest_items(self, items: list[Item], include_body: bool = False) -> int:
        """Insert items into DuckDB, skipping duplicates.

        Args:
            items: List of normalized Item instances.
            include_body: Whether to include body text in DuckDB.

        Returns:
            Number of items inserted.
        """
        con = self._connect()
        count_before = con.execute("SELECT count(*) FROM items").fetchone()[0]  # type: ignore[union-attr]
        for item in items:
            try:
                data = item.model_dump(mode="json")
                if not include_body:
                    data.pop("body", None)
                data_json = json.dumps(data, ensure_ascii=False)
                body_val = item.body if include_body else None

                con.execute(  # type: ignore[union-attr]
                    """INSERT OR IGNORE INTO items
                    (id, source, kind, url, title, body, body_hash,
                     author_handle, created_at, updated_at,
                     run_id, adapter, query_string, fetched_at, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        item.id,
                        item.source,
                        item.kind.value,
                        str(item.url),
                        item.title,
                        body_val,
                        item.body_hash,
                        item.author_handle,
                        item.created_at.isoformat() if item.created_at else None,
                        item.updated_at.isoformat() if item.updated_at else None,
                        item.provenance.run_id,
                        item.provenance.adapter,
                        item.provenance.query_string,
                        item.provenance.fetched_at.isoformat(),
                        data_json,
                    ],
                )
            except Exception as e:
                logger.warning("Failed to insert item %s: %s", item.id, e)
        count_after = con.execute("SELECT count(*) FROM items").fetchone()[0]  # type: ignore[union-attr]
        return int(count_after - count_before)

    def stats(self, run_id: str | None = None) -> dict[str, int]:
        """Get item counts by source.

        Args:
            run_id: Optional filter by run ID.

        Returns:
            Dict mapping source name to item count.
        """
        con = self._connect()
        where = ""
        params: list[str] = []
        if run_id:
            where = "WHERE run_id = ?"
            params = [run_id]
        result = con.execute(  # type: ignore[union-attr]
            f"SELECT source, COUNT(*) as cnt FROM items {where} GROUP BY source",
            params,
        ).fetchall()
        return {row[0]: row[1] for row in result}

    def close(self) -> None:
        """Close the DuckDB connection."""
        if self._con is not None:
            self._con.close()  # type: ignore[union-attr]
            self._con = None
