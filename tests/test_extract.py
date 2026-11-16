"""Tool/method extraction with evidence levels (Phase 2, B2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.enrich import RepoSignals
from msrkit.extract import (
    Extractor,
    dependency_names,
    executable_lines,
    installs,
    parse_manifest,
    summarize,
    workflow_steps,
)
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


def _workflow(*runs: str, uses: str | None = None, head: str = "") -> str:
    """A minimal GitHub Actions workflow with one job (scripts as block scalars)."""
    steps = "".join(f"      - run: |\n          {r}\n" for r in runs)
    if uses:
        steps += f"      - uses: {uses}\n"
    return (
        f"{head}name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        f"    steps:\n{steps}"
    )


def _sig(months: int, contributors: int) -> RepoSignals:
    return RepoSignals(
        repo="acme/rag",
        has_ci=True,
        workflows={".github/workflows/eval.yml": _workflow("npx promptfoo eval -c x")},
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


WF = ".github/workflows/eval.yml"


def _app_sig(
    workflow: str, months: int = 3, contributors: int = 4, **fields: object
) -> RepoSignals:
    return RepoSignals(
        repo="acme/app",
        has_ci=True,
        workflows={WF: workflow},
        contributors=contributors,
        commit_months=[f"2024-0{i + 1}" for i in range(months)],
        **fields,  # type: ignore[arg-type]
    )


class TestCiCodeHits:
    """A workflow hit of a CI query counts only when a step names the tool (ADR-056)."""

    def _hit(self, query: str = "kind:code deepeval path:.github/workflows language:YAML") -> Item:
        return _item(
            f"https://github.com/acme/app/blob/main/{WF}", kind=ItemKind.CODE, query=query, path=WF
        )

    def _ci(self, dets: list) -> list[tuple[str, str, str]]:
        return [(d.entry_id, d.level, d.item_id) for d in dets if d.signal == "ci"]

    def test_a_hit_without_the_workflow_content_is_not_a_ci_signal(self) -> None:
        ex = Extractor(GAZ)
        assert self._ci(ex.run([self._hit()], {})) == []
        assert ex.stats["ci_hits_unverified"] == 1

    def test_a_step_that_runs_the_tool_is_ci_on_the_hit_item(self) -> None:
        hit = self._hit()
        ex = Extractor(GAZ)
        dets = ex.run([hit], {"acme/app": _app_sig(_workflow("deepeval test run tests/"))})
        assert self._ci(dets) == [("deepeval", "N3", hit.id)]
        ci = next(d for d in dets if d.signal == "ci")
        assert ci.location == WF and ci.evidence == "run: deepeval test run tests/"
        assert ex.stats["ci_hits_verified"] == 1
        thin = {"acme/app": _app_sig(_workflow("deepeval test run tests/"), 1, 1)}
        assert self._ci(Extractor(GAZ).run([hit], thin)) == [("deepeval", "N2", hit.id)]

    def test_the_name_outside_an_executable_step_is_rejected(self) -> None:
        text = (
            "# runs deepeval nightly\n"
            "name: DeepEval evaluation\n"
            "on: [push]\n"
            "env:\n  DEEPEVAL_TELEMETRY_OPT_OUT: 'YES'\n"
            "jobs:\n  deepeval:\n    name: deepeval gate\n    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Run deepeval\n"
            "        run: |\n"
            "          # deepeval test run comes later\n"
            "          pytest tests/\n"
            "      - uses: actions/upload-artifact@v4\n"
            "        with:\n          name: deepeval-report\n          path: deepeval/\n"
        )
        ex = Extractor(GAZ)
        assert self._ci(ex.run([self._hit()], {"acme/app": _app_sig(text)})) == []
        assert ex.stats["ci_hits_rejected"] == 1 and ex.stats["ci_hits_verified"] == 0

    def test_the_name_only_in_the_file_name_is_rejected(self) -> None:
        path = ".github/workflows/deepeval-bench.yml"
        hit = _item(
            f"https://github.com/acme/app/blob/main/{path}",
            kind=ItemKind.CODE,
            query="kind:code deepeval path:.github/workflows language:YAML",
            path=path,
        )
        sig = RepoSignals(repo="acme/app", workflows={path: _workflow("pytest -q")})
        ex = Extractor(GAZ)
        assert self._ci(ex.run([hit], {"acme/app": sig})) == []
        assert ex.stats["ci_hits_rejected"] == 1

    def test_signals_filed_under_another_case_are_found(self) -> None:
        hit = self._hit()
        dets = Extractor(GAZ).run([hit], {"Acme/App": _app_sig(_workflow("deepeval test run"))})
        assert self._ci(dets) == [("deepeval", "N3", hit.id)]
        assert {d.repo for d in dets} == {"acme/app"}

    def test_a_workflow_the_search_did_not_return_counts_on_the_repository(self) -> None:
        repo_item = _item("https://github.com/acme/app")
        dets = Extractor(GAZ).run(
            [repo_item], {"acme/app": _app_sig(_workflow("npx promptfoo@latest eval"))}
        )
        assert self._ci(dets) == [("promptfoo", "N3", repo_item.id)]

    def test_an_import_hit_never_becomes_n3(self) -> None:
        hit = _item(
            "https://github.com/acme/app/blob/main/tests/test_x.py",
            kind=ItemKind.CODE,
            query='kind:code "from ragas import" language:Python',
            path="tests/test_x.py",
        )
        dets = Extractor(GAZ).run([hit], {"acme/app": _app_sig(_workflow("pytest -q"))})
        assert [(d.entry_id, d.signal, d.level) for d in dets] == [("ragas", "import", "N2")]

    def test_non_workflow_code_hit_is_not_ci(self) -> None:
        hit = _item(
            "https://github.com/acme/app/blob/main/src/x.py",
            kind=ItemKind.CODE,
            query="deepeval path:.github/workflows",
            path="src/x.py",
        )
        assert not [d for d in Extractor(GAZ).run([hit], {}) if d.signal == "ci"]


class TestWorkflowSteps:
    """Only `run:` scripts and `uses:` references are executable."""

    def test_steps_and_reusable_workflows(self) -> None:
        text = (
            "on: [push]\n"
            "jobs:\n"
            "  call:\n    uses: org/repo/.github/workflows/eval.yml@main\n"
            "  test:\n    steps:\n"
            "      - name: checkout\n        uses: actions/checkout@v4\n"
            "      - run: |\n          pip install ragas \\\n            pytest\n"
            "          # a comment\n          pytest -q\n"
        )
        steps = workflow_steps(text)
        assert steps is not None
        assert [(s.kind, s.text) for s in steps if s.kind == "uses"] == [
            ("uses", "org/repo/.github/workflows/eval.yml@main"),
            ("uses", "actions/checkout@v4"),
        ]
        lines = [line for kind, line in executable_lines(steps) if kind == "run"]
        assert len(lines) == 2 and lines[1] == "pytest -q"  # continuation joined, comment dropped
        assert lines[0].startswith("pip install ragas") and lines[0].endswith("pytest")

    def test_invalid_or_empty_files_have_no_steps(self) -> None:
        assert workflow_steps("jobs:\n  a:\n   steps: [\n") is None
        assert workflow_steps("just a string") is None
        assert workflow_steps("on: push\n") == []
        ex = Extractor(GAZ)
        sig = RepoSignals(repo="acme/app", workflows={WF: "jobs:\n  a:\n   steps: [\n"})
        assert ex.ci_matches(sig) == {} and ex.stats["workflows_invalid_yaml"] == 1


class TestCiNames:
    """How a step may name a tool: CLI, `python -m`, or the anchor token, with word edges."""

    GAZ_CI = Gazetteer.model_validate(
        {
            "version": "t",
            "tools": [
                {"id": "inspect-ai", "name": "Inspect AI", "family": "eval",
                 "python_imports": ["inspect_ai"], "cli": ["inspect eval"],
                 "search_token": "inspect_ai"},
                {"id": "pyrit", "name": "PyRIT", "family": "sec", "python_imports": ["pyrit"]},
                {"id": "webarena", "name": "WebArena", "family": "agents"},
                {"id": "mteb", "name": "MTEB", "family": "ir", "python_imports": ["mteb"]},
                {"id": "promptfoo", "name": "promptfoo", "family": "eval",
                 "cli": ["promptfoo eval", "npx promptfoo"], "repos": ["promptfoo/promptfoo"]},
                {"id": "lm-eval-harness", "name": "lm-evaluation-harness", "family": "eval",
                 "python_imports": ["lm_eval"], "cli": ["lm_eval"], "search_token": "lm_eval"},
                {"id": "langchain", "name": "LangChain", "family": "framework-sut",
                 "python_imports": ["langchain"], "anchor": False},
            ],
        }
    )  # fmt: skip

    def _found(self, *runs: str, uses: str | None = None) -> set[str]:
        sig = RepoSignals(repo="acme/app", workflows={WF: _workflow(*runs, uses=uses)})
        return set(Extractor(self.GAZ_CI).ci_matches(sig)[WF])

    def test_separators_are_interchangeable(self) -> None:
        assert self._found("pip install inspect-ai==0.3.255") == {"inspect-ai"}
        assert self._found("uv run inspect eval tasks/x.py --model m") == {"inspect-ai"}
        assert self._found("python3.11 -m lm_eval --tasks hellaswag") == {"lm-eval-harness"}
        assert self._found("pip install lm-eval") == {"lm-eval-harness"}

    def test_longer_words_that_contain_the_name_do_not_match(self) -> None:
        assert self._found("curl https://ci.example/job/pyrite-dev/lastBuild") == set()
        assert self._found("agbenchmark --test=WebArenaTask_0") == set()
        assert self._found('[[ "$V" == mteb-amazon_polarity-s42 ]]') == set()
        assert self._found("python run_mteb.py", "pytest tests/test_pyrit_utils.py") == set()

    def test_actions_and_commands_match(self) -> None:
        assert self._found(uses="promptfoo/promptfoo-action@v1") == {"promptfoo"}
        assert self._found("npx promptfoo@latest eval -c cfg.yaml") == {"promptfoo"}
        assert self._found("python -m pyrit.cli scan", "mteb run -m model") == {"pyrit", "mteb"}

    def test_frameworks_need_a_command_not_just_their_name(self) -> None:
        assert self._found("pytest tests/langchain/ --tb=short") == set()
        assert self._found("python -m langchain serve") == {"langchain"}


class TestCiForms:
    """What counts as invoking a tool in a step, and how strong the evidence is (ADR-056)."""

    GAZ_FORMS = Gazetteer.model_validate(
        {
            "version": "t",
            "tools": [
                {"id": "ragas", "name": "Ragas", "family": "eval", "python_imports": ["ragas"],
                 "packages": ["ragas"], "repos": ["vibrantlabsai/ragas"]},
                {"id": "ranx", "name": "ranx", "family": "ir", "python_imports": ["ranx"],
                 "packages": ["ranx"], "repos": ["AmenRa/ranx"], "ambiguous": True},
                {"id": "openai-evals", "name": "OpenAI Evals", "family": "eval",
                 "packages": ["evals"], "cli": ["oaieval"], "search_token": "oaieval"},
                {"id": "langsmith", "name": "LangSmith", "family": "tracing",
                 "packages": ["langsmith"]},
                {"id": "tau-bench", "name": "tau-bench", "family": "agents",
                 "repos": ["sierra-research/tau-bench"]},
                {"id": "langchain", "name": "LangChain", "family": "framework-sut",
                 "packages": ["langchain"], "anchor": False},
                {"id": "lm-eval-harness", "name": "lm-evaluation-harness", "family": "eval",
                 "python_imports": ["lm_eval"], "packages": ["lm_eval", "lm-eval"],
                 "cli": ["lm_eval"], "search_token": "lm_eval"},
                {"id": "garak", "name": "garak", "family": "sec", "packages": ["garak"],
                 "cli": ["garak"]},
                {"id": "promptfoo", "name": "promptfoo", "family": "eval",
                 "packages": ["promptfoo"], "cli": ["promptfoo eval", "npx promptfoo"],
                 "repos": ["promptfoo/promptfoo"]},
            ],
        }
    )  # fmt: skip

    def _hits(self, *runs: str, uses: str | None = None) -> dict[str, str]:
        sig = RepoSignals(repo="acme/app", workflows={WF: _workflow(*runs, uses=uses)})
        found = Extractor(self.GAZ_FORMS).ci_matches(sig)[WF]
        return {tool: hit.form for tool, hit in found.items()}

    def test_a_command_counts_where_a_command_goes(self) -> None:
        assert self._hits("uv run --frozen lm_eval --tasks hellaswag") == {"lm-eval-harness": "cli"}
        assert self._hits("FOO=1 garak --list-probes || true") == {"garak": "cli"}
        assert self._hits("RESULT=$(garak --version)", "echo done") == {"garak": "cli"}
        assert self._hits("make lint && npx --yes promptfoo eval -c x.yaml") == {"promptfoo": "cli"}
        # the same word as an argument is not a call of the command
        assert self._hits("flake8 lm_eval.py sudoku.py") == {"lm-eval-harness": "name"}
        assert self._hits("helm template lm-eval charts/svc") == {"lm-eval-harness": "name"}
        assert self._hits("pip install garak pytest") == {"garak": "install"}

    def test_an_action_of_the_tools_organization_is_the_tool(self) -> None:
        assert self._hits(uses="promptfoo/promptfoo-action@v1") == {"promptfoo": "uses"}
        assert self._hits(uses="promptfoo/promptfoo/.github/actions/x@main") == {
            "promptfoo": "uses"
        }
        # a third party's action, or a local workflow file, only carries the name
        assert self._hits(uses="someone/promptfoo@v2") == {"promptfoo": "name"}
        assert self._hits(uses="./.github/workflows/promptfoo.yml") == {"promptfoo": "name"}
        # another product of the organization, or a longer word, is not the tool
        assert self._hits(uses="promptfoo/code-scan-action@v1") == {}
        assert self._hits(uses="someone/run-promptfoo@v2") == {}

    def test_the_bare_name_supports_n2_and_never_n3(self) -> None:
        repo_item = _item("https://github.com/acme/app")

        def level(*runs: str) -> list[tuple[str, str]]:
            sig = _app_sig(_workflow(*runs), months=6, contributors=9)
            dets = Extractor(self.GAZ_FORMS).run([repo_item], {"acme/app": sig})
            return [(d.via, d.level) for d in dets if d.signal == "ci"]

        assert level("pytest tests/ragas/ -q") == [("name", "N2")]
        assert level("pip install ragas") == [("install", "N3")]
        assert level("garak --model_type test") == [("cli", "N3")]

    def test_a_name_that_is_only_printed_is_not_an_invocation(self) -> None:
        assert self._hits('echo "=== Running RAGAS evaluation ==="') == {}
        assert self._hits("print('ragas coverage')", '"ragas",') == {}
        # ...but the command after the message still counts
        assert self._hits('echo "installing" && pip install ragas') == {"ragas": "install"}

    def test_what_a_test_run_leaves_out_is_not_an_invocation(self) -> None:
        assert self._hits("uv run pytest tests/ -p no:langsmith -p no:anyio") == {}
        assert self._hits('pytest -m "not ragas and not slow" --tb=short') == {}
        assert self._hits("pytest --ignore=tests/ragas -q") == {}

    def test_the_strongest_form_in_the_workflow_is_reported(self) -> None:
        assert self._hits("pytest tests/ragas/ -q") == {"ragas": "name"}
        assert self._hits("pytest tests/ragas/ -q", "pip install ragas") == {"ragas": "install"}
        assert self._hits("pip install ragas", 'python -c "import numpy, ragas"') == {
            "ragas": "module"
        }
        assert self._hits("pip install ragas", uses="vibrantlabsai/ragas/.github/actions/x@v1") == {
            "ragas": "uses"
        }
        assert self._hits("oaieval gpt-4 my-eval") == {"openai-evals": "cli"}

    def test_a_name_shared_with_other_software_needs_a_strong_form(self) -> None:
        assert self._hits("apptainer exec docker://someone/ranx:latest myst build") == {}
        assert self._hits("pytest tests/ranx/") == {}
        assert self._hits("pip install numpy ranx tqdm") == {"ranx": "install"}
        assert self._hits('python3 -c "import ranx, scipy"') == {"ranx": "module"}
        assert self._hits(uses="AmenRa/ranx/.github/workflows/ci.yml@main") == {"ranx": "uses"}

    def test_frameworks_follow_the_same_forms_without_the_bare_name(self) -> None:
        assert self._hits("pip install langchain") == {"langchain": "install"}
        assert self._hits("pytest tests/langchain/") == {}

    def test_only_what_a_package_manager_installs_is_an_install(self) -> None:
        def names(*lines: str) -> set[str]:
            return set(installs(list(lines)).packages)

        assert names("pip install evals") == {"evals"}
        # a group, an extra or a script named like a package installs no such package
        assert names("uv sync --extra evals --group ragas") == set()
        assert names('pip install -e ".[dev,evals]"') == set()
        assert names("npm run evals", "poetry install --with evals") == set()
        assert names("pip install -r requirements.txt --upgrade 'ragas[all]>=0.2'") == {"ragas"}
        assert names("conda install -y -c conda-forge ragas") == {"ragas"}
        assert names("uv run --with ragas pytest -q") == {"ragas"}
        assert names("uvx --from inspect-ai inspect eval task.py") == {"inspect-ai", "inspect"}
        assert names("npx --yes promptfoo@latest eval -c x.yaml") == {"promptfoo"}
        assert names("npm install --save-dev @langfuse/client langfuse") == {
            "@langfuse/client",
            "langfuse",
        }
        assert names("python -m pip install deepeval && pipx run garak --help") == {
            "deepeval",
            "garak",
        }
        found = installs(
            [
                'pip install "lm_eval @ git+https://github.com/EleutherAI/lm-evaluation-harness.git"',
                "pip install git+https://github.com/sierra-research/tau-bench",
            ]
        )
        assert "lm-eval" in found.packages
        assert set(found.repos) == {"eleutherai/lm-evaluation-harness", "sierra-research/tau-bench"}
        assert self._hits("pip install git+https://github.com/sierra-research/tau-bench") == {
            "tau-bench": "install"
        }

    def test_the_form_is_kept_on_the_detection(self) -> None:
        repo_item = _item("https://github.com/acme/app")
        sig = _app_sig(_workflow("pip install ragas", "pytest tests/langsmith/"))
        dets = Extractor(self.GAZ_FORMS).run([repo_item], {"acme/app": sig})
        assert {(d.entry_id, d.via) for d in dets if d.signal == "ci"} == {
            ("ragas", "install"),
            ("langsmith", "name"),
        }
        assert all(d.via == "" for d in dets if d.signal != "ci")


class TestRepositorySignalsStayOnArtifacts:
    """Signals read from the repository are never attached to an issue (ADR-056)."""

    def test_a_repository_known_only_through_issues_gets_no_structural_detection(self) -> None:
        issue = _item("https://github.com/acme/rag/issues/7", ItemKind.ISSUE, title="Ragas fails")
        ex = Extractor(GAZ)
        dets = ex.run([issue], {"acme/rag": _sig(3, 4)})
        assert [(d.entry_id, d.signal, d.level) for d in dets] == [("ragas", "text", "N1")]
        assert ex.stats["repos_without_artifact_item"] == 1

    def test_a_code_hit_stands_for_the_repository_when_there_is_no_repository_item(self) -> None:
        issue = _item("https://github.com/acme/rag/issues/7", ItemKind.ISSUE, title="Ragas fails")
        code = _item(
            "https://github.com/acme/rag/blob/main/tests/test_x.py",
            ItemKind.CODE,
            title="test_x.py",
            path="tests/test_x.py",
        )
        dets = Extractor(GAZ).run([issue, code], {"acme/rag": _sig(3, 4)})
        structural = [d for d in dets if d.signal != "text"]
        assert structural and {d.item_id for d in structural} == {code.id}
        assert issue.id not in {d.item_id for d in structural}

    def test_the_repository_item_is_preferred_over_a_code_hit(self) -> None:
        code = _item(
            "https://github.com/acme/rag/blob/main/tests/test_x.py",
            ItemKind.CODE,
            title="test_x.py",
            path="tests/test_x.py",
        )
        repo_item = _item("https://github.com/acme/rag")
        dets = Extractor(GAZ).run([code, repo_item], {"acme/rag": _sig(3, 4)})
        assert {d.item_id for d in dets if d.signal == "dependency"} == {repo_item.id}


class TestManifests:
    """Only declared requirements are dependencies; the project's own name is not one."""

    def test_project_metadata_is_not_a_dependency(self) -> None:
        text = (
            '[project]\nname = "ragas"\ndescription = "like deepeval"\n'
            'keywords = ["promptfoo", "ragas"]\ndependencies = ["numpy>=1"]\n'
            '[project.scripts]\ndeepeval = "pkg.cli:main"\n'
            '[tool.mypy]\nfiles = ["src", "promptfoo"]\n'
        )
        info = parse_manifest("pyproject.toml", text)
        assert set(info.dependencies) == {"numpy"}
        assert info.names == {"ragas"}

    def test_groups_are_read_and_their_names_are_not_packages(self) -> None:
        text = (
            '[project]\nname = "app"\ndependencies = ["ragas[all]>=0.2 ; python_version>\'3.9\'"]\n'
            '[project.optional-dependencies]\nevals = ["deepeval"]\n'
            '[dependency-groups]\ndev = ["pytest", {include-group = "evals"}]\n'
            '[tool.poetry.dependencies]\npython = "^3.11"\npromptfoo = {version = "^1"}\n'
            '[tool.poetry.group.test.dependencies]\ngarak = "^0.9"\n'
            '[tool.uv]\ndev-dependencies = ["opik"]\n'
        )
        deps = set(parse_manifest("pyproject.toml", text).dependencies)
        assert deps == {"ragas", "deepeval", "pytest", "promptfoo", "garak", "opik"}
        assert "evals" not in deps and "python" not in deps

    def test_invalid_toml_falls_back_to_quoted_requirements(self) -> None:
        text = '[project\nname = "ragas"\ndependencies = [\n  "deepeval>=1",\n]\n'
        info = parse_manifest("pyproject.toml", text)
        assert set(info.dependencies) == {"deepeval"} and info.names == {"ragas"}

    def test_setup_py_and_setup_cfg(self) -> None:
        setup_py = (
            "setup(\n    name='irkit',\n    keywords=['ragas'],\n"
            "    install_requires=['ir-measures[pytrec_eval]', 'numpy'],\n"
            "    extras_require={'ragas': ['deepeval>=1'], 'dev': DEV},\n)\n"
        )
        info = parse_manifest("setup.py", setup_py)
        assert set(info.dependencies) == {"ir-measures", "numpy", "deepeval"}
        assert info.names == {"irkit"}
        setup_cfg = (
            "[metadata]\nname = promptfoo\n[options]\ninstall_requires =\n    ragas>=0.1\n"
            "    numpy; python_version<'3.12'\n[options.extras_require]\nevals =\n    deepeval\n"
        )
        info = parse_manifest("setup.cfg", setup_cfg)
        assert set(info.dependencies) == {"ragas", "numpy", "deepeval"}
        assert info.names == {"promptfoo"}

    def test_package_json_name_and_dependencies(self) -> None:
        info = parse_manifest(
            "package.json", '{"name": "promptfoo", "optionalDependencies": {"langfuse": "^3"}}'
        )
        assert set(info.dependencies) == {"langfuse"} and info.names == {"promptfoo"}
        assert parse_manifest("package.json", "[1, 2]").dependencies == {}

    def test_a_copy_of_the_tool_is_not_a_user_of_it(self) -> None:
        """A fork keeps the tool's package name, manifests and workflows."""
        fork = _item("https://github.com/someone/promptfoo-fork")
        sig = RepoSignals(
            repo="someone/promptfoo-fork",
            workflows={WF: _workflow("npx promptfoo eval", "pip install ragas")},
            manifests={
                "package.json": '{"name": "promptfoo", "dependencies": {"promptfoo": "*"}}',
                "requirements.txt": "ragas\n",
            },
            config_paths=["promptfooconfig.yaml"],
            contributors=9,
            commit_months=["2024-01", "2024-02"],
        )
        ex = Extractor(GAZ)
        dets = ex.run([fork], {"someone/promptfoo-fork": sig})
        assert {d.entry_id for d in dets} == {"ragas"}  # the other tools it uses still count
        assert ex.stats["self_copies"] == 1


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
