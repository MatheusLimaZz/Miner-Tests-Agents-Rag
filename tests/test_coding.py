"""Extraction form (Annex A) and quality columns (§9) (Phase 2, T4)."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.coding import (
    CodingField,
    CodingForm,
    export_form,
    import_form,
    load_codings,
    suggestions,
)
from msrkit.config import load_protocol
from msrkit.extract import Detection
from msrkit.gazetteer import Gazetteer, MethodEntry, ToolEntry
from msrkit.models import Item, ItemKind, Provenance
from msrkit.screening import SheetError
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

PROTO = "protocols/v0_rag_agents_testing.yaml"
GAZ = Gazetteer(
    version="t",
    tools=[ToolEntry(id="ragas", name="Ragas", family="eval")],
    methods=[MethodEntry(id="llm-judge", name="LLM-as-a-judge")],
)
FORM = CodingForm(
    failure_modes={"FP1": "a", "AF1": "b"},
    fields=[
        CodingField(name="sistema", type="multi", values=["rag", "agente"], required=True,
                    prefill="auto_sistema"),
        CodingField(name="ferramentas", type="multi", vocabulary="tools",
                    prefill="auto_ferramentas"),
        CodingField(name="modos_falha", type="multi", vocabulary="failure_modes"),
        CodingField(name="agregacao", type="enum", values=["atomico", "agregado"]),
        CodingField(name="evidencia_textual", type="text", required=True, max_length=20),
        CodingField(name="nao_cobre", type="bool"),
    ],
)  # fmt: skip


def _item(i: int, url: str | None = None, kind: ItemKind | None = None) -> Item:
    default_kind = ItemKind.REPO if url else ItemKind.THREAD
    return Item(
        id=f"it{i}",
        source="github" if url else "hackernews",
        kind=kind or default_kind,
        url=url or f"https://x.example/{i}",  # type: ignore[arg-type]
        title=f"Item {i}",
        concepts=["rag", "teste"],
        provenance=Provenance(
            run_id="r",
            query_string="q",
            partition="p",
            adapter="a",
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),
    )


def _rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        return list(reader.fieldnames or []), list(reader)


def _fill(path: Path, values: dict[str, dict[str, str]]) -> None:
    fields, rows = _rows(path)
    for row in rows:
        row.update(values.get(row["item_id"], {}))
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


class TestForm:
    def test_protocol_declares_annex_a_and_quality(self) -> None:
        form = load_protocol(PROTO).coding
        names = {f.name for f in form.fields}
        assert {"sistema", "nivel_evidencia", "evidencia_textual", "confianca"} <= names
        assert {"q_autoridade", "q_objetividade", "q_impacto"} <= names
        assert len(form.failure_modes) == 25  # FP1–FP13, AF1–AF12

    def test_invalid_field_declarations(self) -> None:
        with pytest.raises(ValueError, match="needs values"):
            CodingField(name="x", type="enum")
        with pytest.raises(ValueError, match="prefill"):
            CodingField(name="x", prefill="nope")
        with pytest.raises(ValueError, match="reserved"):
            CodingForm(fields=[CodingField(name="coder")])


class TestSuggestions:
    def test_repo_level_detections_shared_by_items_of_the_repo(self) -> None:
        repo = _item(1, "https://github.com/o/r")
        code = _item(2, "https://github.com/o/r/blob/main/eval.py", kind=ItemKind.CODE)
        dets = [
            Detection(item_id="it1", source="github", repo="o/r", entry_id="ragas",
                      entry_type="tool", level="N3", signal="ci", location="ci.yml",
                      evidence="run: ragas"),
            Detection(item_id="it2", source="github", repo="o/r", entry_id="llm-judge",
                      entry_type="method", level="N1", signal="text", location="body",
                      evidence="judge"),
        ]  # fmt: skip
        auto = suggestions([repo, code], dets, ("rag", "agente"))
        assert auto["it2"]["auto_nivel"] == "N3"
        assert auto["it2"]["auto_ferramentas"] == "ragas"
        assert auto["it2"]["auto_metodos"] == "llm-judge"
        assert auto["it1"]["auto_metodos"] == ""  # text mentions are not shared
        assert auto["it1"]["auto_sistema"] == "rag"

    def test_issues_do_not_inherit_repo_level_ci_or_unrelated_tools(self) -> None:
        repo = _item(1, "https://github.com/guardrails-ai/guardrails")
        issue = _item(
            2,
            "https://github.com/guardrails-ai/guardrails/issues/168",
            kind=ItemKind.ISSUE,
        )
        repo_dets = [
            Detection(
                item_id="it1",
                source="github",
                repo="guardrails-ai/guardrails",
                entry_id="langsmith",
                entry_type="tool",
                level="N3",
                signal="ci",
                location=".github/workflows/ci.yml",
                evidence="run: langsmith test",
            )
        ]
        auto_isolated = suggestions([repo, issue], repo_dets, ("rag", "agente"))
        assert auto_isolated["it2"]["auto_nivel"] == ""
        assert auto_isolated["it2"]["auto_ferramentas"] == ""

        issue_det = Detection(
            item_id="it2",
            source="github",
            repo="guardrails-ai/guardrails",
            entry_id="deepeval",
            entry_type="tool",
            level="N1",
            signal="text",
            location="body",
            evidence="using deepeval",
        )
        auto_with_item = suggestions([repo, issue], repo_dets + [issue_det], ("rag", "agente"))
        assert auto_with_item["it2"]["auto_nivel"] == "N1"
        assert auto_with_item["it2"]["auto_ferramentas"] == "deepeval"
        assert "langsmith" not in auto_with_item["it2"]["auto_ferramentas"]

    def test_repo_items_skip_self_repo_tool_detections_when_gazetteer_passed(self) -> None:
        gaz = Gazetteer(
            version="1",
            tools=[
                ToolEntry(
                    id="garak",
                    name="Garak",
                    family="vulnerability",
                    repos=["NVIDIA/garak"],
                ),
                ToolEntry(id="pytest", name="Pytest", family="test-runner"),
            ],
        )
        repo = _item(1, "https://github.com/NVIDIA/garak", kind=ItemKind.REPO)
        code = _item(
            2,
            "https://github.com/NVIDIA/garak/blob/main/tests/test_run.py",
            kind=ItemKind.CODE,
        )
        dets = [
            Detection(
                item_id="it1",
                source="github",
                repo="NVIDIA/garak",
                entry_id="garak",
                entry_type="tool",
                level="N3",
                signal="config",
                location="garak.yaml",
                evidence="garak config",
            ),
            Detection(
                item_id="it1",
                source="github",
                repo="NVIDIA/garak",
                entry_id="pytest",
                entry_type="tool",
                level="N3",
                signal="ci",
                location=".github/workflows/ci.yml",
                evidence="pytest",
            ),
        ]
        auto = suggestions([repo, code], dets, ("rag", "agente"), gazetteer=gaz)
        assert auto["it2"]["auto_ferramentas"] == "pytest"
        assert "garak" not in auto["it2"]["auto_ferramentas"]

        auto_no_gaz = suggestions([repo, code], dets, ("rag", "agente"), gazetteer=None)
        assert "garak" in auto_no_gaz["it2"]["auto_ferramentas"]
        assert "pytest" in auto_no_gaz["it2"]["auto_ferramentas"]

    def test_issues_ignore_legacy_structural_detections_and_suppress_self_repo_tools(
        self,
    ) -> None:
        gaz = Gazetteer(
            version="1",
            tools=[
                ToolEntry(
                    id="guardrails-ai",
                    name="Guardrails AI",
                    family="guardrail",
                    repos=["guardrails-ai/guardrails"],
                ),
                ToolEntry(
                    id="garak",
                    name="Garak",
                    family="vulnerability",
                    repos=["NVIDIA/garak"],
                ),
                ToolEntry(id="pytest", name="Pytest", family="test-runner"),
            ],
        )
        issue_guardrails = _item(
            1,
            "https://github.com/guardrails-ai/guardrails/issues/100",
            kind=ItemKind.ISSUE,
        )
        dets_guardrails = [
            Detection(
                item_id="it1",
                source="github",
                repo="guardrails-ai/guardrails",
                entry_id="guardrails-ai",
                entry_type="tool",
                level="N1",
                signal="text",
                location="body",
                evidence="guardrails-ai mention",
            ),
            Detection(
                item_id="it1",
                source="github",
                repo="guardrails-ai/guardrails",
                entry_id="ci",
                entry_type="tool",
                level="N3",
                signal="ci",
                location=".github/workflows/ci.yml",
                evidence="ci workflow",
            ),
            Detection(
                item_id="it1",
                source="github",
                repo="guardrails-ai/guardrails",
                entry_id="pytest",
                entry_type="tool",
                level="N1",
                signal="text",
                location="body",
                evidence="pytest mention",
            ),
        ]
        auto_g = suggestions([issue_guardrails], dets_guardrails, ("rag", "agente"), gazetteer=gaz)
        assert auto_g["it1"]["auto_ferramentas"] == "pytest"
        assert auto_g["it1"]["auto_nivel"] == "N1"

        issue_garak = _item(
            2,
            "https://github.com/NVIDIA/garak/issues/50",
            kind=ItemKind.ISSUE,
        )
        dets_garak = [
            Detection(
                item_id="it2",
                source="github",
                repo="NVIDIA/garak",
                entry_id="garak",
                entry_type="tool",
                level="N1",
                signal="text",
                location="body",
                evidence="garak mention",
            ),
        ]
        auto_garak = suggestions([issue_garak], dets_garak, ("rag", "agente"), gazetteer=gaz)
        assert auto_garak["it2"]["auto_ferramentas"] == ""
        assert auto_garak["it2"]["auto_nivel"] == ""


class TestSheet:
    def test_prefill_is_opt_in(self, tmp_path: Path) -> None:
        auto = {"it0": {"auto_sistema": "rag", "auto_ferramentas": "ragas"}}
        blank, primed = tmp_path / "a.csv", tmp_path / "b.csv"
        export_form([_item(0)], FORM, blank, auto=auto)
        export_form([_item(0)], FORM, primed, auto=auto, prefill=True)
        assert _rows(blank)[1][0]["sistema"] == ""
        assert _rows(blank)[1][0]["auto_sistema"] == "rag"
        assert _rows(primed)[1][0]["ferramentas"] == "ragas"

    def test_import_parses_types_and_skips_blank_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "f.csv"
        export_form([_item(0), _item(1)], FORM, path, coder="ana")
        _fill(
            path,
            {
                "it0": {
                    "sistema": "RAG | agente",
                    "ferramentas": "Ragas",
                    "modos_falha": "fp1, AF1",
                    "agregacao": "atômico",
                    "evidencia_textual": "uses ragas in CI",
                    "nao_cobre": "não",
                }
            },
        )
        (rec,) = import_form(path, FORM, {"it0", "it1"}, GAZ)
        assert rec.coder == "ana"
        assert rec.values == {
            "sistema": ["rag", "agente"],
            "ferramentas": ["ragas"],
            "modos_falha": ["FP1", "AF1"],
            "agregacao": "atomico",
            "evidencia_textual": "uses ragas in CI",
            "nao_cobre": False,
        }

    def test_invalid_rows_block_the_import(self, tmp_path: Path) -> None:
        path = tmp_path / "f.csv"
        export_form([_item(0), _item(1)], FORM, path, coder="ana")
        _fill(
            path,
            {
                "it0": {"sistema": "rag", "ferramentas": "deepeval", "evidencia_textual": "x"},
                "it1": {"sistema": "rag", "evidencia_textual": "x" * 21, "nao_cobre": "talvez"},
            },
        )
        with pytest.raises(SheetError) as err:
            import_form(path, FORM, {"it0", "it1"}, GAZ)
        text = " ".join(err.value.problems)
        assert "unknown value 'deepeval'" in text
        assert "longer than 20" in text
        assert "yes/no" in text

    def test_required_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "f.csv"
        export_form([_item(0)], FORM, path, coder="ana")
        _fill(path, {"it0": {"agregacao": "agregado"}})
        with pytest.raises(SheetError, match="2 problem"):
            import_form(path, FORM, {"it0"}, GAZ)


class TestCodingCommands:
    def test_export_needs_screening_then_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        from msrkit.screening import ScreeningDecision, append_decisions

        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items([_item(0), _item(1)], "run-c")
        runner = CliRunner()
        sheet = tmp_path / "form.csv"
        args = ["coding", "export", "--run", "run-c", "-p", PROTO, "--coder", "ana"]
        r = runner.invoke(app, [*args, "-o", str(sheet)])
        assert r.exit_code == 1
        assert "No screening decisions" in r.stdout

        append_decisions(
            tmp_path,
            "run-c",
            [
                ScreeningDecision(item_id="it0", coder="ana", decision="include",
                                  decided_at="t", sheet_sha256="h"),
                ScreeningDecision(item_id="it1", coder="ana", decision="exclude",
                                  reason="off", decided_at="t", sheet_sha256="h"),
            ],
        )  # fmt: skip
        r = runner.invoke(app, [*args, "-o", str(sheet)])
        assert r.exit_code == 0, r.stdout
        assert [row["item_id"] for row in _rows(sheet)[1]] == ["it0"]

        _fill(
            sheet,
            {
                "it0": {
                    "sistema": "rag",
                    "nivel_evidencia": "N1",
                    "evidencia_textual": "we test retrieval",
                    "confianca": "média",
                    "q_objetividade": "sem-vinculo",
                }
            },
        )
        r = runner.invoke(app, ["coding", "import", str(sheet), "--run", "run-c", "-p", PROTO])
        assert r.exit_code == 0, r.stdout
        rec = load_codings(tmp_path, "run-c")[("it0", "ana")]
        assert rec.values["confianca"] == "media"

        r = runner.invoke(app, ["coding", "status", "--run", "run-c"])
        assert "Coded items: 1 · double-coded: 0" in r.stdout
        r = runner.invoke(app, [*args, "-o", str(tmp_path / "next.csv")])
        assert "0 items written" in r.stdout


def test_import_multi_field_semicolon_and_pipe_separators(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    export_form([_item(0)], FORM, sheet, coder="coder1")
    _fill(
        sheet,
        {
            "it0": {
                "sistema": "rag; agente",
                "evidencia_textual": "tested",
            }
        },
    )
    records = import_form(sheet, FORM, {"it0"}, GAZ)
    assert len(records) == 1
    assert set(records[0].values["sistema"]) == {"rag", "agente"}
