"""Tool and method extraction with evidence levels (Protocol E2 v2, §3.4 and §12.1).

Signals and the level each one supports:

| Signal       | Where                                                       | Level |
|--------------|-------------------------------------------------------------|-------|
| `text`       | tool/method name in an item's title, body or tags           | N1    |
| `import`     | code search hit whose literal query is an import of it      | N2    |
| `config`     | tool config file in the repository tree or as a hit         | N2    |
| `dependency` | tool package among the dependencies of a root manifest      | N2    |
| `ci`         | tool named in an executable step of a CI workflow           | N3*   |

*A CI invocation is N3 only when the repository also has commits in ≥2
distinct months of the window and ≥2 contributors; otherwise it counts as N2.

Three rules keep the structural signals honest (ADR-056):

- A CI invocation is read from the workflow file itself: a `run:` script or a
  `uses:` reference of a job has to invoke the tool. A search hit in a workflow
  file is not enough, since the search also matches comments, job names, `env:`
  keys and the file name. A command that only prints the name, or that leaves
  the tool out of a test run, does not count either.
- Repository signals (from `msrkit enrich`) are attached to artifact items only:
  the repository item or, without one, a code hit. A repository the run knows
  only through issues keeps its text mentions and gets no N2/N3.
- A repository that carries a tool's name, as its own name or as the package its
  manifests declare, is that tool, a copy of it or a namesake, and is not counted
  as using it.

Methods are detected by name in text only (N1).
"""

from __future__ import annotations

import configparser
import json
import re
import tomllib
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import yaml
from pydantic import BaseModel

from msrkit.enrich import repo_of
from msrkit.keywords import match_terms
from msrkit.models import ItemKind

if TYPE_CHECKING:
    from collections.abc import Iterator

    from msrkit.enrich import RepoSignals
    from msrkit.gazetteer import Gazetteer, ToolEntry
    from msrkit.models import Item

Level = Literal["N1", "N2", "N3"]
Signal = Literal["text", "import", "config", "dependency", "ci"]
LEVEL_RANK = {"N1": 1, "N2": 2, "N3": 3}
N3_MIN_MONTHS = 2
N3_MIN_CONTRIBUTORS = 2
EVIDENCE_MAX = 300
WORKFLOW_DIR = ".github/workflows/"
# Items that stand for the repository as an artifact; the others are discourse about it.
ARTIFACT_KINDS = (ItemKind.REPO, ItemKind.CODE)
_YAML_LOADER: Any = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


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
    # For `ci`: how the workflow invokes the tool (see CiForm); "" for the other signals.
    via: Literal["", "uses", "cli", "module", "install", "name"] = ""


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


# -- dependency manifests --------------------------------------------------------


class ManifestInfo(NamedTuple):
    """What a dependency manifest declares."""

    dependencies: dict[str, str]  # normalized package name -> the text that declares it
    names: set[str]  # normalized names of the project the manifest belongs to


_REQ_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_QUOTED = re.compile(r"""["']([^"']+)["']""")
# A list literal: its closing bracket is the first one outside a quoted string, so the
# extras of a requirement (`'ir-measures[pytrec_eval]'`) do not end it.
_LIST = r"""\[((?:[^\]"']|"[^"]*"|'[^']*')*)\]"""
_SETUP_LISTS = re.compile(
    rf"\b(?:install_requires|tests_require|setup_requires)\s*=\s*{_LIST}", re.S
)
_SETUP_EXTRAS = re.compile(r"\bextras_require\s*=\s*\{(.*?)\}", re.S)
_SETUP_NAME = re.compile(r"""\bname\s*=\s*["']([^"']+)["']""")
# Keys of project metadata, whose quoted values are not requirements.
_METADATA_KEY = re.compile(
    r"\s*(?:name|version|description|summary|keywords|authors?|maintainers?|classifiers"
    r"|readme|license|homepage|repository|documentation|urls?|packages|include|exclude)\b",
    re.IGNORECASE,
)


def _requirement_name(spec: str) -> str | None:
    """Package name of a PEP 508 requirement (`ragas[all]>=0.2 ; python_version > '3.9'`)."""
    match = _REQ_NAME.match(spec)
    return match.group(1) if match else None


def _add_requirements(deps: dict[str, str], specs: Any) -> None:
    for spec in specs if isinstance(specs, list) else []:
        name = _requirement_name(spec) if isinstance(spec, str) else None
        if name:
            deps.setdefault(_norm_pkg(name), spec.strip())


