"""Analyses A1–A6 of Protocol E2 v2 (§14) over units of analysis.

A *unit* is what is counted once: a GitHub repository (all its items together)
or a single item elsewhere. Units come from one of two bases:

- `detections` (automatic, `msrkit extract`): tools with their best evidence
  level and methods; system label from the item concepts;
- `coding` (manual, `msrkit coding`): the extraction form. With several coders
  the consensus is used — an `enum` value only when all coders agree, the
  intersection for `multi` fields — so disputed values are left out until a
  third coder resolves them.

Reported numbers always say which basis they come from.
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter
from itertools import combinations
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from msrkit.enrich import repo_of
from msrkit.extract import LEVEL_RANK
from msrkit.validation import wilson

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from msrkit.coding import CodingRecord
    from msrkit.extract import Detection
    from msrkit.gazetteer import Gazetteer
    from msrkit.models import Item

Basis = Literal["detections", "coding"]
SYSTEMS = ("rag", "agente")

# Form fields (Annex A) read by the coding basis.
FIELD_SYSTEM = "sistema"
FIELD_LEVEL = "nivel_evidencia"
FIELD_TOOLS = "ferramentas"
FIELD_METHODS = "metodos"
FIELD_FAILURES = "modos_falha"
FIELD_ORACLE = "tipo_oraculo"
FIELD_AGGREGATION = "agregacao"


class Unit(BaseModel):
    """One unit of analysis."""

    id: str  # owner/repo, or item id
    source: str
    item_ids: list[str] = []
    systems: list[str] = []  # subset of SYSTEMS
    tools: dict[str, str] = {}  # tool id -> best evidence level
    tool_channels: dict[str, list[str]] = {}  # tool id -> distinct channels: "open", "anchored"
    methods: list[str] = []
    failure_modes: list[str] = []
    oracle: str | None = None
    aggregation: str | None = None


def is_anchored_query(
    query_string: str,
    tool_id: str,
    tool_entry: Any = None,
) -> bool:
    """Check if query_string was an anchor-directed search specifically targeting tool_id."""
    if not query_string:
        return False
    q_lower = query_string.lower()
    t_id_lower = tool_id.lower()

    if tool_entry is not None:
        for r in getattr(tool_entry, "repos", []):
            if f"repo:{r.lower()}" in q_lower:
                return True
        for cfg in getattr(tool_entry, "config_files", []):
            if cfg.lower() in q_lower:
                return True
        for imp in getattr(tool_entry, "python_imports", []):
            if f"import {imp.lower()}" in q_lower or f"from {imp.lower()} import" in q_lower:
                return True
        token_raw = getattr(tool_entry, "search_token", None) or getattr(tool_entry, "id", "")
        token = str(token_raw or "")
        token_lower = token.lower()
        if "path:.github/workflows" in q_lower and re.search(
            rf"(?<![\w-]){re.escape(token_lower)}(?![\w-])", q_lower
        ):
            return True

    if f"import {t_id_lower}" in q_lower or f"from {t_id_lower} import" in q_lower:
        return True
    return bool("repo:" in q_lower and t_id_lower in q_lower)


def _add_channel(channels_dict: dict[str, list[str]], tool: str, channel: str) -> None:
    lst = channels_dict.setdefault(tool, [])
    if channel not in lst:
        lst.append(channel)


def _unit_key(item: Item) -> str:
    return repo_of(item) or item.id


def units_from_detections(
    items: list[Item],
    detections: list[Detection],
    gazetteer: Gazetteer | None = None,
) -> list[Unit]:
    units: dict[str, Unit] = {}
    item_unit: dict[str, str] = {}
    item_map = {it.id: it for it in items}
    tools_by_id = {t.id: t for t in gazetteer.tools} if gazetteer else {}

    for it in items:
        key = _unit_key(it)
        unit = units.setdefault(key, Unit(id=key, source=it.source))
        unit.item_ids.append(it.id)
        unit.systems = sorted(set(unit.systems) | {c for c in it.concepts if c in SYSTEMS})
        item_unit[it.id] = key
    for d in detections:
        unit_id = item_unit.get(d.item_id) or d.repo
        if unit_id is None or unit_id not in units:
            continue
        unit = units[unit_id]
        if d.entry_type == "tool":
            current = unit.tools.get(d.entry_id)
            if current is None or LEVEL_RANK[d.level] > LEVEL_RANK[current]:
                unit.tools[d.entry_id] = d.level

            item = item_map.get(d.item_id)
            q_str = item.provenance.query_string if item else ""
            tool_entry = tools_by_id.get(d.entry_id)
            channel = "anchored" if is_anchored_query(q_str, d.entry_id, tool_entry) else "open"
            _add_channel(unit.tool_channels, d.entry_id, channel)
        elif d.entry_id not in unit.methods:
            unit.methods.append(d.entry_id)
    for unit in units.values():
        unit.methods.sort()
        for t in unit.tools:
            if t not in unit.tool_channels:
                unit.tool_channels[t] = ["open"]
    return sorted(units.values(), key=lambda u: u.id)


def consensus(records: list[CodingRecord]) -> dict[str, Any]:
    """Values all coders agree on: unanimous enums/bools, intersection of lists.

    If a third coder / supervisor (CONSENSUS_CODERS) resolved the item, their
    record takes precedence over disputed initial codings.
    """
    if not records:
        return {}
    from msrkit.screening import is_consensus_coder

    for r in records:
        if is_consensus_coder(r.coder):
            return dict(r.values)

    out: dict[str, Any] = {}
    all_names = list(dict.fromkeys(k for r in records for k in r.values))
    for name in all_names:
        values = [r.values.get(name) for r in records]
        lists = [v for v in values if isinstance(v, list)]
        if len(lists) == len(values):
            common = set(lists[0]).intersection(*lists[1:])
            out[name] = [v for v in lists[0] if v in common]
        elif all(v == values[0] for v in values):
            out[name] = values[0]
        else:
            out[name] = None
    return out


def units_from_codings(
    items: list[Item],
    codings: dict[tuple[str, str], CodingRecord],
    gazetteer: Gazetteer | None = None,
) -> list[Unit]:
    """One unit per coded item, merged per repository (union of coded values)."""
    by_item: dict[str, list[CodingRecord]] = {}
    for (item_id, _coder), rec in sorted(codings.items()):
        by_item.setdefault(item_id, []).append(rec)
    item_map = {it.id: it for it in items}
    tools_by_id = {t.id: t for t in gazetteer.tools} if gazetteer else {}
    units: dict[str, Unit] = {}

    def _as_list(v: Any) -> list[Any]:
        if isinstance(v, str):
            return [v]
        return list(v) if isinstance(v, (list, set, tuple)) else []

    for item_id, recs in by_item.items():
        item = item_map.get(item_id)
        if item is None:
            continue
        values = consensus(recs)
        key = _unit_key(item)
        unit = units.setdefault(key, Unit(id=key, source=item.source))
        unit.item_ids.append(item_id)
        unit.systems = sorted(set(unit.systems) | set(_as_list(values.get(FIELD_SYSTEM))))
        level = values.get(FIELD_LEVEL)
        q_str = item.provenance.query_string
        for tool in _as_list(values.get(FIELD_TOOLS)):
            current = unit.tools.get(tool)
            if level and (current is None or LEVEL_RANK[level] > LEVEL_RANK[current]):
                unit.tools[tool] = level
            elif current is None:
                unit.tools[tool] = "N1"
            tool_entry = tools_by_id.get(tool)
            channel = "anchored" if is_anchored_query(q_str, tool, tool_entry) else "open"
            _add_channel(unit.tool_channels, tool, channel)
        unit.methods = sorted(set(unit.methods) | set(_as_list(values.get(FIELD_METHODS))))
        unit.failure_modes = sorted(
            set(unit.failure_modes) | set(_as_list(values.get(FIELD_FAILURES)))
        )
        unit.oracle = unit.oracle or values.get(FIELD_ORACLE)
        unit.aggregation = unit.aggregation or values.get(FIELD_AGGREGATION)
    for unit in units.values():
        for t in unit.tools:
            if t not in unit.tool_channels:
                unit.tool_channels[t] = ["open"]
    return sorted(units.values(), key=lambda u: u.id)


# -- A1/A2: frequency and co-occurrence ---------------------------------------


class FrequencyRow(BaseModel):
    entry: str
    kind: Literal["tool", "method"]
    units: int
    family: str = ""  # gazetteer family; `framework-sut` = system under test, not a test tool
    n1: int = 0  # tools: units at each best level (disjoint)
    n2: int = 0
    n3: int = 0
    rag: int = 0  # units labelled rag (a hybrid unit counts in both)
    agente: int = 0
    open_units: int = 0  # units discovered via open/neutral channels
    anchored_units: int = 0  # units retrieved via anchored/targeted code searches
    sources: dict[str, int] = {}


def frequency(
    units: list[Unit],
    families: dict[str, str] | None = None,
    channel: Literal["all", "open", "anchored"] = "all",
) -> list[FrequencyRow]:
    families = families or {}
    rows: dict[tuple[str, str], FrequencyRow] = {}
    for u in units:
        entries: list[tuple[str, Literal["tool", "method"], str | None, list[str]]] = [
            (t, "tool", lv, u.tool_channels.get(t, ["open"])) for t, lv in u.tools.items()
        ]
        entries += [(m, "method", None, ["open"]) for m in u.methods]
        for entry, kind, level, channels in entries:
            is_open = "open" in channels
            is_anchored = "anchored" in channels

            if channel == "open" and not is_open:
                continue
            if channel == "anchored" and not is_anchored:
                continue

            row = rows.setdefault(
                (kind, entry),
                FrequencyRow(entry=entry, kind=kind, units=0, family=families.get(entry, "")),
            )
            row.units += 1
            if level:
                setattr(row, level.lower(), getattr(row, level.lower()) + 1)
            row.rag += "rag" in u.systems
            row.agente += "agente" in u.systems
            if is_open:
                row.open_units += 1
            if is_anchored:
                row.anchored_units += 1
            row.sources[u.source] = row.sources.get(u.source, 0) + 1
    return sorted(rows.values(), key=lambda r: (-r.units, r.kind != "tool", r.entry))


class PairRow(BaseModel):
    a: str
    b: str
    together: int  # units with both
    a_units: int
    b_units: int
    jaccard: float
    lift: float  # P(a,b) / (P(a) P(b)); > 1 means they co-occur more than by chance


def cooccurrence(
    units: list[Unit], pairs: str = "tool-method", min_together: int = 1
) -> list[PairRow]:
    """Co-occurrence within units: `tool-method`, `tool-tool` or `method-method`."""
    n = len(units)
    count: Counter[str] = Counter()
    together: Counter[tuple[str, str]] = Counter()
    for u in units:
        tools, methods = sorted(u.tools), list(u.methods)
        for e in {*tools, *methods}:
            count[e] += 1
        if pairs == "tool-method":
            combos = [(t, m) for t in tools for m in methods]
        elif pairs == "tool-tool":
            combos = list(combinations(tools, 2))
        elif pairs == "method-method":
            combos = list(combinations(sorted(methods), 2))
        else:
            raise ValueError("pairs must be tool-method, tool-tool or method-method")
        together.update(combos)
    rows = []
    for (a, b), both in together.items():
        if both < min_together:
            continue
        union = count[a] + count[b] - both
        rows.append(
            PairRow(
                a=a,
                b=b,
                together=both,
                a_units=count[a],
                b_units=count[b],
                jaccard=round(both / union, 4) if union else 0.0,
                lift=round(both * n / (count[a] * count[b]), 4) if n else 0.0,
            )
        )
    return sorted(rows, key=lambda r: (-r.together, -r.lift, r.a, r.b))


# -- statistics ---------------------------------------------------------------


def binomial_greater(successes: int, n: int, p0: float = 0.5) -> float:
    """One-sided exact binomial p-value for H1: p > p0."""
    return sum(math.comb(n, k) * p0**k * (1 - p0) ** (n - k) for k in range(successes, n + 1))


# -- output -------------------------------------------------------------------


def write_csv(rows: Sequence[BaseModel], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [r.model_dump() for r in rows]
    fields = list(data[0]) if data else []
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        if fields:
            writer.writeheader()
        for row in data:
            formatted_row: dict[str, Any] = {}
            for k, v in row.items():
                if isinstance(v, dict):
                    formatted_row[k] = "; ".join(f"{a}={b}" for a, b in v.items())
                elif isinstance(v, (list, tuple)):
                    formatted_row[k] = ", ".join(str(x) for x in v)
                else:
                    formatted_row[k] = v
            writer.writerow(formatted_row)
    return path


# -- A3: failure-mode coverage ------------------------------------------------


class CoverageCell(BaseModel):
    failure_mode: str
    entry: str
    kind: Literal["tool", "method"]
    system: str  # rag, agente or "unlabelled"
    units: int


class FailureModeSummary(BaseModel):
    failure_mode: str
    label: str
    units: int
    rag: int
    agente: int
    tools: int  # distinct tools co-coded with the mode
    methods: int
    top_entries: str  # most frequent tools/methods, "id (n)"
    covered: bool


def coverage(
    units: list[Unit], catalog: dict[str, str] | None = None, top: int = 5
) -> tuple[list[CoverageCell], list[FailureModeSummary]]:
    """Failure mode × tool/method, per system; every catalog mode is summarized.

    A mode of the catalog without units is reported as not covered — absence is a
    result (§5.2). Modes coded but absent from the catalog are appended.
    """
    catalog = catalog or {}
    cells: Counter[tuple[str, str, str, str]] = Counter()
    per_mode: dict[str, list[Unit]] = {}
    for u in units:
        systems = u.systems or ["unlabelled"]
        for mode in u.failure_modes:
            per_mode.setdefault(mode, []).append(u)
            for system in systems:
                for tool in u.tools:
                    cells[(mode, tool, "tool", system)] += 1
                for method in u.methods:
                    cells[(mode, method, "method", system)] += 1
    cell_rows = [
        CoverageCell(failure_mode=m, entry=e, kind=k, system=s, units=n)  # type: ignore[arg-type]
        for (m, e, k, s), n in sorted(cells.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0]))
    ]
    order = list(catalog) + sorted(set(per_mode) - set(catalog))
    summaries = []
    for mode in order:
        us = per_mode.get(mode, [])
        entries: Counter[str] = Counter()
        tools: set[str] = set()
        methods: set[str] = set()
        for u in us:
            tools |= set(u.tools)
            methods |= set(u.methods)
            entries.update([*u.tools, *u.methods])
        summaries.append(
            FailureModeSummary(
                failure_mode=mode,
                label=catalog.get(mode, "(not in catalog)"),
                units=len(us),
                rag=sum("rag" in u.systems for u in us),
                agente=sum("agente" in u.systems for u in us),
                tools=len(tools),
                methods=len(methods),
                top_entries=", ".join(f"{e} ({n})" for e, n in entries.most_common(top)),
                covered=bool(us),
            )
        )
    return cell_rows, summaries


def coverage_matrix(
    cells: list[CoverageCell], modes: list[str], system: str | None = None
) -> tuple[list[str], list[list[int]]]:
    """Wide matrix (rows = modes, columns = entries by total) for one system or all."""
    chosen = [c for c in cells if system is None or c.system == system]
    totals: Counter[str] = Counter()
    grid: dict[tuple[str, str], int] = {}
    for c in chosen:
        totals[c.entry] += c.units
        grid[(c.failure_mode, c.entry)] = grid.get((c.failure_mode, c.entry), 0) + c.units
    columns = [e for e, _ in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))]
    return columns, [[grid.get((m, e), 0) for e in columns] for m in modes]


def write_matrix(columns: list[str], rows: list[list[int]], modes: list[str], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["failure_mode", *columns])
        for mode, row in zip(modes, rows, strict=True):
            writer.writerow([mode, *row])
    return path


# -- A4: oracle ladder and aggregation (H2) -----------------------------------

LADDER = ["especificado", "derivado", "referencia", "pseudo-automatico", "humano"]
ATOMIC, AGGREGATED = "atomico", "agregado"


class OracleRow(BaseModel):
    system: str  # rag, agente or all
    rung: str
    units: int
    share: float  # of units of that system with at least one rung


def unit_rungs(unit: Unit, method_rungs: dict[str, str] | None = None) -> set[str]:
    """Coded rung, or (detections basis) the typical rungs of the unit's methods."""
    if unit.oracle:
        return {unit.oracle}
    if method_rungs:
        return {method_rungs[m] for m in unit.methods if m in method_rungs}
    return set()


