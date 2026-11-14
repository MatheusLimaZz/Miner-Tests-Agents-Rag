"""Search parameters and source groups from .env (first collection setup)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.config import load_protocol
from msrkit.envfile import load_dotenv, parse_dotenv
from msrkit.models import Query, RawItem

PROTO = "protocols/v0_rag_agents_testing.yaml"
runner = CliRunner()


def _protocol_copy(tmp_path: Path, change: Callable[[dict[str, Any]], None]) -> str:
    """A copy of the sample protocol in tmp_path, edited by `change`."""
    import yaml

    data = yaml.safe_load(Path(PROTO).read_text(encoding="utf-8"))
    data["gazetteer"] = str(Path(PROTO).parent.resolve() / data["gazetteer"])
    change(data)
    path = tmp_path / "protocol.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(path)


def _bilingual_protocol(tmp_path: Path) -> str:
    """The sample protocol with Portuguese declared; E2 itself is English-only (ADR-050)."""
    return _protocol_copy(tmp_path, lambda d: d.update(languages=["en", "pt"]))


class TestDotenv:
    def test_parse(self) -> None:
        text = """
# comment
export A=1
B = "two words"
C='x;y'
D=value # trailing comment
E=
not a line
"""
        assert parse_dotenv(text) == {
            "A": "1",
            "B": "two words",
            "C": "x;y",
            "D": "value",
            "E": "",
        }

    def test_existing_variables_win_and_opt_out(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("A=from-file\nB=from-file\n")
        env = {"A": "from-shell"}
        assert load_dotenv(env_file, env) == ["B"]
        assert env == {"A": "from-shell", "B": "from-file"}
        assert load_dotenv(env_file, {"MSRKIT_NO_DOTENV": "1"}) == []
        assert load_dotenv(tmp_path / "missing", {}) == []

    def test_cli_loads_dotenv_from_cwd(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / ".env").write_text("MSRKIT_WINDOW_SINCE=2025-01-01\n")
        proto = (tmp_path / "p.yaml").as_posix()
        (tmp_path / "p.yaml").write_text(
            "version: 0\nname: p\ndescription: d\nwindow: {since: '2023-01-01', "
            "until: '2026-08-31'}\nterms: [a]\nsources: {hackernews: {enabled: true}}\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("MSRKIT_NO_DOTENV")
        # Registered with monkeypatch so the value load_dotenv sets is undone afterwards.
        monkeypatch.setenv("MSRKIT_WINDOW_SINCE", "placeholder")
        monkeypatch.delenv("MSRKIT_WINDOW_SINCE")
        result = runner.invoke(app, ["validate", proto])
        assert "2025-01-01 → 2026-08-31" in result.stdout
        assert "From .env: MSRKIT_WINDOW_SINCE" in result.stdout


class TestOverrides:
    def test_window_and_terms(self, tmp_path: Path) -> None:
        env = {
            "MSRKIT_WINDOW_SINCE": "2024-01-01",
            "MSRKIT_WINDOW_UNTIL": "2024-06-30",
            "MSRKIT_TERMS": "RAG evaluation; agent testing ;RAG evaluation",
            "MSRKIT_TERMS_PT": "teste de RAG",
        }
        cfg = load_protocol(_bilingual_protocol(tmp_path), env)
        assert (cfg.window.since, cfg.window.until) == ("2024-01-01", "2024-06-30")
        assert cfg.terms == ["RAG evaluation", "agent testing"]
        assert cfg.terms_by_language["pt"] == ["teste de RAG"]
        assert cfg.env_overrides == [
            "MSRKIT_WINDOW_SINCE",
            "MSRKIT_WINDOW_UNTIL",
            "MSRKIT_TERMS",
            "MSRKIT_TERMS_PT",
        ]
        assert set(cfg.differs_from_file()) == {"window", "terms", "terms_by_language"}
        q = cfg.build_queries("hackernews")[0]
        assert str(q.since) == "2024-01-01"
        assert "teste de RAG" in q.terms

    def test_empty_variables_keep_the_protocol(self) -> None:
        cfg = load_protocol(PROTO, {"MSRKIT_TERMS": " ", "MSRKIT_WINDOW_UNTIL": ""})
        assert cfg.env_overrides == []
        assert cfg.differs_from_file() == []
        assert len(cfg.terms) == 20

    @pytest.mark.parametrize(
        ("env", "message"),
        [
            ({"MSRKIT_WINDOW_SINCE": "01/01/2023"}, "YYYY-MM-DD"),
            ({"MSRKIT_WINDOW_SINCE": "2027-01-01"}, "after"),
            ({"MSRKIT_TERMS_ES": "prueba"}, "not in the protocol's languages"),
            ({"MSRKIT_SOURCES_PUBLIC": "hackernews,mastodon"}, "'mastodon' is not a source"),
            (
                {"MSRKIT_SOURCES_PUBLIC": "github", "MSRKIT_SOURCES_PAID": "github"},
                "more than one group",
            ),
        ],
    )
    def test_invalid(self, env: dict[str, str], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            load_protocol(PROTO, env)


class TestSourceGroups:
    def test_public_and_manual_are_collected_paid_never(self) -> None:
        env = {
            "MSRKIT_SOURCES_PUBLIC": "hackernews, devto",
            "MSRKIT_SOURCES_MANUAL": "github",
            "MSRKIT_SOURCES_PAID": "x_twitter",
            "MSRKIT_SOURCES_UNAVAILABLE": "linkedin discord",
        }
        cfg = load_protocol(PROTO, env)
        assert cfg.enabled_sources() == ["github", "hackernews", "devto"]
        assert cfg.source_groups == {
            "hackernews": "public",
            "devto": "public",
            "github": "manual",
            "x_twitter": "paid",
            "linkedin": "unavailable",
            "discord": "unavailable",
        }
        assert cfg.build_queries("stackexchange") == []  # not listed: off

    def test_example_env_matches_the_protocol(self) -> None:
        from pathlib import Path

        env = parse_dotenv(Path(".env.example").read_text(encoding="utf-8"))
        cfg = load_protocol(PROTO, env)
        assert cfg.differs_from_file() == []
        assert set(cfg.source_groups.values()) == {"public", "manual", "paid", "unavailable"}
        assert "x_twitter" not in cfg.enabled_sources()

    def test_validate_shows_groups(self, monkeypatch) -> None:
        monkeypatch.setenv("MSRKIT_SOURCES_PUBLIC", "hackernews")
        monkeypatch.setenv("MSRKIT_SOURCES_PAID", "x_twitter")
        result = runner.invoke(app, ["validate", PROTO])
        assert result.exit_code == 0
        assert "public (collected, no setup): hackernews" in result.stdout
        assert "paid (never collected): x_twitter" in result.stdout
        assert "Enabled sources: hackernews" in result.stdout


class TestRunRecordsEffectiveProtocol:
    def _mock_hn(self, monkeypatch) -> None:
        from msrkit.adapters.hackernews import HackerNewsAdapter

        def search(self: HackerNewsAdapter, query: Query) -> Any:
            for i in range(5):
                native = f"{query.terms[0]}-{i}"
                yield RawItem(
                    source="hackernews",
                    native_id=native,
                    payload={"objectID": native, "title": f"{query.terms[0]} {i}",
                             "created_at_i": 1705000000, "_tags": ["story"]},
                    fetched_at=datetime.now(UTC),
                )  # fmt: skip

        monkeypatch.setattr(HackerNewsAdapter, "search", search)
        monkeypatch.setattr(HackerNewsAdapter, "estimate", lambda *a, **k: 5)

    def test_manifest_and_per_query_limit(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        monkeypatch.setenv("MSRKIT_TERMS", "RAG evaluation;agent testing;LLM evaluation")
        monkeypatch.setenv("MSRKIT_SOURCES_PUBLIC", "hackernews")
        self._mock_hn(monkeypatch)
        result = runner.invoke(app, ["run", PROTO, "--per-query-limit", "2"])
        assert result.exit_code == 0, result.stdout
        (run_dir,) = (tmp_path / "runs").iterdir()
        manifest = json.loads((run_dir / "manifest.json").read_text())
        assert manifest["effective_protocol"]["terms"][0] == "RAG evaluation"
        assert manifest["effective_protocol"]["enabled_sources"] == ["hackernews"]
        assert "MSRKIT_TERMS" in manifest["env_overrides"]
        assert manifest["run_options"] == {"per_query_limit": 2}
        queries = next(s for s in manifest["sources"] if s["name"] == "hackernews")["queries"]
        assert queries and all(q["items"] <= 2 for q in queries)
        assert all("item_limit" in q["truncation_reasons"] for q in queries)
        assert manifest["effective_protocol"]["census_sources"] == ["devto"]

    def test_census_source_ignores_the_per_query_limit(self, tmp_path: Path, monkeypatch) -> None:
        proto = _protocol_copy(tmp_path, lambda d: d["sources"]["hackernews"].update(census=True))
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        monkeypatch.setenv("MSRKIT_TERMS", "RAG evaluation;agent testing")
        monkeypatch.setenv("MSRKIT_SOURCES_PUBLIC", "hackernews")
        self._mock_hn(monkeypatch)
        result = runner.invoke(app, ["run", proto, "--per-query-limit", "2"])
        assert result.exit_code == 0, result.stdout
        assert "Census: --per-query-limit does not apply" in result.stdout
        (run_dir,) = (tmp_path / "runs").iterdir()
        manifest = json.loads((run_dir / "manifest.json").read_text())
        assert "hackernews" in manifest["effective_protocol"]["census_sources"]
        queries = next(s for s in manifest["sources"] if s["name"] == "hackernews")["queries"]
        assert len(queries) == 2
        assert all(q["items"] == 5 and not q["truncation_reasons"] for q in queries)


class TestPilotFixes:
    def test_stackexchange_tag_modes(self) -> None:
        from msrkit.adapters.stackexchange import StackExchangeAdapter

        tags = {"tagged": ["rag", "langchain"]}
        q = Query(source="stackexchange", terms=["x"], extra=tags)
        assert StackExchangeAdapter.tag_filters(q) == ["rag;langchain"]
        q_any = q.model_copy(update={"extra": {**tags, "tagged_mode": "any"}})
        assert StackExchangeAdapter.tag_filters(q_any) == ["rag", "langchain"]
        q_off = q.model_copy(update={"extra": {**tags, "tagged_mode": "off"}})
        assert StackExchangeAdapter.tag_filters(q_off) == [None]
        adapter = StackExchangeAdapter()
        params = adapter._build_params(q_off, site="stackoverflow", page=1, pagesize=10, term="x")
        assert "tagged" not in params
        with pytest.raises(ValueError, match="tagged_mode"):
            StackExchangeAdapter.tag_filters(
                q.model_copy(update={"extra": {**tags, "tagged_mode": "some"}})
            )

    def test_protocol_turns_tags_off(self) -> None:
        cfg = load_protocol(PROTO, {})
        assert cfg.sources["stackexchange"].extra["tagged_mode"] == "off"

    def test_per_query_cap_splits_term_searches_only(self, tmp_path: Path, monkeypatch) -> None:
        from msrkit.adapters.devto import DevToAdapter
        from msrkit.adapters.huggingface import HuggingFaceAdapter

        seen: dict[str, list[list[str]]] = {"huggingface": [], "devto": []}

        def fake(name: str) -> Any:
            def search(self: Any, query: Query) -> Any:
                seen[name].append(list(query.terms))
                return iter(())

            return search

        monkeypatch.setattr(HuggingFaceAdapter, "search", fake("huggingface"))
        monkeypatch.setattr(DevToAdapter, "search", fake("devto"))
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        monkeypatch.setenv("MSRKIT_TERMS", "RAG evaluation;agent testing")
        monkeypatch.setenv("MSRKIT_SOURCES_PUBLIC", "huggingface,devto")
        result = runner.invoke(app, ["run", PROTO, "--per-query-limit", "5"])
        assert result.exit_code == 0, result.stdout
        hf_terms = seen["huggingface"]
        assert all(len(t) == 1 for t in hf_terms)  # one query per term (and kind)
        assert {t[0] for t in hf_terms} == {"RAG evaluation", "agent testing"}
        assert seen["devto"] and all(len(t) == 2 for t in seen["devto"])  # tag fetch kept whole
