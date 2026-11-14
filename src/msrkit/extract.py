"""Tool and method extraction with evidence levels (Protocol E2 v2, §3.4 and §12.1).

Signals and the level each one supports:

| Signal       | Where                                                    | Level |
|--------------|----------------------------------------------------------|-------|
| `text`       | tool/method name in an item's title, body or tags        | N1    |
| `import`     | code search hit whose literal query is an import of it   | N2    |
| `config`     | tool config file in the repository tree or as a hit      | N2    |
| `dependency` | tool package in a root dependency manifest               | N2    |
| `ci`         | tool invoked in a CI workflow file                       | N3*   |

*A CI invocation is N3 only when the repository also has commits in ≥2
distinct months of the window and ≥2 contributors; otherwise it counts as N2.

Methods are detected by name in text only (N1). Repository-level signals come
from `msrkit enrich` and are attached to the repository item (or, if the run has
none, to the first item of that repository).
"""

from __future__ import annotations

import configparser
import json
import re
import tomllib
from functools import lru_cache
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from msrkit.enrich import repo_of
from msrkit.keywords import match_terms
from msrkit.models import ItemKind

if TYPE_CHECKING:
    from msrkit.enrich import RepoSignals
    from msrkit.gazetteer import Gazetteer, MethodEntry, ToolEntry
    from msrkit.models import Item

Level = Literal["N1", "N2", "N3"]
Signal = Literal["text", "import", "config", "dependency", "ci"]
LEVEL_RANK = {"N1": 1, "N2": 2, "N3": 3}
N3_MIN_MONTHS = 2
N3_MIN_CONTRIBUTORS = 2
EVIDENCE_MAX = 300


class Detection(BaseModel):
    """One tool or method found in one item, with the evidence that supports it."""

    item_id: str
    source: str
    repo: str | None = None
    entry_id: str
    entry_type: Literal["tool", "method"]
    level: Level
    signal: Signal
    location: str  # item field, file path or query
    evidence: str  # at most EVIDENCE_MAX characters
    # For ambiguous tool names (§12.2): what confirmed the mention.
    confirmed_by: Literal["", "context", "structural"] = ""


def is_sustained(sig: RepoSignals) -> bool:
    """N3 thresholds: commits in >= 2 months of the window and >= 2 contributors."""
    return (
        not sig.error
        and len(sig.commit_months) >= N3_MIN_MONTHS
        and (sig.contributors or 0) >= N3_MIN_CONTRIBUTORS
    )


def _norm_pkg(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EVIDENCE_MAX else text[: EVIDENCE_MAX - 1] + "…"


def _parse_pep508_name(spec: str) -> str | None:
    raw = spec.strip()
    if not raw or raw.startswith("#"):
        return None
    # Egg in URL: git+https://...#egg=pkg
    m_egg = re.search(r"#egg=([A-Za-z0-9][A-Za-z0-9._-]*)", raw)
    if m_egg:
        return m_egg.group(1)
    # Strip comments if not egg
    if " #" in raw:
        raw = raw.split(" #", 1)[0].strip()
    # Check pkg @ url
    m_at = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*@", raw)
    if m_at:
        return m_at.group(1)
    # Check git/vcs/http direct url: git+https://github.com/vibrantlabsai/ragas.git
    if any(raw.startswith(p) for p in ("git+", "https://", "http://", "ssh://", "git://")):
        url = raw.split("#", 1)[0]
        last_seg = url.split("/")[-1].split("@", 1)[0]
        if last_seg.endswith(".git"):
            last_seg = last_seg[:-4]
        if re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", last_seg):
            cand = last_seg.lower()
            if cand not in {"git", "https", "http"}:
                return last_seg
        return None
    # Standard PEP 508: name [extras] specifier ; markers
    m_pep = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:[<>=!~;@\s].*|$)", raw)
    if m_pep:
        cand = m_pep.group(1)
        if cand.lower() not in {"git", "https", "http"}:
            return cand
    return None


def _parse_req_line(line: str) -> str | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    # Strip editable flags
    if raw.startswith(("-e ", "--editable ")):
        raw = re.sub(r"^(-e|--editable)\s+", "", raw).strip()
    # Ignore flags or local directories
    if raw.startswith(("-", "--", ".", "/")):
        return None
    return _parse_pep508_name(raw)


