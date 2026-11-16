"""Repository enrichment for artifact evidence (Protocol E2 v2, §3.4).

Search results say little about a repository's testing practice. For each GitHub
repository in a run, the enricher reads, through the official REST API and the
GitHub adapter's rate governor:

- the file tree (test directories, evaluation configs, CI workflows);
- the content of root dependency manifests and of workflow files, which the
  extractor (`msrkit extract`) scans for N2/N3 signals;
- the number of contributors and the distinct months with commits inside the
  protocol window — the N3 "sustained adoption" thresholds.

Results go to data/enrich/<run_id>/github_repos.jsonl (one record per
repository, with the SHA-256 of every response) and are summarized back into
the items (`tech.has_ci`, `tech.has_tests`, `tech.contributors`,
`tech.active_months`).
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlparse

from pydantic import BaseModel, field_validator

from msrkit.models import ItemKind, RateLimit

if TYPE_CHECKING:
    from msrkit.adapters.github import GitHubAdapter
    from msrkit.models import Item

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
TEST_DIRS = {
    "tests",
    "test",
    "evals",
    "eval",
    "evaluation",
    "__tests__",
    "spec",
    "benchmarks",
    "testing",
}
TEST_FILE_RE = re.compile(
    r"(^test_.*\.py$|.*_test\.py$|.*\.(test|spec)\.[jt]sx?$|.*_test\.go$|.*_test\.rs$)"
)
MANIFEST_RE = re.compile(
    r"^(pyproject\.toml|setup\.cfg|setup\.py|package\.json|requirements[^/]*\.txt|Cargo\.toml|go\.mod)$"
)
MAX_WORKFLOWS = 10
# Workflow files a code search hit pointed at are read first, and all of them up to
# this cap: they are the ones the extractor has to check for a CI invocation (ADR-056).
MAX_PRIORITY_WORKFLOWS = 20
WORKFLOW_DIR = ".github/workflows/"
MAX_MANIFESTS = 6
MAX_COMMIT_PAGES = 3
MAX_FILE_BYTES = 200_000
# Enrichment calls the core REST API (5,000 requests/hour with a token), not the
# search API the adapter's policy is tuned for (30/minute). With the search rate,
# 300 repositories x ~12 requests took about two hours; this keeps a margin under
# the core quota, and a 403/429 still backs off through the governor.
CORE_RATE_LIMIT = RateLimit(requests=4500, per_seconds=3600, burst=10)


def effective_core_rate_limit() -> RateLimit:
    """Core rate limit: 4,500 req/h with GITHUB_TOKEN, 55 req/h without token (burst 2)."""
    import os

    if os.environ.get("GITHUB_TOKEN"):
        return CORE_RATE_LIMIT
    return RateLimit(requests=55, per_seconds=3600, burst=2)


class RepoSignals(BaseModel):
    """What the enricher learned about one repository."""

    repo: str  # owner/name
    default_branch: str | None = None
    tree_truncated: bool = False
    has_tests: bool = False
    test_paths: list[str] = []  # sample, at most 50
    has_ci: bool = False
    workflows: dict[str, str] = {}  # path -> content
    # Workflow files a code search hit pointed at that could not be read: not in the
    # tree any more, too large, or over the cap. Known as tried, so not fetched again.
    workflows_missing: list[str] = []
    config_paths: list[str] = []  # files named in the gazetteer's config_files
    manifests: dict[str, str] = {}  # root dependency files: path -> content
    contributors: int | None = None
    commit_months: list[str] = []  # YYYY-MM with commits inside the window
    requests: int = 0
    response_sha256: list[str] = []
    fetched_at: str = ""
    error: str | None = None
    reused_from: str | None = None  # run that fetched these signals, when copied (ADR-054)

    @field_validator("workflows", "manifests", mode="before")
    @classmethod
    def _coerce_dict(cls, v: Any) -> dict[str, str]:
        if isinstance(v, list):
            return {p: "" for p in v}
        return v or {}


def repo_of(item: Item) -> str | None:
    """owner/name of the GitHub repository an item belongs to."""
    if item.source != "github":
        return None
    parts = [p for p in urlparse(str(item.url)).path.split("/") if p]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}".lower()


def _is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return any(part in TEST_DIRS for part in pure.parts[:-1]) or bool(TEST_FILE_RE.match(pure.name))


def workflow_hits(items: list[Item]) -> dict[str, list[str]]:
    """Repository -> workflow files that a code search of the run returned, in order."""
    hits: dict[str, list[str]] = {}
    for it in items:
        path = it.tech.path or ""
        repo = repo_of(it)
        if repo and it.kind == ItemKind.CODE and path.startswith(WORKFLOW_DIR):
            paths = hits.setdefault(repo, [])
            if path not in paths:
                paths.append(path)
    return hits


def select_workflows(
    in_tree: list[str], priority: list[str] | None, tree_complete: bool = True
) -> list[str]:
    """Workflow files to read: the ones code search hit first, then others up to the cap.

    A hit that is no longer in the tree is skipped, unless the tree listing was cut
    short by the API: then the file may still exist, and reading it settles it.
    """
    present = set(in_tree)
    first = [p for p in dict.fromkeys(priority or []) if p in present or not tree_complete]
    first = first[:MAX_PRIORITY_WORKFLOWS]
    chosen = set(first)
    rest = [p for p in in_tree if p not in chosen]
    return first + rest[: max(MAX_WORKFLOWS - len(first), 0)]


def has_file_contents(record: dict[str, Any]) -> bool:
    """Whether a stored signal still carries the files it read.

    The reproducibility package keeps the paths only (a list, or empty contents), and a
    signal copied from it would yield no dependency and no CI detection.
    """
    contents: list[str] = []
    for field in ("workflows", "manifests"):
        files = record.get(field) or {}
        if isinstance(files, list):
            if files:
                return False  # the package's format: paths only
        else:
            contents += list(files.values())
    # A repository may hold an empty file; none of its files with content is the sign.
    return not contents or any(contents)


def covers_workflows(sig: RepoSignals, wanted: list[str] | None) -> bool:
    """Whether the signal read, or already tried to read, every workflow in `wanted`."""
    known = set(sig.workflows) | set(sig.workflows_missing)
    return all(p in known for p in wanted or [])


class GitHubEnricher:
    """Collects repository signals through a (governed) GitHub adapter."""

    def __init__(
        self,
        adapter: GitHubAdapter,
        config_files: set[str] | None = None,
        since: date | None = None,
        until: date | None = None,
        use_graphql: bool = False,
    ) -> None:
        self._adapter = adapter
        self._config_files = {c.lower() for c in (config_files or set())}
        self._since = since
        self._until = until
        self._use_graphql = use_graphql

    def _get(self, sig: RepoSignals, url: str, params: dict[str, Any] | None = None) -> Any:
        resp = self._adapter._governed_get(url, params=params)
        sig.requests += 1
        sig.response_sha256.append(self._adapter.last_response_sha256)
        return resp

    def _post_graphql(self, sig: RepoSignals, query: str, variables: dict[str, Any]) -> Any:
        resp = self._adapter._governed_post(
            f"{_API}/graphql",
            json={"query": query, "variables": variables},
        )
        sig.requests += 1
        sig.response_sha256.append(self._adapter.last_response_sha256)
        return resp

    def enrich(
        self,
        repo: str,
        default_branch: str | None = None,
        priority_paths: list[str] | None = None,
    ) -> RepoSignals:
        """Read a repository's signals; `priority_paths` are workflow files to read first."""
        sig = RepoSignals(repo=repo, fetched_at=datetime.now(UTC).isoformat())
        try:
            self._collect(sig, default_branch, priority_paths)
        except Exception as e:  # recorded, never fatal for the batch
            sig.error = f"{type(e).__name__}: {e}"
        return sig

    def _collect_graphql(
        self,
        sig: RepoSignals,
        default_branch: str,
        workflow_paths: list[str],
        manifest_paths: list[str],
    ) -> bool:
        """Batch fetch file contents, contributors, and commit months in 1 GraphQL query."""
        if "/" not in sig.repo:
            return False
        owner, name = sig.repo.split("/", 1)

        wf_aliases = {f"wf_{i}": p for i, p in enumerate(workflow_paths)}
        mf_aliases = {f"mf_{i}": p for i, p in enumerate(manifest_paths[:MAX_MANIFESTS])}

        blob_fields: list[str] = []
        ref = default_branch or "HEAD"
        for alias, path in {**wf_aliases, **mf_aliases}.items():
            safe_expr = f"{ref}:{path}"
            blob_fields.append(
                f'{alias}: object(expression: "{safe_expr}") {{ ... on Blob {{ text byteSize }} }}'
            )

        blobs_subquery = "\n    ".join(blob_fields) if blob_fields else ""

        if not blob_fields:
            return True

        query = f"""
query RepoEnrich($owner: String!, $name: String!) {{
  repository(owner: $owner, name: $name) {{
    {blobs_subquery}
  }}
}}
"""
        variables: dict[str, Any] = {"owner": owner, "name": name}

        resp = self._post_graphql(sig, query, variables)
        if resp.status_code != 200:
            logger.warning("GraphQL request failed with HTTP %d for %s", resp.status_code, sig.repo)
            return False

        body = resp.json()
        if not isinstance(body, dict):
            return False
        data = body.get("data")
        if not isinstance(data, dict):
            return False
        repo_data = data.get("repository")
        if not isinstance(repo_data, dict):
            return False

        # 3. Workflows
        for alias, path in wf_aliases.items():
            blob = repo_data.get(alias)
            if isinstance(blob, dict):
                text = blob.get("text")
                if text is not None and (blob.get("byteSize") or 0) <= MAX_FILE_BYTES:
                    sig.workflows[path] = text

        # 4. Manifests
        for alias, path in mf_aliases.items():
            blob = repo_data.get(alias)
            if isinstance(blob, dict):
                text = blob.get("text")
                if text is not None and (blob.get("byteSize") or 0) <= MAX_FILE_BYTES:
                    sig.manifests[path] = text

        return True

    def _collect(
        self, sig: RepoSignals, default_branch: str | None, priority_paths: list[str] | None = None
    ) -> None:
        repo = sig.repo
        if not default_branch:
            resp = self._get(sig, f"{_API}/repos/{repo}")
            if resp.status_code != 200:
                sig.error = f"repo metadata HTTP {resp.status_code}"
                return
            default_branch = resp.json().get("default_branch")
        sig.default_branch = default_branch
        if not default_branch:
            sig.error = "empty repository (no default branch)"
            return

        resp = self._get(
            sig, f"{_API}/repos/{repo}/git/trees/{quote(str(default_branch))}", {"recursive": "1"}
        )
        if resp.status_code != 200:
            sig.error = f"tree HTTP {resp.status_code}"
            return
        tree = resp.json()
        sig.tree_truncated = bool(tree.get("truncated"))
        paths = [e["path"] for e in tree.get("tree", []) if e.get("type") == "blob"]

        test_paths = [p for p in paths if _is_test_path(p)]
        sig.has_tests = bool(test_paths)
        sig.test_paths = test_paths[:50]

        all_workflows = [
            p for p in paths if p.startswith(WORKFLOW_DIR) and p.endswith((".yml", ".yaml"))
        ]
        sig.has_ci = bool(all_workflows)
        workflow_paths = select_workflows(all_workflows, priority_paths, not sig.tree_truncated)
        sig.config_paths = [p for p in paths if PurePosixPath(p).name.lower() in self._config_files]
        manifest_paths = [p for p in paths if "/" not in p and MANIFEST_RE.match(p)]

        graphql_ok = False
        if self._use_graphql:
            try:
                graphql_ok = self._collect_graphql(
                    sig, str(default_branch), workflow_paths, manifest_paths
                )
            except Exception as e:
                logger.debug("GraphQL collection failed for %s, falling back to REST: %s", repo, e)
                graphql_ok = False

        if not graphql_ok:
            for path in workflow_paths:
                content = self._file(sig, path, default_branch)
                if content is not None:
                    sig.workflows[path] = content
            for path in manifest_paths[:MAX_MANIFESTS]:
                content = self._file(sig, path, default_branch)
                if content is not None:
                    sig.manifests[path] = content

        sig.workflows_missing = [
            p for p in dict.fromkeys(priority_paths or []) if p not in sig.workflows
        ]
        sig.contributors = self._contributors(sig)
        sig.commit_months = self._commit_months(sig)

    def _file(self, sig: RepoSignals, path: str, ref: str | None) -> str | None:
        resp = self._get(
            sig, f"{_API}/repos/{sig.repo}/contents/{quote(path)}", {"ref": ref} if ref else None
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return None
        if (data.get("size") or 0) > MAX_FILE_BYTES:
            return None
        return base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")

    def _contributors(self, sig: RepoSignals) -> int | None:
        resp = self._get(
            sig, f"{_API}/repos/{sig.repo}/contributors", {"per_page": 1, "anon": "true"}
        )
        if resp.status_code == 204:
            return 0
        if resp.status_code != 200:
            return None
        last = re.search(r'[?&]page=(\d+)>; rel="last"', resp.headers.get("link", ""))
        data = resp.json()
        return int(last.group(1)) if last else (len(data) if isinstance(data, list) else 0)

    def _commit_months(self, sig: RepoSignals) -> list[str]:
        params: dict[str, Any] = {"per_page": 100}
        if self._since:
            params["since"] = f"{self._since.isoformat()}T00:00:00Z"
        if self._until:
            params["until"] = f"{self._until.isoformat()}T23:59:59Z"
        months: set[str] = set()
        for page in range(1, MAX_COMMIT_PAGES + 1):
            resp = self._get(sig, f"{_API}/repos/{sig.repo}/commits", {**params, "page": page})
            if resp.status_code != 200:
                break
            commits = resp.json()
            if not isinstance(commits, list):
                break
            for c in commits:
                if not isinstance(c, dict):
                    continue
                stamp = ((c.get("commit") or {}).get("committer") or {}).get("date") or ""
                if len(stamp) >= 7:
                    months.add(stamp[:7])
            if len(commits) < 100:
                break
        return sorted(months)


def enrich_priority(items: list[Item]) -> list[str]:
    """Repositories ordered by the evidence they can yield, for a limited enrichment.

    CI workflow hits first (the N3 candidates), then other code hits (N2), then
    repositories found as such, then the rest (e.g. only issues); ties keep the
    collection order.
    """
    score: dict[str, int] = {}
    for it in items:
        repo = repo_of(it)
        if not repo:
            continue
        path = it.tech.path or ""
        if path.startswith(".github/workflows/"):
            s = 3
        elif it.kind == ItemKind.CODE:
            s = 2
        elif it.kind == ItemKind.REPO:
            s = 1
        else:
            s = 0
        score[repo] = max(score.get(repo, 0), s)
    order = list(score)
    return sorted(order, key=lambda r: (-score[r], order.index(r)))


def apply_signals(items: list[Item], signals: dict[str, RepoSignals]) -> int:
    """Copy repository signals into the items of each repository; returns items updated."""
    updated = 0
    for item in items:
        repo = repo_of(item)
        sig = signals.get(repo or "")
        if sig is None or sig.error:
            continue
        item.tech.has_ci = sig.has_ci
        item.tech.has_tests = sig.has_tests
        item.tech.contributors = sig.contributors
        item.tech.active_months = len(sig.commit_months)
        updated += 1
    return updated
