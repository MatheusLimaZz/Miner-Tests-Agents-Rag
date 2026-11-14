"""Tool/method extraction with evidence levels (Phase 2, B2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.enrich import RepoSignals
from msrkit.extract import Detection, Extractor, dependency_names, summarize
from msrkit.gazetteer import Gazetteer
from msrkit.models import Item, ItemKind, Provenance
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

GAZ = Gazetteer.model_validate(
    {
        "version": "t",
        "tools": [
            {
                "id": "ragas",
                "name": "Ragas",
                "family": "eval",
                "python_imports": ["ragas"],
                "packages": ["ragas"],
            },
            {
                "id": "promptfoo",
                "name": "promptfoo",
                "family": "eval",
                "packages": ["promptfoo"],
                "config_files": ["promptfooconfig.yaml"],
                "cli": ["promptfoo eval"],
            },
            {
                "id": "deepeval",
                "name": "DeepEval",
                "family": "eval",
                "python_imports": ["deepeval"],
                "cli": ["deepeval test run"],
            },
        ],
        "methods": [{"id": "llm-as-judge", "name": "LLM-as-a-judge", "aliases": ["LLM judge"]}],
    }
)


def _item(
    url: str,
    kind: ItemKind = ItemKind.REPO,
    title: str = "t",
    body: str | None = None,
    query: str = "q",
    path: str | None = None,
    source: str = "github",
) -> Item:
    it = Item(
        id=url,
        source=source,
        kind=kind,
        url=url,
        title=title,
        body=body,  # type: ignore[arg-type]
        provenance=Provenance(
            run_id="r",
            query_string=query,
            partition="p",
            adapter=source,
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),
    )
    it.tech.path = path
    return it


def _sig(months: int, contributors: int, workflows: dict[str, str] | None = None) -> RepoSignals:
    if workflows is None:
        workflows = {".github/workflows/eval.yml": "steps:\n  - run: npx promptfoo eval -c x\n"}
    return RepoSignals(
        repo="acme/rag",
        has_ci=True,
        workflows=workflows,
        manifests={"requirements.txt": "ragas==0.2.1\nnumpy\n"},
        config_paths=["promptfooconfig.yaml"],
        contributors=contributors,
        commit_months=[f"2024-0{i + 1}" for i in range(months)],
    )


class TestSignals:
    def test_text_mentions_are_n1(self) -> None:
        it = _item(
            "https://news.example/1",
            ItemKind.THREAD,
            title="Why Ragas beats LLM judge",
            source="hackernews",
        )
        dets = Extractor(GAZ).from_item(it)
        assert {(d.entry_id, d.level, d.signal) for d in dets} == {
            ("ragas", "N1", "text"),
            ("llm-as-judge", "N1", "text"),
        }

    def test_code_hit_from_import_query_is_n2(self) -> None:
        it = _item(
            "https://github.com/acme/rag/blob/main/tests/test_x.py",
            ItemKind.CODE,
            title="test_x.py",
            query='kind:code "from ragas import" language:Python',
            path="tests/test_x.py",
        )
        dets = Extractor(GAZ).from_item(it)
        assert [(d.entry_id, d.level, d.signal, d.repo) for d in dets] == [
            ("ragas", "N2", "import", "acme/rag")
        ]

    def test_ci_invocation_is_n3_only_when_sustained(self) -> None:
        repo_item = _item("https://github.com/acme/rag")
        sustained = Extractor(GAZ).from_repo(_sig(months=3, contributors=4), repo_item)
        ci = [d for d in sustained if d.signal == "ci"]
        assert [(d.entry_id, d.level) for d in ci] == [("promptfoo", "N3")]
        assert "npx promptfoo eval" in ci[0].evidence

        young = Extractor(GAZ).from_repo(_sig(months=1, contributors=4), repo_item)
        assert [d.level for d in young if d.signal == "ci"] == ["N2"]
        solo = Extractor(GAZ).from_repo(_sig(months=5, contributors=1), repo_item)
        assert [d.level for d in solo if d.signal == "ci"] == ["N2"]

    def test_dependency_and_config_are_n2(self) -> None:
        dets = Extractor(GAZ).from_repo(_sig(3, 4), _item("https://github.com/acme/rag"))
        assert ("ragas", "dependency", "ragas==0.2.1") in {
            (d.entry_id, d.signal, d.evidence) for d in dets
        }
        assert ("promptfoo", "config") in {(d.entry_id, d.signal) for d in dets}

    def test_dependency_parsing(self) -> None:
        assert set(
            dependency_names(
                "pyproject.toml", '[project]\ndependencies = ["ragas>=0.1", "deep_eval[x]==1"]\n'
            )
        ) >= {"ragas", "deep-eval"}
        assert set(
            dependency_names("package.json", '{"devDependencies": {"promptfoo": "^0.9"}}')
        ) == {"promptfoo"}
        assert set(dependency_names("requirements-dev.txt", "# c\nRagas==1\n-e .\n")) == {"ragas"}
        cargo = '[dependencies]\nragas = "0.1"\ndeepeval = { version = "1.0" }\n'
        assert set(dependency_names("Cargo.toml", cargo)) >= {"ragas", "deepeval"}
        gomod = "module test\n\nrequire (\n\tgithub.com/promptfoo/promptfoo v0.1.0\n)\n"
        assert set(dependency_names("go.mod", gomod)) >= {"promptfoo"}


class TestSummaryAndCommand:
    def test_each_unit_counts_once_at_its_highest_level(self) -> None:
        repo_item = _item("https://github.com/acme/rag")
        text = _item("https://github.com/acme/rag/issues/1", ItemKind.ISSUE, title="Ragas fails")
        dets = Extractor(GAZ).run([text, repo_item], {"acme/rag": _sig(3, 4)})
        table = summarize(dets)
        assert table["promptfoo"] == {"N1": 0, "N2": 0, "N3": 1}
        assert table["ragas"] == {"N1": 0, "N2": 1, "N3": 0}  # text N1 superseded by dependency

    def test_extract_command_writes_detections(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(
            [
                _item(
                    "https://news.example/1",
                    ItemKind.THREAD,
                    title="Ragas in CI",
                    source="hackernews",
                )
            ],
            "run-x",
        )
        result = CliRunner().invoke(
            app, ["extract", "--run", "run-x", "-p", "protocols/v0_rag_agents_testing.yaml"]
        )
        assert result.exit_code == 0, result.stdout
        assert (tmp_path / "extract" / "run-x" / "detections.csv").exists()
        assert "ragas" in (tmp_path / "extract" / "run-x" / "detections.jsonl").read_text()


class TestDisambiguation:
    """Ambiguous tool names need lexicon context or a structural signal (B3)."""

    GAZ_AMB = Gazetteer.model_validate(
        {
            "version": "t",
            "tools": [
                {
                    "id": "arize-phoenix",
                    "name": "Arize Phoenix",
                    "family": "tracing",
                    "aliases": ["phoenix"],
                    "packages": ["arize-phoenix"],
                    "ambiguous": True,
                },
            ],
        }
    )

    def test_context_confirms_an_ambiguous_mention(self) -> None:
        ex = Extractor(self.GAZ_AMB, context_terms=["evaluation", "tracing"])
        ok = _item(
            "https://news.example/1",
            ItemKind.THREAD,
            source="hackernews",
            title="Phoenix tracing for RAG evaluation",
        )
        noise = _item(
            "https://news.example/2",
            ItemKind.THREAD,
            source="hackernews",
            title="Moving to Phoenix, Arizona next year",
        )
        dets = ex.run([ok, noise], {})
        assert [(d.item_id, d.confirmed_by) for d in dets] == [
            ("https://news.example/1", "context")
        ]
        assert ex.dropped_ambiguous == 1

    def test_structural_signal_confirms_an_ambiguous_mention(self) -> None:
        ex = Extractor(self.GAZ_AMB, context_terms=["evaluation"])
        readme = _item("https://github.com/acme/app", title="app", body="Uses phoenix locally")
        sig = RepoSignals(repo="acme/app", manifests={"requirements.txt": "arize-phoenix==4\n"})
        dets = ex.run([readme], {"acme/app": sig})
        text = [d for d in dets if d.signal == "text"]
        assert [d.confirmed_by for d in text] == ["structural"]
        assert ex.dropped_ambiguous == 0

    def test_aliases_converge_to_the_canonical_id(self) -> None:
        ex = Extractor(GAZ)
        it = _item(
            "https://news.example/3",
            ItemKind.THREAD,
            source="hackernews",
            title="deepeval vs DeepEval",
        )
        assert {d.entry_id for d in ex.from_item(it)} == {"deepeval"}


class TestCiCodeHits:
    """CI workflow hits from `{anchor} path:.github/workflows` queries (ADR-045)."""

    def _hit(self) -> Item:
        return _item(
            "https://github.com/acme/app/blob/main/.github/workflows/eval.yml",
            kind=ItemKind.CODE,
            query="deepeval path:.github/workflows language:YAML",
            path=".github/workflows/eval.yml",
        )

    def test_workflow_hit_is_a_ci_signal(self) -> None:
        dets = Extractor(GAZ).run([self._hit()], {})
        ci = [d for d in dets if d.signal == "ci"]
        assert [(d.entry_id, d.level) for d in ci] == [("deepeval", "N2")]

    def test_promoted_to_n3_when_adoption_is_sustained(self) -> None:
        signals = {"acme/app": _sig(months=3, contributors=4, workflows={})}
        dets = Extractor(GAZ).run([self._hit()], signals)
        assert ("deepeval", "N3") in {(d.entry_id, d.level) for d in dets if d.signal == "ci"}
        thin = {"acme/app": _sig(months=1, contributors=1, workflows={})}
        dets = Extractor(GAZ).run([self._hit()], thin)
        assert {d.level for d in dets if d.signal == "ci"} == {"N2"}

    def test_promoted_to_n3_with_mixed_case_signals_repo(self) -> None:
        signals = {"Acme/App": _sig(months=3, contributors=4, workflows={})}
        dets = Extractor(GAZ).run([self._hit()], signals)
        assert ("deepeval", "N3") in {(d.entry_id, d.level) for d in dets if d.signal == "ci"}

    def test_non_workflow_code_hit_is_not_ci(self) -> None:
        hit = _item(
            "https://github.com/acme/app/blob/main/src/x.py",
            kind=ItemKind.CODE,
            query="deepeval path:.github/workflows",
            path="src/x.py",
        )
        assert not [d for d in Extractor(GAZ).run([hit], {}) if d.signal == "ci"]


def test_enrich_priority_puts_ci_and_code_hits_first() -> None:
    from msrkit.enrich import enrich_priority

    items = [
        _item("https://github.com/a/issues-only/issues/1", kind=ItemKind.ISSUE),
        _item("https://github.com/b/repo", kind=ItemKind.REPO),
        _item("https://github.com/c/code/blob/main/x.py", kind=ItemKind.CODE, path="x.py"),
        _item("https://github.com/d/ci/blob/main/.github/workflows/t.yml", kind=ItemKind.CODE,
              path=".github/workflows/t.yml"),
    ]  # fmt: skip
    assert enrich_priority(items) == ["d/ci", "c/code", "b/repo", "a/issues-only"]


def test_from_repo_prefers_code_over_issue_regardless_of_order() -> None:
    issue_item = _item("https://github.com/acme/app/issues/42", kind=ItemKind.ISSUE)
    code_item = _item(
        "https://github.com/acme/app/blob/main/eval.py",
        kind=ItemKind.CODE,
        path="eval.py",
    )
    sig = RepoSignals(
        repo="acme/app",
        has_ci=True,
        workflows={".github/workflows/eval.yml": "steps:\n  - run: promptfoo eval\n"},
        manifests={"requirements.txt": "ragas==0.2.1\n"},
        contributors=2,
        commit_months=["2024-01"],
    )
    dets = Extractor(GAZ).run([issue_item, code_item], {"acme/app": sig})
    repo_dets = [d for d in dets if d.signal in {"ci", "dependency"}]
    assert repo_dets, "Expected from_repo detections (ci, dependency)"
    assert all(d.item_id == code_item.id for d in repo_dets)
    assert not any(d.item_id == issue_item.id for d in repo_dets)


def test_from_repo_never_attaches_to_issue_only_repo() -> None:
    issue_item = _item(
        "https://github.com/NVIDIA/garak/issues/100",
        kind=ItemKind.ISSUE,
        title="Issue mentioning ragas",
    )
    sig = RepoSignals(
        repo="NVIDIA/garak",
        has_ci=True,
        workflows={".github/workflows/eval.yml": "steps:\n  - run: promptfoo eval\n"},
        manifests={"pyproject.toml": '[project]\ndependencies = ["ragas"]\n'},
        config_paths=["promptfooconfig.yaml"],
        contributors=3,
        commit_months=["2024-01"],
    )
    dets = Extractor(GAZ).run([issue_item], {"NVIDIA/garak": sig})
    structural_dets = [d for d in dets if d.signal in {"ci", "dependency", "config"}]
    assert not structural_dets
    assert all(d.signal == "text" for d in dets)
    assert any(d.item_id == issue_item.id and d.signal == "text" for d in dets)


def test_dependency_parsing_regression() -> None:
    content = """
