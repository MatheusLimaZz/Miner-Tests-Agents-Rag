"""Mining report of a run (Phase 3, R6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.enrich import RepoSignals
from msrkit.extract import Detection
from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Item,
    ItemKind,
    Manifest,
    Provenance,
    QueryManifestEntry,
    SourceManifestEntry,
    TermHit,
)
from msrkit.provenance import save_manifest
from msrkit.report import _duration, _n, build_report, render_markdown
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

PROV = Provenance(
    run_id="run-r", query_string="q", partition="p", adapter="a", adapter_version="1",
    fetched_at=datetime(2026, 10, 1, tzinfo=UTC), response_sha256="", raw_ref="",
)  # fmt: skip


def _item(
    i: int,
    source: str,
    kind: ItemKind = ItemKind.THREAD,
    created: str | None = "2025-03-01",
    terms: tuple[tuple[str, str], ...] = (),
    concepts: tuple[str, ...] = (),
) -> Item:
    return Item(
        id=f"{source[:2]}{i}",
        source=source,
        kind=kind,
        url=f"https://x.example/{source}/{i}",  # type: ignore[arg-type]
        created_at=datetime.fromisoformat(f"{created}T12:00:00+00:00") if created else None,
        matched_terms=[TermHit(term=t, field="title", context="", lang=lang) for t, lang in terms],
        concepts=list(concepts),
        provenance=PROV,
    )


def _items() -> list[Item]:
    return [
        _item(0, "github", ItemKind.CODE, created=None),
        _item(1, "github", ItemKind.ISSUE, terms=(("RAG testing", "en"),), concepts=("rag",)),
        _item(2, "github", ItemKind.ISSUE, created="2022-05-01", terms=(("RAG testing", "en"),)),
        _item(3, "hackernews", terms=(("teste de RAG", "pt"),), concepts=("rag", "teste")),
        _item(4, "hackernews", created="2024-07-01"),  # removed as a duplicate below
    ]


def _q(items: int, *, discarded: int = 0, reasons: tuple[str, ...] = ()) -> QueryManifestEntry:
    return QueryManifestEntry(
        query_string="q", partitions=1, requests=2, items=items, truncated=bool(reasons),
        truncation_reasons=list(reasons), discarded=discarded, completed=True,
    )  # fmt: skip


def _manifest() -> Manifest:
    ok = Availability(status=AvailabilityStatus.OK, reason="ok")
    return Manifest(
        run_id="run-r",
        msrkit_version="t",
        protocol_path="protocols/p.yaml",
        protocol_sha256="abcdef0123456789",
        started_at=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 10, 1, 11, 30, tzinfo=UTC),
        effective_protocol={
            "window": {"since": "2023-01-01", "until": "2026-08-31"},
            "terms": ["RAG testing", "LLM as a judge"],
            "terms_by_language": {"pt": ["teste de RAG"]},
            "census_sources": ["hackernews"],
            "strata": "year",
        },
        run_options={"per_query_limit": 10},
        sources=[
            SourceManifestEntry(
                name="github", adapter_version="1", availability=ok,
                planned=[{"raw": True, "label": "ci:ragas"}, {"raw": True, "label": "ci:deepeval"},
                         {"raw": False, "terms": ["RAG testing"]}],
                queries=[_q(2, reasons=("item_limit",)), _q(1)],
            ),
            SourceManifestEntry(
                name="hackernews", adapter_version="1", availability=ok,
                planned=[{"terms": ["RAG testing"]}],
                queries=[_q(2, discarded=3)],
            ),
            SourceManifestEntry(
                name="linkedin", adapter_version="1",
                availability=Availability(status=AvailabilityStatus.UNSUPPORTED, reason="no API"),
            ),
        ],
    )  # fmt: skip


def _evidence() -> tuple[dict[str, RepoSignals], list[Detection]]:
    signals = {
        "o/a": RepoSignals(repo="o/a", has_ci=True, has_tests=True),
        "o/b": RepoSignals(repo="o/b", has_tests=True, reused_from="run-0"),
    }

    def det(item: str, repo: str | None, level: str, entry: str = "ragas") -> Detection:
        kind = "method" if entry == "llm-as-judge" else "tool"
        return Detection(
            item_id=item, source="github", repo=repo, entry_id=entry,
            entry_type=kind, level=level, signal="text", location="title", evidence="e",  # type: ignore[arg-type]
        )  # fmt: skip

    dets = [
        det("gi0", "o/a", "N2"),
        det("gi1", "o/a", "N3"),  # same unit as gi0: counted once, at N3
        det("hn3", None, "N1"),
        det("hn3", None, "N1", "llm-as-judge"),
    ]
    return signals, dets


def _report(tmp_path: Path, **kwargs: object):  # type: ignore[no-untyped-def]
    items = _items()
    discards = tmp_path / "discarded.jsonl"
    discards.write_text((json.dumps({"reason": "no_term_match"}) + "\n") * 3)
    args = {
        "manifest": _manifest(),
        "dedupe_report": {"removed": {"url": 1, "content": 0, "near": 0}},
        **kwargs,
    }
    return build_report(
        "run-r", args["manifest"], items, items[:4], args["dedupe_report"], discards,  # type: ignore[arg-type]
        signals=args.get("signals"), detections=args.get("detections"),  # type: ignore[arg-type]
        protocol_window=args.get("protocol_window"),  # type: ignore[arg-type]
        earlier_urls=args.get("earlier_urls"), earlier_runs=args.get("earlier_runs", 0),  # type: ignore[arg-type]
        extract_stats=args.get("extract_stats"),  # type: ignore[arg-type]
    )  # fmt: skip


class TestSamplingAndWhatWasLeftOut:
    """Sampling fractions of the capped queries and what the extraction refused (ADR-056/058)."""

    def _sampled_manifest(self) -> Manifest:
        from datetime import date

        from msrkit.models import Query
        from msrkit.partition import query_key

        def stratum(term: str, year: int, items: int, total: int | None, *reasons: str):
            q = Query(source="github", terms=[term], kind="issue",
                      since=date(year, 1, 1), until=date(year, 12, 31))  # fmt: skip
            entry = QueryManifestEntry(
                query_string=term, partitions=1, requests=1, items=items, truncated=bool(reasons),
                truncation_reasons=list(reasons), estimated_total=total, key=query_key(q),
                completed=True,
            )  # fmt: skip
            return q.model_dump(mode="json"), entry

        m = _manifest()
        parts = [
            stratum("LLM evaluation", 2026, 100, 5000, "item_limit"),
            stratum("agent testing", 2026, 100, None, "item_limit"),
            stratum("LLM test oracle", 2023, 4, 4),
        ]
        m.sources[0].planned = [p for p, _ in parts]
        m.sources[0].queries = [e for _, e in parts]
        return m

    def test_sampling_fractions_weights_and_warning(self, tmp_path: Path) -> None:
        r = _report(tmp_path, manifest=self._sampled_manifest())
        s = r.sampling
        assert (s.cut_short, s.uncounted, s.not_examined) == (2, 1, 4900)
        assert s.max_weight == 50 and s.complete_groups >= 1
        (row,) = s.rows
        assert (row.source, row.kind, row.family, row.year) == ("github", "issue", "termo", 2026)
        # One query of the group has no total: the group's size and fraction are unknown,
        # not the fraction of the query that happens to have one.
        assert (row.cut_short, row.unknown, row.examined, row.available) == (2, 1, 200, None)
        assert row.fraction is None and row.not_retrieved == 4900
        md = render_markdown(r)
        assert "## Amostragem" in md
        assert (
            "| github | issue | termo | 2026 | 2/2 (1 sem total) | 200 | — | desconhecida | 4.900 |"
            in md
        )
        w = "\n".join(r.warnings)
        assert "deixaram de examinar cerca de 4.900 registros" in w
        assert "1 consultas truncadas não têm total informado pela fonte" in w

    def test_no_sampling_section_without_capped_queries(self, tmp_path: Path) -> None:
        m = _manifest()
        m.sources[0].queries[0].truncation_reasons = []
        assert "## Amostragem" not in render_markdown(_report(tmp_path, manifest=m))

    def test_what_the_extraction_left_out_is_listed(self, tmp_path: Path) -> None:
        signals, dets = _evidence()
        stats = {
            "ci_hits_verified": 416, "ci_hits_rejected": 1175, "ci_hits_unverified": 395,
            "repos_without_artifact_item": 2072, "self_copies": 130, "dropped_ambiguous": 245,
        }  # fmt: skip
        r = _report(tmp_path, signals=signals, detections=dets, extract_stats=stats)
        assert r.evidence is not None and r.evidence.left_out == stats
        md = render_markdown(r)
        assert "416 com a ferramenta chamada num passo `run:`/`uses:`" in md
        assert "1.175 só a citam fora de passo executável" in md
        assert "2.072 repositórios conhecidos só por issues" in md
        assert "130 pares repositório–ferramenta" in md


class TestBuildReport:
    def test_window_terms_and_options(self, tmp_path: Path) -> None:
        r = _report(tmp_path)
        assert (r.since, r.until) == ("2023-01-01", "2026-08-31")
        assert r.terms == {"en": ["RAG testing", "LLM as a judge"], "pt": ["teste de RAG"]}
        assert r.run_options == {"per_query_limit": 10}
        assert r.duration_s == 5400
        assert not r.window_overridden

    def test_sources(self, tmp_path: Path) -> None:
        r = _report(tmp_path)
        gh, hn, li = r.sources
        assert (gh.planned, gh.executed, gh.not_executed) == (3, 2, 1)
        assert (gh.requests, gh.returned, gh.unique) == (4, 3, 3)
        assert gh.truncated == {"item_limit": 1}
        assert gh.literal == {"ci": 2}
        assert (hn.returned, hn.discarded, hn.unique) == (2, 3, 1)
        assert (li.status, li.executed) == ("UNSUPPORTED", 0)

    def test_counts_match_prisma_identification(self, tmp_path: Path) -> None:
        r = _report(tmp_path)
        assert r.identified == 8  # github 3 + hackernews 2 kept + 3 filtered
        assert r.filtered == {"no_term_match": 3}
        assert r.kept == 5
        assert r.duplicates == {"url": 1, "content": 0, "near": 0}
        assert r.unique == 4
        assert r.by_source_kind == {"github": {"code": 1, "issue": 2}, "hackernews": {"thread": 1}}

    def test_dates_terms_concepts(self, tmp_path: Path) -> None:
        r = _report(tmp_path)
        assert r.by_year == {"2022": 1, "2025": 2}
        assert (r.undated, r.oldest, r.newest) == (1, "2022-05-01", "2025-03-01")
        assert r.out_of_window == {"github": 1}
        assert r.term_hits == {"RAG testing": 2, "teste de RAG": 1}
        assert r.term_languages == {"en": 2, "": 1, "pt": 1}
        assert r.concepts == {"": 2, "rag": 2, "teste": 1}

    def test_evidence_counts_units_at_highest_level(self, tmp_path: Path) -> None:
        signals, dets = _evidence()
        r = _report(tmp_path, signals=signals, detections=dets)
        assert r.evidence is not None
        assert (r.evidence.repos_enriched, r.evidence.repos_with_ci) == (2, 1)
        assert r.evidence.repos_reused == 1
        assert r.evidence.repos_with_tests == 2
        assert r.evidence.detections == {"N1": 2, "N2": 1, "N3": 1}
        assert r.evidence.items_with_detection == 3
        top = {e.entry: (e.kind, e.n1, e.n2, e.n3) for e in r.evidence.top}
        assert top == {"ragas": ("tool", 1, 0, 1), "llm-as-judge": ("method", 1, 0, 0)}
        assert r.evidence.top[0].entry == "ragas"  # most units first

    def test_no_evidence_when_nothing_extracted(self, tmp_path: Path) -> None:
        assert _report(tmp_path).evidence is None

    def test_warnings(self, tmp_path: Path) -> None:
        w = "\n".join(_report(tmp_path).warnings)
        assert "github: 1 de 3 consultas planejadas não foram executadas" in w
        assert "1 consultas pararam no limite de itens configurado (limite = 10)" in w
        assert "1 itens únicos têm data fora da janela (github 1)" in w
        assert "linkedin" not in w  # unavailable sources are listed, not warned

    def test_source_cap_failed_and_unfinished_run(self, tmp_path: Path) -> None:
        m = _manifest()
        m.finished_at = None
        m.sources[0].queries[0].truncation_reasons = ["source_cap"]
        m.sources[0].queries[1].failed_partitions = ["k"]
        m.sources[1].queries[0].truncation_reasons = ["no_historical_coverage"]
        w = "\n".join(_report(tmp_path, manifest=m).warnings)
        assert "não registrou o término" in w
        assert "github: 1 consultas bateram no teto de resultados" in w
        assert "github: 1 consultas falharam ou ficaram incompletas" in w
        assert "hackernews: a fonte só entrega itens recentes" in w

    def test_without_manifest_or_dedupe(self, tmp_path: Path) -> None:
        r = _report(tmp_path, manifest=None, dedupe_report=None)
        assert r.sources == [] and r.terms == {}
        assert r.duplicates is None
        assert r.kept == 5  # falls back to the items
        w = "\n".join(r.warnings)
        assert "Sem manifesto" in w
        assert "Deduplicação não executada" in w
        md = render_markdown(r)
        assert "| − Duplicatas | n/a |" in md
        assert "duração desconhecida" in md


class TestSourcesWindowAndAccumulation:
    def test_skip_reasons_follow_source_groups(self, tmp_path: Path) -> None:
        m = _manifest()
        m.effective_protocol["enabled_sources"] = ["github", "hackernews", "stackexchange"]
        m.effective_protocol["source_groups"] = {
            "github": "manual", "hackernews": "public", "stackexchange": "public",
            "linkedin": "unavailable", "x_twitter": "paid",
        }  # fmt: skip
        ok = Availability(status=AvailabilityStatus.OK, reason="token configured")
        no = Availability(status=AvailabilityStatus.UNSUPPORTED, reason="paid plan")
        m.sources += [
            SourceManifestEntry(name="devto", adapter_version="1", availability=ok),
            SourceManifestEntry(name="x_twitter", adapter_version="1", availability=no),
            SourceManifestEntry(name="stackexchange", adapter_version="1", availability=ok),
        ]
        r = _report(tmp_path, manifest=m)
        reasons = {s.name: s.skip_reason for s in r.sources if not s.executed}
        assert reasons == {
            "linkedin": "indisponível (grupo `unavailable`): no API",
            "devto": "fora desta execução (não está em `MSRKIT_SOURCES_PUBLIC` nem `_MANUAL`)",
            "x_twitter": "fonte paga, fora da coleta (grupo `paid`)",
            "stackexchange": "habilitada e disponível, mas nenhuma consulta foi executada",
        }
        w = "\n".join(r.warnings)
        assert "stackexchange: habilitada e disponível, mas nenhuma consulta" in w
        assert "devto:" not in w

    def test_disabled_in_protocol_without_groups(self, tmp_path: Path) -> None:
        m = _manifest()
        m.effective_protocol["enabled_sources"] = ["github", "hackernews"]
        r = _report(tmp_path, manifest=m)
        assert r.sources[2].skip_reason == "desativada no protocolo"

    def test_window_extended_beyond_protocol(self, tmp_path: Path) -> None:
        m = _manifest()
        m.effective_protocol["window"]["until"] = "2026-10-31"
        m.env_overrides = ["MSRKIT_WINDOW_UNTIL"]
        r = _report(tmp_path, manifest=m, protocol_window=("2023-01-01", "2024-12-31"))
        assert (r.protocol_since, r.protocol_until) == ("2023-01-01", "2024-12-31")
        assert r.outside_protocol_window == 3  # two items of 2025 and one of 2022
        assert r.window_overridden
        w = "\n".join(r.warnings)
        assert "difere da do protocolo (2023-01-01 a 2024-12-31): 3 itens únicos" in w
        md = render_markdown(r)
        assert "- Janela do protocolo: **2023-01-01 a 2024-12-31** (3 itens únicos fora dela)" in md

    def test_same_window_has_no_contrast(self, tmp_path: Path) -> None:
        r = _report(tmp_path, protocol_window=("2023-01-01", "2026-08-31"))
        assert r.protocol_since is None and r.outside_protocol_window == 0
        assert "Janela do protocolo" not in render_markdown(r)

    def test_accumulation_against_earlier_runs(self, tmp_path: Path) -> None:
        earlier = {"https://x.example/github/1", "http://www.x.example/other/9/"}
        r = _report(tmp_path, earlier_urls=earlier, earlier_runs=2)
        assert (r.earlier_runs, r.new_items, r.accumulated) == (2, 3, 5)
        md = render_markdown(r)
        assert "Destes, 3 não apareciam nas 2 execuções anteriores; o acumulado soma 5" in md
        assert "### Acúmulo entre execuções" in md
        assert "nenhuma anterior trouxe: **3** de 4" in md
        assert r.seen_before == {"github": 1, "hackernews": 0}
        assert "| github | 3 | 1 | 33% |" in md
        assert "| hackernews | 1 | 0 | 0% |" in md

    def test_no_accumulation_without_earlier_runs(self, tmp_path: Path) -> None:
        r = _report(tmp_path)
        assert r.new_items is None and r.accumulated is None
        assert "Acúmulo" not in render_markdown(r)


class TestRender:
    def test_sections_and_content(self, tmp_path: Path) -> None:
        signals, dets = _evidence()
        md = render_markdown(_report(tmp_path, signals=signals, detections=dets))
        for heading in (
            "# Relatório de mineração — run-r",
            "## Resumo",
            "## Espaço temporal",
            "## Locais (fontes)",
            "## Termos buscados",
            "## Quantidades",
            "### Evidência (enriquecimento e extração)",
            "## Avisos",
        ):
            assert heading in md
        assert "(`abcdef012345`)" in md
        assert "**2023-01-01 a 2026-08-31**" in md
        assert "(1 h 30 min)" in md
        assert "| github | 2/3 | 4 | 3 | 0 | 3 | item_limit 1 |" in md
        assert "- **linkedin**: UNSUPPORTED: no API" in md  # manifest without source groups
        assert "- **Português (1):** `teste de RAG`" in md
        assert "- Coletadas inteiras na janela, sem o limite por consulta: hackernews" in md
        assert "- Amostra estratificada por ano:" in md
        assert "- **Consultas literais em github** (protocolo `queries:`): ci (2)" in md
        assert "Termos sem nenhum casamento: `LLM as a judge`" in md
        assert "| `RAG testing` | en | 2 |" in md
        assert "| **Itens únicos** | **4** |" in md
        assert "| ragas | tool | 1 | 0 | 1 | 2 |" in md
        assert "com testes: 2; 1 reaproveitados de execuções anteriores)" in md

    def test_summary_mentions_enrichment_only_when_it_ran(self, tmp_path: Path) -> None:
        signals, dets = _evidence()
        with_repos = render_markdown(_report(tmp_path, signals=signals, detections=dets))
        assert "O enriquecimento analisou 2 repositórios do GitHub." in with_repos
        only_text = render_markdown(_report(tmp_path, signals={}, detections=dets))
        assert "enriquecimento analisou" not in only_text
        assert "Repositórios do GitHub enriquecidos" not in only_text
        assert "A extração encontrou 4 detecções de ferramentas e métodos." in only_text

    def test_no_warnings_message(self, tmp_path: Path) -> None:
        r = _report(tmp_path)
        r.warnings = []
        assert render_markdown(r).rstrip().endswith("Nenhum aviso.")

    def test_formatting_helpers(self) -> None:
        assert _n(23541) == "23.541"
        assert _duration(None) == "duração desconhecida"
        assert _duration(42) == "42 s"
        assert _duration(57 * 60 + 29) == "57 min"
        assert _duration(6570) == "1 h 49 min"


class TestReportCommand:
    def test_writes_markdown_and_json(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        items = _items()
        storage = ItemStorage(tmp_path)
        storage.save_items(items, "run-r")
        storage.rewrite_items(items[:4], "run-r", deduped=True)
        storage.save_items(items[1:2], "run-a")  # an earlier run that already had gi1
        # The repository's own protocol, with the window extended as the daily RSS run does.
        from pathlib import Path

        from msrkit.config import load_protocol

        protocol = Path(__file__).resolve().parent.parent / "protocols/v0_rag_agents_testing.yaml"
        m = _manifest()
        m.protocol_path = str(protocol)
        m.effective_protocol["window"]["until"] = "2099-12-31"
        save_manifest(m, tmp_path)
        r = CliRunner().invoke(app, ["report", "--run", "run-r"])
        assert r.exit_code == 0, r.stdout
        assert "Identified" in r.stdout and "unique 4" in r.stdout
        md = (tmp_path / "reports/run-r/relatorio_mineracao.md").read_text(encoding="utf-8")
        assert "## Termos buscados" in md
        assert "nenhuma anterior trouxe: **3** de 4" in md
        w = load_protocol(protocol, environ={}).window
        assert f"- Janela do protocolo: **{w.since} a {w.until}**" in md
        data = json.loads((tmp_path / "reports/run-r/relatorio_mineracao.json").read_text())
        assert data["unique"] == 4 and data["sources"][0]["name"] == "github"