def dependency_names(path: str, content: str) -> dict[str, str]:
    """Normalized dependency name -> the line that declares it."""
    names: dict[str, str] = {}
    base = PurePosixPath(path).name
    if base == "package.json":
        try:
            data = json.loads(content)
        except ValueError:
            return names
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            for dep, version in (data.get(key) or {}).items():
                names.setdefault(_norm_pkg(dep), f'"{dep}": "{version}"')
        return names

    if base == "pyproject.toml":
        try:
            data = tomllib.loads(content)
        except (ValueError, tomllib.TOMLDecodeError):
            return names

        # 1. project.dependencies
        proj = data.get("project")
        if isinstance(proj, dict):
            deps = proj.get("dependencies")
            if isinstance(deps, list):
                for dep in deps:
                    if isinstance(dep, str):
                        pkg = _parse_pep508_name(dep)
                        if pkg:
                            names.setdefault(_norm_pkg(pkg), dep.strip())
            # 2. project.optional-dependencies (values only)
            opt_deps = proj.get("optional-dependencies")
            if isinstance(opt_deps, dict):
                for _grp, grp_deps in opt_deps.items():
                    if isinstance(grp_deps, list):
                        for dep in grp_deps:
                            if isinstance(dep, str):
                                pkg = _parse_pep508_name(dep)
                                if pkg:
                                    names.setdefault(_norm_pkg(pkg), dep.strip())

        # 3. dependency-groups (strings only, ignore dicts)
        dep_groups = data.get("dependency-groups")
        if isinstance(dep_groups, dict):
            for _grp, grp_items in dep_groups.items():
                if isinstance(grp_items, list):
                    for item in grp_items:
                        if isinstance(item, str):
                            pkg = _parse_pep508_name(item)
                            if pkg:
                                names.setdefault(_norm_pkg(pkg), item.strip())

        # 4. tool.poetry.dependencies / dev-dependencies / group.*.dependencies
        tool = data.get("tool")
        if isinstance(tool, dict):
            poetry = tool.get("poetry")
            if isinstance(poetry, dict):
                for sec in ("dependencies", "dev-dependencies"):
                    sec_deps = poetry.get(sec)
                    if isinstance(sec_deps, dict):
                        for dep, spec in sec_deps.items():
                            if dep.lower() != "python":
                                spec_str = (
                                    f'{dep} = "{spec}"'
                                    if isinstance(spec, str)
                                    else f"{dep} = {spec}"
                                )
                                names.setdefault(_norm_pkg(dep), spec_str)
                groups = poetry.get("group")
                if isinstance(groups, dict):
                    for _grp_name, grp_data in groups.items():
                        if isinstance(grp_data, dict):
                            grp_deps = grp_data.get("dependencies")
                            if isinstance(grp_deps, dict):
                                for dep, spec in grp_deps.items():
                                    if dep.lower() != "python":
                                        spec_str = (
                                            f'{dep} = "{spec}"'
                                            if isinstance(spec, str)
                                            else f"{dep} = {spec}"
                                        )
                                        names.setdefault(_norm_pkg(dep), spec_str)
        return names

    if base == "Cargo.toml":
        try:
            data = tomllib.loads(content)
        except (ValueError, tomllib.TOMLDecodeError):
            return names
        for sec in ("dependencies", "dev-dependencies", "build-dependencies"):
            sec_dict = data.get(sec)
            if isinstance(sec_dict, dict):
                for dep, spec in sec_dict.items():
                    spec_str = f'{dep} = "{spec}"' if isinstance(spec, str) else f"{dep} = {spec}"
                    names.setdefault(_norm_pkg(dep), spec_str)
        ws = data.get("workspace")
        if isinstance(ws, dict):
            ws_deps = ws.get("dependencies")
            if isinstance(ws_deps, dict):
                for dep, spec in ws_deps.items():
                    spec_str = f'{dep} = "{spec}"' if isinstance(spec, str) else f"{dep} = {spec}"
                    names.setdefault(_norm_pkg(dep), spec_str)
        return names

    if base == "setup.cfg":
        cfg = configparser.ConfigParser(interpolation=None)
        try:
            cfg.read_string(content)
        except Exception:
            return names
        if cfg.has_option("options", "install_requires"):
            for line in cfg.get("options", "install_requires").splitlines():
                pkg = _parse_req_line(line)
                if pkg:
                    names.setdefault(_norm_pkg(pkg), line.strip())
        if cfg.has_section("options.extras_require"):
            for _extra, extra_val in cfg.items("options.extras_require"):
                for line in extra_val.splitlines():
                    pkg = _parse_req_line(line)
                    if pkg:
                        names.setdefault(_norm_pkg(pkg), line.strip())
        return names

    if base.endswith(".txt"):
        for line in content.splitlines():
            pkg = _parse_req_line(line)
            if pkg:
                names.setdefault(_norm_pkg(pkg), line.strip())
        return names

    if base == "go.mod":
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            go_match = re.search(r"(?:require\s+)?([A-Za-z0-9._/-]+)\s+v[0-9]", stripped)
            if go_match:
                pkg_full = go_match.group(1)
                names.setdefault(_norm_pkg(pkg_full), stripped)
                names.setdefault(_norm_pkg(pkg_full.split("/")[-1]), stripped)
        return names

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        candidates = re.findall(
            r'["\']([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*[<>=!~;"\']', stripped
        )
        for cand in candidates:
            if cand.lower() not in {"git", "https", "http", "name", "keywords", "testpaths"}:
                names.setdefault(_norm_pkg(cand), stripped)
    return names


