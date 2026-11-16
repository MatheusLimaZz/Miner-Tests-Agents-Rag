"""Sampling fractions and design weights (ADR-058)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from msrkit.extract import Detection
from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Item,
    ItemKind,
    Manifest,
    Provenance,
    Query,
    QueryManifestEntry,
    SourceManifestEntry,
)
from msrkit.partition import query_key
from msrkit.weights import fractions, item_weights, kish, pair_weights, strata

OK = Availability(status=AvailabilityStatus.OK, reason="ok")


def _query(term: str, year: int | None = 2024, kind: str = "issue", **kw: object) -> Query:
    since = date(year, 1, 1) if year else date(2023, 1, 1)
    until = date(year, 12, 31) if year else date(2026, 8, 31)
    return Query(source="github", terms=[term], kind=kind, since=since, until=until, **kw)  # type: ignore[arg-type]


def _entry(
    q: Query, items: int, total: int | None, *reasons: str, discarded: int = 0
) -> QueryManifestEntry:
    return QueryManifestEntry(
        query_string=" ".join(q.terms),
        partitions=1,
        requests=1,
        items=items,
        discarded=discarded,
        truncated=bool(reasons),
        truncation_reasons=list(reasons),
        estimated_total=total,
        key=query_key(q),
        completed=True,
    )


def _manifest(pairs: list[tuple[Query, QueryManifestEntry]]) -> Manifest:
    return Manifest(
        run_id="run-w",
        msrkit_version="t",
        protocol_path="p.yaml",
        protocol_sha256="",
        started_at=datetime.now(UTC),
        sources=[
            SourceManifestEntry(
                name="github",
                adapter_version="1",
                availability=OK,
                planned=[q.model_dump(mode="json") for q, _ in pairs],
                queries=[e for _, e in pairs],
            )
        ],
    )


def _item(iid: str, q: Query, kind: ItemKind = ItemKind.ISSUE, repo: str = "acme/app") -> Item:
    path = {ItemKind.ISSUE: f"issues/{iid}", ItemKind.CODE: f"blob/main/{iid}.py"}.get(kind, "")
    return Item(
        id=iid,
        source="github",
        kind=kind,
        url=f"https://github.com/{repo}/{path}".rstrip("/"),  # type: ignore[arg-type]
        provenance=Provenance(
            run_id="run-w",
            query_string="q",
            partition=query_key(q),
            adapter="github",
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),
    )


def _det(item: Item, entry: str, level: str = "N1", signal: str = "text") -> Detection:
    parts = str(item.url).split("/")
    return Detection(
        item_id=item.id,
        source="github",
        repo=f"{parts[3]}/{parts[4]}".lower(),
        entry_id=entry,
        entry_type="tool",
        level=level,  # type: ignore[arg-type]
        signal=signal,  # type: ignore[arg-type]
        location="x",
        evidence="x",
    )


class TestStrata:
    def test_fraction_is_examined_over_available_for_a_query_cut_short(self) -> None:
        q = _query("LLM evaluation", 2026)
        (s,) = strata(_manifest([(q, _entry(q, 100, 4000, "item_limit"))]))
        assert (s.kind, s.family, s.year) == ("issue", "termo", 2026)
        assert s.fraction == pytest.approx(0.025) and s.weight == pytest.approx(40)
        assert s.not_retrieved == 3900 and s.known

    def test_what_a_local_filter_dropped_was_examined_too(self) -> None:
        """With the bot filter, 100 kept of 1,000 examined stand for a tenth of the people."""
        q = _query("eval harness", 2025)
        (s,) = strata(_manifest([(q, _entry(q, 100, 5000, "item_limit", discarded=900))]))
        assert s.examined == 1000 and s.weight == pytest.approx(5)
        assert s.not_retrieved == 4000

    def test_a_query_that_was_not_cut_short_is_a_census_of_its_stratum(self) -> None:
        q = _query("metamorphic testing LLM", 2023)
        (s,) = strata(_manifest([(q, _entry(q, 7, 12))]))  # the estimate was off: all came
        assert s.fraction == 1.0 and s.weight == 1.0 and s.not_retrieved == 0

    def test_without_a_count_the_fraction_is_unknown_not_guessed(self) -> None:
        q = _query('"from ragas import"', None, kind="code", raw=True, label="ragas-import")
        (s,) = strata(_manifest([(q, _entry(q, 100, None, "item_limit"))]))
        assert s.fraction is None and not s.known and s.weight == 1.0
        assert (s.family, s.year) == ("ragas-import", None)

    def test_a_stale_count_never_gives_a_weight_below_one(self) -> None:
        q = _query("RAG testing", 2024)
        (s,) = strata(_manifest([(q, _entry(q, 100, 80, "item_limit"))]))
        assert s.fraction == 1.0 and s.weight == 1.0

    def test_template_queries_are_grouped_by_their_label(self) -> None:
        q = _query("ragas path:.github/workflows", None, kind="code", raw=True, label="ci:ragas")
        (s,) = strata(_manifest([(q, _entry(q, 100, 2500, "item_limit"))]))
        assert s.family == "ci" and s.weight == pytest.approx(25)


class TestItemWeights:
    def test_items_carry_the_weight_of_the_query_that_kept_them(self) -> None:
        big, small = _query("LLM evaluation", 2026), _query("LLM test oracle", 2023)
        manifest = _manifest(
            [(big, _entry(big, 100, 2000, "item_limit")), (small, _entry(small, 3, 3))]
        )
        items = [_item("a", big), _item("b", small)]
        weights = item_weights(manifest, items)
        assert weights["a"].weight == pytest.approx(20) and weights["b"].weight == 1.0
        stranger = _item("c", _query("not planned", 2024))
        assert item_weights(manifest, [stranger])["c"].known is False
        assert item_weights(None, items)["a"].weight == 1.0

    def test_kish_effective_sample_size(self) -> None:
        assert kish([1, 1, 1, 1]) == pytest.approx(4)
        assert kish([10, 1, 1, 1]) == pytest.approx(13**2 / 103)
        assert kish([]) == 0.0


class TestFractions:
    def test_groups_by_source_kind_family_and_year(self) -> None:
        a, b, c = _query("a", 2026), _query("b", 2026), _query("c", 2023)
        manifest = _manifest(
            [
                (a, _entry(a, 100, 900, "item_limit")),
                (b, _entry(b, 40, 55)),  # complete: 40 is all it matches
                (c, _entry(c, 100, None, "item_limit")),
            ]
        )
        rows = {(r.year): r for r in fractions(strata(manifest))}
        assert rows[2026].queries == 2 and rows[2026].cut_short == 1
        assert rows[2026].examined == 140 and rows[2026].available == 940
        assert rows[2026].fraction == pytest.approx(140 / 940)
        assert rows[2026].not_retrieved == 800
        assert rows[2023].unknown == 1 and rows[2023].fraction is None


class TestPairWeights:
    """A unit counts once per entry: the weight is that of its most probable route."""

    def test_a_mention_weighs_as_the_item_that_carries_it(self) -> None:
        big, small = _query("LLM evaluation", 2026), _query("agent testing", 2023)
        manifest = _manifest(
            [
                (big, _entry(big, 100, 5000, "item_limit")),
                (small, _entry(small, 50, 100, "item_limit")),
            ]
        )
        heavy, light = _item("i1", big), _item("i2", small)
        other = _item("i3", big, repo="acme/other")
        items = [heavy, light, other]
        dets = [_det(heavy, "ragas"), _det(light, "ragas"), _det(other, "deepeval")]
        pairs = pair_weights(items, dets, item_weights(manifest, items))
        assert pairs[("acme/app", "ragas")] == pytest.approx(2)  # the lighter of the two routes
        assert pairs[("acme/other", "deepeval")] == pytest.approx(50)

    def test_structural_evidence_weighs_as_the_route_to_the_repository(self) -> None:
        issues = _query("LLM evaluation", 2026)
        ci = _query("ragas path:.github/workflows", None, kind="code", raw=True, label="ci:ragas")
        repos = _query("RAG evaluation", 2024, kind="repo")
        manifest = _manifest(
            [
                (issues, _entry(issues, 100, 5000, "item_limit")),
                (ci, _entry(ci, 100, 1000, "item_limit")),
                (repos, _entry(repos, 30, 30)),
            ]
        )
        issue = _item("i1", issues)
        hit = _item("c1", ci, ItemKind.CODE)
        items = [issue, hit]
        dets = [_det(issue, "ragas"), _det(hit, "ragas", "N3", "ci")]
        pairs = pair_weights(items, dets, item_weights(manifest, items))
        assert pairs[("acme/app", "ragas")] == pytest.approx(10)  # the CI query, not the issue
        # Found by a complete repository search as well: every such repository is observed.
        found = _item("r1", repos, ItemKind.REPO)
        pairs = pair_weights([*items, found], dets, item_weights(manifest, [*items, found]))
        assert pairs[("acme/app", "ragas")] == 1.0
