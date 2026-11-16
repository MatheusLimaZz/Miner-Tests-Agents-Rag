"""Versioned gazetteer and query templates (Phase 2, A3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.config import load_protocol
from msrkit.gazetteer import Gazetteer, load_gazetteer

if TYPE_CHECKING:
    from pathlib import Path


class TestGazetteerFile:
    def test_seed_loads_and_covers_annex_a_families(self) -> None:
        gaz = load_gazetteer("protocols/gazetteer.yaml")
        families = {t.family for t in gaz.tools}
        assert {
            "avaliacao-llm-rag",
            "guardrails-seguranca",
            "recuperacao",
            "agentes",
            "framework-sut",
        } <= families
        assert gaz.by_id("ragas") is not None
        assert all(not t.anchor for t in gaz.tools if t.family == "framework-sut")

    def test_duplicate_ids_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate gazetteer ids"):
            Gazetteer.model_validate(
                {
                    "version": "1",
                    "tools": [
                        {"id": "a", "name": "A", "family": "f"},
                        {"id": "a", "name": "B", "family": "f"},
                    ],
                }
            )

    def test_alias_shared_by_two_entries_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="alias shared"):
            Gazetteer.model_validate(
                {
                    "version": "1",
                    "tools": [
                        {"id": "a", "name": "A", "family": "f", "aliases": ["same"]},
                        {"id": "b", "name": "B", "family": "f", "aliases": ["same"]},
                    ],
                }
            )


def _protocol(tmp_path: Path, queries: str, gazetteer: bool = True) -> Path:
    (tmp_path / "g.yaml").write_text(
        "version: '1'\ntools:\n"
        "  - {id: ragas, name: Ragas, family: eval, repos: [explodinggradients/ragas]}\n"
        "  - {id: garak, name: garak, family: sec, repos: [NVIDIA/garak]}\n"
        "  - {id: langchain, name: LangChain, family: sut, anchor: false}\n"
    )
    proto = tmp_path / "p.yaml"
    proto.write_text(
        "version: 0\nname: t\ndescription: d\n"
        "window: {since: '2023-01-01', until: '2023-12-31'}\n"
        "terms: [x]\n"
        + ("gazetteer: g.yaml\n" if gazetteer else "")
        + "sources:\n  github:\n    enabled: true\n    use_terms: false\n"
        f"    queries:\n{queries}"
    )
    return proto


class TestTemplates:
    def test_anchor_and_repo_expand_over_anchor_tools(self, tmp_path: Path) -> None:
        proto = _protocol(
            tmp_path,
            "      - {kind: code, label: ci, q: '{anchor} path:.github/workflows'}\n"
            "      - {kind: issue, q: 'repo:{repo} is:issue'}\n",
        )
        queries = load_protocol(proto).build_queries("github")
        assert [(q.terms[0], q.label) for q in queries] == [
            ("ragas path:.github/workflows", "ci:ragas"),
            ("garak path:.github/workflows", "ci:garak"),
            ("repo:explodinggradients/ragas is:issue", "ragas"),
            ("repo:NVIDIA/garak is:issue", "garak"),
        ]

    def test_anchor_families_filter(self, tmp_path: Path) -> None:
        proto = _protocol(tmp_path, "      - {q: '{anchor}', anchor_families: [sec]}\n")
        assert [q.terms[0] for q in load_protocol(proto).build_queries("github")] == ["garak"]

    def test_template_without_gazetteer_fails_validation(self, tmp_path: Path) -> None:
        proto = _protocol(tmp_path, "      - {q: '{anchor}'}\n", gazetteer=False)
        result = CliRunner().invoke(app, ["validate", str(proto)])
        assert result.exit_code == 1
        assert "no gazetteer" in result.stdout


def test_catalog_origin_counts() -> None:
    from msrkit.discovery import catalog_origin
    from msrkit.gazetteer import Gazetteer

    gaz = Gazetteer.model_validate(
        {
            "version": "1",
            "tools": [
                {"id": "a", "name": "A", "family": "f", "origin": "seed"},
                {"id": "b", "name": "B", "family": "f", "origin": "discovery"},
                {"id": "c", "name": "C", "family": "f", "origin": "seed"},
            ],
        }
    )
    counts = catalog_origin(gaz)
    assert counts == {"seed": 2, "discovery": 1}


def test_repo_template_skips_empty_repos(tmp_path: Path) -> None:
    proto = _protocol(
        tmp_path,
        "      - {kind: issue, q: 'repo:{repo} is:issue'}\n",
    )
    (tmp_path / "g.yaml").write_text(
        "version: '1'\ntools:\n"
        "  - {id: no_repo, name: NoRepo, family: eval, repos: ['', '  ']}\n"
        "  - {id: valid, name: Valid, family: eval, repos: ['owner/valid']}\n"
    )
    queries = load_protocol(proto).build_queries("github")
    assert [q.terms[0] for q in queries] == ["repo:owner/valid is:issue"]