_SPLIT_CMDS = re.compile(r"\s*(?:&&|\|\||;)\s*")
_YAML_KEY_COLON = re.compile(r"^[\w-]+:")
_ENV_VAR = re.compile(r"^[A-Z0-9_]+\s*[:=]")
_RUN_RE = re.compile(r"(?:^|\s)(?:-\s+)?run:\s*(.*)")
_USES_RE = re.compile(r"(?:^|\s)(?:-\s+)?uses:\s*(.*)")
_PIP_INSTALL_RE = re.compile(r"^(?:pip\d?|uv\s+pip|uv|poetry|npm|pnpm|yarn)\s+(?:install|add)\b")
_ECHO_RE = re.compile(r"^echo\b")


@lru_cache(maxsize=512)
def _compile_import_patterns(python_imports: tuple[str, ...]) -> list[re.Pattern[str]]:
    return [
        re.compile(
            rf"(?:^|\W)(?:from\s+{re.escape(mod)}(?:\.\w+)*\s+import|import\s+{re.escape(mod)}\b)"
        )
        for mod in python_imports
    ]


@lru_cache(maxsize=512)
def _compile_ci_patterns(
    cli: tuple[str, ...], python_imports: tuple[str, ...]
) -> list[re.Pattern[str]]:
    commands = [
        *cli,
        *(f"python -m {m}" for m in python_imports),
        *(f"python3 -m {m}" for m in python_imports),
    ]
    patterns = [re.compile(rf"(?<![\w-]){re.escape(c)}(?![\w-])", re.IGNORECASE) for c in commands]
    for m in python_imports:
        patterns.append(re.compile(rf"\bpytest\b[^\n]*\b{re.escape(m)}\b", re.IGNORECASE))
    return patterns


def _import_patterns(tool: ToolEntry) -> list[re.Pattern[str]]:
    return _compile_import_patterns(tuple(tool.python_imports))


def _ci_patterns(tool: ToolEntry) -> list[re.Pattern[str]]:
    return _compile_ci_patterns(tuple(tool.cli), tuple(tool.python_imports))


def _subcmd_is_real_invocation(sub: str, tool: ToolEntry, patterns: list[re.Pattern[str]]) -> bool:
    sub = sub.strip()
    if not sub:
        return False
    if _PIP_INSTALL_RE.match(sub):
        return False
    if _ECHO_RE.match(sub):
        return False
    return any(p.search(sub) for p in patterns)


_YAML_IGNORED_KEYS = (
    "name:",
    "description:",
    "on:",
    "paths:",
    "branches:",
    "concurrency:",
    "outputs:",
    "if:",
    "id:",
    "timeout-minutes:",
    "continue-on-error:",
    "strategy:",
    "matrix:",
    "needs:",
    "runs-on:",
    "defaults:",
    "working-directory:",
    "shell:",
    "env:",
)