def _toml_dependencies(node: Any, deps: dict[str, str]) -> None:
    """Collect requirements under every table key that ends in `dependencies`.

    Covers `project.dependencies`, optional and group dependencies (PEP 621 and 735),
    Poetry, uv, PDM, Hatch and pixi tables, and Cargo's `[dependencies]`. A list holds
    requirement strings; a table maps package names to versions, or group names to lists.
    """
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if not str(key).lower().replace("_", "-").endswith(("dependencies", "dependency-groups")):
            _toml_dependencies(value, deps)
        elif isinstance(value, list):
            _add_requirements(deps, value)
        elif isinstance(value, dict):
            for name, spec in value.items():
                if isinstance(spec, list) and _is_group(spec):
                    _add_requirements(deps, spec)  # a group: its key is not a package
                elif str(name).lower() != "python":
                    shown = spec if isinstance(spec, str) else json.dumps(spec, default=str)
                    deps.setdefault(_norm_pkg(str(name)), f"{name} = {shown}")


def _is_group(spec: list[Any]) -> bool:
    """A list of requirements, where PEP 735 also allows `{include-group = "x"}`.

    A list of tables without it is one package with several constraints (Poetry).
    """
    return not spec or any(
        isinstance(s, str) or (isinstance(s, dict) and "include-group" in s) for s in spec
    )


def _line_fallback(content: str) -> dict[str, str]:
    """Quoted requirement specs, for a TOML file that does not parse."""
    deps: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or _METADATA_KEY.match(stripped):
            continue
        for cand in re.findall(
            r'["\']([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*[<>=!~;"\']', stripped
        ):
            deps.setdefault(_norm_pkg(cand), stripped)
    return deps


def _toml_manifest(content: str, name_paths: tuple[tuple[str, ...], ...]) -> ManifestInfo:
    try:
        doc = tomllib.loads(content)
    except ValueError:  # tomllib.TOMLDecodeError is one
        names = {_norm_pkg(m.group(1)) for m in [_SETUP_NAME.search(content)] if m}
        return ManifestInfo(_line_fallback(content), names)
    deps: dict[str, str] = {}
    _toml_dependencies(doc, deps)
    names = set()
    for path in name_paths:
        node: Any = doc
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str) and node.strip():
            names.add(_norm_pkg(node))
    return ManifestInfo(deps, names)


def _setup_cfg(content: str) -> ManifestInfo:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(content)
    except configparser.Error:
        return ManifestInfo({}, set())
    deps: dict[str, str] = {}
    values = [
        parser.get("options", key, fallback="")
        for key in ("install_requires", "tests_require", "setup_requires")
    ]
    if parser.has_section("options.extras_require"):
        values += [v for _, v in parser.items("options.extras_require")]
    for value in values:
        _add_requirements(deps, [ln for ln in value.splitlines() if ln.strip()])
    name = parser.get("metadata", "name", fallback="").strip()
    return ManifestInfo(deps, {_norm_pkg(name)} if name else set())


def _setup_py(content: str) -> ManifestInfo:
    deps: dict[str, str] = {}
    blocks = [m.group(1) for m in _SETUP_LISTS.finditer(content)]
    for extras in _SETUP_EXTRAS.finditer(content):
        blocks += re.findall(_LIST, extras.group(1), re.S)  # the lists, not the keys
    for block in blocks:
        _add_requirements(deps, _QUOTED.findall(block))
    name = _SETUP_NAME.search(content)
    return ManifestInfo(deps, {_norm_pkg(name.group(1))} if name else set())


def parse_manifest(path: str, content: str) -> ManifestInfo:
    """Dependencies and project name declared by a root manifest.

    Only declared requirements count. The project's own metadata (`name`, `keywords`,
    `description`) is not a dependency: read as one, it made every copy of a tool's
    repository look like a repository that uses the tool.
    """
    base = PurePosixPath(path).name
    if base == "package.json":
        try:
            data = json.loads(content)
        except ValueError:
            return ManifestInfo({}, set())
        if not isinstance(data, dict):
            return ManifestInfo({}, set())
        deps: dict[str, str] = {}
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            section = data.get(key)
            for dep, version in section.items() if isinstance(section, dict) else []:
                deps.setdefault(_norm_pkg(dep), f'"{dep}": "{version}"')
        name = data.get("name")
        return ManifestInfo(deps, {_norm_pkg(name)} if isinstance(name, str) and name else set())
    if base == "pyproject.toml":
        return _toml_manifest(content, (("project", "name"), ("tool", "poetry", "name")))
    if base == "Cargo.toml":
        return _toml_manifest(content, (("package", "name"),))
    if base == "setup.cfg":
        return _setup_cfg(content)
    if base == "setup.py":
        return _setup_py(content)
    deps = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if base == "go.mod":
            go_match = re.search(r"(?:require\s+)?([A-Za-z0-9._/-]+)\s+v[0-9]", stripped)
            candidates = [go_match.group(1), go_match.group(1).split("/")[-1]] if go_match else []
        else:  # requirements*.txt: the name opens the line
            match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", stripped)
            candidates = [match.group(1)] if match else []
        for cand in candidates:
            deps.setdefault(_norm_pkg(cand), stripped)
    return ManifestInfo(deps, set())


