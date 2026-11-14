"""Queries stratified by year of the window, with the size of each stratum (ADR-055)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import yaml
from typer.testing import CliRunner

from msrkit.adapters.devto import DevToAdapter
from msrkit.adapters.github import GitHubAdapter
from msrkit.adapters.hackernews import HackerNewsAdapter
from msrkit.adapters.huggingface import HuggingFaceAdapter
from msrkit.adapters.stackexchange import StackExchangeAdapter
from msrkit.cli import app
from msrkit.cli.collect import _year_strata
from msrkit.config import load_protocol
from msrkit.models import Query, RawItem
from msrkit.partition import split_by_year

if TYPE_CHECKING:
    import pytest

PROTO = "protocols/v0_rag_agents_testing.yaml"
WINDOW = {"since": date(2023, 1, 1), "until": date(2026, 8, 31)}


def _resp(data: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = data
    return resp


class TestSplitByYear:
    def test_one_query_per_calendar_year_clipped_to_the_window(self) -> None:
        q = Query(source="hackernews", terms=["a"], since=date(2023, 3, 1), until=date(2026, 8, 31))
        windows = [(s.since, s.until) for s in split_by_year(q)]
        assert windows == [
            (date(2023, 3, 1), date(2023, 12, 31)),
            (date(2024, 1, 1), date(2024, 12, 31)),
            (date(2025, 1, 1), date(2025, 12, 31)),
            (date(2026, 1, 1), date(2026, 8, 31)),
        ]

    def test_single_year_or_open_window_is_kept(self) -> None:
        one_year = Query(source="x", terms=["a"], since=date(2024, 2, 1), until=date(2024, 5, 1))
        assert split_by_year(one_year) == [one_year]
        open_ended = Query(source="x", terms=["a"], since=date(2023, 1, 1))
        assert split_by_year(open_ended) == [open_ended]


class TestWhichQueriesSplit:
    def test_github_splits_repos_and_issues_but_not_code(self) -> None:
        adapter = GitHubAdapter()
        repo = Query(source="github", terms=["a"], kind="repo", **WINDOW)
        issue = repo.model_copy(update={"kind": "issue"})
        code = repo.model_copy(update={"kind": "code"})
        fixed = repo.model_copy(update={"raw": True, "terms": ["rag created:2024-01-01..*"]})
        assert [len(_year_strata(adapter, [q])) for q in (repo, issue, code, fixed)] == [4, 4, 1, 1]

    def test_sources_without_date_filter_are_not_split(self) -> None:
        q = Query(source="x", terms=["a"], **WINDOW)
        assert len(_year_strata(HackerNewsAdapter(), [q])) == 4
        assert len(_year_strata(StackExchangeAdapter(), [q])) == 4
        assert len(_year_strata(HuggingFaceAdapter(), [q])) == 1
        assert len(_year_strata(DevToAdapter(), [q])) == 1


class TestPopulation:
    def test_github_sums_the_languages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = GitHubAdapter()
        totals = {"language:Python": 70, "language:TypeScript": 30}
        sent: list[str] = []

        def fake_get(url: str, params: dict[str, Any]) -> MagicMock:
            sent.append(params["q"])
            return _resp({"total_count": next(v for k, v in totals.items() if k in params["q"])})

        monkeypatch.setattr(adapter, "_governed_get", fake_get)
        q = Query(
            source="github", terms=["RAG evaluation"], kind="issue",
            since=date(2024, 1, 1), until=date(2024, 12, 31),
            extra={"languages": ["Python", "TypeScript"]},
        )  # fmt: skip
        assert adapter.population(q) == 100
        assert adapter.estimate(q) == 70  # the partition decision still uses the largest
        assert all("created:2024-01-01..2024-12-31" in s and "is:issue" in s for s in sent)

    def test_stackexchange_sums_the_sites(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = StackExchangeAdapter()
        totals = {"stackoverflow": 40, "datascience": 2}
        monkeypatch.setattr(
            adapter, "_governed_get",
            lambda url, params: _resp({"total": totals[params["site"]]}),
        )  # fmt: skip
        q = Query(source="stackexchange", terms=["a"], extra={"sites": list(totals)}, **WINDOW)
        assert adapter.population(q) == 42
        assert adapter.estimate(q) == 40  # first site only

    def test_failed_count_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = GitHubAdapter()
        failed = MagicMock(status_code=403)
        monkeypatch.setattr(adapter, "_governed_get", lambda url, params: failed)
        assert adapter.population(Query(source="github", terms=["a"], kind="repo")) is None


class TestRunWithStrata:
    def _protocol(self, tmp_path: Path, **sources: dict[str, Any]) -> str:
        data = yaml.safe_load(Path(PROTO).read_text(encoding="utf-8"))
        data["gazetteer"] = str(Path(PROTO).parent.resolve() / data["gazetteer"])
        for name, changes in sources.items():
            data["sources"][name].update(changes)
        path = tmp_path / "protocol.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return str(path)

    def _mock_hn(self, monkeypatch: pytest.MonkeyPatch) -> list[Query]:
        searched: list[Query] = []

        def search(self: HackerNewsAdapter, query: Query) -> Any:
            searched.append(query)
            for i in range(3):
                native = f"{query.terms[0]}-{query.since}-{i}"
                yield RawItem(
                    source="hackernews",
                    native_id=native,
                    payload={"objectID": native, "title": query.terms[0],
                             "created_at_i": 1705000000, "_tags": ["story"]},
                    fetched_at=datetime.now(UTC),
                )  # fmt: skip

        monkeypatch.setattr(HackerNewsAdapter, "search", search)
        # nbHits under the 1,000 cap, so partition() keeps one query per stratum.
        monkeypatch.setattr(HackerNewsAdapter, "estimate", lambda self, q: q.since.year - 2000)
        return searched

    def _run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, proto: str) -> dict[str, Any]:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        monkeypatch.setenv("MSRKIT_TERMS", "RAG evaluation")
        monkeypatch.setenv("MSRKIT_SOURCES_PUBLIC", "hackernews")
        result = CliRunner().invoke(app, ["run", proto, "--per-query-limit", "2"])
        assert result.exit_code == 0, result.stdout
        (run_dir,) = (tmp_path / "runs").iterdir()
        manifest: dict[str, Any] = json.loads((run_dir / "manifest.json").read_text())
        return manifest

    def test_each_year_gets_the_cap_and_records_its_size(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        searched = self._mock_hn(monkeypatch)
        manifest = self._run(tmp_path, monkeypatch, PROTO)
        assert manifest["effective_protocol"]["strata"] == "year"
        assert [(str(q.since), str(q.until)) for q in searched] == [
            ("2023-01-01", "2023-12-31"),
            ("2024-01-01", "2024-12-31"),
            ("2025-01-01", "2025-12-31"),
            ("2026-01-01", "2026-08-31"),
        ]
        queries = next(s for s in manifest["sources"] if s["name"] == "hackernews")["queries"]
        assert [q["items"] for q in queries] == [2, 2, 2, 2]
        assert [q["estimated_total"] for q in queries] == [23, 24, 25, 26]

    def test_census_source_is_not_stratified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        searched = self._mock_hn(monkeypatch)
        self._run(tmp_path, monkeypatch, self._protocol(tmp_path, hackernews={"census": True}))
        assert [(str(q.since), str(q.until)) for q in searched] == [("2023-01-01", "2026-08-31")]

    def test_plan_counts_the_strata(self) -> None:
        result = CliRunner().invoke(app, ["plan", PROTO, "--source", "stackexchange"])
        assert result.exit_code == 0, result.stdout
        terms = len(load_protocol(PROTO, {}).all_terms())
        assert f"≥{terms * 4}" in result.stdout  # one query per term and year


def test_sample_protocol_stratifies_by_year() -> None:
    cfg = load_protocol(PROTO, {})
    assert cfg.sampling.strata == "year"
    assert cfg.effective()["strata"] == "year"
