"""Sampling fractions and design weights of a run (ADR-055, ADR-058).

A query that stops at the per-query cap brings a sample of what the source holds. For
each executed query the manifest keeps what it examined (items kept plus items a local
filter dropped) and, when the source reports it, how many results the query matches
(`estimated_total`). The query is a stratum of the sample (source, kind, term or literal
query, and year when the run is stratified):

    fraction = examined / available        weight = available / examined

Every item the query kept carries that weight: it stands for `weight` records of the
stratum that are like it. A query that was not cut short examined everything it
matches, so its fraction is 1 whatever the source estimated.

What the weights do, and do not do:

- they undo the unequal allocation among strata (a 100-item cap takes 45% of a small
  stratum and 2% of a large one), so frequencies describe the sources, not the caps;
- they do not undo the order in which a source returns results inside a stratum
  (GitHub: most recently updated first), which stays a threat to declare;
- a record that several queries match is counted in the total of each of them, so
  weighted totals are upper bounds; shares and rankings depend on it far less.

When the source gives no count for a truncated query the fraction is unknown: the
weight stays 1 and the stratum is reported as such, never guessed.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from msrkit.enrich import repo_of
from msrkit.extract import ARTIFACT_KINDS, LEVEL_RANK
from msrkit.models import Query
from msrkit.partition import query_key

if TYPE_CHECKING:
    from collections.abc import Iterable

    from msrkit.extract import Detection
    from msrkit.models import Item, Manifest

# Reasons that mean the query stopped before exhausting what it matches.
CUT_SHORT = ("item_limit", "source_cap", "quota_exhausted")
TERM_FAMILY = "termo"  # queries built from the protocol lexicon


class Stratum(BaseModel):
    """One executed query as a stratum of the sample."""

    key: str  # the query's stable id; items carry it in `provenance.partition`
    source: str
    kind: str = ""  # repo, code, issue... ("" when the source has a single kind)
    family: str = TERM_FAMILY  # lexicon term, or the label of the literal query
    year: int | None = None  # set when the query covers a single calendar year
    query_string: str = ""
    kept: int = 0
    examined: int = 0  # kept + dropped by a local filter
    available: int | None = None  # results the source reports for the query
    cut_short: bool = False
    fraction: float | None = 1.0  # None: cut short and the source gave no count
    weight: float = 1.0

    @property
    def known(self) -> bool:
        return self.fraction is not None

    @property
    def not_retrieved(self) -> int:
        """Results the query matches that the run did not examine (0 when unknown)."""
        if not self.cut_short or self.available is None:
            return 0
        return max(self.available - self.examined, 0)


def _fraction(examined: int, available: int | None, cut_short: bool) -> float | None:
    if not cut_short:
        return 1.0
    if available is None or available <= 0 or examined <= 0:
        return None
    return min(1.0, examined / available)


def _planned_by_key(planned: list[dict[str, Any]]) -> dict[str, Query]:
    out: dict[str, Query] = {}
    for raw in planned:
        try:
            query = Query.model_validate(raw)
        except ValueError:
            continue
        out[query_key(query)] = query
    return out


def strata(manifest: Manifest | None) -> list[Stratum]:
    """The strata of a run, one per executed query, in manifest order."""
    out: list[Stratum] = []
    for src in manifest.sources if manifest else []:
        planned = _planned_by_key(src.planned)
        for entry in src.queries:
            query = planned.get(entry.key)
            cut_short = any(reason in CUT_SHORT for reason in entry.truncation_reasons)
            examined = entry.items + entry.discarded
            fraction = _fraction(examined, entry.estimated_total, cut_short)
            year = None
            if query and query.since and query.until and query.since.year == query.until.year:
                year = query.since.year
            family = TERM_FAMILY
            if query and query.raw:
                family = query.label.split(":", 1)[0] or "(sem rótulo)"
            out.append(
                Stratum(
                    key=entry.key,
                    source=src.name,
                    kind=(query.kind or "") if query else "",
                    family=family,
                    year=year,
                    query_string=entry.query_string,
                    kept=entry.items,
                    examined=examined,
                    available=entry.estimated_total,
                    cut_short=cut_short,
                    fraction=fraction,
                    weight=1.0 if not fraction else 1.0 / fraction,
                )
            )
    return out


class ItemWeight(BaseModel):
    """Design weight of an item and whether its stratum's fraction is known."""

    weight: float = 1.0
    known: bool = True
    stratum: str = ""


