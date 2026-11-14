"""Configuration loader: YAML protocol file → Pydantic models.

Loads, validates, and provides typed access to the research protocol
defined in YAML files.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, PrivateAttr, field_validator, model_validator

from msrkit.coding import CodingForm
from msrkit.gazetteer import Gazetteer, load_gazetteer
from msrkit.models import Query
from msrkit.screening import ScreeningCriteria

if TYPE_CHECKING:
    from collections.abc import Mapping

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

    @model_validator(mode="after")
    def _check_order(self) -> WindowConfig:
        if self.since > self.until:
            raise ValueError(f"window.since ({self.since}) is after window.until ({self.until})")
        return self


class RawQueryConfig(BaseModel):
    """A literal query in the source's own search syntax (Protocol E2 v2, §7.3)."""

    q: str
    kind: str | None = None  # e.g. repo | code | issue for GitHub
    label: str = ""
    evidence: Literal["N1", "N2", "N3"] | None = None  # evidence level the query targets
    # Families of gazetteer anchors that `{anchor}` / `{repo}` expand over (None: all).
    anchor_families: list[str] | None = None


class SourceConfig(BaseModel):
    """Configuration for a single source in the protocol."""

    enabled: bool = False
    kinds: list[str] = []
    extra: dict[str, Any] = {}
    # Literal per-source queries, run in addition to (or instead of) the lexicon.
    queries: list[RawQueryConfig] = []
    use_terms: bool = True  # False: run only `queries`
    # Collected whole inside the window: the run's per-query cap does not apply.
    census: bool = False


