"""Gazetteer: versioned catalog of known testing tools and methods.

Loaded from the YAML file named by `gazetteer:` in the protocol (Protocol E2 v2,
§7.2 and §12.2). It anchors three things:

- query templates: `{anchor}` and `{repo}` in protocol `queries:` expand to one
  query per anchor tool (see ProtocolConfig.build_queries);
- extraction: aliases (N1 text mentions) and structural signals — Python imports,
  declared dependencies, config file names, CLI invocations (N2/N3);
- disambiguation: tools whose names are common words are marked `ambiguous` and
  only count with lexicon context or a structural signal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

SystemLabel = Literal["rag", "agente"]
Origin = Literal["seed", "discovery"]  # seed catalog, or added from the discovery pass (§7.6)
OracleLevel = Literal["especificado", "derivado", "referencia", "pseudo-automatico", "humano"]


class ToolEntry(BaseModel):
    """A testing tool (or system-under-test framework) with its detection signals."""

    id: str
    name: str
    family: str
    applies_to: list[SystemLabel] = ["rag", "agente"]
    aliases: list[str] = []
    python_imports: list[str] = []  # top-level modules: `import X` / `from X import`
    packages: list[str] = []  # dependency names in pyproject/requirements/package.json
    config_files: list[str] = []  # file names whose presence signals use
    cli: list[str] = []  # commands whose invocation (e.g. in CI) signals use
    repos: list[str] = []  # owner/repo of the tool itself
    ambiguous: bool = False  # name collides with common words: needs context
    anchor: bool = True  # expands `{anchor}` / `{repo}` in query templates
    search_token: str | None = None  # token used for `{anchor}`; defaults to id
    origin: Origin = "seed"

    def names(self) -> list[str]:
        """Every textual name of the tool (canonical name first)."""
        seen = dict.fromkeys([self.name, *self.aliases])
        return list(seen)

    def token(self) -> str:
        return self.search_token or self.id


class MethodEntry(BaseModel):
    """A testing method or technique, detected in text by its aliases."""

    id: str
    name: str
    aliases: list[str] = []
    oracle: OracleLevel | None = None  # typical rung on the oracle ladder (§5.3)
    applies_to: list[SystemLabel] = ["rag", "agente"]
    origin: Origin = "seed"

    def names(self) -> list[str]:
        return list(dict.fromkeys([self.name, *self.aliases]))


class Gazetteer(BaseModel):
    """The whole gazetteer file."""

    version: str
    tools: list[ToolEntry] = []
    methods: list[MethodEntry] = []

    @model_validator(mode="after")
    def _check_unique(self) -> Gazetteer:
        entries: list[ToolEntry | MethodEntry] = [*self.tools, *self.methods]
        ids = [e.id for e in entries]
        dup_ids = sorted({i for i in ids if ids.count(i) > 1})
        if dup_ids:
            raise ValueError(f"duplicate gazetteer ids: {dup_ids}")
        owner: dict[str, str] = {}
        clashes: list[str] = []
        for entry in entries:
            for alias in entry.names():
                key = alias.lower()
                if key in owner and owner[key] != entry.id:
                    clashes.append(f"'{alias}' ({owner[key]}, {entry.id})")
                owner.setdefault(key, entry.id)
        if clashes:
            raise ValueError(f"alias shared by different entries: {', '.join(clashes)}")
        return self

    def anchors(self, families: list[str] | None = None) -> list[ToolEntry]:
        """Tools that expand query templates, optionally restricted to families."""
        return [t for t in self.tools if t.anchor and (families is None or t.family in families)]

    def by_id(self, entry_id: str) -> ToolEntry | MethodEntry | None:
        entries: list[ToolEntry | MethodEntry] = [*self.tools, *self.methods]
        for entry in entries:
            if entry.id == entry_id:
                return entry
        return None


def load_gazetteer(path: str | Path) -> Gazetteer:
    """Load and validate a gazetteer YAML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Gazetteer file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Gazetteer file must contain a YAML mapping")
    return Gazetteer.model_validate(data)
