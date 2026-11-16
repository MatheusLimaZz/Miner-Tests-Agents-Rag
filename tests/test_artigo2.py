"""Article 2 analyses (analysis/artigo2.py) on a synthetic reproducibility package."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import re
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from msrkit.extract import Detection
from msrkit.models import Engagement, Item, ItemKind, Provenance, TermHit

_PATH = Path(__file__).resolve().parents[1] / "analysis" / "artigo2.py"
_SPEC = importlib.util.spec_from_file_location("artigo2", _PATH)
assert _SPEC and _SPEC.loader
artigo2 = importlib.util.module_from_spec(_SPEC)
sys.modules["artigo2"] = artigo2
_SPEC.loader.exec_module(artigo2)

FETCHED = datetime(2026, 9, 1, tzinfo=UTC)
OLD = datetime(2025, 6, 1, tzinfo=UTC)  # 15 months before collection
RECENT = datetime(2026, 8, 15, tzinfo=UTC)  # below the 3-month minimum age

GAZETTEER = {
    "version": "1",
    "tools": [
        {
            "id": "ragas",
            "name": "Ragas",
            "family": "avaliacao-llm-rag",
            "applies_to": ["rag"],
            "repos": ["explodinggradients/ragas"],
        },
        {
            "id": "deepeval",
            "name": "DeepEval",
            "family": "avaliacao-llm-rag",
            "repos": ["confident-ai/deepeval"],
        },
        {
            "id": "promptfoo",
            "name": "promptfoo",
            "family": "avaliacao-llm-rag",
            "repos": ["promptfoo/promptfoo"],
        },
        {
            "id": "garak",
            "name": "garak",
            "family": "guardrails-seguranca",
            "repos": ["NVIDIA/garak"],
        },
        {
            "id": "webarena",
            "name": "WebArena",
            "family": "agentes",
            "applies_to": ["agente"],
            "repos": ["web-arena-x/webarena"],
        },
        {
            "id": "langchain",
            "name": "LangChain",
            "family": "framework-sut",
            "repos": ["langchain-ai/langchain"],
            "anchor": False,
        },
    ],
    "methods": [
        {"id": "llm-as-judge", "name": "LLM-as-a-judge", "oracle": "pseudo-automatico"},
        {"id": "golden-dataset", "name": "golden dataset", "oracle": "referencia"},
        {"id": "trajectory-evaluation", "name": "trajectory evaluation"},
    ],
}


def _item(
    iid: str,
    source: str,
    kind: ItemKind,
    url: str,
    *,
    created: datetime = OLD,
    concepts: tuple[str, ...] = ("rag",),
    title: str = "Evaluating RAG pipelines",
    query: str = "RAG evaluation",
    pt: bool = False,
    **engagement: int,
) -> Item:
    return Item(
        id=iid,
        source=source,
        kind=kind,
        url=url,
        title=title,
        created_at=created,
        engagement=Engagement(**engagement),
        concepts=["teste", *concepts],
        matched_terms=[TermHit(term="teste de RAG", field="title", context="", lang="pt")]
        if pt
        else [],
        provenance=Provenance(
            run_id="2026-09-01T00-00-00Z-test",
            query_string=query,
            partition="all",
            adapter=source,
            adapter_version="0.1",
            fetched_at=FETCHED,
            response_sha256="0" * 64,
            raw_ref="raw:0",
        ),
    )


def _det(item: Item, tool: str, level: str = "N1", kind: str = "tool") -> Detection:
    signal = {"N1": "text", "N2": "import", "N3": "ci"}[level]
    repo = None
    if item.source == "github":
        parts = str(item.url).split("/")
        repo = f"{parts[3]}/{parts[4]}"
    return Detection(
        item_id=item.id,
        source=item.source,
        repo=repo,
        entry_id=tool,
        entry_type=kind,
        level=level if kind == "tool" else "N1",
        signal=signal if kind == "tool" else "text",
        location="title",
        evidence=tool,
    )


def _corpus() -> tuple[list[Item], list[Detection]]:
    gh = "https://github.com"
    repo = ItemKind.REPO
    items = [
        _item("r-self", "github", repo, f"{gh}/explodinggradients/ragas", stars=9000),
        _item("r-app1", "github", repo, f"{gh}/acme/app1", stars=10),
        _item("r-app2", "github", repo, f"{gh}/acme/app2", stars=5),
        _item("r-app3", "github", repo, f"{gh}/acme/app3", stars=7, concepts=("agente",)),
        _item("r-app4", "github", repo, f"{gh}/acme/app4", stars=3, concepts=("agente",)),
        _item("r-app5", "github", repo, f"{gh}/acme/app5", stars=1),
        _item(
            "i-bot",
            "github",
            ItemKind.ISSUE,
            f"{gh}/acme/app1/issues/1",
            title="Bump ragas from 0.1 to 0.2",
            reactions=0,
        ),
        _item(
            "i-bug",
            "github",
            ItemKind.ISSUE,
            f"{gh}/explodinggradients/ragas/issues/9",
            title="Faithfulness crashes",
            reactions=4,
            comments=6,
            query="kind:issue repo:explodinggradients/ragas is:issue label:bug",
        ),
        _item(
            "i-new",
            "github",
            ItemKind.ISSUE,
            f"{gh}/acme/app2/issues/3",
            created=RECENT,
            reactions=50,
        ),
    ]
    votes = [12, 3, 40, 7, 1, 25, 9, 2]
    for n, v in enumerate(votes):
        concepts = ("agente",) if n % 3 == 0 else ("rag",)
        items.append(
            _item(
                f"hn{n}",
                "hackernews",
                ItemKind.THREAD,
                f"https://news.ycombinator.com/item?id={n}",
                votes=v,
                concepts=concepts,
                pt=n == 0,
            )
        )
    for n, v in enumerate([5, 0, 11, 2, 8, 1]):
        items.append(
            _item(
                f"se{n}",
                "stackexchange",
                ItemKind.THREAD,
                f"https://stackoverflow.com/questions/{n}",
                votes=v,
                views=100,
            )
        )
    for n, r in enumerate([30, 4, 15, 0]):
        items.append(
            _item(
                f"dv{n}",
                "devto",
                ItemKind.ARTICLE,
                f"https://dev.to/a/post-{n}",
                reactions=r,
                concepts=("agente",),
            )
        )
    by_id = {it.id: it for it in items}
    dets = [
        _det(by_id["r-self"], "ragas", "N3"),  # self: set apart
        _det(by_id["r-app1"], "ragas", "N3"),
        _det(by_id["r-app1"], "langchain", "N2"),  # framework: never ranked
        _det(by_id["r-app2"], "deepeval", "N2"),
        _det(by_id["r-app3"], "promptfoo", "N3"),
        _det(by_id["r-app4"], "promptfoo", "N3"),
        _det(by_id["r-app4"], "webarena", "N3"),
        _det(by_id["r-app5"], "deepeval", "N2"),
        _det(by_id["i-bot"], "ragas"),
        _det(by_id["i-bug"], "ragas"),
        _det(by_id["i-new"], "deepeval"),
    ]
    for n in range(8):
        dets.append(_det(by_id[f"hn{n}"], "ragas"))
        if n % 2:
            dets.append(_det(by_id[f"hn{n}"], "deepeval"))
        dets.append(_det(by_id[f"hn{n}"], "llm-as-judge", kind="method"))
    for n in range(6):
        dets.append(_det(by_id[f"se{n}"], "garak" if n < 3 else "deepeval"))
        dets.append(_det(by_id[f"se{n}"], "golden-dataset", kind="method"))
    for n in range(4):
        dets.append(_det(by_id[f"dv{n}"], "promptfoo"))
        dets.append(_det(by_id[f"dv{n}"], "trajectory-evaluation", kind="method"))
    return items, dets


def _write_package(
    root: Path,
    items: list[Item] | None = None,
    dets: list[Detection] | None = None,
    manifest: dict | None = None,
    repos: list[dict] | None = None,
) -> Path:
    if items is None or dets is None:
        items, dets = _corpus()
    (root / "corpus").mkdir(parents=True)
    (root / "evidence").mkdir()
    (root / "protocol").mkdir()
    (root / "run").mkdir()
    lines = [
        json.dumps({**json.loads(it.model_dump_json()), "redistribution": "metadata_only"})
        for it in items
    ]
    (root / "corpus" / "items.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "evidence" / "detections.jsonl").write_text(
        "\n".join(d.model_dump_json() for d in dets) + "\n", encoding="utf-8"
    )
    repos = repos or [
        {"repo": "acme/app1", "has_ci": True, "workflows": [".github/workflows/ci.yml"]},
        {"repo": "acme/app2", "has_ci": False, "error": "tree HTTP 404"},
    ]
    (root / "evidence" / "github_repos.jsonl").write_text(
        "\n".join(json.dumps(r) for r in repos) + "\n", encoding="utf-8"
    )
    (root / "protocol" / "gazetteer.yaml").write_text(yaml.safe_dump(GAZETTEER), encoding="utf-8")
    manifest = manifest or {
        "run_id": "2026-09-01T00-00-00Z-test",
        "protocol_sha256": "ab" * 32,
        "run_options": {"per_query_limit": 10},
        "sources": [
            {
                "name": "hackernews",
                "queries": [{"query_string": "RAG evaluation", "truncated": False}],
            }
        ],
    }
    (root / "run" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "reports").mkdir()
    flow = {
        "identified": {"github": 12, "hackernews": 8},
        "filtered": {"window": 2},
        "duplicates": {"url": 1},
        "screened": 17,
        "included": 0,
        "pending": 17,
        "coded": 0,
    }
    (root / "reports" / "prisma.json").write_text(json.dumps(flow), encoding="utf-8")
    (root / "package_manifest.json").write_text(
        json.dumps({"run_id": manifest["run_id"], "msrkit_version": "0.1.0"}), encoding="utf-8"
    )
    return root


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _macros(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    return dict(re.findall(r"\\newcommand\{\\(\w+)\}\{(.*)\}", text))


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    pkg = _write_package(tmp_path / "pkg")
    out = tmp_path / "out"
    code = artigo2.main(
        [str(pkg), "--out", str(out), "--bootstrap", "200", "--min-n", "3", "--min-together", "1"]
    )
    assert code == 0
    return out


def test_pyramid_excludes_frameworks_and_sets_self_apart(run_dir: Path) -> None:
    rows = {r["tool"]: r for r in _rows(run_dir / "qp1_piramide.csv")}
    assert "langchain" not in rows  # framework-sut is context, not a testing tool
    assert rows["ragas"]["self"] == "1"  # explodinggradients/ragas is the tool itself
    assert rows["ragas"]["n3_ext"] == "1"  # only acme/app1 counts as adoption
    assert rows["promptfoo"]["n3_ext"] == "2"
    assert rows["deepeval"]["n2"] == "2"
    # best level per unit: the app1 issue (N1) merges into the app1 repository (N3) and
    # the bug issue merges into the tool's own repository, so N1 holds only the HN items
    assert rows["ragas"]["n1"] == "8"
    assert rows["deepeval"]["n1"] == "7"


def test_macros_define_every_registry_entry_once(run_dir: Path) -> None:
    text = (run_dir / "macros.tex").read_text(encoding="utf-8")
    names = re.findall(r"\\newcommand\{\\(\w+)\}", text)
    assert sorted(names) == sorted(artigo2.MACROS)
    macros = _macros(run_dir / "macros.tex")
    assert macros["ResTopNTres"] == "promptfoo"
    assert macros["ResFwTop"] == "LangChain"
    assert macros["ResItensPt"] == "1"
    assert macros["ResPerQueryLimit"] == "10"
    assert macros["ResReposErro"] == "1"
    assert macros["ResPrismaIdentificados"] == "20"
    assert macros["ResPrismaTriagem"] == "17"
    assert "\\pend" not in text  # a real run never leaves placeholders


def test_kendall_and_summary(run_dir: Path) -> None:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    tau = summary["kendall"]["UmTres"]
    assert tau["tools"] == 5
    assert -1 <= tau["tau"] <= 1
    assert tau["boot"] > 0 and tau["lo"] <= tau["hi"]
    assert summary["basis"] == "detections"


def test_engagement_filters_and_percentiles(run_dir: Path) -> None:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    excluded = summary["engagement_excluded"]
    assert excluded["bot"] == 1  # "Bump ragas ..."
    assert excluded["dor"] == 1  # bug issue of the tool's own repository
    assert excluded["idade"] == 1  # issue created two weeks before collection
    tools = {r["entry"]: r for r in _rows(run_dir / "qp3_engajamento_ferramentas.csv")}
    for row in tools.values():
        if row.get("mean"):
            assert 0.0 <= float(row["lo"]) <= float(row["mean"]) <= float(row["hi"]) <= 1.0
            assert 0.0 <= float(row["median"]) <= 1.0
            assert 0.0 <= float(row["share_floor"]) <= 1.0
    assert int(tools["ragas"]["n"]) == 8  # HN items only (bot and bug issues excluded)
    pain = _rows(run_dir / "qp3_dor.csv")
    assert pain[0]["tool"] == "ragas" and pain[0]["issues"] == "1"
    assert _macros(run_dir / "macros.tex")["ResItensNaoTruncadas"] == "8"


def test_tables_and_figure_are_latex(run_dir: Path) -> None:
    table = (run_dir / "tab_qp1.tex").read_text(encoding="utf-8")
    assert table.splitlines()[0].startswith("promptfoo & avaliação LLM/RAG & 2 &")
    assert all(line.count("&") == 7 for line in table.splitlines())
    oracles = (run_dir / "tab_qp2_oraculos.tex").read_text(encoding="utf-8")
    assert oracles.count("\\\\") == 5
    figure = (run_dir / "fig_qp1_dispersao.tex").read_text(encoding="utf-8")
    assert "\\begin{axis}" in figure and "coordinates" in figure


def test_zip_package_gives_the_same_result(tmp_path: Path, run_dir: Path) -> None:
    pkg = _write_package(tmp_path / "pkg2")
    archive = tmp_path / "pkg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in pkg.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(pkg).as_posix())
    out = tmp_path / "out_zip"
    artigo2.main(
        [
            str(archive),
            "--out",
            str(out),
            "--bootstrap",
            "200",
            "--min-n",
            "3",
            "--min-together",
            "1",
        ]
    )
    assert _rows(out / "qp1_piramide.csv") == _rows(run_dir / "qp1_piramide.csv")


def test_a_line_separator_inside_a_text_does_not_split_the_item(tmp_path: Path) -> None:
    # The package writes text as it came: U+2028 stays raw inside the JSON string, and in
    # the ba7b run three Stack Exchange items were read as twenty broken lines.
    items, dets = _corpus()
    pkg = _write_package(tmp_path / "pkg", items, dets)
    path = pkg / "corpus" / "items.jsonl"
    lines = path.read_text(encoding="utf-8").split("\n")
    first = json.loads(lines[0])
    first["title"] = "Heap issue in CI after the upgrade\x0cand again"
    lines[0] = json.dumps(first, ensure_ascii=False)
    path.write_bytes("\n".join(lines).encode("utf-8"))

    loaded = artigo2.load_package(pkg)

    assert len(loaded.items) == len(items)
    assert not loaded.skipped
    assert loaded.items[0].title == first["title"]


def test_pending_writes_placeholders(tmp_path: Path) -> None:
    out = tmp_path / "pending"
    assert artigo2.main(["--pending", "--out", str(out)]) == 0
    macros = _macros(out / "macros.tex")
    assert sorted(macros) == sorted(artigo2.MACROS)
    assert macros["ResItens"] == "\\verificar{11.433}"
    assert macros["ResRho"] == "\\pend{a calcular}"
    for table in artigo2.TABLES:
        assert "\\pend" in (out / f"{table}.tex").read_text(encoding="utf-8")


def _run(tmp_path: Path, name: str, **package: object) -> tuple[dict, dict[str, str], Path]:
    pkg = _write_package(tmp_path / f"pkg-{name}", **package)  # type: ignore[arg-type]
    out = tmp_path / f"out-{name}"
    args = [
        str(pkg),
        "--out",
        str(out),
        "--bootstrap",
        "200",
        "--min-n",
        "3",
        "--min-together",
        "1",
    ]
    assert artigo2.main(args) == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    return summary, _macros(out / "macros.tex"), out


def _issues(tool: str, repo: str, reactions: list[int], start: int) -> tuple[list[Item], list]:
    """Issues of one repository that mention `tool`, each with the given reactions."""
    items = [
        _item(
            f"{tool}-{n}",
            "github",
            ItemKind.ISSUE,
            f"https://github.com/{repo}/issues/{start + n}",
            title=f"{tool} question {n}",
            reactions=r,
        )
        for n, r in enumerate(reactions)
    ]
    return items, [_det(it, tool) for it in items]


def test_mean_ranks_tools_where_the_median_is_stuck_at_the_floor(tmp_path: Path) -> None:
    """Most issues have no reaction: every tool's median is the same number (ADR-059).

    Run 37a6 had 21 tools with the same median E, and Spearman's rho was undefined
    although Kruskal-Wallis showed that the tools differ.
    """
    gh = "https://github.com"
    reactions = {
        "ragas": [0, 0, 0, 0, 0, 0, 9],
        "deepeval": [0, 0, 0, 0, 0, 3, 5],
        "promptfoo": [0, 0, 0, 0, 2, 4, 6],
        "garak": [0, 0, 0, 0, 0, 0, 0],
    }
    items: list[Item] = []
    dets: list = []
    for k, (tool, values) in enumerate(reactions.items()):
        new_items, new_dets = _issues(tool, f"acme/talk{k}", values, 100 * k)
        items += new_items
        dets += new_dets
    # adoption: repositories that run each tool in CI
    for k, (tool, n3) in enumerate({"ragas": 1, "deepeval": 2, "promptfoo": 4, "garak": 0}.items()):
        for j in range(n3):
            repo_item = _item(f"r{k}{j}", "github", ItemKind.REPO, f"{gh}/user{k}/app{j}", stars=1)
            items.append(repo_item)
            dets.append(_det(repo_item, tool, "N3"))
    summary, macros, out = _run(tmp_path, "zeros", items=items, dets=dets)

    rows = {r["entry"]: r for r in _rows(out / "qp3_engajamento_ferramentas.csv")}
    assert len({rows[t]["median"] for t in reactions}) == 1  # the same floor for all four
    assert float(rows["garak"]["share_floor"]) == 1.0
    means = [float(rows[t]["mean"]) for t in ("garak", "ragas", "deepeval", "promptfoo")]
    assert means == sorted(means) and means[0] < means[-1]  # the mean tells them apart
    assert macros["ResRhoMediana"] == "n/d"  # the statistic first registered: undefined
    assert macros["ResRho"] != "n/d" and summary["spearman"]["rho"] == pytest.approx(1.0)
    assert macros["ResEngEstatistica"] == "média"
    assert macros["ResPDoisVeredito"] == "não sustenta P2"  # here engagement follows adoption
    assert summary["spearman"]["ci95"][0] <= summary["spearman"]["rho"]
    table = (out / "tab_qp3.tex").read_text(encoding="utf-8")
    assert table.splitlines()[0].startswith("promptfoo & 7 & ")  # highest mean first


def test_percentile_is_a_mid_rank_and_weights_change_the_reference() -> None:
    plain, weighted = artigo2.percentiles([0, 0, 0, 5], [1.0, 1.0, 1.0, 1.0])
    assert list(plain) == pytest.approx([1 / 3, 1 / 3, 1 / 3, 1.0])
    assert plain.mean() == pytest.approx(0.5)  # whatever the share of zeros
    assert list(weighted) == pytest.approx(list(plain))
    # a zero that stands for 10 records: the 5 still beats everything, the zeros tie more
    _, heavy = artigo2.percentiles([0, 0, 0, 5], [10.0, 1.0, 1.0, 1.0])
    assert heavy[3] == pytest.approx(1.0) and heavy[1] == pytest.approx(0.5 * 11 / 12)
    one, _ = artigo2.percentiles([7], [1.0])
    assert list(one) == [0.5]


def test_automated_titles() -> None:
    """Bot upkeep and periodic bulletins, which ordinary accounts publish too."""
    automated = [
        "Bump ragas from 0.1 to 0.2",
        "2024-12-25, Papers for large language model, llm",
        "Latest 15 Papers - November 17, 2025",
        "[每日信息流] 2025-11-08",
        "Arxiv Paper Digest for 2025-10-09",
        "Showing new listings for Wednesday, 25 September 2024",
        "Daily Hacker News 22-04-2026",
        "2025-04-08-05_7",
    ]
    people = [
        "[Question]: How to evaluate Agent?",
        "[HOLD for payment 2024-12-17] [$250] Contact method not displayed",
        "lamini apis broke on Jan 26, 2024",
        "[eval] Marin Eval Policy (August 2026)",
        "Weekly sync notes",
        "Faithfulness crashes on empty context",
    ]
    assert all(artigo2.automated_title(t) for t in automated)
    assert not any(artigo2.automated_title(t) for t in people)


def test_automated_issues_leave_every_analysis(tmp_path: Path) -> None:
    gh = "https://github.com"
    bulletin = _item(
        "i-digest", "github", ItemKind.ISSUE, f"{gh}/acme/digest/issues/1",
        title="Arxiv Paper Digest for 2025-10-09", reactions=0,
    )  # fmt: skip
    items, dets = _corpus()
    summary, macros, out = _run(
        tmp_path, "digest", items=[*items, bulletin], dets=[*dets, _det(bulletin, "garak")]
    )
    assert macros["ResItensAutomaticos"] == "2"  # the bulletin and "Bump ragas ..."
    rows = {r["tool"]: r for r in _rows(out / "qp1_piramide.csv")}
    assert rows["garak"]["n1"] == "3"  # the Stack Exchange items only: no unit for the bulletin
    assert summary["engagement_excluded"]["bot"] == 2


def _weighted_package() -> dict:
    """A corpus where one issue stratum was sampled at 1% and another came whole."""
    from datetime import date

    from msrkit.models import Query
    from msrkit.partition import query_key

    gh = "https://github.com"
    big = Query(source="github", terms=["LLM evaluation"], kind="issue",
                since=date(2026, 1, 1), until=date(2026, 8, 31))  # fmt: skip
    small = Query(source="github", terms=["LLM test oracle"], kind="issue",
                  since=date(2023, 1, 1), until=date(2023, 12, 31))  # fmt: skip
    repos = Query(source="github", terms=["RAG evaluation"], kind="repo",
                  since=date(2024, 1, 1), until=date(2024, 12, 31))  # fmt: skip

    def entry(q: Query, items: int, total: int, *reasons: str) -> dict:
        return {
            "query_string": q.terms[0], "partitions": 1, "requests": 1, "items": items,
            "truncated": bool(reasons), "truncation_reasons": list(reasons),
            "estimated_total": total, "key": query_key(q), "completed": True,
        }  # fmt: skip

    items: list[Item] = []
    dets: list = []

    def add(iid: str, kind: ItemKind, url: str, q: Query, tool: str, level: str = "N1") -> None:
        it = _item(iid, "github", kind, url, reactions=1 if kind == ItemKind.ISSUE else 0)
        it.provenance.partition = query_key(q)
        items.append(it)
        dets.append(_det(it, tool, level))

    # ragas: mentioned in the heavily sampled stratum; deepeval and garak: in the whole one
    for n in range(2):
        add(f"b{n}", ItemKind.ISSUE, f"{gh}/big/r{n}/issues/1", big, "ragas")
    for n in range(4):
        add(f"s{n}", ItemKind.ISSUE, f"{gh}/small/d{n}/issues/1", small, "deepeval")
    for n in range(3):
        add(f"g{n}", ItemKind.ISSUE, f"{gh}/small/g{n}/issues/1", small, "garak")
    for n in range(3):
        add(f"a{n}", ItemKind.REPO, f"{gh}/users/a{n}", repos, "deepeval", "N3")
    for n in range(4):  # ragas: the most adopted
        add(f"x{n}", ItemKind.REPO, f"{gh}/users/x{n}", repos, "ragas", "N3")
    add("p0", ItemKind.REPO, f"{gh}/users/p0", repos, "promptfoo", "N3")
    manifest = {
        "run_id": "2026-09-01T00-00-00Z-w",
        "msrkit_version": "t",
        "protocol_path": "p.yaml",
        "protocol_sha256": "cd" * 32,
        "started_at": "2026-09-01T00:00:00Z",
        "run_options": {"per_query_limit": 100},
        "sources": [
            {
                "name": "github",
                "adapter_version": "0.3.0",
                "availability": {"status": "OK", "reason": "ok"},
                "planned": [q.model_dump(mode="json") for q in (big, small, repos)],
                "queries": [
                    entry(big, 100, 10000, "item_limit"),
                    entry(small, 7, 7),
                    entry(repos, 8, 8),
                ],
            }
        ],
    }
    return {"items": items, "dets": dets, "manifest": manifest}


def test_design_weights_reach_the_counts_and_the_propositions(tmp_path: Path) -> None:
    summary, macros, out = _run(tmp_path, "weights", **_weighted_package())
    assert summary["weights"]["available"] is True and summary["weights"]["max"] == 100
    assert macros["ResPesos"] == "ponderado pelas frações de amostragem"
    rows = {r["tool"]: r for r in _rows(out / "qp1_piramide.csv")}
    # two mentions in a stratum sampled at 1% stand for 200; the others for themselves
    assert (rows["ragas"]["n1"], rows["ragas"]["n1_w"]) == ("2", "200.0")
    assert (rows["deepeval"]["n1"], rows["deepeval"]["n1_w"]) == ("4", "4.0")
    assert rows["deepeval"]["n3_ext_w"] == "3.0"  # a complete repository search: weight 1
    assert summary["kendall_weighted"]["tools"] == 4
    # In the sample ragas is the most adopted and one of the least mentioned (tau = 0).
    # Its two mentions come from a stratum sampled at 1%: weighted, it leads both
    # rankings (tau = 2/3). The readings disagree, so P1 is not called either way.
    assert summary["kendall"]["UmTres"]["tau"] == pytest.approx(0.0)
    assert summary["kendall_weighted"]["tau"] == pytest.approx(2 / 3)
    assert summary["p1"] == {
        "sample": "sustenta P1",
        "weighted": "não sustenta P1",
        "conclusion": "inconclusivo (depende da ponderação)",
    }
    assert macros["ResPUmConclusao"] == "inconclusivo (depende da ponderação)"
    first = (out / "tab_qp1_pond.tex").read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("Ragas & 4,0 & ") and first.count("&") == 4


def test_without_totals_in_the_manifest_there_are_no_weighted_results(run_dir: Path) -> None:
    macros = _macros(run_dir / "macros.tex")
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert macros["ResPesos"] == "sem pesos" and summary["weights"]["available"] is False
    assert macros["ResTauUmTresPond"] == "n/d" and macros["ResPUmVereditoPond"] == "n/d"
    assert macros["ResPUmConclusao"] == macros["ResPUmVeredito"]  # the sample reading alone


def test_the_bare_name_in_ci_is_adoption_only_in_the_sensitivity_analysis(tmp_path: Path) -> None:
    items, dets = _corpus()
    by_id = {it.id: it for it in items}
    named = _det(by_id["r-app5"], "garak", "N2").model_copy(update={"signal": "ci", "via": "name"})
    repos = [
        {"repo": "acme/app5", "contributors": 4, "commit_months": ["2025-01", "2025-02"]},
        {"repo": "acme/app1", "contributors": 1, "commit_months": ["2025-01"]},
    ]
    summary, macros, out = _run(tmp_path, "name", items=items, dets=[*dets, named], repos=repos)
    rows = {r["tool"]: r for r in _rows(out / "qp1_piramide.csv")}
    assert (rows["garak"]["n3_ext"], rows["garak"]["n2"]) == ("0", "1")  # N2 in the main analysis
    assert int(macros["ResUnidadesComNTresNome"]) == int(macros["ResUnidadesComNTres"]) + 1
    assert summary["kendall_name_as_n3"]["tools"] == summary["kendall"]["UmTres"]["tools"]


def test_conclusion_needs_both_readings_to_agree() -> None:
    assert artigo2.conclusion("sustenta P1", "sustenta P1") == "sustenta P1"
    assert artigo2.conclusion("sustenta P1", "não sustenta P1").startswith("inconclusivo")
    assert artigo2.conclusion("sustenta P1", "n/d") == "sustenta P1"
    assert artigo2.verdict(0.36, 0.5, "P1") == "sustenta P1"
    assert artigo2.verdict(-0.6, 0.4, "P2", absolute=True) == "não sustenta P2"
    assert artigo2.verdict(math.nan, 0.4, "P2") == "n/d"


def test_statistics_helpers() -> None:
    assert artigo2.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert artigo2.cliffs_delta([3, 4, 5], [1, 2, 3]) == pytest.approx(1 - 1 / 9)
    assert artigo2.fmt_int(11433) == "11.433"
    assert artigo2.fmt_float(-0.25) == "\\ensuremath{-}0,25"
    assert artigo2.fmt_float(math.nan) == "n/d"
    assert artigo2.fmt_p(0.0001) == "\\ensuremath{<}\\,0,001"
    kw = artigo2.kruskal_dunn({"a": [0.1, 0.2, 0.3], "b": [0.7, 0.8, 0.9], "c": [0.4, 0.5, 0.6]})
    assert kw["groups"] == 3 and len(kw["pairs"]) == 3
    assert all(p["p_holm"] >= p["p"] for p in kw["pairs"])
    assert artigo2.join_pt(["A", "B", "C"]) == "A, B e C"


def test_maintainer_org_siblings_are_counted_as_self(tmp_path: Path) -> None:
    items, dets = _corpus()
    sibling = _item(
        "r-org-sib",
        "github",
        ItemKind.REPO,
        "https://github.com/explodinggradients/ragas-validators",
        stars=10,
    )
    items.append(sibling)
    dets.append(_det(sibling, "ragas", "N3"))
    _, _, out = _run(tmp_path, "org-self", items=items, dets=dets)
    rows = {r["tool"]: r for r in _rows(out / "qp1_piramide.csv")}
    assert rows["ragas"]["self"] == "2"
    assert rows["ragas"]["n3_ext"] == "1"