class SamplingConfig(BaseModel):
    """How queries sample their sources (ADR-055)."""

    # "year": each query of a source that filters by date runs once per calendar
    # year of the window, every stratum with the run's per-query cap.
    strata: Literal["none", "year"] = "none"


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
    terms: list[str]  # base lexicon, in English
    # Additional lexicons per language, e.g. {"pt": ["teste de RAG", ...]}.
    terms_by_language: dict[str, list[str]] = {}
    languages: list[str] = ["en"]
    sources: dict[str, SourceConfig]
    sampling: SamplingConfig = SamplingConfig()
    limits: LimitsConfig = LimitsConfig()
    # Concept groups with their own lexicons, e.g. {"rag": [...], "agente": [...],
    # "teste": [...]}; items are labeled with every concept whose lexicon matches.
    concepts: dict[str, list[str]] = {}
    # Optional query terms built as the product of concept lexicons, e.g.
    # [["rag", "teste"]] adds "<rag term> <teste term>" for every pair.
    concept_queries: list[list[str]] = []
    # Path to the gazetteer YAML, relative to the protocol file (§7.2).
    gazetteer: str | None = None
    # Eligibility criteria for the screening sheets (§8, §11).
    screening: ScreeningCriteria = ScreeningCriteria()
    # Extraction form (Annex A) and gray-literature quality (§9).
    coding: CodingForm = CodingForm()

    _gazetteer: Gazetteer | None = PrivateAttr(default=None)
    # Set by apply_env_overrides: variables applied, source -> access group, and the
    # values the protocol file had before the overrides.
    _env_overrides: list[str] = PrivateAttr(default_factory=list)
    _source_groups: dict[str, str] = PrivateAttr(default_factory=dict)
    _file_values: dict[str, Any] = PrivateAttr(default_factory=dict)

    @property
    def gazetteer_data(self) -> Gazetteer | None:
        """The loaded gazetteer, if the protocol names one."""
        return self._gazetteer

    @property
    def env_overrides(self) -> list[str]:
        """Names of the MSRKIT_* variables that changed this protocol (see .env)."""
        return list(self._env_overrides)

    @property
    def source_groups(self) -> dict[str, str]:
        """Source -> access group (public, manual, paid, unavailable) declared in .env."""
        return dict(self._source_groups)

    def effective(self) -> dict[str, Any]:
        """Search parameters actually in force, for the run manifest."""
        return {
            "window": self.window.model_dump(),
            "terms": list(self.terms),
            "terms_by_language": {k: list(v) for k, v in self.terms_by_language.items()},
            "enabled_sources": self.enabled_sources(),
            "source_groups": self.source_groups,
            "census_sources": sorted(n for n, s in self.sources.items() if s.census),
            "strata": self.sampling.strata,
        }

    def differs_from_file(self) -> list[str]:
        """Parameters whose value in force differs from the protocol file."""
        if not self._file_values:
            return []
        now = self.effective()
        return [k for k, v in self._file_values.items() if k != "source_groups" and now.get(k) != v]

    @model_validator(mode="after")
    def _check_term_languages(self) -> ProtocolConfig:
        """Every extra lexicon must belong to a language declared in `languages`."""
        undeclared = sorted(set(self.terms_by_language) - set(self.languages))
        if undeclared:
            raise ValueError(
                f"terms_by_language has languages not listed in `languages`: {undeclared}"
            )
        return self

    @model_validator(mode="after")
    def _check_concept_queries(self) -> ProtocolConfig:
        for combo in self.concept_queries:
            unknown = sorted(set(combo) - set(self.concepts))
            if unknown:
                raise ValueError(f"concept_queries uses undefined concepts: {unknown}")
        return self

    def all_terms(self) -> list[str]:
        """Base terms, language lexicons and concept combinations, deduplicated, in order."""
        seen: dict[str, None] = dict.fromkeys(self.terms)
        for lexicon in self.terms_by_language.values():
            seen.update(dict.fromkeys(lexicon))
        for combo in self.concept_queries:
            for parts in itertools.product(*(self.concepts[c] for c in combo)):
                seen[" ".join(parts)] = None
        return list(seen)

    def term_languages(self) -> dict[str, str]:
        """Map each term to the language of the lexicon it came from."""
        mapping = dict.fromkeys(self.terms, "en")
        for lang, lexicon in self.terms_by_language.items():
            for term in lexicon:
                mapping.setdefault(term, lang)
        return mapping

    def _expand_template(self, raw_q: RawQueryConfig) -> list[tuple[str, str]]:
        """Expand `{anchor}` / `{repo}` over gazetteer anchors; plain queries pass through."""
        if "{anchor}" not in raw_q.q and "{repo}" not in raw_q.q:
            return [(raw_q.q, raw_q.label)]
        if self._gazetteer is None:
            raise ValueError(f"query '{raw_q.q}' uses a template but no gazetteer is loaded")
        out: list[tuple[str, str]] = []
        for tool in self._gazetteer.anchors(raw_q.anchor_families):
            repos = (
                [r.strip() for r in tool.repos if r and r.strip()] if "{repo}" in raw_q.q else [""]
            )
            for repo in repos:
                text = raw_q.q.replace("{anchor}", tool.token()).replace("{repo}", repo)
                out.append((text, f"{raw_q.label}:{tool.id}" if raw_q.label else tool.id))
        return out

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

        # Priority 1: Literal queries (CI workflows, tool imports, configs, issues).
        # These carry the strongest structural evidence (N2/N3) and must execute first
        # so they are never starved if generic text queries exhaust the source quota.
        for raw_q in src_cfg.queries:
            for text, label in self._expand_template(raw_q):
                queries.append(
                    Query(
                        source=source_name,
                        terms=[text],
                        kind=raw_q.kind,
                        since=since,
                        until=until,
                        extra=src_cfg.extra,
                        limit=self.limits.max_items_per_source,
                        raw=True,
                        label=label,
                    )
                )

        # Priority 2: Generic open-text terms across kinds (repo, code, etc.)
        if src_cfg.use_terms:
            for kind in kinds:
                queries.append(
                    Query(
                        source=source_name,
                        terms=self.all_terms(),
                        kind=kind,
                        since=since,
                        until=until,
                        extra=src_cfg.extra,
                        limit=self.limits.max_items_per_source,
                    )
                )

        return queries


# ---------------------------------------------------------------------------
# Environment overrides (.env)
# ---------------------------------------------------------------------------

ENV_SINCE = "MSRKIT_WINDOW_SINCE"
ENV_UNTIL = "MSRKIT_WINDOW_UNTIL"
ENV_TERMS = "MSRKIT_TERMS"
ENV_TERMS_LANG_PREFIX = "MSRKIT_TERMS_"  # + language code, e.g. MSRKIT_TERMS_PT
# Access groups of the sources. Public and manual sources are collected; paid and
# unavailable ones never are, whatever the protocol file says.
SOURCE_GROUP_VARS = {
    "MSRKIT_SOURCES_PUBLIC": "public",
    "MSRKIT_SOURCES_MANUAL": "manual",
    "MSRKIT_SOURCES_PAID": "paid",
    "MSRKIT_SOURCES_UNAVAILABLE": "unavailable",
}
COLLECTED_GROUPS = {"public", "manual"}