def oracle_distribution(
    units: list[Unit], method_rungs: dict[str, str] | None = None
) -> list[OracleRow]:
    rows: list[OracleRow] = []
    for system in ("all", *SYSTEMS):
        chosen = [u for u in units if system == "all" or system in u.systems]
        rung_sets = [unit_rungs(u, method_rungs) for u in chosen]
        with_rung = sum(bool(r) for r in rung_sets)
        counts = Counter(r for rs in rung_sets for r in rs)
        order = LADDER + sorted(set(counts) - set(LADDER))
        rows += [
            OracleRow(
                system=system,
                rung=rung,
                units=counts[rung],
                share=round(counts[rung] / with_rung, 4) if with_rung else 0.0,
            )
            for rung in order
        ]
    return rows


class AggregationTest(BaseModel):
    """H2: the large majority of oracles are atomic."""

    system: str
    atomic: int
    aggregated: int
    unidentified: int  # coded as not identified, or not coded
    proportion_atomic: float  # among identified
    ci95: tuple[float, float]
    threshold: float
    p_value: float  # one-sided exact binomial, H1: proportion > threshold
    supported: bool  # p_value < alpha


def aggregation_test(
    units: list[Unit], system: str = "all", threshold: float = 0.5, alpha: float = 0.05
) -> AggregationTest:
    chosen = [u for u in units if system == "all" or system in u.systems]
    atomic = sum(u.aggregation == ATOMIC for u in chosen)
    aggregated = sum(u.aggregation == AGGREGATED for u in chosen)
    n = atomic + aggregated
    p = binomial_greater(atomic, n, threshold) if n else 1.0
    lo, hi = wilson(atomic, n)
    return AggregationTest(
        system=system,
        atomic=atomic,
        aggregated=aggregated,
        unidentified=len(chosen) - n,
        proportion_atomic=round(atomic / n, 4) if n else 0.0,
        ci95=(round(lo, 4), round(hi, 4)),
        threshold=threshold,
        p_value=round(p, 6),
        supported=bool(n) and p < alpha,
    )