[project]
name = "ragas"
keywords = ["ragas", "evals"]
dependencies = [
    "ragas>=0.1",
    "deep_eval[x]==1"
]

[project.optional-dependencies]
evals = ["pytest"]

[tool.pytest.ini_options]
testpaths = ["tests"]
"""
    deps = dependency_names("pyproject.toml", content)
    assert "ragas" in deps
    assert "deep-eval" in deps
    assert "pytest" in deps
    assert "evals" not in deps
    assert "tests" not in deps

    cfg_content = """
[options]
install_requires =
    ragas >= 0.1
    numpy
[options.extras_require]
test =
    deepeval >= 1.0
"""
    cfg_deps = dependency_names("setup.cfg", cfg_content)
    assert "ragas" in cfg_deps
    assert "numpy" in cfg_deps
    assert "deepeval" in cfg_deps
    assert "test" not in cfg_deps

    req_content = "git+https://github.com/vibrantlabsai/ragas.git\n"
    req_deps = dependency_names("requirements.txt", req_content)
    assert "ragas" in req_deps
    assert "git" not in req_deps
    assert "https" not in req_deps


def test_ci_workflow_parsing_regression() -> None:
    gaz = Gazetteer.model_validate(
        {
            "version": "t",
            "tools": [
                {
                    "id": "garak",
                    "name": "garak",
                    "family": "sec",
                    "cli": ["garak"],
                    "python_imports": ["garak"],
                },
                {
                    "id": "promptfoo",
                    "name": "promptfoo",
                    "family": "eval",
                    "cli": ["promptfoo eval"],
                },
            ],
        }
    )
    repo_item = _item("https://github.com/acme/rag")

    negative_yaml = """
