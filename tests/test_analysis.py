"""Analyses A1–A6 (Phase 2, N1–N5)."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from msrkit.analysis import (
    Unit,
    aggregation_test,
    binomial_greater,
    compare_systems,
    consensus,
    cooccurrence,
    coverage,
    coverage_matrix,
    frequency,
    oracle_distribution,
    saturation,
    units_from_codings,
    units_from_detections,
    write_csv,
)
from msrkit.cli import app
from msrkit.coding import CodingRecord, append_codings
from msrkit.extract import Detection
from msrkit.models import Item, ItemKind, Provenance
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path


def _item(iid: str, url: str, source: str = "github", concepts: list[str] | None = None) -> Item:
    return Item(
        id=iid,
        source=source,
        kind=ItemKind.REPO,
        url=url,  # type: ignore[arg-type]
        concepts=concepts or [],
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


def _det(item: str, entry: str, level: str = "N1", kind: str = "tool", repo: str | None = None):
    return Detection(
        item_id=item,
        source="github",
        repo=repo,
        entry_id=entry,
        entry_type=kind,  # type: ignore[arg-type]
        level=level,
        signal="text" if level == "N1" else "ci",  # type: ignore[arg-type]
        location="x",
        evidence="e",
    )


def _rec(item: str, coder: str, **values: object) -> CodingRecord:
    return CodingRecord(item_id=item, coder=coder, values=values, coded_at="t", sheet_sha256="h")


ITEMS = [
    _item("r1", "https://github.com/o/a", concepts=["rag", "teste"]),
    _item("i1", "https://github.com/o/a/issues/1", concepts=["agente"]),
    _item("r2", "https://github.com/o/b", concepts=["rag"]),
    _item("s1", "https://stackoverflow.com/q/1", source="stackexchange", concepts=["agente"]),
]
DETS = [
    _det("r1", "ragas", "N3", repo="o/a"),
    _det("i1", "ragas", "N1", repo="o/a"),
    _det("i1", "llm-judge", kind="method", repo="o/a"),
    _det("r2", "ragas", "N2", repo="o/b"),
    _det("r2", "deepeval", "N1", repo="o/b"),
    _det("s1", "deepeval"),
    _det("s1", "llm-judge", kind="method"),
]


class TestUnits:
    def test_repository_items_merge_into_one_unit(self) -> None:
        units = {u.id: u for u in units_from_detections(ITEMS, DETS)}
        assert set(units) == {"o/a", "o/b", "s1"}
        assert units["o/a"].tools == {"ragas": "N3"}  # best level wins
        assert units["o/a"].systems == ["agente", "rag"]
        assert units["o/a"].methods == ["llm-judge"]

    def test_consensus(self) -> None:
        a = _rec("x", "ana", nivel="N2", modos=["FP1", "AF1"], ok=True)
        b = _rec("x", "bia", nivel="N3", modos=["AF1"], ok=True)
        assert consensus([a, b]) == {"nivel": None, "modos": ["AF1"], "ok": True}
        assert consensus([a]) == a.values
        assert consensus([]) == {}

    def test_units_from_codings(self) -> None:
        codings = {
            ("r1", "ana"): _rec("r1", "ana", sistema=["rag"], nivel_evidencia="N2",
                                ferramentas=["ragas"], metodos=[], modos_falha=["FP2"],
                                tipo_oraculo="referencia", agregacao="atomico"),
            ("i1", "ana"): _rec("i1", "ana", sistema=["agente"], nivel_evidencia=None,
                                ferramentas=["promptfoo"], metodos=["llm-judge"],
                                modos_falha=["AF1"]),
        }  # fmt: skip
        (unit,) = units_from_codings(ITEMS, codings)
        assert unit.id == "o/a"
        assert unit.systems == ["agente", "rag"]
        assert unit.tools == {"ragas": "N2", "promptfoo": "N1"}
        assert unit.failure_modes == ["AF1", "FP2"]
        assert unit.oracle == "referencia"


class TestFrequency:
    def test_counts_by_level_system_and_source(self) -> None:
        rows = {r.entry: r for r in frequency(units_from_detections(ITEMS, DETS))}
        ragas = rows["ragas"]
        assert (ragas.units, ragas.n1, ragas.n2, ragas.n3) == (2, 0, 1, 1)
        assert (ragas.rag, ragas.agente) == (2, 1)
        assert rows["deepeval"].sources == {"github": 1, "stackexchange": 1}
        assert rows["llm-judge"].kind == "method"

    def test_cooccurrence_and_lift(self) -> None:
        units = [
            Unit(id="a", source="s", tools={"t1": "N1"}, methods=["m1"]),
            Unit(id="b", source="s", tools={"t1": "N1"}, methods=["m1"]),
            Unit(id="c", source="s", tools={"t2": "N1"}, methods=[]),
            Unit(id="d", source="s", tools={"t1": "N1", "t2": "N1"}),
        ]
        (pair,) = cooccurrence(units, "tool-method")
        assert (pair.a, pair.b, pair.together) == ("t1", "m1", 2)
        assert pair.lift == pytest.approx(2 * 4 / (3 * 2), abs=1e-4)
        assert pair.jaccard == pytest.approx(2 / 3, abs=1e-4)
        (tt,) = cooccurrence(units, "tool-tool")
        assert (tt.a, tt.b) == ("t1", "t2")
        with pytest.raises(ValueError):
            cooccurrence(units, "x")

    def test_binomial(self) -> None:
        assert binomial_greater(10, 10) == pytest.approx(0.5**10)
        assert binomial_greater(0, 5) == pytest.approx(1.0)


class TestAnalyzeFrequencyCommand:
    def test_writes_csv(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(ITEMS, "run-a")
        runner = CliRunner()
        r = runner.invoke(app, ["analyze", "frequency", "--run", "run-a"])
        assert r.exit_code == 1  # no detections yet
        out = tmp_path / "extract" / "run-a"
        out.mkdir(parents=True)
        (out / "detections.jsonl").write_text("\n".join(d.model_dump_json() for d in DETS))
        r = runner.invoke(app, ["analyze", "frequency", "--run", "run-a"])
        assert r.exit_code == 0, r.stdout
        path = tmp_path / "reports/run-a/analysis/frequency_detections.csv"
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert rows[0]["kind"] == "tool"  # ties: tools before methods
        assert {r["entry"] for r in rows} == {"ragas", "deepeval", "llm-judge"}
        r = runner.invoke(app, ["analyze", "frequency", "--run", "run-a", "--basis", "coding"])
        assert r.exit_code == 1  # no codings
        rec = _rec("s1", "ana", sistema=["agente"], ferramentas=["deepeval"], metodos=[])
        append_codings(tmp_path, "run-a", [rec])
        r = runner.invoke(app, ["analyze", "frequency", "--run", "run-a", "--basis", "coding"])
        assert r.exit_code == 0, r.stdout
        assert (tmp_path / "reports/run-a/analysis/frequency_coding.csv").exists()


COVERAGE_UNITS = [
    Unit(id="a", source="github", systems=["rag"], tools={"ragas": "N2"}, methods=["llm-judge"],
         failure_modes=["FP2", "FP4"]),
    Unit(id="b", source="github", systems=["rag", "agente"], tools={"promptfoo": "N1"},
         failure_modes=["FP4", "AF1"]),
    Unit(id="c", source="devto", systems=[], methods=["metamorphic"], failure_modes=["AF99"]),
]  # fmt: skip
CATALOG = {"FP2": "top-k", "FP4": "not extracted", "AF1": "wrong tool", "AF2": "bad args"}


class TestCoverage:
    def test_summary_reports_uncovered_and_uncatalogued(self) -> None:
        cells, summaries = coverage(COVERAGE_UNITS, CATALOG)
        by_mode = {s.failure_mode: s for s in summaries}
        assert [s.failure_mode for s in summaries] == ["FP2", "FP4", "AF1", "AF2", "AF99"]
        assert not by_mode["AF2"].covered and by_mode["AF2"].units == 0
        assert by_mode["AF99"].label == "(not in catalog)"
        fp4 = by_mode["FP4"]
        assert (fp4.units, fp4.rag, fp4.agente, fp4.tools, fp4.methods) == (2, 2, 1, 2, 1)
        systems = {(c.failure_mode, c.entry, c.system) for c in cells}
        assert ("AF1", "promptfoo", "agente") in systems
        assert ("AF99", "metamorphic", "unlabelled") in systems

    def test_matrix_per_system(self) -> None:
        cells, summaries = coverage(COVERAGE_UNITS, CATALOG)
        modes = [s.failure_mode for s in summaries]
        columns, rows = coverage_matrix(cells, modes, "agente")
        assert columns == ["promptfoo"]
        assert dict(zip(modes, (r[0] for r in rows), strict=True))["AF1"] == 1
        columns_all, _ = coverage_matrix(cells, modes)
        assert set(columns_all) == {"ragas", "llm-judge", "promptfoo", "metamorphic"}

    def test_command(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(ITEMS, "run-c")
        rec = _rec("r2", "ana", sistema=["rag"], ferramentas=["ragas"], modos_falha=["FP2"])
        append_codings(tmp_path, "run-c", [rec])
        r = CliRunner().invoke(
            app,
            ["analyze", "coverage", "--run", "run-c", "-p", "protocols/v0_rag_agents_testing.yaml"],
        )
        assert r.exit_code == 0, r.stdout
        out = tmp_path / "reports/run-c/analysis"
        assert (
            (out / "coverage_matrix_rag.csv")
            .read_text(encoding="utf-8-sig")
            .startswith("failure_mode;ragas")
        )
        assert "Not addressed by any unit" in r.stdout


ORACLE_UNITS = [
    Unit(id=f"u{i}", source="s", systems=["rag"], oracle="referencia", aggregation="atomico")
    for i in range(9)
] + [
    Unit(id="v", source="s", systems=["agente"], oracle="humano", aggregation="agregado"),
    Unit(id="w", source="s", systems=["agente"], oracle=None, aggregation="nao-identificado"),
    Unit(id="x", source="s", systems=[], methods=["llm-judge", "metamorphic"]),
]


class TestOracles:
    def test_distribution_coded_and_gazetteer_rungs(self) -> None:
        rows = {(r.system, r.rung): r for r in oracle_distribution(ORACLE_UNITS)}
        assert rows[("all", "referencia")].units == 9
        assert rows[("all", "referencia")].share == pytest.approx(0.9)
        assert rows[("agente", "humano")].share == 1.0
        rungs = {"llm-judge": "pseudo-automatico", "metamorphic": "derivado"}
        rows = {(r.system, r.rung): r for r in oracle_distribution(ORACLE_UNITS, rungs)}
        assert rows[("all", "derivado")].units == 1
        assert rows[("all", "pseudo-automatico")].units == 1

    def test_h2(self) -> None:
        t = aggregation_test(ORACLE_UNITS)
        assert (t.atomic, t.aggregated, t.unidentified) == (9, 1, 2)
        assert t.p_value == pytest.approx(11 / 1024, abs=1e-6)
        assert t.supported
        assert not aggregation_test(ORACLE_UNITS, threshold=0.9).supported
        empty = aggregation_test([])
        assert not empty.supported and empty.p_value == 1.0
        assert aggregation_test(ORACLE_UNITS, "agente").proportion_atomic == 0.0

    def test_command(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(ITEMS, "run-o")
        recs = [
            _rec("r2", "ana", sistema=["rag"], tipo_oraculo="referencia", agregacao="atomico"),
            _rec("s1", "ana", sistema=["agente"], tipo_oraculo="humano", agregacao="agregado"),
        ]
        append_codings(tmp_path, "run-o", recs)
        r = CliRunner().invoke(app, ["analyze", "oracles", "--run", "run-o"])
        assert r.exit_code == 0, r.stdout
        with open(
            tmp_path / "reports/run-o/analysis/h2_aggregation.csv", encoding="utf-8-sig"
        ) as fh:
            rows = {row["system"]: row for row in csv.DictReader(fh, delimiter=";")}
        assert rows["all"]["supported"] == "False"  # 1 of 2 atomic
        assert rows["rag"]["atomic"] == "1"


SYSTEM_UNITS = [
    Unit(id="a", source="s", item_ids=["i1"], systems=["rag"], tools={"ragas": "N2"},
         failure_modes=["FP2"], oracle="referencia"),
    Unit(id="b", source="s", item_ids=["i2"], systems=["agente"], tools={"promptfoo": "N1"},
         methods=["trajectory-eval"], failure_modes=["AF1", "FP8"], oracle="referencia"),
    Unit(id="c", source="s", item_ids=["i3", "i4"], systems=["rag", "agente"],
         tools={"promptfoo": "N1"}, failure_modes=["FP8"]),
    Unit(id="d", source="s", item_ids=["i5"], systems=["rag"], tools={"ragas": "N1"}),
]  # fmt: skip


class TestCompare:
    def test_categories_and_summary(self) -> None:
        rows, summaries = compare_systems(SYSTEM_UNITS)
        cat = {(r.kind, r.entry): r for r in rows}
        assert cat[("tool", "ragas")].category == "rag-only"
        assert cat[("tool", "promptfoo")].category == "transfers"
        assert cat[("tool", "promptfoo")].hybrid_units == 1
        assert cat[("method", "trajectory-eval")].category == "agent-only"
        assert cat[("failure_mode", "FP8")].category == "transfers"
        assert cat[("oracle", "referencia")].category == "transfers"
        tools = next(s for s in summaries if s.kind == "tool")
        assert (tools.rag_entries, tools.agente_entries, tools.shared) == (2, 1, 1)
        assert tools.agent_transfer_rate == 1.0
        fm = next(s for s in summaries if s.kind == "failure_mode")
        assert fm.agent_transfer_rate == 0.5  # FP8 shared, AF1 new

    def test_saturation(self) -> None:
        batches = {"i1": 1, "i2": 2, "i3": 3, "i4": 1, "i5": 4}
        sat = saturation(SYSTEM_UNITS, batches, window=1)
        assert [p.batch for p in sat.points] == [1, 2, 4]  # unit c counts in batch 1
        assert sat.points[0].new == ["failure_mode:FP2", "failure_mode:FP8", "tool:promptfoo",
                                     "tool:ragas"]  # fmt: skip
        assert [p.new_entries for p in sat.points] == [4, 2, 0]
        assert sat.saturated and sat.saturated_at == 4
        assert not saturation(SYSTEM_UNITS, batches, window=2).saturated
        assert saturation(SYSTEM_UNITS, batches, window=2, tolerance=2).saturated
        assert not saturation([], {}, window=1).saturated

    def test_command(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(ITEMS, "run-x")
        append_codings(
            tmp_path,
            "run-x",
            [
                _rec("r2", "ana", sistema=["rag"], ferramentas=["ragas"], modos_falha=["FP2"]),
                _rec("s1", "ana", sistema=["agente"], ferramentas=["ragas"], modos_falha=["AF1"]),
            ],
        )
        r = CliRunner().invoke(app, ["analyze", "compare", "--run", "run-x", "--window", "1"])
        assert r.exit_code == 0, r.stdout
        assert "Saturation curve" in r.stdout
        out = tmp_path / "reports/run-x/analysis"
        assert (out / "compare_coding.csv").exists() and (out / "saturation_coding.csv").exists()


def test_frequency_reports_gazetteer_family() -> None:
    units = [Unit(id="a", source="s", tools={"langchain": "N2", "ragas": "N1"})]
    rows = {r.entry: r for r in frequency(units, {"langchain": "framework-sut"})}
    assert rows["langchain"].family == "framework-sut"
    assert rows["ragas"].family == ""


def test_write_csv_formats_lists_tuples_and_dicts(tmp_path: Path) -> None:
    class SampleRow(BaseModel):
        name: str
        tags: list[str]
        bounds: tuple[float, float]
        meta: dict[str, int]

    rows = [
        SampleRow(name="u1", tags=["rag", "eval"], bounds=(0.1, 0.9), meta={"a": 1, "b": 2}),
    ]
    csv_file = tmp_path / "sample.csv"
    write_csv(rows, csv_file)

    with open(csv_file, encoding="utf-8-sig") as fh:
        parsed = list(csv.DictReader(fh, delimiter=";"))
    assert len(parsed) == 1
    assert parsed[0]["tags"] == "rag, eval"
    assert parsed[0]["bounds"] == "0.1, 0.9"
    assert parsed[0]["meta"] == "a=1; b=2"


def test_consensus_cross_field_and_supervisor() -> None:
    rec_a = _rec("r1", "ana", f1="val1")
    rec_b = _rec("r1", "bob", f1="val1", f2="val2")
    res = consensus([rec_a, rec_b])
    assert res["f1"] == "val1"
    assert res["f2"] is None

    rec_sup = _rec("r1", "supervisor_joao", f1="val1", f2="override")
    res_sup = consensus([rec_a, rec_b, rec_sup])
    assert res_sup["f2"] == "override"


def test_units_from_codings_scalar_strings_no_splintering() -> None:
    item = _item("r1", "https://github.com/org/repo")
    codings = {
        ("r1", "ana"): _rec(
            "r1",
            "ana",
            sistema="rag",
            ferramentas="pytest",
            metodos="fuzzing",
            modos_falha="FP1",
        )
    }
    units = units_from_codings([item], codings)
    assert len(units) == 1
    unit = units[0]
    assert unit.systems == ["rag"]
    assert "pytest" in unit.tools
    assert unit.methods == ["fuzzing"]
    assert unit.failure_modes == ["FP1"]


def test_frequency_open_vs_anchored_channels() -> None:
    item_open = _item("r1", "https://github.com/org/repo1")
    item_open.provenance.query_string = "window:2023-01-01..2026-08-31 RAG testing"
    det_open = _det("r1", "ragas", "N1")

    item_anchored = _item("r2", "https://github.com/org/repo2")
    item_anchored.provenance.query_string = '"from ragas import" language:Python'
    det_anchored = _det("r2", "ragas", "N2")

    units = units_from_detections([item_open, item_anchored], [det_open, det_anchored])
    assert len(units) == 2

    all_rows = {r.entry: r for r in frequency(units, channel="all")}
    assert all_rows["ragas"].units == 2
    assert all_rows["ragas"].open_units == 1
    assert all_rows["ragas"].anchored_units == 1

    open_rows = {r.entry: r for r in frequency(units, channel="open")}
    assert open_rows["ragas"].units == 1
    assert open_rows["ragas"].open_units == 1

    anchored_rows = {r.entry: r for r in frequency(units, channel="anchored")}
    assert anchored_rows["ragas"].units == 1
    assert anchored_rows["ragas"].anchored_units == 1


def test_cli_frequency_channel_option(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
    item1 = _item("r1", "https://github.com/org/repo1")
    item1.provenance.query_string = "window:2023-01-01..2026-08-31 RAG testing"
    ItemStorage(tmp_path).save_items([item1], "run-ch")
    det1 = Detection(
        item_id="r1",
        source="github",
        entry_id="ragas",
        entry_type="tool",
        level="N1",
        signal="text",
        location="x",
        evidence="e",
    )
    det_dir = tmp_path / "extract" / "run-ch"
    det_dir.mkdir(parents=True, exist_ok=True)
    with open(det_dir / "detections.jsonl", "w", encoding="utf-8") as fh:
        fh.write(det1.model_dump_json() + "\n")

    runner = CliRunner()
    r = runner.invoke(app, ["analyze", "frequency", "--run", "run-ch", "--channel", "open"])
    assert r.exit_code == 0, r.stdout
    assert "OPEN" in r.stdout
    assert "Open" in r.stdout