def _split(value: str, separators: str) -> list[str]:
    parts = re.split(f"[{re.escape(separators)}]", value)
    return list(dict.fromkeys(p.strip() for p in parts if p.strip()))


def apply_env_overrides(config: ProtocolConfig, environ: Mapping[str, str]) -> list[str]:
    """Apply MSRKIT_* search parameters and source groups from the environment.

    - `MSRKIT_WINDOW_SINCE` / `MSRKIT_WINDOW_UNTIL`: temporal window (YYYY-MM-DD);
    - `MSRKIT_TERMS`: base lexicon, terms separated by `;`;
    - `MSRKIT_TERMS_<LANG>` (e.g. `MSRKIT_TERMS_PT`): lexicon of a declared language;
    - `MSRKIT_SOURCES_PUBLIC|MANUAL|PAID|UNAVAILABLE`: source names by access group,
      separated by `,`. When any group is set, exactly the public and manual sources
      are enabled; a source must be declared in the protocol and in one group only.

    Empty variables are ignored. Returns the names of the variables applied.
    """
    config._file_values = config.effective()
    applied: list[str] = []

    since = environ.get(ENV_SINCE, "").strip()
    until = environ.get(ENV_UNTIL, "").strip()
    if since or until:
        try:
            config.window = WindowConfig(
                since=since or config.window.since, until=until or config.window.until
            )
        except ValueError as e:
            raise ValueError(f"{ENV_SINCE}/{ENV_UNTIL} in .env: {e}") from None
        applied += [name for name, val in ((ENV_SINCE, since), (ENV_UNTIL, until)) if val]

    terms = _split(environ.get(ENV_TERMS, ""), ";")
    if terms:
        config.terms = terms
        applied.append(ENV_TERMS)
    for key in sorted(environ):
        if not key.startswith(ENV_TERMS_LANG_PREFIX) or key == ENV_TERMS:
            continue
        lang = key[len(ENV_TERMS_LANG_PREFIX) :].lower()
        lexicon = _split(environ[key], ";")
        if not lexicon:
            continue
        if lang not in config.languages:
            raise ValueError(f"{key} in .env: language '{lang}' is not in the protocol's languages")
        config.terms_by_language[lang] = lexicon
        applied.append(key)

    groups: dict[str, str] = {}
    problems: list[str] = []
    for var, group in SOURCE_GROUP_VARS.items():
        names = _split(environ.get(var, ""), ",; \t")
        if names:
            applied.append(var)
        for name in names:
            if name not in config.sources:
                problems.append(f"{var}: '{name}' is not a source declared in the protocol")
            elif groups.get(name, group) != group:
                problems.append(f"'{name}' is in more than one group ({groups[name]}, {group})")
            groups[name] = group
    if problems:
        raise ValueError("source groups in .env: " + "; ".join(problems))
    if groups:
        for name, cfg in config.sources.items():
            cfg.enabled = groups.get(name) in COLLECTED_GROUPS
        config._source_groups = groups

    config._env_overrides = applied
    return applied


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_protocol(path: str | Path, environ: Mapping[str, str] | None = None) -> ProtocolConfig:
    """Load and validate a protocol YAML file, then apply the .env overrides.

    Args:
        path: Path to the YAML protocol file.
        environ: Environment to read MSRKIT_* overrides from (default: os.environ).

    Returns:
        Validated ProtocolConfig instance.

    Raises:
        FileNotFoundError: If the protocol file does not exist.
        ValueError: If the YAML content is invalid.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Protocol file not found: {p}")

    raw = p.read_text(encoding="utf-8-sig")
    data = yaml.safe_load(raw)

    if not isinstance(data, dict):
        raise ValueError(f"Protocol file must contain a YAML mapping, got {type(data).__name__}")

    config = ProtocolConfig.model_validate(data)
    if config.gazetteer:
        config._gazetteer = load_gazetteer(p.parent / config.gazetteer)
    apply_env_overrides(config, os.environ if environ is None else environ)
    return config


def protocol_sha256(path: str | Path) -> str:
    """Compute SHA-256 hash of a protocol file for provenance.

    Line endings are normalized to LF first: a Windows checkout (CRLF) and the CI
    runner (LF) must give the same hash for the same protocol.
    """
    content = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()
