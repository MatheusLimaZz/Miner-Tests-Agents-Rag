"""Screening sheet export/import (Phase 2, T2)."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.extract import Detection
from msrkit.models import Item, ItemKind, Provenance, TermHit
from msrkit.screening import (
    ScreeningCriteria,
    SheetError,
    export_sheet,
    final_decisions,
    import_sheet,
    load_decisions,
    rank_items,
    relevance_score,
)
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

CRIT = ScreeningCriteria(
    inclusion={"I1": "trata de teste", "I2": "janela"},
    exclusion={"E1": "LLM4Testing", "E5": "tutorial genérico"},
    batch_size=2,
)
PROTO = "protocols/v0_rag_agents_testing.yaml"


def _items(n: int = 3) -> list[Item]:
    return [
        Item(
            id=f"it{i}",
            source="hackernews",
            kind=ItemKind.THREAD,
            url=f"https://x.example/{i}",
            title=f"Item {i}",  # type: ignore[arg-type]
            provenance=Provenance(
                run_id="r",
                query_string="q",
                partition="p",
                adapter="h",
                adapter_version="1",
                fetched_at=datetime.now(UTC),
                response_sha256="",
                raw_ref="",
            ),
        )
        for i in range(n)
    ]


def _fill(path: Path, rows: dict[str, dict[str, str]]) -> None:
    with open(path, encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        fields = reader.fieldnames
        data = list(reader)
    for row in data:
        row.update(rows.get(row["item_id"], {}))
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(data)


class TestSheet:
    def test_export_has_criteria_columns_and_batches(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        assert export_sheet(_items(), CRIT, path, coder="ana") == 3
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert {"I1", "I2", "E1", "E5", "decision", "reason", "coder"} <= set(rows[0])
        assert [r["batch"] for r in rows] == ["1", "1", "2"]
        assert rows[0]["coder"] == "ana"

    def test_export_blind_masks_scores_and_terms(self, tmp_path: Path) -> None:
        path = tmp_path / "blind.csv"
        items = _items(2)
        items[0].concepts = ["rag", "teste"]
        items[0].matched_terms = [TermHit(term="rag", field="title", context="rag")]
        ranking = {
            "it0": rank_items(items, None, batch_size=2)["it0"],
            "it1": rank_items(items, None, batch_size=2)["it1"],
        }
        export_sheet(items, CRIT, path, coder="ana", ranking=ranking, blind=True)
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert rows[0]["score"] == ""
        assert rows[0]["evidence"] == ""
        assert rows[0]["concepts"] == ""
        assert rows[0]["matched_terms"] == ""
        assert rows[0]["rank"] == ""
        assert rows[0]["title"] == "Item 0"
        assert rows[0]["coder"] == "ana"

    def test_import_roundtrip_and_blank_rows_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        export_sheet(_items(), CRIT, path, coder="ana")
        _fill(
            path,
            {
                "it0": {"I1": "x", "I2": "x", "decision": "include"},
                "it1": {"E5": "sim", "decision": "exclude"},
            },
        )
        decisions = import_sheet(path, CRIT, {"it0", "it1", "it2"})
        assert [(d.item_id, d.decision, d.criteria_met) for d in decisions] == [
            ("it0", "include", ["I1", "I2"]),
            ("it1", "exclude", ["E5"]),
        ]
        assert decisions.skipped == ["line 4 (it2)"]

    def test_import_sheet_portuguese_aliases(self, tmp_path: Path) -> None:
        path = tmp_path / "pt.csv"
        export_sheet(_items(), CRIT, path, coder="ana")
        _fill(
            path,
            {
                "it0": {"I1": "x", "I2": "x", "decision": "incluir"},
                "it1": {"E5": "x", "decision": "excluir"},
                "it2": {"decision": "incerteza"},
            },
        )
        decisions = import_sheet(path, CRIT, {"it0", "it1", "it2"})
        assert [(d.item_id, d.decision) for d in decisions] == [
            ("it0", "include"),
            ("it1", "exclude"),
            ("it2", "uncertain"),
        ]

    def test_inconsistent_rows_block_the_whole_import(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        export_sheet(_items(), CRIT, path, coder="ana")
        _fill(
            path,
            {
                "it0": {"E1": "x", "decision": "include"},
                "it1": {"decision": "exclude"},
                "it2": {"decision": "maybe"},
            },
        )
        with pytest.raises(SheetError) as err:
            import_sheet(path, CRIT, {"it0", "it1", "it2"})
        assert len(err.value.problems) == 3

    def test_final_decision_is_uncertain_on_disagreement(self) -> None:
        from msrkit.screening import ScreeningDecision

        def d(item: str, coder: str, dec: str) -> ScreeningDecision:
            return ScreeningDecision(
                item_id=item,
                coder=coder,
                decision=dec,  # type: ignore[arg-type]
                decided_at="t",
                sheet_sha256="h",
            )

        latest = {
            ("a", "ana"): d("a", "ana", "include"),
            ("a", "bia"): d("a", "bia", "include"),
            ("b", "ana"): d("b", "ana", "include"),
            ("b", "bia"): d("b", "bia", "exclude"),
        }
        assert final_decisions(latest) == {"a": "include", "b": "uncertain"}

    def test_final_decision_consensus_resolves_disagreement(self) -> None:
        from msrkit.screening import ScreeningDecision

        def d(item: str, coder: str, dec: str) -> ScreeningDecision:
            return ScreeningDecision(
                item_id=item,
                coder=coder,
                decision=dec,  # type: ignore[arg-type]
                decided_at="t",
                sheet_sha256="h",
            )

        latest = {
            ("a", "ana"): d("a", "ana", "include"),
            ("a", "bia"): d("a", "bia", "exclude"),
            ("a", "consensus"): d("a", "consensus", "include"),
            ("b", "ana"): d("b", "ana", "include"),
            ("b", "bia"): d("b", "bia", "exclude"),
            ("b", "consenso"): d("b", "consenso", "exclude"),
        }
        assert final_decisions(latest) == {"a": "include", "b": "exclude"}


class TestScreenCommands:
    def test_export_import_status(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(_items(), "run-s")
        runner = CliRunner()
        sheet = tmp_path / "ana.csv"
        r = runner.invoke(
            app,
            ["screen", "export", "--run", "run-s", "-p", PROTO, "--coder", "ana", "-o", str(sheet)],
        )
        assert r.exit_code == 0, r.stdout
        _fill(
            sheet,
            {"it0": {"I1": "x", "decision": "include"}, "it1": {"E3": "x", "decision": "exclude"}},
        )
        r = runner.invoke(app, ["screen", "import", str(sheet), "--run", "run-s", "-p", PROTO])
        assert r.exit_code == 0, r.stdout
        assert len(load_decisions(tmp_path, "run-s")) == 2

        r = runner.invoke(app, ["screen", "status", "--run", "run-s"])
        assert "Pending (no decision): 1" in r.stdout

        r = runner.invoke(
            app,
            [
                "screen",
                "export",
                "--run",
                "run-s",
                "-p",
                PROTO,
                "--coder",
                "ana",
                "-o",
                str(tmp_path / "next.csv"),
            ],
        )
        assert "1 items written" in r.stdout  # already-decided items are skipped

    def test_export_blind_and_random_cli(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(_items(5), "run-blind")
        runner = CliRunner()
        sheet = tmp_path / "blind_sheet.csv"
        r = runner.invoke(
            app,
            [
                "screen",
                "export",
                "--run",
                "run-blind",
                "-p",
                PROTO,
                "--coder",
                "helena",
                "--order",
                "random",
                "--blind",
                "-o",
                str(sheet),
            ],
        )
        assert r.exit_code == 0, r.stdout
        assert "blind: yes" in r.stdout
        with open(sheet, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert len(rows) == 5
        assert all(r["score"] == "" and r["matched_terms"] == "" for r in rows)

    def test_export_blind_forces_random_order(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        items = _items(5)
        ItemStorage(tmp_path).save_items(items, "run-blind-force")
        sheet = tmp_path / "blind_forced.csv"
        # Sem passar --order, o padrão é 'relevance', mas --blind deve forçar 'random'
        r = CliRunner().invoke(
            app,
            [
                "screen",
                "export",
                "--run",
                "run-blind-force",
                "--coder",
                "helena",
                "--blind",
                "-o",
                str(sheet),
            ],
        )
        assert r.exit_code == 0, r.stdout
        assert "ordenação por relevância forçada para 'random'" in r.stdout
        normalized_out = " ".join(r.stdout.split())
        assert "order: random" in normalized_out
        assert "blind: yes" in r.stdout


class TestRanking:
    def _ranked_items(self) -> list[Item]:
        items = _items(4)
        items[1].concepts = ["rag", "teste"]
        items[1].matched_terms = [
            TermHit(term=t, field="title", context=t) for t in ("rag", "eval")
        ]
        items[2].concepts = ["agente"]
        items[3].tech.has_ci = True
        return items

    def test_score_components(self) -> None:
        items = self._ranked_items()
        assert relevance_score(items[0]) == (0.0, "")
        assert relevance_score(items[1]) == (6.0, "")  # 2 terms + system + testing
        det = Detection(
            item_id="it2", source="github", entry_id="ragas", entry_type="tool",
            level="N3", signal="ci", location="ci.yml", evidence="ragas",
        )  # fmt: skip
        assert relevance_score(items[2], [det]) == (7.0, "N3")

    def test_rank_is_deterministic_and_batches_fixed(self) -> None:
        items = self._ranked_items()
        ranking = rank_items(items, None, batch_size=2)
        order = [r.item_id for r in sorted(ranking.values(), key=lambda r: r.rank)]
        assert order == ["it1", "it2", "it3", "it0"]
        assert [ranking[i].batch for i in order] == [1, 1, 2, 2]
        assert rank_items(list(reversed(items)), None, batch_size=2) == ranking

    def test_export_follows_ranking_and_filters_by_batch(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(self._ranked_items(), "run-r")
        sheet = tmp_path / "b2.csv"
        r = CliRunner().invoke(
            app,
            ["screen", "export", "--run", "run-r", "-p", PROTO, "-b", "2", "-o", str(sheet)],
        )
        assert r.exit_code == 0, r.stdout
        with open(sheet, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert rows == []  # protocol batch_size is 25: all four items are in batch 1
        r = CliRunner().invoke(
            app, ["screen", "export", "--run", "run-r", "-p", PROTO, "-o", str(sheet)]
        )
        with open(sheet, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert [row["item_id"] for row in rows] == ["it1", "it2", "it3", "it0"]
        assert [row["rank"] for row in rows] == ["1", "2", "3", "4"]

    def test_invalid_order_rejected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(_items(), "run-o")
        r = CliRunner().invoke(app, ["screen", "export", "--run", "run-o", "--order", "x"])
        assert r.exit_code == 1


class TestInteractiveScreening:
    def test_interactive_screening_records_decisions(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        items = _items(2)
        ItemStorage(tmp_path).save_items(items, "run-int")
        # Simula resposta interativa: item 1 -> 'i' (incluir), item 2 -> 'q' (sair)
        r = CliRunner().invoke(
            app,
            ["screen", "interactive", "--run", "run-int", "--coder", "joao", "-p", PROTO],
            input="i\nq\n",
        )
        assert r.exit_code == 0, r.stdout
        assert "Iniciando triagem interativa" in r.stdout
        assert "1 decisões gravadas com sucesso" in r.stdout
        decisions = load_decisions(tmp_path, "run-int")
        assert (items[0].id, "joao") in decisions
        assert decisions[(items[0].id, "joao")].decision == "include"

    def test_interactive_screening_blind(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        items = _items(2)
        items[0].matched_terms = [TermHit(term="rag", field="title", context="rag")]
        ItemStorage(tmp_path).save_items(items, "run-blind-int")
        r = CliRunner().invoke(
            app,
            [
                "screen",
                "interactive",
                "--run",
                "run-blind-int",
                "--coder",
                "helena",
                "-p",
                PROTO,
                "--blind",
            ],
            input="i\nq\n",
        )
        assert r.exit_code == 0, r.stdout
        assert "Modo Cego Ativado" in r.stdout
        assert "Scores e Sinais Ocultados" in r.stdout
        assert "Escore" not in r.stdout
        assert "Termos casados:" not in r.stdout
        decisions = load_decisions(tmp_path, "run-blind-int")
        assert (items[0].id, "helena") in decisions or (items[1].id, "helena") in decisions


def test_final_decisions_fuzzy_consensus_coder() -> None:
    from msrkit.screening import ScreeningDecision, final_decisions

    dec1 = ScreeningDecision(
        item_id="i1",
        coder="ana",
        decision="include",
        criteria_met=["I1"],
        decided_at="2026-10-14T00:00:00Z",
        sheet_sha256="test",
    )
    dec2 = ScreeningDecision(
        item_id="i1",
        coder="bia",
        decision="exclude",
        criteria_met=["E1"],
        decided_at="2026-10-14T00:00:00Z",
        sheet_sha256="test",
    )
    # Disagreement between ana and bia
    assert final_decisions({("i1", "ana"): dec1, ("i1", "bia"): dec2})["i1"] == "uncertain"

    # Tiebreaker by supervisor_joao
    dec3 = ScreeningDecision(
        item_id="i1",
        coder="supervisor_joao",
        decision="include",
        criteria_met=["I1"],
        decided_at="2026-10-14T00:00:00Z",
        sheet_sha256="test",
    )
    decisions = {("i1", "ana"): dec1, ("i1", "bia"): dec2, ("i1", "supervisor_joao"): dec3}
    assert final_decisions(decisions)["i1"] == "include"


def test_is_consensus_coder_detection() -> None:
    from msrkit.screening import is_consensus_coder

    assert is_consensus_coder("consenso")
    assert is_consensus_coder("supervisor_ana")
    assert is_consensus_coder("terceiro_revisor")
    assert is_consensus_coder("tiebreaker")
    assert not is_consensus_coder("ana")
    assert not is_consensus_coder("joao_silva")


def test_load_decisions_handles_unicode_line_separator(tmp_path: Path) -> None:
    """JSONL containing \u2028 inside string fields must not crash line parsing."""
    from msrkit.screening import ScreeningDecision, load_decisions

    run_dir = tmp_path / "screening" / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)
    dec_file = run_dir / "decisions.jsonl"
    record = ScreeningDecision(
        item_id="item-u2028",
        coder="ana",
        decision="include",
        criteria_met=["I1"],
        reason="note with \u2028 unicode separator",
        decided_at="2026-10-14T00:00:00Z",
        sheet_sha256="abc",
    )
    dec_file.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    loaded = load_decisions(tmp_path, "r1")
    assert ("item-u2028", "ana") in loaded
    assert loaded[("item-u2028", "ana")].reason == "note with \u2028 unicode separator"


def test_import_sheet_handles_multiline_quoted_csv_cell(tmp_path: Path) -> None:
    """Quoted CSV fields containing embedded newlines must not break sheet importing."""
    sheet = tmp_path / "sheet.csv"
    sheet.write_text(
        "item_id;decision;criteria_met;coder;notes\n"
        'it0;include;I1;ana;"first line\nsecond line in note"\n',
        encoding="utf-8-sig",
    )
    decisions = import_sheet(sheet, criteria=CRIT, known_ids={"it0"})
    assert len(decisions) == 1
    assert decisions[0].item_id == "it0"
    assert decisions[0].coder == "ana"
