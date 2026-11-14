"""Literal per-source queries from the protocol (Phase 2, A1)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from typer.testing import CliRunner

from msrkit.adapters.github import GitHubAdapter
from msrkit.cli import app
from msrkit.config import ProtocolConfig, load_protocol
from msrkit.models import Query

if TYPE_CHECKING:
    from pathlib import Path


def _config(**source_overrides: Any) -> ProtocolConfig:
    github = {"enabled": True, "kinds": ["repo"], "extra": {"languages": ["Python"]}}
    github.update(source_overrides)
    return ProtocolConfig.model_validate(
        {
            "version": 0,
            "name": "t",
            "description": "d",
            "window": {"since": "2023-01-01", "until": "2023-12-31"},
            "terms": ["RAG testing"],
            "sources": {"github": github},
        }
    )


class TestBuildQueries:
    def test_raw_queries_run_before_terms_to_prevent_starvation(self) -> None:
        cfg = _config(queries=[{"kind": "code", "label": "ragas", "q": '"from ragas import"'}])
        queries = cfg.build_queries("github")
        assert [(q.raw, q.kind) for q in queries] == [(True, "code"), (False, "repo")]
        raw = queries[0]
        assert raw.terms == ['"from ragas import"']
        assert raw.label == "ragas"

    def test_use_terms_false_runs_only_raw_queries(self) -> None:
        cfg = _config(use_terms=False, queries=[{"q": "x"}])
        assert [q.raw for q in cfg.build_queries("github")] == [True]


class TestGitHubRawQuery:
    def test_raw_code_query_is_verbatim(self) -> None:
        q = Query(
            source="github",
            terms=["filename:promptfooconfig.yaml"],
            kind="code",
            raw=True,
            extra={"languages": ["Python"]},
        )
        assert (
            GitHubAdapter()._build_single_query_string(q, term=q.terms[0])
            == "filename:promptfooconfig.yaml"
        )

    def test_raw_repo_query_gets_only_the_date_window(self) -> None:
        q = Query(
            source="github",
            terms=['("RAG") (eval OR test) stars:>5'],
            kind="repo",
            raw=True,
            since=date(2023, 1, 1),
            until=date(2023, 6, 30),
            extra={"languages": ["Python"], "min_stars": 3},
        )
        qs = GitHubAdapter()._build_single_query_string(q, term=q.terms[0])
        assert qs == '("RAG") (eval OR test) stars:>5 created:2023-01-01..2023-06-30'

    def test_raw_repo_query_half_open_date_windows(self) -> None:
        adapter = GitHubAdapter()
        q_since = Query(
            source="github",
            terms=["stars:>5"],
            kind="repo",
            raw=True,
            since=date(2023, 1, 1),
        )
        assert (
            adapter._build_single_query_string(q_since, term=q_since.terms[0])
            == "stars:>5 created:>=2023-01-01"
        )

        q_until = Query(
            source="github",
            terms=["stars:>5"],
            kind="repo",
            raw=True,
            until=date(2023, 6, 30),
        )
        assert (
            adapter._build_single_query_string(q_until, term=q_until.terms[0])
            == "stars:>5 created:<=2023-06-30"
        )

    def test_raw_query_with_own_created_range_is_not_date_split(self, monkeypatch) -> None:
        adapter = GitHubAdapter()
        monkeypatch.setattr(adapter, "estimate", lambda q: 5000)
        q = Query(
            source="github",
            terms=["rag created:2024-01-01..2024-02-01"],
            kind="repo",
            raw=True,
            since=date(2023, 1, 1),
            until=date(2023, 12, 31),
        )
        parts = adapter.partition(q)
        assert len(parts) == 1 and parts[0].truncated

    def test_search_ignores_language_fanout_for_raw(self, monkeypatch) -> None:
        adapter = GitHubAdapter()
        sent: list[str] = []

        def fake_get(url: str, params: dict[str, Any] | None = None, headers=None) -> MagicMock:
            sent.append(params["q"])
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"items": []}
            return resp

        monkeypatch.setattr(adapter, "_governed_get", fake_get)
        q = Query(
            source="github",
            terms=['"import deepeval"'],
            kind="issue",
            raw=True,
            extra={"languages": ["Python", "TypeScript"]},
            limit=5,
        )
        list(adapter.search(q))
        assert sent == ['"import deepeval" is:issue']  # issue type is required (ADR-049)


class TestValidate:
    def test_sample_protocol_has_section_7_3_queries(self) -> None:
        cfg = load_protocol("protocols/v0_rag_agents_testing.yaml")
        labels = {q.label for q in cfg.sources["github"].queries}
        assert {"ragas-import", "promptfoo-config", "ci"} <= labels

    def test_raw_queries_on_devto_are_rejected(self, tmp_path: Path) -> None:
        proto = tmp_path / "p.yaml"
        proto.write_text(
            "version: 0\nname: t\ndescription: d\n"
            "window: {since: '2023-01-01', until: '2023-12-31'}\n"
            "terms: [x]\n"
            "sources:\n  devto:\n    enabled: true\n    queries: [{q: 'rag'}]\n"
        )
        result = CliRunner().invoke(app, ["validate", str(proto)])
        assert result.exit_code == 1
        assert "not supported" in result.stdout