def _extract_workflow_commands(
    content: str, wf_path: str = ""
) -> list[tuple[Literal["run", "uses"], str, tuple[str, ...]]]:
    commands: list[tuple[Literal["run", "uses"], str, tuple[str, ...]]] = []
    lines = content.splitlines()
    in_run_block = False
    run_indent = 0
    is_script = wf_path.endswith((".sh", ".bash")) if wf_path else False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if is_script:
            subcmds = tuple(
                s.strip()
                for s in _SPLIT_CMDS.split(stripped)
                if s.strip()
                and not _PIP_INSTALL_RE.match(s.strip())
                and not _ECHO_RE.match(s.strip())
            )
            if subcmds:
                commands.append(("run", line, subcmds))
            continue

        indent = len(line) - len(line.lstrip())

        if in_run_block:
            if indent <= run_indent and (
                stripped.startswith("- ") or _YAML_KEY_COLON.match(stripped)
            ):
                in_run_block = False
            else:
                subcmds = tuple(
                    s.strip()
                    for s in _SPLIT_CMDS.split(stripped)
                    if s.strip()
                    and not _PIP_INSTALL_RE.match(s.strip())
                    and not _ECHO_RE.match(s.strip())
                )
                if subcmds:
                    commands.append(("run", line, subcmds))
                continue

        sub = stripped[2:].strip() if stripped.startswith("- ") else stripped
        if sub.startswith(_YAML_IGNORED_KEYS):
            continue
        if _ENV_VAR.match(sub):
            continue

        m_run = _RUN_RE.search(line)
        if m_run:
            cmd = m_run.group(1).strip()
            if cmd in ("|", ">", "|-", ">-", "|+", ">+") or not cmd:
                in_run_block = True
                run_indent = indent
                continue
            subcmds = tuple(
                s.strip()
                for s in _SPLIT_CMDS.split(cmd)
                if s.strip()
                and not _PIP_INSTALL_RE.match(s.strip())
                and not _ECHO_RE.match(s.strip())
            )
            if subcmds:
                commands.append(("run", line, subcmds))
            continue

        m_uses = _USES_RE.search(line)
        if m_uses:
            uses_val = m_uses.group(1).strip()
            commands.append(("uses", line, (uses_val,)))
            continue

    return commands


def _match_workflow_commands(
    commands: list[tuple[Literal["run", "uses"], str, tuple[str, ...]]],
    tool: ToolEntry,
) -> str | None:
    patterns = _ci_patterns(tool)
    tool_id_slash = f"{tool.id}/".lower() if tool.id else None
    tool_repos_lower = [r.lower() for r in getattr(tool, "repos", [])]
    for kind, line, args in commands:
        if kind == "run":
            if any(any(p.search(s) for p in patterns) for s in args):
                return line
        elif kind == "uses":
            uses_val = args[0]
            uses_lower = uses_val.lower()
            if (
                any(p.search(uses_val) for p in patterns)
                or (tool_id_slash and tool_id_slash in uses_lower)
                or any(r in uses_lower for r in tool_repos_lower)
            ):
                return line
    return None


def _ci_invocation_line(content: str, tool: ToolEntry, wf_path: str = "") -> str | None:
    commands = _extract_workflow_commands(content, wf_path)
    return _match_workflow_commands(commands, tool)


