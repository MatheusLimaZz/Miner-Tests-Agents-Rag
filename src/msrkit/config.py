"""Configuration loader: YAML protocol file → Pydantic models.

Loads, validates, and provides typed access to the research protocol
defined in YAML files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator

from msrkit.models import Query

# ---------------------------------------------------------------------------
# Protocol config models
# ---------------------------------------------------------------------------


class WindowConfig(BaseModel):
    """Temporal window for the collection."""

    since: str  # YYYY-MM-DD
    until: str  # YYYY-MM-DD

    @field_validator("since", "until")
    @classmethod
    def _validate_date_format(cls, v: str) -> str:
        """Ensure date strings are in YYYY-MM-DD format."""
        from datetime import date as date_type

        try:
            date_type.fromisoformat(v)
        except ValueError as e:
            raise ValueError(f"Invalid date format '{v}', expected YYYY-MM-DD") from e
        return v


class SourceConfig(BaseModel):
    """Configuration for a single source in the protocol."""

    enabled: bool = False
    kinds: list[str] = []
    extra: dict[str, Any] = {}


class LimitsConfig(BaseModel):
    """Global limits for the collection run."""

    max_items_per_source: int = 5000
    max_requests_per_source: int = 2000
    stop_on_quota_exhausted: bool = True


class ProtocolConfig(BaseModel):
    """Full research protocol configuration."""

    version: int
    name: str
    description: str
    window: WindowConfig
    terms: list[str]
    languages: list[str] = ["en"]
    sources: dict[str, SourceConfig]
    limits: LimitsConfig = LimitsConfig()

    def enabled_sources(self) -> list[str]:
        """Return names of all enabled sources."""
        return [name for name, cfg in self.sources.items() if cfg.enabled]

    def build_queries(self, source_name: str) -> list[Query]:
        """Build Query objects for a given source from protocol config."""
        from datetime import date

        src_cfg = self.sources.get(source_name)
        if src_cfg is None or not src_cfg.enabled:
            return []

        since = date.fromisoformat(self.window.since)
        until = date.fromisoformat(self.window.until)

        queries: list[Query] = []
        # Se a fonte tem kinds configurados, cria uma query por kind
        kinds = src_cfg.kinds if src_cfg.kinds else [None]

        for kind in kinds:
            queries.append(
                Query(
                    source=source_name,
                    terms=self.terms,
                    kind=kind,
                    since=since,
                    until=until,
                    extra=src_cfg.extra,
                    limit=self.limits.max_items_per_source,
                )
            )

        return queries


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_protocol(path: str | Path) -> ProtocolConfig:
    """Load and validate a protocol YAML file.

    Args:
        path: Path to the YAML protocol file.

    Returns:
        Validated ProtocolConfig instance.

    Raises:
        FileNotFoundError: If the protocol file does not exist.
        ValueError: If the YAML content is invalid.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Protocol file not found: {p}")

    raw = p.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)

    if not isinstance(data, dict):
        raise ValueError(f"Protocol file must contain a YAML mapping, got {type(data).__name__}")

    return ProtocolConfig.model_validate(data)


def protocol_sha256(path: str | Path) -> str:
    """Compute SHA-256 hash of a protocol file for provenance."""
    content = Path(path).read_bytes()
    return hashlib.sha256(content).hexdigest()
