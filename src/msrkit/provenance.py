"""Provenance: run IDs, hashes, and manifest management.

Generates unique run identifiers, computes response hashes, and
manages the run manifest for full reproducibility.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from msrkit.models import Manifest


def generate_run_id() -> str:
    """Generate a unique run ID: ISO timestamp + short UUID.

    Format: 2026-08-26T14-03-11Z-a3f9
    """
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    short = uuid.uuid4().hex[:4]
    return f"{ts}-{short}"


def hash_response(content: bytes) -> str:
    """Compute SHA-256 hash of raw API response content."""
    return hashlib.sha256(content).hexdigest()


def hash_string(text: str) -> str:
    """Compute SHA-256 hash of a string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_manifest(manifest: Manifest, data_dir: Path) -> Path:
    """Save run manifest to disk.

    Args:
        manifest: The manifest to save.
        data_dir: Base data directory.

    Returns:
        Path to the saved manifest file.
    """
    run_dir = data_dir / "runs" / manifest.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return manifest_path


def load_manifest(data_dir: Path, run_id: str) -> Manifest:
    """Load a run manifest from disk.

    Args:
        data_dir: Base data directory.
        run_id: The run ID to load.

    Returns:
        Loaded Manifest instance.

    Raises:
        FileNotFoundError: If the manifest does not exist.
    """
    manifest_path = data_dir / "runs" / run_id / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    raw = manifest_path.read_text(encoding="utf-8")
    return Manifest.model_validate(json.loads(raw))
