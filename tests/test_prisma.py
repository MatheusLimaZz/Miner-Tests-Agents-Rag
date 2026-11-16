"""PRISMA flow from run records (Phase 2, T6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.coding import CodingRecord, append_codings
from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Item,
    ItemKind,
    Manifest,
    Provenance,
    QueryManifestEntry,
    SourceManifestEntry,
)
from msrkit.prisma import build_flow, render_markdown
from msrkit.provenance import save_manifest
from msrkit.screening import ScreeningDecision, append_decisions
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path


def _item(i: int, source: str) -> Item:
    return Item(
        id=f"{source[:2]}{i}",
        source=source,
        kind=ItemKind.THREAD,
        url=f"https://x.example/{source}/{i}",  # type: ignore[arg-type]
        provenance=Provenance(
            run_id="r",
            query_string="q",
            partition="p",
            adapter="a",
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),  # fmt: skip
    )


OK = Availability(status=AvailabilityStatus.OK, reason="ok")


def _manifest() -> Manifest:
    def q(items: int, discarded: int = 0, est: int | None = None, cap: bool = False):
        return QueryManifestEntry(
            query_string="q", partitions=1, requests=1, items=items, truncated=cap,
            truncation_reasons=["source_cap"] if cap else [], estimated_total=est,
            discarded=discarded,
        )  # fmt: skip

    return Manifest(
        run_id="run-p",
        msrkit_version="t",
        protocol_path="protocols/v0_rag_agents_testing.yaml",
        protocol_sha256="",
        started_at=datetime.now(UTC),
        sources=[
            SourceManifestEntry(name="github", adapter_version="1",
                                availability=OK,
                                queries=[q(3, est=1003, cap=True)]),
            SourceManifestEntry(name="hackernews", adapter_version="1",
                                availability=OK, queries=[q(2, discarded=3)]),
        ],
    )  # fmt: skip


def _dec(item: str, coder: str, decision: str, crit: list[str] | None = None):
    return ScreeningDecision(
        item_id=item,
        coder=coder,
        decision=decision,  # type: ignore[arg-type]
        criteria_met=crit or [],
        reason="r",
        decided_at="t",
        sheet_sha256="h",
    )


def _setup(tmp_path: Path) -> list[Item]:
    items = [_item(i, "github") for i in range(3)] + [_item(i, "hackernews") for i in range(2)]
    discards = tmp_path / "discarded.jsonl"
    discards.write_text(
        "\n".join(json.dumps({"reason": r}) for r in ("out_of_window", "out_of_window")) + "\n"
    )
    return items


class TestFlow:
    def test_counts_and_reasons(self, tmp_path: Path) -> None:
        items = _setup(tmp_path)
        pool = items[:4]  # hn1 removed as a duplicate
        decisions = {
            ("gi0", "ana"): _dec("gi0", "ana", "include"),
            ("gi0", "bia"): _dec("gi0", "bia", "include"),
            ("gi1", "ana"): _dec("gi1", "ana", "exclude", ["E1", "I2"]),
            ("gi1", "bia"): _dec("gi1", "bia", "exclude", ["E3"]),
            ("gi2", "ana"): _dec("gi2", "ana", "include"),
            ("gi2", "bia"): _dec("gi2", "bia", "exclude"),
            ("hn1", "ana"): _dec("hn1", "ana", "include"),  # not in pool: ignored
        }
        flow = build_flow(
            "run-p", _manifest(), items, pool,
            {"removed": {"url": 1, "content": 0, "near": 0}},
            tmp_path / "discarded.jsonl", decisions, {"gi0", "gi1"},
        )  # fmt: skip
        assert flow.identified == {"github": 3, "hackernews": 5}
        assert flow.not_retrieved_estimate == {"github": 1000}
        assert flow.filtered == {"out_of_window": 2, "unspecified": 1}
        assert flow.duplicates == {"url": 1, "content": 0, "near": 0}
        assert (flow.screened, flow.excluded, flow.uncertain, flow.pending) == (4, 1, 1, 1)
        assert flow.exclusion_reasons == {"E1": 1, "E3": 1}
        assert (flow.included, flow.coded) == (1, 1)
        assert flow.included_by_source == {"github": 1}

    def test_planned_queries_not_executed(self, tmp_path: Path) -> None:
        items = _setup(tmp_path)
        m = _manifest()
        m.sources[0].planned = [{}, {}, {}]  # 3 planned, 1 executed
        flow = build_flow("r", m, items, items, None, tmp_path / "x.jsonl", {}, set())
        assert flow.not_executed == {"github": 2}
        md = render_markdown(flow)
        assert "Planned queries not executed (source cap or interruption): 2 (github 2)" in md
        none = build_flow("r", _manifest(), items, items, None, tmp_path / "x.jsonl", {}, set())
        assert none.not_executed == {}
        assert "Planned queries not executed (source cap or interruption): 0\n" in (
            render_markdown(none)
        )

    def test_queries_cut_by_the_item_limit_are_counted_as_a_sample(self, tmp_path: Path) -> None:
        """The 015e flow said 0 not retrieved with 122 queries stopped at 100 items."""
        from msrkit.prisma import render_mermaid, render_tikz

        def q(items: int, est: int | None, discarded: int = 0, *reasons: str):
            return QueryManifestEntry(
                query_string="q", partitions=1, requests=1, items=items, truncated=bool(reasons),
                truncation_reasons=list(reasons), estimated_total=est, discarded=discarded,
            )  # fmt: skip

        m = _manifest()
        m.sources[0].queries = [
            q(100, 4000, 0, "item_limit"),
            q(100, 1500, 400, "item_limit"),  # 500 examined: 400 dropped by a local filter
            q(100, None, 0, "item_limit"),  # the source gave no total
            q(30, 30),  # complete
            q(3, 1003, 0, "source_cap"),  # the source's cap, counted apart
        ]
        items = _setup(tmp_path)
        flow = build_flow("r", m, items, items, None, tmp_path / "x.jsonl", {}, set())
        assert flow.sampled_out == {"github": 3900 + 1000}
        assert (flow.sampled_queries, flow.sampled_queries_uncounted) == (3, 1)
        assert flow.not_retrieved_estimate == {"github": 1000} and flow.truncated_queries == 1
        md = render_markdown(flow)
        assert "| github | 733 | 1000 | 4900 |" in md
        assert "Queries stopped at the run's item limit (a sample of their results): 3" in md
        assert "1 without a total from the source" in md and "upper bound" in md
        assert "item limit (est., upper bound): 4900" in render_mermaid(flow)
        assert "teto de itens (est., limite superior): 4900" in render_tikz(flow)
        # nothing to say when no query was cut by the limit
        plain = build_flow("r", _manifest(), items, items, None, tmp_path / "x.jsonl", {}, set())
        assert plain.sampled_out == {} and "upper bound" not in render_markdown(plain)

    def test_missing_dedupe_is_na_not_zero(self, tmp_path: Path) -> None:
        from msrkit.prisma import render_tikz

        items = _setup(tmp_path)
        flow = build_flow("r", None, items, items, None, tmp_path / "none.jsonl", {}, set())
        assert flow.duplicates is None
        assert flow.pending == 5
        md = render_markdown(flow)
        assert "Duplicates: n/a" in md
        assert "flowchart TD" in md
        tikz = render_tikz(flow)
        assert r"\begin{tikzpicture}" in tikz
        assert r"Identificação" in tikz


class TestPrismaCommand:
    def test_writes_reports(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        items = _setup(tmp_path)
        ItemStorage(tmp_path).save_items(items, "run-p")
        save_manifest(_manifest(), tmp_path)
        append_decisions(tmp_path, "run-p", [_dec("gi0", "ana", "include")])
        append_codings(
            tmp_path,
            "run-p",
            [CodingRecord(item_id="gi0", coder="ana", values={}, coded_at="t", sheet_sha256="h")],
        )
        r = CliRunner().invoke(app, ["prisma", "--run", "run-p"])
        assert r.exit_code == 0, r.stdout
        assert "Identified 8" in r.stdout and "included 1" in r.stdout
        data = json.loads((tmp_path / "reports/run-p/prisma.json").read_text())
        assert data["coded"] == 1
        assert (tmp_path / "reports/run-p/prisma.md").exists()
        assert (tmp_path / "reports/run-p/prisma.tikz").exists()

    def test_tikz_and_mermaid_escaping(self) -> None:
        from msrkit.prisma import PrismaFlow, _latex_escape, render_mermaid, render_tikz

        assert _latex_escape("rag_agent & testing % #1 $val {ok}") == (
            r"rag\_agent \& testing \% \#1 \$val \{ok\}"
        )
        flow = PrismaFlow(
            run_id="r1",
            identified={"google_scholar": 10, 'quote_"source"': 5},
            not_retrieved_estimate={},
            truncated_queries=0,
            filtered={"out_of_scope": 2},
            after_filters=8,
            duplicates=None,
            screened=8,
            excluded=3,
            exclusion_reasons={'reason_"x"_with_underscore': 3},
            uncertain=0,
            pending=0,
            included=5,
            coded=2,
        )
        tikz = render_tikz(flow)
        assert r"google\_scholar" in tikz
        assert r"reason\_'x'\_with\_underscore" in tikz or r"reason\_" in tikz

        mermaid = render_mermaid(flow)
        assert "\"quote_'source'\"" not in mermaid  # inner double quotes replaced by single
        assert "reason_'x'_with_underscore" in mermaid

    def test_build_flow_non_string_criteria_met(self, tmp_path: Path) -> None:
        decisions = {
            ("it1", "coder1"): ScreeningDecision.model_construct(
                item_id="it1",
                coder="coder1",
                decision="exclude",
                criteria_met=["E1", None, 123],
                reason="test",
                decided_at="now",
                sheet_sha256="s",
            )
        }
        item = _item(1, "github")
        item.id = "it1"
        flow = build_flow(
            run_id="r1",
            manifest=None,
            items=[item],
            pool=[item],
            dedupe_report=None,
            discard_file=tmp_path / "discard.jsonl",
            decisions=decisions,
            coded_ids=set(),
        )
        assert flow.excluded == 1
        assert "E1" in flow.exclusion_reasons

    def test_build_flow_uses_manifest_query_items_when_deduped_pool_given(
        self, tmp_path: Path
    ) -> None:
        """When pre-deduped items.jsonl is absent, PRISMA uses manifest queries count."""
        manifest = Manifest(
            run_id="r1",
            msrkit_version="0.1.0",
            protocol_path="proto.yaml",
            protocol_sha256="abc",
            started_at=datetime.now(UTC),
            sources=[
                SourceManifestEntry(
                    name="github",
                    adapter_version="1",
                    availability=OK,
                    queries=[
                        QueryManifestEntry(
                            query_string="q1",
                            partitions=1,
                            requests=1,
                            items=100,
                            truncated=False,
                            discarded=10,
                        )
                    ],
                )
            ],
        )
        pool_items = [_item(i, "github") for i in range(80)]
        flow = build_flow(
            run_id="r1",
            manifest=manifest,
            items=pool_items,
            pool=pool_items,
            dedupe_report={"removed": {"github": 20}},
            discard_file=tmp_path / "discard.jsonl",
            decisions={},
            coded_ids=set(),
        )
        assert flow.identified["github"] == 110
        assert flow.filtered["unspecified"] == 10
