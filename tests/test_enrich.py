"""GitHub repository enrichment for N3 signals (Phase 2, B1)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from typer.testing import CliRunner

from msrkit.adapters.github import GitHubAdapter
from msrkit.cli import app
from msrkit.enrich import (
    CORE_RATE_LIMIT,
    MAX_PRIORITY_WORKFLOWS,
    MAX_WORKFLOWS,
    GitHubEnricher,
    RepoSignals,
    _is_test_path,
    apply_signals,
    covers_workflows,
    has_file_contents,
    repo_of,
    select_workflows,
    workflow_hits,
)
from msrkit.models import Item, ItemKind, Provenance
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

PROTOCOL = "protocols/v0_rag_agents_testing.yaml"

TREE = {
    "truncated": False,
    "tree": [
        {"path": "src/app.py", "type": "blob"},
        {"path": "tests/test_rag.py", "type": "blob"},
        {"path": "promptfooconfig.yaml", "type": "blob"},
        {"path": ".github/workflows/eval.yml", "type": "blob"},
        {"path": "pyproject.toml", "type": "blob"},
        {"path": "docs/requirements.txt", "type": "blob"},
    ],
}
WORKFLOW = "jobs:\n  eval:\n    steps:\n      - run: npx promptfoo eval\n"


def _b64(text: str) -> dict[str, Any]:
    return {
        "encoding": "base64",
        "size": len(text),
        "content": base64.b64encode(text.encode()).decode(),
    }


def _fake_get(url: str, params: dict[str, Any] | None = None, headers=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    if url.endswith("/repos/acme/rag"):
        resp.json.return_value = {"default_branch": "main"}
    elif "/git/trees/main" in url:
        resp.json.return_value = TREE
    elif url.endswith("/contents/.github/workflows/eval.yml"):
        resp.json.return_value = _b64(WORKFLOW)
    elif url.endswith("/contents/pyproject.toml"):
        resp.json.return_value = _b64('[project]\ndependencies = ["ragas>=0.1"]\n')
    elif url.endswith("/contributors"):
        resp.headers = {"link": '<https://api.github.com/x?per_page=1&page=7>; rel="last"'}
        resp.json.return_value = [{}]
    elif url.endswith("/commits"):
        resp.json.return_value = [
            {"commit": {"committer": {"date": "2024-03-02T10:00:00Z"}}},
            {"commit": {"committer": {"date": "2024-03-20T10:00:00Z"}}},
            {"commit": {"committer": {"date": "2024-05-01T10:00:00Z"}}},
        ]
    else:
        resp.status_code = 404
    return resp


def _enricher(monkeypatch: pytest.MonkeyPatch) -> GitHubEnricher:
    adapter = GitHubAdapter()
    monkeypatch.setattr(adapter, "_governed_get", _fake_get)
    return GitHubEnricher(
        adapter,
        config_files={"promptfooconfig.yaml"},
        since=date(2024, 1, 1),
        until=date(2024, 12, 31),
    )


def _item(url: str) -> Item:
    return Item(
        id=url[-8:],
        source="github",
        kind=ItemKind.REPO,
        url=url,  # type: ignore[arg-type]
        title="t",
        provenance=Provenance(
            run_id="r",
            query_string="q",
            partition="p",
            adapter="github",
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),
    )


class TestEnricher:
    def test_collects_tree_ci_manifests_contributors_and_months(self, monkeypatch) -> None:
        sig = _enricher(monkeypatch).enrich("acme/rag")
        assert sig.error is None
        assert sig.default_branch == "main"
        assert sig.has_tests and sig.test_paths == ["tests/test_rag.py"]
        assert sig.has_ci and "npx promptfoo eval" in sig.workflows[".github/workflows/eval.yml"]
        assert sig.config_paths == ["promptfooconfig.yaml"]
        assert list(sig.manifests) == ["pyproject.toml"]  # only root manifests
        assert sig.contributors == 7
        assert sig.commit_months == ["2024-03", "2024-05"]
        assert sig.requests == len(sig.response_sha256) == 6

    def test_http_error_is_recorded_not_raised(self, monkeypatch) -> None:
        sig = _enricher(monkeypatch).enrich("nope/missing")
        assert sig.error == "repo metadata HTTP 404"

    def test_test_path_detection(self) -> None:
        assert _is_test_path("tests/unit/test_x.py")
        assert _is_test_path("src/foo_test.py")
        assert _is_test_path("web/app.spec.ts")
        assert _is_test_path("evals/golden.jsonl")
        assert not _is_test_path("src/testing_utils.py")

    def test_repo_of_and_apply_signals(self, monkeypatch) -> None:
        sig = _enricher(monkeypatch).enrich("acme/rag")
        code_item = _item("https://github.com/acme/rag/blob/main/tests/test_rag.py")
        other = _item("https://github.com/other/repo")
        assert repo_of(code_item) == "acme/rag"
        assert apply_signals([code_item, other], {"acme/rag": sig}) == 1
        assert code_item.tech.has_ci is True
        assert code_item.tech.contributors == 7
        assert code_item.tech.active_months == 2
        assert other.tech.has_ci is None

    def test_collects_via_graphql_batches_in_single_query(self, monkeypatch) -> None:
        adapter = GitHubAdapter()
        monkeypatch.setattr(adapter, "_governed_get", _fake_get)
        monkeypatch.setattr(adapter, "_governed_post", _fake_graphql_post)
        enricher = GitHubEnricher(
            adapter,
            config_files={"promptfooconfig.yaml"},
            since=date(2024, 1, 1),
            until=date(2024, 12, 31),
            use_graphql=True,
        )
        sig = enricher.enrich("acme/rag")
        assert sig.error is None
        assert sig.has_tests and sig.test_paths == ["tests/test_rag.py"]
        assert sig.has_ci and "npx promptfoo eval" in sig.workflows[".github/workflows/eval.yml"]
        assert list(sig.manifests) == ["pyproject.toml"]
        assert sig.contributors == 7
        assert sig.commit_months == ["2024-03", "2024-05"]
        # In GraphQL mode: 1 metadata + 1 tree + 1 graphql batch +
        # 1 contributors + 1 commits = 5 requests
        assert sig.requests == 5

    def test_graphql_failure_falls_back_to_rest(self, monkeypatch) -> None:
        adapter = GitHubAdapter()
        monkeypatch.setattr(adapter, "_governed_get", _fake_get)
        fail_post = MagicMock()
        fail_post.status_code = 500
        monkeypatch.setattr(
            adapter, "_governed_post", lambda url, json=None, headers=None: fail_post
        )
        enricher = GitHubEnricher(
            adapter,
            config_files={"promptfooconfig.yaml"},
            since=date(2024, 1, 1),
            until=date(2024, 12, 31),
            use_graphql=True,
        )
        sig = enricher.enrich("acme/rag")
        assert sig.error is None
        assert sig.contributors == 7
        assert "npx promptfoo eval" in sig.workflows[".github/workflows/eval.yml"]


def _fake_graphql_post(url: str, json: dict[str, Any] | None = None, headers=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {
        "data": {
            "repository": {
                "mentionableUsers": {"totalCount": 15},
                "defaultBranchRef": {
                    "target": {
                        "history": {
                            "nodes": [
                                {"committedDate": "2024-03-02T10:00:00Z"},
                                {"committedDate": "2024-05-01T10:00:00Z"},
                            ]
                        }
                    }
                },
                "wf_0": {
                    "text": WORKFLOW,
                    "byteSize": len(WORKFLOW),
                },
                "mf_0": {
                    "text": '[project]\ndependencies = ["ragas>=0.1"]\n',
                    "byteSize": 35,
                },
            }
        }
    }
    return resp


class TestEnrichCommand:
    def test_enriches_run_items_and_resumes(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        monkeypatch.setattr(
            GitHubAdapter,
            "_governed_get",
            lambda self, url, params=None, headers=None: _fake_get(url, params),
        )
        ItemStorage(tmp_path).save_items([_item("https://github.com/acme/rag")], "run-e")

        runner = CliRunner()
        result = runner.invoke(app, ["enrich", "--run", "run-e"])
        assert result.exit_code == 0, result.stdout
        assert "1 items updated" in result.stdout
        assert ItemStorage(tmp_path).read_items("run-e")[0].tech.has_ci is True

        result = runner.invoke(app, ["enrich", "--run", "run-e"])
        assert "1 already enriched, 0 to go" in result.stdout

    def test_enrich_cli_accepts_graphql_flag(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        monkeypatch.setattr(
            GitHubAdapter,
            "_governed_get",
            lambda self, url, params=None, headers=None: _fake_get(url, params),
        )
        monkeypatch.setattr(
            GitHubAdapter,
            "_governed_post",
            lambda self, url, json=None, headers=None: _fake_graphql_post(url, json),
        )
        ItemStorage(tmp_path).save_items([_item("https://github.com/acme/rag")], "run-gql")
        runner = CliRunner()
        result = runner.invoke(app, ["enrich", "--run", "run-gql", "--graphql"])
        assert result.exit_code == 0, result.stdout
        assert "1 items updated" in result.stdout
        enriched = ItemStorage(tmp_path).read_items("run-gql")[0]
        assert enriched.tech.has_ci is True
        assert enriched.tech.contributors == 7


class TestEnrichReuse:
    """Signals another run fetched recently, under the same window, are copied (ADR-054)."""

    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        fetched: list[str] = []

        def fake_get(self: GitHubAdapter, url: str, params: Any = None, headers: Any = None) -> Any:
            fetched.append(url)
            return _fake_get(url, params)

        monkeypatch.setattr(GitHubAdapter, "_governed_get", fake_get)
        return fetched

    def _signals(self, tmp_path: Path, run_id: str) -> dict[str, RepoSignals]:
        path = tmp_path / "enrich" / run_id / "github_repos.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        return {s.repo: s for s in map(RepoSignals.model_validate_json, lines)}

    def _append(self, tmp_path: Path, run_id: str, *signals: RepoSignals) -> None:
        path = tmp_path / "enrich" / run_id / "github_repos.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.writelines(s.model_dump_json() + "\n" for s in signals)

    def test_copies_recent_error_free_signals(self, tmp_path: Path, monkeypatch) -> None:
        fetched = self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        ItemStorage(tmp_path).save_items([_item("https://github.com/acme/rag")], "run-a")
        assert runner.invoke(app, ["enrich", "--run", "run-a"]).exit_code == 0
        old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        now = datetime.now(UTC).isoformat()
        self._append(
            tmp_path, "run-a",
            RepoSignals(repo="acme/old", fetched_at=old, has_ci=True),
            RepoSignals(repo="acme/err", fetched_at=now, error="HTTPError: 502"),
        )  # fmt: skip
        urls = ["https://github.com/acme/rag", "https://github.com/acme/old",
                "https://github.com/acme/err"]  # fmt: skip
        ItemStorage(tmp_path).save_items([_item(u) for u in urls], "run-b")
        fetched.clear()

        result = runner.invoke(app, ["enrich", "--run", "run-b", "--reuse-days", "14"])
        assert result.exit_code == 0, result.stdout
        assert "0 already enriched, 1 reused, 2 to go" in result.stdout
        assert "Reused from: run-a" in result.stdout
        assert not any("/repos/acme/rag" in u for u in fetched)  # copied, not fetched
        signals = self._signals(tmp_path, "run-b")
        assert signals["acme/rag"].reused_from == "run-a"
        original = self._signals(tmp_path, "run-a")["acme/rag"]
        assert signals["acme/rag"].fetched_at == original.fetched_at
        for repo in ("acme/old", "acme/err"):  # too old, or failed: fetched again
            assert signals[repo].reused_from is None
            assert any(f"/repos/{repo}" in u for u in fetched)
        assert ItemStorage(tmp_path).read_items("run-b")[0].tech.has_ci is True

    def test_off_by_default_and_needs_the_same_window(self, tmp_path: Path, monkeypatch) -> None:
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        for run_id in ("run-a", "run-b", "run-c"):
            ItemStorage(tmp_path).save_items([_item("https://github.com/acme/rag")], run_id)
        assert runner.invoke(app, ["enrich", "--run", "run-a"]).exit_code == 0
        meta = tmp_path / "enrich" / "run-a" / "meta.json"
        key = json.loads(meta.read_text(encoding="utf-8"))
        meta.write_text(json.dumps({**key, "since": "2024-01-01"}), encoding="utf-8")

        result = runner.invoke(app, ["enrich", "--run", "run-b", "--reuse-days", "14"])
        assert "0 reused, 1 to go" in result.stdout
        assert "0 earlier run(s) eligible" in result.stdout

        meta.write_text(json.dumps(key), encoding="utf-8")  # same window again
        result = runner.invoke(app, ["enrich", "--run", "run-c"])
        assert "0 already enriched, 1 to go" in result.stdout  # reuse is opt-in

    def test_runs_without_meta_use_the_manifest_window(self, tmp_path: Path, monkeypatch) -> None:
        from msrkit.models import Manifest
        from msrkit.provenance import save_manifest

        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        for run_id in ("run-a", "run-b"):
            ItemStorage(tmp_path).save_items([_item("https://github.com/acme/rag")], run_id)
        assert runner.invoke(app, ["enrich", "--run", "run-a"]).exit_code == 0
        key = json.loads((tmp_path / "enrich" / "run-a" / "meta.json").read_text("utf-8"))
        (tmp_path / "enrich" / "run-a" / "meta.json").unlink()
        manifest = Manifest(
            run_id="run-a", msrkit_version="t", protocol_path=PROTOCOL, protocol_sha256="x",
            started_at=datetime.now(UTC),
            effective_protocol={"window": {"since": key["since"], "until": key["until"]}},
        )  # fmt: skip
        save_manifest(manifest, tmp_path)
        result = runner.invoke(app, ["enrich", "--run", "run-b", "--reuse-days", "14"])
        assert "1 reused, 0 to go" in result.stdout


def _code_item(repo: str, path: str) -> Item:
    item = _item(f"https://github.com/{repo}/blob/main/{path}")
    item.id = f"{repo}:{path}"
    item.kind = ItemKind.CODE
    item.tech.path = path
    return item


class TestWorkflowHits:
    """Workflow files that code search returned are read first (ADR-056)."""

    def test_hits_are_grouped_by_repository(self) -> None:
        items = [
            _code_item("Acme/Rag", ".github/workflows/eval.yml"),
            _code_item("acme/rag", ".github/workflows/eval.yml"),
            _code_item("acme/rag", ".github/workflows/nightly.yml"),
            _code_item("acme/rag", "tests/test_x.py"),
            _item("https://github.com/acme/other"),
        ]
        assert workflow_hits(items) == {
            "acme/rag": [".github/workflows/eval.yml", ".github/workflows/nightly.yml"]
        }

    def test_hits_come_first_and_the_rest_fills_the_cap(self) -> None:
        tree = [f".github/workflows/w{i:02d}.yml" for i in range(30)]
        chosen = select_workflows(tree, [tree[25], "gone.yml", tree[3]])
        assert chosen[:2] == [tree[25], tree[3]]  # in hit order; the missing file is skipped
        assert len(chosen) == MAX_WORKFLOWS and chosen[2] == tree[0]
        many = select_workflows(tree, tree[5:])
        assert many == tree[5 : 5 + MAX_PRIORITY_WORKFLOWS]
        assert select_workflows(tree, None) == tree[:MAX_WORKFLOWS]
        # A tree cut short by the API may hide the file: it is still tried.
        assert select_workflows([], ["gone.yml"], tree_complete=False) == ["gone.yml"]

    def test_the_hit_workflow_is_read_even_beyond_the_default_cap(self, monkeypatch) -> None:
        paths = [f".github/workflows/w{i:02d}.yml" for i in range(14)]
        tree = {"truncated": False, "tree": [{"path": p, "type": "blob"} for p in paths]}

        def fake_get(url: str, params: Any = None, headers: Any = None) -> MagicMock:
            if "/git/trees/main" in url:
                resp = MagicMock(status_code=200, headers={})
                resp.json.return_value = tree
                return resp
            if "/contents/.github/workflows/" in url:
                resp = MagicMock(status_code=200, headers={})
                resp.json.return_value = _b64(f"# {url.rsplit('/', 1)[-1]}\n" + WORKFLOW)
                return resp
            return _fake_get(url, params)

        adapter = GitHubAdapter()
        monkeypatch.setattr(adapter, "_governed_get", fake_get)
        wanted = [paths[13], ".github/workflows/deleted.yml"]
        sig = GitHubEnricher(adapter).enrich("acme/rag", priority_paths=wanted)
        assert sig.error is None
        assert list(sig.workflows)[0] == paths[13] and len(sig.workflows) == MAX_WORKFLOWS
        assert sig.workflows_missing == [".github/workflows/deleted.yml"]
        assert covers_workflows(sig, wanted)  # read or tried: no need to fetch again
        assert not covers_workflows(sig, [".github/workflows/w12.yml"])

    def test_signals_from_a_package_have_no_file_contents(self) -> None:
        kept = {"repo": "a/b", "workflows": {"x.yml": "jobs: {}"}, "manifests": {"setup.py": ""}}
        assert has_file_contents(kept)
        assert has_file_contents({"repo": "a/b", "workflows": {}, "manifests": {}})
        assert not has_file_contents({"repo": "a/b", "workflows": ["x.yml"], "manifests": []})
        assert not has_file_contents({"repo": "a/b", "workflows": {"x.yml": ""}})


class TestReuseNeedsUsableSignals:
    """A copied signal has to carry what the extractor reads (ADR-056)."""

    def _run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[CliRunner, list[str]]:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        fetched: list[str] = []

        def fake_get(self: GitHubAdapter, url: str, params: Any = None, headers: Any = None) -> Any:
            fetched.append(url)
            return _fake_get(url, params)

        monkeypatch.setattr(GitHubAdapter, "_governed_get", fake_get)
        return CliRunner(), fetched

    def _signal(self, tmp_path: Path, run_id: str) -> dict[str, Any]:
        path = tmp_path / "enrich" / run_id / "github_repos.jsonl"
        last = path.read_text(encoding="utf-8").splitlines()[-1]
        data: dict[str, Any] = json.loads(last)
        return data

    def test_a_copy_without_file_contents_is_fetched_again(self, tmp_path, monkeypatch) -> None:
        runner, fetched = self._run(tmp_path, monkeypatch)
        for run_id in ("run-a", "run-b"):
            ItemStorage(tmp_path).save_items([_item("https://github.com/acme/rag")], run_id)
        assert runner.invoke(app, ["enrich", "--run", "run-a"]).exit_code == 0
        # What the reproducibility package keeps: the paths, not the files.
        stripped = self._signal(tmp_path, "run-a")
        stripped["workflows"] = sorted(stripped["workflows"])
        stripped["manifests"] = sorted(stripped["manifests"])
        path = tmp_path / "enrich" / "run-a" / "github_repos.jsonl"
        path.write_text(json.dumps(stripped) + "\n", encoding="utf-8")
        fetched.clear()

        result = runner.invoke(app, ["enrich", "--run", "run-b", "--reuse-days", "14"])
        assert result.exit_code == 0, result.stdout
        assert "0 reused, 1 to go" in result.stdout
        assert "Not reused, fetched again: 1 without file contents" in result.stdout
        assert any("/repos/acme/rag" in u for u in fetched)
        again = self._signal(tmp_path, "run-b")
        assert again["reused_from"] is None
        assert "npx promptfoo eval" in again["workflows"][".github/workflows/eval.yml"]

    def test_a_copy_that_never_read_the_hit_workflow_is_fetched_again(
        self, tmp_path, monkeypatch
    ) -> None:
        runner, fetched = self._run(tmp_path, monkeypatch)
        repo_item = _item("https://github.com/acme/rag")
        ItemStorage(tmp_path).save_items([repo_item], "run-a")
        assert runner.invoke(app, ["enrich", "--run", "run-a"]).exit_code == 0
        hit = _code_item("acme/rag", ".github/workflows/nightly.yml")  # not read by run-a
        for run_id in ("run-b", "run-c"):
            ItemStorage(tmp_path).save_items([repo_item, hit], run_id)
        fetched.clear()

        result = runner.invoke(app, ["enrich", "--run", "run-b", "--reuse-days", "14"])
        assert result.exit_code == 0, result.stdout
        assert "0 reused, 1 to go" in result.stdout
        assert "1 without a workflow hit by code search" in result.stdout
        assert any("/repos/acme/rag" in u for u in fetched)
        # The file is not in the tree: recorded as tried, so the next run can copy it.
        assert self._signal(tmp_path, "run-b")["workflows_missing"] == [
            ".github/workflows/nightly.yml"
        ]
        fetched.clear()
        result = runner.invoke(app, ["enrich", "--run", "run-c", "--reuse-days", "14"])
        assert "1 reused, 0 to go" in result.stdout and not fetched

    def test_a_run_enriched_before_the_hit_was_known_is_completed(
        self, tmp_path, monkeypatch
    ) -> None:
        runner, fetched = self._run(tmp_path, monkeypatch)
        repo_item = _item("https://github.com/acme/rag")
        ItemStorage(tmp_path).save_items([repo_item], "run-a")
        assert runner.invoke(app, ["enrich", "--run", "run-a"]).exit_code == 0
        hit = _code_item("acme/rag", ".github/workflows/nightly.yml")
        ItemStorage(tmp_path).save_items([hit], "run-a")  # appended: same run, a new hit
        fetched.clear()

        result = runner.invoke(app, ["enrich", "--run", "run-a"])
        assert result.exit_code == 0, result.stdout
        assert "0 already enriched, 1 to go" in result.stdout
        assert "1 enriched before without a workflow that code search hit" in result.stdout
        assert any("/repos/acme/rag" in u for u in fetched)
        result = runner.invoke(app, ["enrich", "--run", "run-a"])
        assert "1 already enriched, 0 to go" in result.stdout


def test_core_rate_limit_is_under_quota_and_above_search_rate():
    per_hour = CORE_RATE_LIMIT.requests * 3600 / CORE_RATE_LIMIT.per_seconds
    search = GitHubAdapter.policy.rate_limit
    assert per_hour < 5000  # core REST quota with a token
    assert per_hour > search.requests * 3600 / search.per_seconds


def test_repo_of_case_insensitivity():
    item = _item("https://github.com/AcmeCorp/RAG-Agent")
    assert repo_of(item) == "acmecorp/rag-agent"


def test_empty_repo_handling(monkeypatch):
    adapter = GitHubAdapter()

    def _empty_fake_get(url: str, params=None, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        if url.endswith("/repos/acme/empty"):
            resp.json.return_value = {"default_branch": None}
        elif url.endswith("/commits"):
            resp.json.return_value = {"message": "Git Repository is empty."}
        else:
            resp.status_code = 404
        return resp

    monkeypatch.setattr(adapter, "_governed_get", _empty_fake_get)
    enricher = GitHubEnricher(adapter)
    sig = enricher.enrich("acme/empty")
    assert sig.error == "empty repository (no default branch)"