def dependency_names(path: str, content: str) -> dict[str, str]:
    """Normalized dependency name -> the text that declares it."""
    return parse_manifest(path, content).dependencies


# -- CI workflows ----------------------------------------------------------------


class WorkflowStep(NamedTuple):
    """An executable part of a workflow: a `run:` script or a `uses:` reference."""

    kind: Literal["run", "uses"]
    text: str


def workflow_steps(content: str) -> list[WorkflowStep] | None:
    """Executable steps of a GitHub Actions workflow; None when it is not valid YAML.

    Only what a runner executes is returned: the `run:` script and the `uses:`
    reference of each job step, and the `uses:` of a job that calls a reusable
    workflow. Names, comments, `env:`, `with:` inputs and triggers are left out.
    """
    try:
        doc = yaml.load(content, Loader=_YAML_LOADER)  # CSafeLoader or SafeLoader
    except (yaml.YAMLError, ValueError, RecursionError):
        return None
    if not isinstance(doc, dict):
        return None
    jobs = doc.get("jobs")
    steps: list[WorkflowStep] = []
    for job in jobs.values() if isinstance(jobs, dict) else []:
        if not isinstance(job, dict):
            continue
        if isinstance(job.get("uses"), str):
            steps.append(WorkflowStep("uses", job["uses"]))
        job_steps = job.get("steps")
        for step in job_steps if isinstance(job_steps, list) else []:
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("run"), str):
                steps.append(WorkflowStep("run", step["run"]))
            if isinstance(step.get("uses"), str):
                steps.append(WorkflowStep("uses", step["uses"]))
    return steps


def executable_lines(steps: list[WorkflowStep]) -> Iterator[tuple[str, str]]:
    """(kind, line) of every executed line: script lines without comments, and references."""
    for step in steps:
        if step.kind == "uses":
            if step.text.strip():
                yield "uses", step.text.strip()
            continue
        script = re.sub(r"\\\r?\n", " ", step.text)  # join backslash continuations
        for line in script.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                yield "run", stripped


# A command that only prints, or a bare string: it names things without running them.
_OUTPUT = re.compile(
    r"^\s*(?:echo|printf|print|write-host|write-output|console\.log)\b|^\s*[\"']", re.IGNORECASE
)
_COMMAND_SEPARATOR = re.compile(r"&&|\|\||[;|]")
# What a test run leaves out: `-p no:langsmith`, `-m "not ragas"`, `--ignore=tests/ragas`.
_NEGATED = re.compile(
    r"(?:-p\s+no:|\bnot\s+)[\w.-]+|--(?:ignore|ignore-glob|deselect)[=\s]\S+", re.IGNORECASE
)


def command_text(line: str) -> str:
    """The part of a script line that runs something.

    Commands that only print are dropped, and so is what a command excludes: a line
    that echoes a tool's name, or that deselects its tests, does not invoke it.
    """
    parts = [part for part in _COMMAND_SEPARATOR.split(line) if not _OUTPUT.search(part)]
    return _NEGATED.sub(" ", " ; ".join(parts))