# -- A6: RAG × agents, and saturation ------------------------------------------


class ComparisonRow(BaseModel):
    entry: str
    kind: Literal["tool", "method", "failure_mode", "oracle"]
    rag_units: int
    agente_units: int
    hybrid_units: int  # units labelled with both
    category: Literal["transfers", "rag-only", "agent-only"]


class ComparisonSummary(BaseModel):
    kind: str
    rag_entries: int
    agente_entries: int
    shared: int
    jaccard: float
    agent_transfer_rate: float  # share of agent entries also found in RAG units


def _entries(u: Unit, method_rungs: dict[str, str] | None) -> list[tuple[str, str]]:
    out = [(t, "tool") for t in u.tools] + [(m, "method") for m in u.methods]
    out += [(f, "failure_mode") for f in u.failure_modes]
    out += [(r, "oracle") for r in sorted(unit_rungs(u, method_rungs))]
    return out


def compare_systems(
    units: list[Unit], method_rungs: dict[str, str] | None = None
) -> tuple[list[ComparisonRow], list[ComparisonSummary]]:
    """What appears in RAG units, in agent units, or in both (RQ5)."""
    rag: Counter[tuple[str, str]] = Counter()
    agent: Counter[tuple[str, str]] = Counter()
    hybrid: Counter[tuple[str, str]] = Counter()
    for u in units:
        for key in _entries(u, method_rungs):
            rag[key] += "rag" in u.systems
            agent[key] += "agente" in u.systems
            hybrid[key] += {"rag", "agente"} <= set(u.systems)
    rows = []
    for key in sorted(set(rag) | set(agent)):
        r, a = rag[key], agent[key]
        if not r and not a:
            continue
        category = "transfers" if r and a else ("rag-only" if r else "agent-only")
        rows.append(
            ComparisonRow(
                entry=key[0],
                kind=key[1],  # type: ignore[arg-type]
                rag_units=r,
                agente_units=a,
                hybrid_units=hybrid[key],
                category=category,  # type: ignore[arg-type]
            )
        )
    rows.sort(key=lambda x: (x.kind, x.category, -(x.rag_units + x.agente_units), x.entry))
    summaries = []
    for kind in ("tool", "method", "failure_mode", "oracle"):
        r_set = {row.entry for row in rows if row.kind == kind and row.rag_units}
        a_set = {row.entry for row in rows if row.kind == kind and row.agente_units}
        union = r_set | a_set
        summaries.append(
            ComparisonSummary(
                kind=kind,
                rag_entries=len(r_set),
                agente_entries=len(a_set),
                shared=len(r_set & a_set),
                jaccard=round(len(r_set & a_set) / len(union), 4) if union else 0.0,
                agent_transfer_rate=round(len(r_set & a_set) / len(a_set), 4) if a_set else 0.0,
            )
        )
    return rows, summaries