class Extractor:
    """Applies the gazetteer to items and repository signals."""

    def __init__(self, gazetteer: Gazetteer, context_terms: list[str] | None = None) -> None:
        self.gazetteer = gazetteer
        # Lexicon that confirms an ambiguous name when found in its context window.
        self.context_terms = context_terms or []
        self.dropped_ambiguous = 0  # ambiguous mentions without confirmation, set by run()
        self._pending: list[Detection] = []
        self._tool_cache = [
            (
                tool,
                tool.names(),
                re.compile(rf"(?<![\w-]){re.escape(tool.token())}(?![\w-])", re.IGNORECASE),
                _import_patterns(tool),
                frozenset(c.lower() for c in tool.config_files),
            )
            for tool in self.gazetteer.tools
        ]
        self._method_cache = [(method, method.names()) for method in self.gazetteer.methods]

    # -- item-level ----------------------------------------------------------

    def from_item(self, item: Item) -> list[Detection]:
        repo = repo_of(item)
        out: list[Detection] = []
        title_to_match = item.title or ""
        if (
            item.kind == ItemKind.REPO
            and title_to_match
            and "/" in title_to_match
            and " " not in title_to_match
        ):
            title_to_match = title_to_match.split("/", 1)[1]

        text_fields = {
            "title": title_to_match,
            "body": item.body,
            "tags": " ".join(item.tech.tags) or None,
        }

        for tool, names, _token_pat, _imports, _configs in self._tool_cache:
            hits = match_terms(
                names,
                title=text_fields["title"],
                body=text_fields["body"],
                tags=item.tech.tags or None,
                mode="exact",
            )
            self._disambiguate_hits(item, repo, tool, "tool", hits, out)

        for method, names in self._method_cache:
            hits = match_terms(
                names,
                title=text_fields["title"],
                body=text_fields["body"],
                tags=item.tech.tags or None,
            )
            self._disambiguate_hits(item, repo, method, "method", hits, out)

        if item.kind == ItemKind.CODE:
            out.extend(self._from_code_hit(item, repo))
        return out

    def _disambiguate_hits(
        self,
        item: Item,
        repo: str | None,
        entry: ToolEntry | MethodEntry,
        entry_type: Literal["tool", "method"],
        hits: list[Any],
        out: list[Detection],
    ) -> None:
        if not hits:
            return
        if not getattr(entry, "ambiguous", False):
            out.append(
                self._det(
                    item, repo, entry.id, entry_type, "N1", "text", hits[0].field, hits[0].context
                )
            )
            return

        confirmed = next(
            (
                h
                for h in hits
                if self.context_terms and match_terms(self.context_terms, body=h.context)
            ),
            None,
        )
        det = self._det(
            item,
            repo,
            entry.id,
            entry_type,
            "N1",
            "text",
            (confirmed or hits[0]).field,
            (confirmed or hits[0]).context,
        )
        if confirmed is not None:
            out.append(det.model_copy(update={"confirmed_by": "context"}))
        else:
            self._pending.append(det)

    def _from_code_hit(self, item: Item, repo: str | None) -> list[Detection]:
        """A code search hit contains the literal query string (GitHub guarantees the match).

        A hit in a CI workflow for a query naming a tool is a CI invocation: N2 here,
        promoted to N3 in run() when the repository shows sustained adoption.
        """
        query = item.provenance.query_string
        path = item.tech.path or item.title or ""
        name = PurePosixPath(path).name.lower()
        in_workflow = path.startswith(".github/workflows/")
        out: list[Detection] = []
        for tool, _names, token_pat, import_pats, config_set in self._tool_cache:
            if in_workflow and token_pat.search(query):
                out.append(self._det(item, repo, tool.id, "tool", "N2", "ci", path, query))
            if any(p.search(query) for p in import_pats):
                out.append(self._det(item, repo, tool.id, "tool", "N2", "import", path, query))
            if name in config_set:
                out.append(self._det(item, repo, tool.id, "tool", "N2", "config", path, name))
        return out

    # -- repository-level ----------------------------------------------------

    def from_repo(self, sig: RepoSignals, item: Item) -> list[Detection]:
        out: list[Detection] = []
        if sig.error:
            return out
        sustained = is_sustained(sig)
        deps: dict[str, tuple[str, str]] = {}
        for path, content in sig.manifests.items():
            for dep, line in dependency_names(path, content).items():
                deps.setdefault(dep, (path, line))
        config_names = {PurePosixPath(p).name.lower(): p for p in sig.config_paths}

        parsed_workflows = [
            (wf_path, _extract_workflow_commands(content, wf_path))
            for wf_path, content in sig.workflows.items()
        ]

        for tool in self.gazetteer.tools:
            for pkg in tool.packages:
                if _norm_pkg(pkg) in deps:
                    path, line = deps[_norm_pkg(pkg)]
                    out.append(
                        self._det(item, sig.repo, tool.id, "tool", "N2", "dependency", path, line)
                    )
                    break
            for cfg in tool.config_files:
                if cfg.lower() in config_names:
                    path = config_names[cfg.lower()]
                    out.append(
                        self._det(item, sig.repo, tool.id, "tool", "N2", "config", path, path)
                    )
                    break
            for wf_path, cmds in parsed_workflows:
                ci_line = _match_workflow_commands(cmds, tool)
                if ci_line is not None:
                    level: Level = "N3" if sustained else "N2"
                    out.append(
                        self._det(item, sig.repo, tool.id, "tool", level, "ci", wf_path, ci_line)
                    )
                    break
        return out

    # -- run-level -----------------------------------------------------------

    def run(self, items: list[Item], signals: dict[str, RepoSignals]) -> list[Detection]:
        detections: list[Detection] = []
        self._pending = []
        norm_signals = {k.lower(): v for k, v in signals.items()}
        enriched_workflows = {
            (r, p)
            for r, s in norm_signals.items()
            for p, c in s.workflows.items()
            if c and c.strip()
        }

        anchor_priority = {ItemKind.REPO: 3, ItemKind.CODE: 2}
        anchor_item: dict[str, Item] = {}
        for item in items:
            item_dets = self.from_item(item)
            if enriched_workflows:
                repo_norm = (repo_of(item) or "").lower()
                path = item.tech.path or item.title or ""
                if (repo_norm, path) in enriched_workflows:
                    item_dets = [d for d in item_dets if d.signal != "ci"]
            detections.extend(item_dets)
            repo = repo_of(item)
            if repo and item.kind != ItemKind.ISSUE:
                repo_norm = repo.lower()
                current = anchor_item.get(repo_norm)
                if current is None or (
                    anchor_priority.get(item.kind, 1) > anchor_priority.get(current.kind, 1)
                ):
                    anchor_item[repo_norm] = item
        for repo_norm, sig in norm_signals.items():
            if repo_norm in anchor_item:
                detections.extend(self.from_repo(sig, anchor_item[repo_norm]))
        # CI invocations found by code search become N3 where adoption is sustained.
        detections = [
            d.model_copy(update={"level": "N3"})
            if (
                d.signal == "ci"
                and d.level == "N2"
                and d.repo
                and d.repo.lower() in norm_signals
                and is_sustained(norm_signals[d.repo.lower()])
            )
            else d
            for d in detections
        ]

        structural = {
            (d.entry_id, (d.repo or "").lower())
            for d in detections
            if d.signal != "text" and d.repo
        }
        self.dropped_ambiguous = 0
        for det in self._pending:
            det_repo = (det.repo or "").lower()
            if det.entry_type == "tool" and det_repo and (det.entry_id, det_repo) in structural:
                detections.append(det.model_copy(update={"confirmed_by": "structural"}))
            else:
                self.dropped_ambiguous += 1
        self._pending = []
        return detections

    @staticmethod
    def _det(
        item: Item,
        repo: str | None,
        entry_id: str,
        entry_type: Literal["tool", "method"],
        level: Level,
        signal: Signal,
        location: str | None,
        evidence: str,
    ) -> Detection:
        norm_repo = repo.lower() if repo else None
        return Detection(
            item_id=item.id,
            source=item.source,
            repo=norm_repo,
            entry_id=entry_id,
            entry_type=entry_type,
            level=level,
            signal=signal,
            location=location or "",
            evidence=_clip(evidence),
        )


def summarize(detections: list[Detection]) -> dict[str, dict[str, int]]:
    """Per entry: number of distinct units (repository, or item outside GitHub) at each level.

    Each unit counts once per entry, at the highest level reached — so N1, N2 and N3
    are disjoint counts, as §3.4 requires for reporting.
    """
    best: dict[tuple[str, str], str] = {}
    for d in detections:
        unit = d.repo.lower() if d.repo else f"item:{d.item_id}"
        key = (d.entry_id, unit)
        if LEVEL_RANK[d.level] > LEVEL_RANK.get(best.get(key, ""), 0):
            best[key] = d.level
    table: dict[str, dict[str, int]] = {}
    for (entry, _unit), level in best.items():
        row = table.setdefault(entry, {"N1": 0, "N2": 0, "N3": 0})
        row[level] += 1
    return table
