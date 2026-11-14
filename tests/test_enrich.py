"""GitHub repository enrichment for N3 signals (Phase 2, B1)."""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from typer.testing import CliRunner

from msrkit.adapters.github import GitHubAdapter
from msrkit.cli import app
from msrkit.enrich import CORE_RATE_LIMIT, GitHubEnricher, _is_test_path, apply_signals, repo_of
from msrkit.models import Item, ItemKind, Provenance
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

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


def test_repo_of_strips_git_suffix():
    item = _item("https://github.com/owner/repo.git")
    assert repo_of(item) == "owner/repo"
    item_blob = _item("https://github.com/owner/repo.git/blob/main/tests/test_rag.py")
    assert repo_of(item_blob) == "owner/repo"


def test_enrich_signals_fork_and_archived_rest(monkeypatch):
    adapter = GitHubAdapter()

    def _meta_get(url: str, params=None, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        if url.endswith("/repos/acme/forked"):
            resp.json.return_value = {
                "default_branch": "main",
                "fork": True,
                "archived": True,
                "parent": {"full_name": "upstream/orig"},
            }
        elif "/git/trees/main" in url:
            resp.json.return_value = {"tree": [], "truncated": False}
        elif url.endswith("/contributors"):
            resp.status_code = 204
            resp.json.return_value = []
        elif url.endswith("/commits"):
            resp.json.return_value = []
        else:
            resp.status_code = 404
        return resp

    monkeypatch.setattr(adapter, "_governed_get", _meta_get)
    enricher = GitHubEnricher(adapter)
    sig = enricher.enrich("acme/forked")
    assert sig.error is None
    assert sig.is_fork is True
    assert sig.is_archived is True
    assert sig.fork_parent == "upstream/orig"


def test_enrich_signals_fork_and_archived_graphql(monkeypatch):
    adapter = GitHubAdapter()

    def _meta_get(url: str, params=None, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        if url.endswith("/repos/acme/gql-forked"):
            resp.json.return_value = {"default_branch": "main"}
        elif "/git/trees/main" in url:
            resp.json.return_value = {"tree": [], "truncated": False}
        elif url.endswith("/contributors"):
            resp.status_code = 204
            resp.json.return_value = []
        elif url.endswith("/commits"):
            resp.json.return_value = []
        else:
            resp.status_code = 404
        return resp

    def _meta_post(url: str, json=None, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = {
            "data": {
                "repository": {
                    "isFork": True,
                    "isArchived": True,
                    "parent": {"nameWithOwner": "upstream/gql-orig"},
                    "mentionableUsers": {"totalCount": 20},
                }
            }
        }
        return resp

    monkeypatch.setattr(adapter, "_governed_get", _meta_get)
    monkeypatch.setattr(adapter, "_governed_post", _meta_post)
    enricher = GitHubEnricher(adapter, use_graphql=True)
    sig = enricher.enrich("acme/gql-forked")
    assert sig.error is None
    assert sig.is_fork is True
    assert sig.is_archived is True
    assert sig.fork_parent == "upstream/gql-orig"


def test_contributors_http_202_retry_success(monkeypatch):
    adapter = GitHubAdapter()
    calls = 0

    def _contrib_get(url: str, params=None, headers=None):
        nonlocal calls
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        if url.endswith("/repos/acme/retry"):
            resp.json.return_value = {"default_branch": "main"}
        elif "/git/trees/main" in url:
            resp.json.return_value = {"tree": [], "truncated": False}
        elif url.endswith("/contributors"):
            calls += 1
            if calls == 1:
                resp.status_code = 202
            else:
                resp.status_code = 200
                resp.headers = {"link": '<https://api.github.com/x?page=5>; rel="last"'}
                resp.json.return_value = [{}]
        elif url.endswith("/commits"):
            resp.json.return_value = []
        return resp

    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(adapter, "_governed_get", _contrib_get)
    enricher = GitHubEnricher(adapter)
    sig = enricher.enrich("acme/retry")
    assert sig.error is None
    assert calls == 2
    assert sig.contributors == 5


def test_contributors_http_202_fallback_to_graphql_mentionable_users(monkeypatch):
    adapter = GitHubAdapter()

    def _contrib_get(url: str, params=None, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        if url.endswith("/repos/acme/fallback"):
            resp.json.return_value = {"default_branch": "main"}
        elif "/git/trees/main" in url:
            resp.json.return_value = {"tree": [], "truncated": False}
        elif url.endswith("/contributors"):
            resp.status_code = 202
        elif url.endswith("/commits"):
            resp.json.return_value = []
        return resp

    def _fake_post(url: str, json=None, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = {
            "data": {
                "repository": {
                    "mentionableUsers": {"totalCount": 42},
                }
            }
        }
        return resp

    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(adapter, "_governed_get", _contrib_get)
    monkeypatch.setattr(adapter, "_governed_post", _fake_post)
    enricher = GitHubEnricher(adapter, use_graphql=True)
    sig = enricher.enrich("acme/fallback")
    assert sig.error is None
    assert sig.contributors == 42


def test_contributors_http_202_returns_none_after_failed_retry(monkeypatch):
    adapter = GitHubAdapter()

    def _contrib_get(url: str, params=None, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        if url.endswith("/repos/acme/retry-fail"):
            resp.json.return_value = {"default_branch": "main"}
        elif "/git/trees/main" in url:
            resp.json.return_value = {"tree": [], "truncated": False}
        elif url.endswith("/contributors"):
            resp.status_code = 202
        elif url.endswith("/commits"):
            resp.json.return_value = []
        return resp

    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(adapter, "_governed_get", _contrib_get)
    enricher = GitHubEnricher(adapter)
    sig = enricher.enrich("acme/retry-fail")
    assert sig.error is None
    assert sig.contributors is None


def test_max_workflows_elevated_and_prioritizes_items(monkeypatch):
    from msrkit.enrich import MAX_WORKFLOWS

    assert MAX_WORKFLOWS == 40

    adapter = GitHubAdapter()
    fetched_workflows: list[str] = []

    tree_blobs = [{"path": f".github/workflows/wf_{i:02d}.yml", "type": "blob"} for i in range(15)]

    def _wf_get(url: str, params=None, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        if url.endswith("/repos/acme/workflows"):
            resp.json.return_value = {"default_branch": "main"}
        elif "/git/trees/main" in url:
            resp.json.return_value = {"tree": tree_blobs, "truncated": False}
        elif "/contents/.github/workflows/" in url:
            path_part = url.split("/contents/")[1]
            from urllib.parse import unquote

            path = unquote(path_part)
            fetched_workflows.append(path)
            resp.json.return_value = _b64("name: test\non: push\n")
        elif url.endswith("/contributors"):
            resp.status_code = 204
            resp.json.return_value = []
        elif url.endswith("/commits"):
            resp.json.return_value = []
        return resp

    monkeypatch.setattr(adapter, "_governed_get", _wf_get)
    enricher = GitHubEnricher(adapter)

    item = _item("https://github.com/acme/workflows/blob/main/.github/workflows/wf_14.yml")
    item.tech.path = ".github/workflows/wf_14.yml"

    sig = enricher.enrich("acme/workflows", items=[item])
    assert sig.error is None
    assert len(sig.workflows) == 15
    assert fetched_workflows[0] == ".github/workflows/wf_14.yml"


def test_reusable_signals_restores_unstripped_and_leaves_unknown_empty(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    from datetime import timedelta

    from msrkit.cli.evidence import _enrich_key, _reusable_signals

    monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
    key = _enrich_key(None, None, set())

    now = datetime.now(UTC)
    t1 = (now - timedelta(days=2)).isoformat()
    t2 = (now - timedelta(days=1)).isoformat()

    # Older run with full unstripped contents
    run_1_dir = tmp_path / "enrich" / "run-2026-03-01T00-00-00Z"
    run_1_dir.mkdir(parents=True)
    (run_1_dir / "meta.json").write_text(json.dumps(key), encoding="utf-8")
    run_1_sig = {
        "repo": "acme/rag",
        "default_branch": "main",
        "workflows": {"ci.yml": "name: CI\njobs: test\n"},
        "manifests": {"pyproject.toml": '[project]\nname = "rag"\n'},
        "fetched_at": t1,
        "error": None,
    }
    (run_1_dir / "github_repos.jsonl").write_text(json.dumps(run_1_sig) + "\n", encoding="utf-8")

    # Newer run within window, but stripped to path lists
    run_2_dir = tmp_path / "enrich" / "run-2026-03-10T00-00-00Z"
    run_2_dir.mkdir(parents=True)
    (run_2_dir / "meta.json").write_text(json.dumps(key), encoding="utf-8")
    run_2_sig = {
        "repo": "acme/rag",
        "default_branch": "main",
        "workflows": ["ci.yml", "deploy.yml"],
        "manifests": ["pyproject.toml"],
        "fetched_at": t2,
        "error": None,
    }
    (run_2_dir / "github_repos.jsonl").write_text(json.dumps(run_2_sig) + "\n", encoding="utf-8")

    pool, runs = _reusable_signals(
        run_id="run-target", key=key, wanted={"acme/rag"}, max_age_days=30
    )

    assert "acme/rag" in pool
    sig = pool["acme/rag"]
    assert sig.reused_from == "run-2026-03-10T00-00-00Z"
    # ci.yml and pyproject.toml restored from run 1
    assert sig.workflows["ci.yml"] == "name: CI\njobs: test\n"
    assert sig.manifests["pyproject.toml"] == '[project]\nname = "rag"\n'
    # deploy.yml was not in unstripped predecessor; remains empty for hydration in enrich
    assert sig.workflows["deploy.yml"] == ""


def test_enrich_hydrates_reused_signals_with_missing_contents_rest(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    from datetime import timedelta

    from msrkit.cli.evidence import _enrich_key

    monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
    monkeypatch.setattr("msrkit.cli.evidence._load_run_protocol", lambda *a, **k: None)
    monkeypatch.setattr(
        GitHubAdapter,
        "_governed_get",
        lambda self, url, params=None, headers=None: _fake_get(url, params),
    )

    key = _enrich_key(None, None, set())
    t1 = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    prior_dir = tmp_path / "enrich" / "run-prior-rest"
    prior_dir.mkdir(parents=True)
    (prior_dir / "meta.json").write_text(json.dumps(key), encoding="utf-8")
    prior_sig = {
        "repo": "acme/rag",
        "default_branch": "main",
        "has_ci": True,
        "workflows": [".github/workflows/eval.yml"],
        "manifests": ["pyproject.toml"],
        "fetched_at": t1,
        "error": None,
    }
    (prior_dir / "github_repos.jsonl").write_text(json.dumps(prior_sig) + "\n", encoding="utf-8")

    ItemStorage(tmp_path).save_items([_item("https://github.com/acme/rag")], "run-curr-rest")

    runner = CliRunner()
    result = runner.invoke(app, ["enrich", "--run", "run-curr-rest", "--reuse-days", "7"])
    assert result.exit_code == 0, result.stdout
    assert "1 reused" in result.stdout

    out_file = tmp_path / "enrich" / "run-curr-rest" / "github_repos.jsonl"
    assert out_file.exists()
    lines = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    hydrated_sig = lines[0]
    assert "npx promptfoo eval" in hydrated_sig["workflows"][".github/workflows/eval.yml"]
    assert "ragas>=0.1" in hydrated_sig["manifests"]["pyproject.toml"]


def test_enrich_hydrates_reused_signals_with_missing_contents_graphql(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    from datetime import timedelta

    from msrkit.cli.evidence import _enrich_key

    monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
    monkeypatch.setattr("msrkit.cli.evidence._load_run_protocol", lambda *a, **k: None)
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

    key = _enrich_key(None, None, set())
    t1 = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    prior_dir = tmp_path / "enrich" / "run-prior-gql"
    prior_dir.mkdir(parents=True)
    (prior_dir / "meta.json").write_text(json.dumps(key), encoding="utf-8")
    prior_sig = {
        "repo": "acme/rag",
        "default_branch": "main",
        "has_ci": True,
        "workflows": [".github/workflows/eval.yml"],
        "manifests": ["pyproject.toml"],
        "fetched_at": t1,
        "error": None,
    }
    (prior_dir / "github_repos.jsonl").write_text(json.dumps(prior_sig) + "\n", encoding="utf-8")

    ItemStorage(tmp_path).save_items([_item("https://github.com/acme/rag")], "run-curr-gql")

    runner = CliRunner()
    result = runner.invoke(
        app, ["enrich", "--run", "run-curr-gql", "--reuse-days", "7", "--graphql"]
    )
    assert result.exit_code == 0, result.stdout
    assert "1 reused" in result.stdout

    out_file = tmp_path / "enrich" / "run-curr-gql" / "github_repos.jsonl"
    assert out_file.exists()
    lines = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    hydrated_sig = lines[0]
    assert "npx promptfoo eval" in hydrated_sig["workflows"][".github/workflows/eval.yml"]
    assert "ragas>=0.1" in hydrated_sig["manifests"]["pyproject.toml"]