# Package managers and the subcommands after which they take requirements. "" means
# the requirement comes right after the command (`npx promptfoo@latest eval`).
_INSTALLERS: dict[str, frozenset[str]] = {
    "pip": frozenset({"install", "download", "wheel"}),
    "pip3": frozenset({"install", "download", "wheel"}),
    "uv": frozenset({"add", "install"}),
    "poetry": frozenset({"add"}),
    "pdm": frozenset({"add"}),
    "pipx": frozenset({"install", "inject", "run"}),
    "conda": frozenset({"install"}),
    "mamba": frozenset({"install"}),
    "micromamba": frozenset({"install"}),
    "npm": frozenset({"install", "i", "add"}),
    "pnpm": frozenset({"add", "install", "i", "dlx"}),
    "yarn": frozenset({"add", "dlx"}),
    "bun": frozenset({"add", "install", "x"}),
    "npx": frozenset({""}),
    "uvx": frozenset({""}),
    "bunx": frozenset({""}),
}
# After these, one package follows and the rest are its own arguments.
_RUNS_ONE_PACKAGE = frozenset({"", "run", "dlx", "x"})
# Options whose value is a requirement, per command (`uv run --with ragas`).
_REQUIREMENT_OPTIONS: dict[str, frozenset[str]] = {
    "uv": frozenset({"--with", "--from"}),
    "uvx": frozenset({"--with", "--from"}),
    "pipx": frozenset({"--spec"}),
    "npx": frozenset({"--package"}),
    "bunx": frozenset({"--package"}),
}
# Options that take a value which is not a requirement (a file, a group, an index).
_VALUE_OPTIONS = frozenset(
    {
        "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "--extra", "--group",
        "--only-group", "--with", "--without", "--only", "-i", "--index-url", "--extra-index-url",
        "-f", "--find-links", "--python", "-p", "--target", "-t", "--prefix", "--root", "-d",
        "--dest", "--platform", "--cache-dir", "-n", "--name", "-w", "--channel", "--registry",
    }
)  # fmt: skip
_INSTALLER_WORD = re.compile(
    rf"(?<![\w.-])(?:{'|'.join(sorted(_INSTALLERS, key=len, reverse=True))})(?![\w-])"
)
_SHELL_WORD = re.compile(r"""(?:[^\s"']+|"[^"]*"|'[^']*')+""")
_GITHUB_REPO = re.compile(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?(?=[@#/\s\"']|$)", re.I)


class Installs(NamedTuple):
    """What the package managers of a script install."""

    packages: dict[str, str]  # normalized package name -> the line that installs it
    repos: dict[str, str]  # owner/repo of a repository installed by URL -> the line


def _requirement(word: str) -> tuple[str | None, str | None]:
    """(package name, GitHub repository) that a requirement word names."""
    repo = _GITHUB_REPO.search(word)
    name = re.match(r"@[\w.-]+/[\w.-]+" if word.startswith("@") else r"[A-Za-z0-9][\w.-]*", word)
    return (name.group(0) if name else None), (repo.group(1).lower() if repo else None)


def _requirement_words(command: str) -> Iterator[str]:
    """Requirement words of one simple command (`pip install -U "ragas>=0.2" -r x.txt`)."""
    words = [w.strip("\"'") for w in _SHELL_WORD.findall(command)]
    programs = [w.rsplit("/", 1)[-1].lower().removesuffix(".exe") for w in words]
    start = next((i for i, name in enumerate(programs) if name in _INSTALLERS), None)
    if start is None:
        return
    tool = programs[start]
    verbs = _INSTALLERS[tool]
    value_is_requirement = _REQUIREMENT_OPTIONS.get(tool, frozenset())
    verb: str | None = "" if "" in verbs else None
    skip = take = False
    for word in words[start + 1 :]:
        option, _, attached = word.partition("=")
        if take:
            take = False
            yield word
        elif skip:
            skip = False
        elif word.startswith("-"):
            if option in value_is_requirement:
                if attached:
                    yield attached
                else:
                    take = True
            elif not attached and option in _VALUE_OPTIONS:
                skip = True
        elif verb is None:
            verb = word if word in verbs else None
        else:
            yield word
            if verb in _RUNS_ONE_PACKAGE:
                return


def installs(lines: list[str]) -> Installs:
    """Packages and repositories that package managers install in the given script lines."""
    found = Installs({}, {})
    for line in lines:
        if not _INSTALLER_WORD.search(line):
            continue
        for command in _COMMAND_SEPARATOR.split(line):
            for word in _requirement_words(command):
                name, repo = _requirement(word)
                if name:
                    found.packages.setdefault(_norm_pkg(name), line)
                if repo:
                    found.repos.setdefault(repo, line)
    return found


def _name_regex(name: str) -> str:
    """Regex for a command or package name, as shells and package indexes spell it.

    `-`, `_` and `.` inside a word are interchangeable (`inspect_ai` is installed as
    `inspect-ai`), and words are separated by any whitespace.
    """
    words = [
        re.sub(r"(?<=[A-Za-z0-9])(?:\\-|_|\\\.)(?=[A-Za-z0-9])", "[-_.]", re.escape(word))
        for word in name.split()
    ]
    return r"\s+".join(words)


def _bounded(body: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-])(?:{body})(?![\w-])", re.IGNORECASE)


def _import_patterns(tool: ToolEntry) -> list[re.Pattern[str]]:
    return [
        re.compile(
            rf"(?:^|\W)(?:from\s+{re.escape(mod)}(?:\.\w+)*\s+import|import\s+{re.escape(mod)}\b)"
        )
        for mod in tool.python_imports
    ]


# What may come before a command's own name: variable assignments, options and the
# programs that launch another one (`FOO=1 uv run --frozen inspect eval ...`).
_LAUNCHERS = (
    r"(?:\s*(?:[A-Za-z_]\w*=\S*|-\S+|\d+\w*|sudo|time|env|exec|command|nohup|nice|timeout"
    r"|xvfb-run|uv|run|tool|poetry|pdm|hatch|pipenv|conda|npx|pnpm|yarn|bunx?|pipx|uvx)\s+)*"
)
# Where one simple command ends and the next begins, command substitutions included.
_COMMAND_START = re.compile(r"&&|\|\||[;|`]|\$\(")

# How a workflow step may invoke a tool, strongest evidence first (ADR-056).
CiForm = Literal["uses", "cli", "module", "install", "name"]
CI_FORMS: tuple[CiForm, ...] = ("uses", "cli", "module", "install", "name")


class CiHit(NamedTuple):
    """The executed line that invokes a tool, and in which form."""

    form: CiForm
    line: str  # "run: ..." or "uses: ..."


class _CiSpec(NamedTuple):
    """What to look for in a workflow, for one tool."""

    commands: list[re.Pattern[str]]  # its CLI
    modules: list[re.Pattern[str]]  # `python -m <module>` and inline imports
    name: re.Pattern[str] | None  # its anchor token, when the name is its own
    packages: frozenset[str]  # normalized package names
    repos: tuple[str, ...]  # its repositories, lowercased
    owners: frozenset[str]  # the accounts that own them
    in_repo_name: re.Pattern[str]  # its token opening a word of a repository name
    literals: tuple[str, ...]  # prefilter: one of them is in every match


def _ci_spec(tool: ToolEntry) -> _CiSpec:
    """The forms in which a workflow can invoke `tool`.

    - `uses`: an action or reusable workflow of the tool's own repository;
    - `cli`: one of its commands, where a command goes (`flake8 lm_eval.py` is not
      a call of `lm_eval`);
    - `module`: `python -m <module>` or an inline import of it;
    - `install`: a package manager installs its package or its repository;
    - `name`: its anchor token elsewhere in a command (a path, an argument).

    The last form is the weakest: it shows that the workflow refers to the tool, not
    that it runs it, so it supports N2 and never N3 (see from_repo). It is not accepted
    for a tool whose name collides with other software (`ambiguous`), since
    `apptainer exec docker://x/ranx` runs another ranx, and it is reserved for anchor
    tools, whose token the CI query searches for.
    """
    modules = [_name_regex(m) for m in tool.python_imports]
    module_patterns = [_bounded(rf"python[\d.]*\s+-m\s+{m}") for m in modules]
    module_patterns += [
        re.compile(rf"\bimport\s+(?:[\w.]+\s*,\s*)*{m}\b|\bfrom\s+{m}(?:\.\w+)*\s+import\b")
        for m in modules
    ]
    use_name = tool.anchor and not tool.ambiguous
    names = [*tool.cli, *tool.python_imports, *tool.packages, *([tool.token()] if use_name else [])]
    names += [r.rsplit("/", 1)[-1] for r in tool.repos]
    literals: list[str] = []
    for name in names:
        chunks = re.findall(r"[a-z0-9]+", name.lower())
        if chunks:
            literals.append(max(chunks, key=len))
    return _CiSpec(
        commands=[
            re.compile(rf"^{_LAUNCHERS}(?:{_name_regex(c)})(?![\w-])", re.IGNORECASE)
            for c in tool.cli
        ],
        modules=module_patterns,
        name=_bounded(_name_regex(tool.token())) if use_name else None,
        packages=frozenset(_norm_pkg(p) for p in tool.packages),
        repos=tuple(r.lower() for r in tool.repos),
        owners=frozenset(r.split("/", 1)[0].lower() for r in tool.repos),
        in_repo_name=re.compile(rf"(?<![a-z0-9]){_name_regex(tool.token())}", re.IGNORECASE),
        literals=tuple(dict.fromkeys(literals)),
    )


def _uses_repo(target: str, repos: tuple[str, ...]) -> bool:
    """Whether a `uses:` target (`owner/repo[/path]`, lowercased) is inside one of `repos`."""
    return any(target == repo or target.startswith(repo + "/") for repo in repos)


class Extractor:
    """Applies the gazetteer to items and repository signals."""

    def __init__(self, gazetteer: Gazetteer, context_terms: list[str] | None = None) -> None:
        self.gazetteer = gazetteer
        # Lexicon that confirms an ambiguous name when found in its context window.
        self.context_terms = context_terms or []
        self.dropped_ambiguous = 0  # ambiguous mentions without confirmation, set by run()
        # What run() left out, for the extraction log and the reproducibility package.
        self.stats: dict[str, int] = {}
        self._pending: list[Detection] = []
        self._imports = {t.id: _import_patterns(t) for t in gazetteer.tools}
        self._ci = {t.id: _ci_spec(t) for t in gazetteer.tools}
        # Names under which a repository is the tool itself: its id, its packages and
        # the names of its own repositories.
        self._tool_names = {
            t.id: {
                _norm_pkg(n) for n in [t.id, *t.packages, *(r.rsplit("/", 1)[-1] for r in t.repos)]
            }
            for t in gazetteer.tools
        }
        self._token = {t.id: _bounded(_name_regex(t.token())) for t in gazetteer.tools}

    # -- item-level ----------------------------------------------------------

    def from_item(self, item: Item) -> list[Detection]:
        repo = repo_of(item)
        out: list[Detection] = []
        text_fields = {
            "title": item.title,
            "body": item.body,
            "tags": " ".join(item.tech.tags) or None,
        }

        for tool in self.gazetteer.tools:
            hits = match_terms(
                tool.names(),
                title=text_fields["title"],
                body=text_fields["body"],
                tags=item.tech.tags or None,
                mode="exact",
            )
            if not hits:
                continue
            if not tool.ambiguous:
                out.append(
                    self._det(
                        item, repo, tool.id, "tool", "N1", "text", hits[0].field, hits[0].context
                    )
                )
                continue
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
                tool.id,
                "tool",
                "N1",
                "text",
                (confirmed or hits[0]).field,
                (confirmed or hits[0]).context,
            )
            if confirmed is not None:
                out.append(det.model_copy(update={"confirmed_by": "context"}))
            else:
                self._pending.append(det)  # kept only with a structural signal (see run)
        for method in self.gazetteer.methods:
            hits = match_terms(
                method.names(), title=item.title, body=item.body, tags=item.tech.tags or None
            )
            if hits:
                out.append(
                    self._det(
                        item,
                        repo,
                        method.id,
                        "method",
                        "N1",
                        "text",
                        hits[0].field,
                        hits[0].context,
                    )
                )

        if item.kind == ItemKind.CODE:
            out.extend(self._from_code_hit(item, repo))
        return out

    def _from_code_hit(self, item: Item, repo: str | None) -> list[Detection]:
        """A code search hit contains the literal query string (GitHub guarantees the match).

        That holds for an import (a quoted phrase) and for a config file name. It does
        not hold for a CI invocation: a hit in a workflow file only says the tool's name
        is somewhere in the file, so the `ci` signal comes from the workflow content,
        in from_repo().
        """
        query = item.provenance.query_string
        path = item.tech.path or item.title or ""
        name = PurePosixPath(path).name.lower()
        out: list[Detection] = []
        for tool in self.gazetteer.tools:
            if any(p.search(query) for p in self._imports[tool.id]):
                out.append(self._det(item, repo, tool.id, "tool", "N2", "import", path, query))
            if name in {c.lower() for c in tool.config_files}:
                out.append(self._det(item, repo, tool.id, "tool", "N2", "config", path, name))
        return out

    # -- repository-level ----------------------------------------------------

    def ci_matches(self, sig: RepoSignals) -> dict[str, dict[str, CiHit]]:
        """Workflow path -> {tool id: the executed line that invokes the tool, and how}."""
        out: dict[str, dict[str, CiHit]] = {}
        for wf_path, content in sorted(sig.workflows.items()):
            if not content:
                continue
            steps = workflow_steps(content)
            if steps is None:
                self.stats["workflows_invalid_yaml"] = (
                    self.stats.get("workflows_invalid_yaml", 0) + 1
                )
                continue
            uses = [line for kind, line in executable_lines(steps) if kind == "uses"]
            # (what the line runs, the line as written), for the script lines that run something
            runs = [
                (command, line)
                for kind, line in executable_lines(steps)
                if kind == "run" and (command := command_text(line)).strip()
            ]
            installed = installs([command for command, _ in runs])
            written = {command: line for command, line in runs}
            blob = "\n".join([*uses, *(command for command, _ in runs)]).lower()
            found: dict[str, CiHit] = {}
            for tool in self.gazetteer.tools:
                spec = self._ci[tool.id]
                if not any(literal in blob for literal in spec.literals):
                    continue
                hit = self._ci_hit(spec, uses, runs, installed, written)
                if hit is not None:
                    found[tool.id] = hit
            out[wf_path] = found
        return out

    @staticmethod
    def _ci_hit(
        spec: _CiSpec,
        uses: list[str],
        runs: list[tuple[str, str]],
        installed: Installs,
        written: dict[str, str],
    ) -> CiHit | None:
        """The strongest form in which the workflow invokes the tool, if any."""
        named_in: str | None = None  # a reference that only carries the tool's name
        for ref in uses:
            target = ref.split("@", 1)[0].strip().lower()
            if _uses_repo(target, spec.repos):
                return CiHit("uses", f"uses: {ref}")
            owner, _, rest = target.partition("/")
            local = target.startswith(".")
            # An action the tool's own organization names after it
            # (`promptfoo/promptfoo-action`) is the tool.
            if not local and owner in spec.owners and spec.in_repo_name.search(rest):
                return CiHit("uses", f"uses: {ref}")
            # A third party's action, or a local workflow file, only carries the name.
            if spec.name is not None and spec.name.search(target if local else rest):
                named_in = named_in or f"uses: {ref}"
        for command, line in runs:
            simple = [part.strip() for part in _COMMAND_START.split(command)]
            if any(p.search(part) for part in simple for p in spec.commands):
                return CiHit("cli", f"run: {line}")
        for command, line in runs:
            if any(p.search(command) for p in spec.modules):
                return CiHit("module", f"run: {line}")
        lines = [installed.packages[p] for p in spec.packages if p in installed.packages]
        lines += [installed.repos[r] for r in spec.repos if r in installed.repos]
        if lines:
            return CiHit("install", f"run: {written.get(lines[0], lines[0])}")
        if spec.name is not None:
            for command, line in runs:
                if spec.name.search(command):
                    return CiHit("name", f"run: {line}")
        return CiHit("name", named_in) if named_in else None

    def from_repo(
        self,
        sig: RepoSignals,
        item: Item,
        workflow_items: dict[str, Item] | None = None,
        ci: dict[str, dict[str, CiHit]] | None = None,
    ) -> list[Detection]:
        """Structural detections of a repository, attached to the artifact item `item`.

        `workflow_items` maps a workflow path to the code hit of that file, when the run
        has one: a CI invocation is then attached to the item it was read from.
        """
        out: list[Detection] = []
        if sig.error:
            return out
        repo = repo_of(item) or sig.repo.lower()  # the unit the detections belong to
        sustained = is_sustained(sig)
        deps: dict[str, tuple[str, str]] = {}
        # What the repository calls itself: its name and the projects its manifests declare.
        own_names = {_norm_pkg(repo.rsplit("/", 1)[-1])}
        for path, content in sig.manifests.items():
            info = parse_manifest(path, content)
            own_names |= info.names
            for dep, line in info.dependencies.items():
                deps.setdefault(dep, (path, line))
        config_names = {PurePosixPath(p).name.lower(): p for p in sig.config_paths}
        ci = self.ci_matches(sig) if ci is None else ci
        workflow_items = workflow_items or {}

        for tool in self.gazetteer.tools:
            if self._tool_names[tool.id] & own_names:
                # The repository is this tool, a copy of it or a namesake: not a user of it.
                self.stats["self_copies"] = self.stats.get("self_copies", 0) + 1
                continue
            for pkg in tool.packages:
                if _norm_pkg(pkg) in deps:
                    path, line = deps[_norm_pkg(pkg)]
                    out.append(
                        self._det(item, repo, tool.id, "tool", "N2", "dependency", path, line)
                    )
                    break
            for cfg in tool.config_files:
                if cfg.lower() in config_names:
                    path = config_names[cfg.lower()]
                    out.append(self._det(item, repo, tool.id, "tool", "N2", "config", path, path))
                    break
            # The workflows that invoke the tool, the strongest form of evidence first.
            ranked = sorted(
                (CI_FORMS.index(found[tool.id].form), p)
                for p, found in ci.items()
                if tool.id in found
            )
            paths = [p for _, p in ranked]
            # One detection per workflow the run holds as an item; else one on `item`.
            for wf_path in [p for p in paths if p in workflow_items] or paths[:1]:
                hit = ci[wf_path][tool.id]
                # N3 needs a step that runs the tool; its bare name in a command is N2.
                level: Level = "N3" if sustained and hit.form != "name" else "N2"
                det = self._det(
                    workflow_items.get(wf_path, item),
                    repo,
                    tool.id,
                    "tool",
                    level,
                    "ci",
                    wf_path,
                    hit.line,
                )
                out.append(det.model_copy(update={"via": hit.form}))
        return out

    # -- run-level -----------------------------------------------------------

    def run(self, items: list[Item], signals: dict[str, RepoSignals]) -> list[Detection]:
        detections: list[Detection] = []
        self._pending = []
        self.stats = {}
        anchor_item: dict[str, Item] = {}
        workflow_items: dict[str, dict[str, Item]] = {}
        for item in items:
            detections.extend(self.from_item(item))
            repo = repo_of(item)
            if not repo or item.kind not in ARTIFACT_KINDS:
                continue  # an issue is discourse: it never carries the repository's signals
            current = anchor_item.get(repo)
            if current is None or (current.kind != ItemKind.REPO and item.kind == ItemKind.REPO):
                anchor_item[repo] = item
            path = item.tech.path or ""
            if item.kind == ItemKind.CODE and path.startswith(WORKFLOW_DIR):
                workflow_items.setdefault(repo, {}).setdefault(path, item)

        norm_signals = {k.lower(): v for k, v in signals.items()}
        matches: dict[str, dict[str, dict[str, CiHit]]] = {}
        for repo, sig in norm_signals.items():
            if sig.error:
                continue
            if repo not in anchor_item:
                self.stats["repos_without_artifact_item"] = (
                    self.stats.get("repos_without_artifact_item", 0) + 1
                )
                continue
            matches[repo] = self.ci_matches(sig)
            detections.extend(
                self.from_repo(sig, anchor_item[repo], workflow_items.get(repo), matches[repo])
            )
        self._count_ci_hits(workflow_items, norm_signals, matches)

        structural = {(d.entry_id, d.repo) for d in detections if d.signal != "text" and d.repo}
        self.dropped_ambiguous = 0
        for det in self._pending:
            if det.repo and (det.entry_id, det.repo) in structural:
                detections.append(det.model_copy(update={"confirmed_by": "structural"}))
            else:
                self.dropped_ambiguous += 1
        self._pending = []
        self.stats["dropped_ambiguous"] = self.dropped_ambiguous
        return detections

    def _count_ci_hits(
        self,
        workflow_items: dict[str, dict[str, Item]],
        signals: dict[str, RepoSignals],
        matches: dict[str, dict[str, dict[str, CiHit]]],
    ) -> None:
        """What became of the workflow hits of the CI queries (`{anchor} path:.github/workflows`).

        verified: a step of the workflow invokes the tool; rejected: the workflow's
        content was read and no step does; unverified: the content was not read
        (repository not enriched, enrichment error, file not fetched).
        """
        counts = {"verified": 0, "rejected": 0, "unverified": 0}
        for repo, by_path in workflow_items.items():
            sig = signals.get(repo)
            for path, item in by_path.items():
                query = item.provenance.query_string
                for tool in self.gazetteer.anchors():
                    if not self._token[tool.id].search(query):
                        continue
                    if sig is None or sig.error or not sig.workflows.get(path):
                        counts["unverified"] += 1
                    elif tool.id in matches.get(repo, {}).get(path, {}):
                        counts["verified"] += 1
                    else:
                        counts["rejected"] += 1
        for key, value in counts.items():
            self.stats[f"ci_hits_{key}"] = value

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
        return Detection(
            item_id=item.id,
            source=item.source,
            repo=repo,
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
        key = (d.entry_id, d.repo or f"item:{d.item_id}")
        if LEVEL_RANK[d.level] > LEVEL_RANK.get(best.get(key, ""), 0):
            best[key] = d.level
    table: dict[str, dict[str, int]] = {}
    for (entry, _unit), level in best.items():
        row = table.setdefault(entry, {"N1": 0, "N2": 0, "N3": 0})
        row[level] += 1
    return table