class SaturationPoint(BaseModel):
    batch: int
    units: int  # units first seen in this batch
    new_entries: int
    cumulative_entries: int
    new: list[str]  # "kind:entry"


class Saturation(BaseModel):
    points: list[SaturationPoint]
    saturated: bool
    saturated_at: int | None  # first batch of the final run of `window` quiet batches
    window: int
    tolerance: int


def saturation(
    units: list[Unit],
    batch_of_item: dict[str, int],
    window: int = 2,
    tolerance: int = 0,
    method_rungs: dict[str, str] | None = None,
) -> Saturation:
    """New codes (tools, methods, failure modes) per screening batch (§11).

    A unit belongs to the earliest batch of its items. Saturation is reached when
    the last `window` batches each add at most `tolerance` new codes.
    """
    by_batch: dict[int, list[Unit]] = {}
    for u in units:
        batches = [batch_of_item[i] for i in u.item_ids if i in batch_of_item]
        if batches:
            by_batch.setdefault(min(batches), []).append(u)
    seen: set[str] = set()
    points = []
    for batch in sorted(by_batch):
        new: list[str] = []
        for u in by_batch[batch]:
            for entry, kind in _entries(u, method_rungs):
                if kind == "oracle":
                    continue
                code = f"{kind}:{entry}"
                if code not in seen:
                    seen.add(code)
                    new.append(code)
        points.append(
            SaturationPoint(
                batch=batch,
                units=len(by_batch[batch]),
                new_entries=len(new),
                cumulative_entries=len(seen),
                new=sorted(new),
            )
        )
    tail = points[-window:] if window > 0 else []
    saturated = len(points) >= window > 0 and all(p.new_entries <= tolerance for p in tail)
    return Saturation(
        points=points,
        saturated=saturated,
        saturated_at=tail[0].batch if saturated else None,
        window=window,
        tolerance=tolerance,
    )