# promptfoo eval comment
name: Promptfoo Eval
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      RAGAS_DO_NOT_TRACK: 'true'
    steps:
      - run: echo garak
      - run: pip install garak
"""
    sig_neg = RepoSignals(
        repo="acme/rag",
        has_ci=True,
        workflows={".github/workflows/ci.yml": negative_yaml},
        contributors=3,
        commit_months=["2024-01", "2024-02"],
    )
    dets_neg = Extractor(gaz).from_repo(sig_neg, repo_item)
    ci_neg = [d for d in dets_neg if d.signal == "ci"]
    assert not ci_neg

    positive_yaml = """
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: garak --model_type test
      - run: |
          echo "running evals"
          npx promptfoo eval -c config.yaml
"""
    sig_pos = RepoSignals(
        repo="acme/rag",
        has_ci=True,
        workflows={".github/workflows/ci.yml": positive_yaml},
        contributors=3,
        commit_months=["2024-01", "2024-02"],
    )
    dets_pos = Extractor(gaz).from_repo(sig_pos, repo_item)
    ci_pos = [d for d in dets_pos if d.signal == "ci"]
    assert {d.entry_id for d in ci_pos} == {"garak", "promptfoo"}
    assert all(d.level == "N3" for d in ci_pos)


def test_from_code_hit_no_hallucination_when_workflows_enriched() -> None:
    code_hit = _item(
        "https://github.com/acme/rag/blob/main/.github/workflows/clang_format.yml",
        kind=ItemKind.CODE,
        query="deepeval path:.github/workflows language:YAML",
        path=".github/workflows/clang_format.yml",
    )
    sig = RepoSignals(
        repo="acme/rag",
        has_ci=True,
        workflows={
            ".github/workflows/clang_format.yml": "steps:\n  - run: clang-format --dry-run\n"
        },
        contributors=3,
        commit_months=["2024-01", "2024-02"],
    )
    dets = Extractor(GAZ).run([code_hit], {"acme/rag": sig})
    ci_dets = [d for d in dets if d.signal == "ci"]
    assert not ci_dets, f"Expected zero CI detections, got {ci_dets}"


def test_ambiguous_method_disambiguation() -> None:
    gaz = Gazetteer.model_validate(
        {
            "version": "t",
            "tools": [],
            "methods": [
                {
                    "id": "exact-match",
                    "name": "exact match",
                    "aliases": ["exact match"],
                    "ambiguous": True,
                },
                {
                    "id": "human-evaluation",
                    "name": "human evaluation",
                    "aliases": ["manual review"],
                    "ambiguous": True,
                },
            ],
        }
    )
    ex = Extractor(gaz, context_terms=["llm", "evaluation", "rag"])
    item_pr = _item("https://github.com/acme/rag/pull/1", title="PR requires manual review")
    item_sql = _item("https://github.com/acme/rag/issues/2", title="SQL exact match query")
    item_llm = _item(
        "https://github.com/acme/rag/issues/3",
        title="LLM evaluation exact match score",
    )
    dets = ex.run([item_pr, item_sql, item_llm], {})
    assert len(dets) == 1
    assert dets[0].entry_id == "exact-match"
    assert dets[0].confirmed_by == "context"
    assert ex.dropped_ambiguous == 2


def test_repo_owner_name_does_not_match() -> None:
    gaz = Gazetteer.model_validate(
        {
            "version": "t",
            "tools": [
                {
                    "id": "arize-phoenix",
                    "name": "Arize Phoenix",
                    "family": "tracing",
                    "aliases": ["phoenix"],
                    "packages": ["arize-phoenix"],
                }
            ],
        }
    )
    ex = Extractor(gaz)
    repo_item = _item(
        "https://github.com/phoenix-zhou/industrial-general-rag",
        kind=ItemKind.REPO,
        title="phoenix-zhou/industrial-general-rag",
    )
    dets = ex.from_item(repo_item)
    assert not dets, f"Expected no detections for owner prefix match, got {dets}"


def test_summarize_merges_mixed_case_repos() -> None:
    d1 = Detection(
        item_id="1",
        source="github",
        repo="Acme/Rag",
        entry_id="ragas",
        entry_type="tool",
        level="N1",
        signal="text",
        location="title",
        evidence="Ragas",
    )
    d2 = Detection(
        item_id="2",
        source="github",
        repo="acme/rag",
        entry_id="ragas",
        entry_type="tool",
        level="N2",
        signal="dependency",
        location="requirements.txt",
        evidence="ragas==0.2.1",
    )
    summary = summarize([d1, d2])
    assert summary["ragas"] == {"N1": 0, "N2": 1, "N3": 0}