def item_weights(manifest: Manifest | None, items: Iterable[Item]) -> dict[str, ItemWeight]:
    """Item id -> design weight, from the stratum of the query that kept the item.

    After deduplication an item keeps the provenance of the copy that stayed (the first
    collected), so it carries the weight of that query. An item whose query is not in
    the manifest (a run without checkpoints, a consolidated corpus) weighs 1, unknown.
    """
    by_key = {(s.source, s.key): s for s in strata(manifest) if s.key}
    out: dict[str, ItemWeight] = {}
    for item in items:
        stratum = by_key.get((item.source, item.provenance.partition))
        if stratum is None:
            out[item.id] = ItemWeight(known=False)
        else:
            out[item.id] = ItemWeight(
                weight=stratum.weight, known=stratum.known, stratum=stratum.key
            )
    return out


def kish(weights: Iterable[float]) -> float:
    """Kish's effective sample size, (Σw)² / Σw².

    The number of equal-weight items that would give the same precision: the more the
    weights differ, the further it falls below the number of items.
    """
    values = [w for w in weights if w > 0]
    total = sum(values)
    squares = sum(w * w for w in values)
    return total * total / squares if squares else 0.0


def pair_weights(
    items: Iterable[Item], detections: Iterable[Detection], weights: dict[str, ItemWeight]
) -> dict[tuple[str, str], float]:
    """(unit, entry) -> design weight of the unit's best evidence for that entry.

    A unit is a GitHub repository with all its items, or one item elsewhere, and counts
    once per entry at its best level (ADR-035). The weight is that of the item whose
    sampling made the evidence observable:

    - a mention in text (N1) is observed when the item that carries it is sampled:
      the smallest weight among the items with the mention at the best level;
    - a structural signal (N2, N3) comes from reading the repository, which happens
      whenever any artifact item of it (the repository or a code hit) is sampled: the
      smallest weight among the unit's artifact items.

    Taking the smallest weight is taking the most probable route. A unit with several
    routes is more likely to be observed than by any single one, so its true weight is
    smaller still: weighted counts lean high for such units.
    """
    unit_of: dict[str, str] = {}
    artifact: dict[str, float] = {}
    for item in items:
        unit = repo_of(item) or item.id
        unit_of[item.id] = unit
        if item.kind in ARTIFACT_KINDS:
            w = weights[item.id].weight if item.id in weights else 1.0
            artifact[unit] = min(artifact.get(unit, w), w)

    best: dict[tuple[str, str], int] = {}
    textual: dict[tuple[str, str], float] = {}
    structural: set[tuple[str, str]] = set()
    for d in detections:
        owner = unit_of.get(d.item_id) or d.repo
        if owner is None:
            continue
        pair, rank = (owner, d.entry_id), LEVEL_RANK[d.level]
        if rank > best.get(pair, 0):
            best[pair] = rank
            textual.pop(pair, None)
            structural.discard(pair)
        if rank < best[pair]:
            continue
        if d.signal == "text":
            w = weights[d.item_id].weight if d.item_id in weights else 1.0
            textual[pair] = min(textual.get(pair, w), w)
        else:
            structural.add(pair)
    out: dict[tuple[str, str], float] = {}
    for pair in best:
        if pair in structural:
            out[pair] = artifact.get(pair[0], 1.0)
        else:
            out[pair] = textual.get(pair, 1.0)
    return out


class FractionRow(BaseModel):
    """Sampling fraction of a group of strata (for the mining report)."""

    source: str
    kind: str
    family: str
    year: int | None
    queries: int
    cut_short: int  # queries that stopped before exhausting their results
    unknown: int  # of those, the ones without a count from the source
    examined: int
    available: int | None  # None when a stratum of the group has no count
    not_retrieved: int

    @property
    def fraction(self) -> float | None:
        if not self.available:
            return None
        return min(1.0, self.examined / self.available)


def fractions(rows: list[Stratum]) -> list[FractionRow]:
    """Strata grouped by source, kind, query family and year.

    `available` adds, for a query that was not cut short, what it examined (it is all
    the query matches), so the group's fraction compares like with like. One stratum of
    unknown size makes the group's size, and so its fraction, unknown: a fraction over
    the known strata alone would read as if the whole group had been measured.
    """
    groups: dict[tuple[str, str, str, int | None], list[Stratum]] = defaultdict(list)
    for s in rows:
        groups[(s.source, s.kind, s.family, s.year)].append(s)
    out: list[FractionRow] = []
    for (source, kind, family, year), members in groups.items():
        unknown = sum(not s.known for s in members)
        sizes = [(s.available or 0) if s.cut_short else s.examined for s in members]
        out.append(
            FractionRow(
                source=source,
                kind=kind,
                family=family,
                year=year,
                queries=len(members),
                cut_short=sum(s.cut_short for s in members),
                unknown=unknown,
                examined=sum(s.examined for s in members),
                available=None if unknown else sum(sizes),
                not_retrieved=sum(s.not_retrieved for s in members),
            )
        )
    return sorted(out, key=lambda r: (r.source, r.kind, r.family, r.year or 0))
