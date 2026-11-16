#!/usr/bin/env python3
"""Article 2 analyses (QP1–QP3) over an MSR-Kit reproducibility package.

Reads the zip (or the unpacked folder) written by `msrkit package` and computes,
on the automatic `detections` basis, what the Article 2 protocol guide asks for:

- QP1: evidence pyramid per testing tool (external N3, N2, N1) without the
  system-under-test frameworks (family `framework-sut`) and with the tool's own
  repository ("self") set apart; RAG x agents split by the matched concepts;
  mention x adoption gap (Kendall tau-b between rankings, bootstrap CI over
  units); co-occurrence (Jaccard and lift), including the frameworks the tests
  run on.
- QP2: methods per system and the oracle ladder from each method's typical rung
  (an approximation; the manual coding is the reference).
- QP3: engagement normalized within each source x kind (percentile with average
  ranks for ties) after a minimum-age filter; mean per tool and per method with
  bootstrap CIs (the median is reported, but most issues have no reaction and it
  sits on the floor for every tool); Kruskal-Wallis with Dunn/Holm, epsilon
  squared and Cliff's delta; Spearman between engagement and external N3;
  sensitivity analyses (top share of the stratum, without listicles, only
  non-truncated queries); a negative binomial robustness check; proposition P3;
  and the "pain" engagement of the tools' own bug issues.

Queries that stopped at the run's item limit are samples. When the package's
manifest has the per-query totals, P1 and P2 are also computed on design-weighted
statistics (msrkit.weights), and a proposition is read as supported only when the
sample and the weighted readings agree. Automated GitHub issues (bot upkeep and
periodic bulletins, by title) are left out of every analysis.

Outputs go to --out: CSV files, `summary.json` and LaTeX files the paper
includes (`macros.tex` plus table bodies and the scatter figure). Every macro
carries its origin (file, basis, evidence level) in a comment. With --pending
the same files are written with visible placeholders, so the paper compiles
before the analysis runs.

Usage:
    python analysis/artigo2.py PACKAGE --out results/ [options]
    python analysis/artigo2.py --pending --out results/

Option defaults are provisional author decisions (ADR-046 in docs/decisions.md).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import warnings
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import yaml
from pydantic import ValidationError
from scipy import stats

from msrkit.analysis import Unit, oracle_distribution, units_from_detections
from msrkit.extract import LEVEL_RANK, N3_MIN_CONTRIBUTORS, N3_MIN_MONTHS, Detection
from msrkit.gazetteer import Gazetteer
from msrkit.models import Item, Manifest
from msrkit.weights import ItemWeight, item_weights, kish, pair_weights, strata

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

FAMILY_SUT = "framework-sut"
SYSTEMS = ("rag", "agente")
DAYS_PER_MONTH = 30.4375
LADDER = ("especificado", "derivado", "referencia", "pseudo-automatico", "humano")

# Primary engagement metric per source (guide §4.4). GitHub depends on the kind.
SOURCE_METRIC = {
    "hackernews": "votes",
    "stackexchange": "votes",
    "devto": "reactions",
    "huggingface": "stars",  # likes; downloads include automation
    "bluesky": "reactions",
    "reddit": "votes",
}
BUG_QUERY = re.compile(r"repo:(\S+)\s+is:issue\s+label:bug", re.IGNORECASE)
# Package items carry pseudonymized authors, so automated issues are spotted by
# title: dependency bumps, lock-file maintenance and vulnerability reports.
BOT_TITLE = re.compile(
    r"^(bump |chore\(deps|build\(deps|fix\(deps|update dependency|update \S+ requirement"
    r"|update \S+ to v?\d|lock file maintenance|dependency dashboard|\[snyk\]"
    r"|\[pre-commit\.ci\]|configure renovate)"
    r"|vulnerabilit(?:y|ies) \(highest severity",
    re.IGNORECASE,
)
# Periodic bulletins that ordinary accounts publish as issues (daily arXiv listings,
# news digests, weekly reports): a date in the title plus a word of the genre, or a
# title that is nothing but a date. The collection's bot filter only sees the author.
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATED_TITLE = re.compile(
    rf"\b\d{{4}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}\b|\b\d{{1,2}}[-/.]\d{{1,2}}[-/.]\d{{4}}\b"
    rf"|\b{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}\b|\b\d{{1,2}}\s+{_MONTH}\s+\d{{4}}\b",
    re.IGNORECASE,
)
SERIAL_TITLE = re.compile(
    r"\b(?:papers?|arxiv|digest|daily|weekly|nightly|radar|newsletter|roundup|hacker\s*news"
    r"|trending|briefing|listings?|submissions?)\b|信息流|日报|周报|论文|速递",
    re.IGNORECASE,
)


def automated_title(title: str) -> bool:
    """Whether an issue title is that of an automated record (bot upkeep or bulletin)."""
    if BOT_TITLE.search(title):
        return True
    if not DATED_TITLE.search(title):
        return False
    return bool(SERIAL_TITLE.search(title)) or not re.search(r"[^\W\d_]", title)


FAMILY_LABEL = {
    "avaliacao-llm-rag": "avaliação LLM/RAG",
    "agentes": "agentes",
    "guardrails-seguranca": "guardrails/segurança",
    "recuperacao": "recuperação",
    "tracing-observabilidade": "rastreamento",
    FAMILY_SUT: "framework (SUT)",
}
ORACLE_LABEL = {
    "especificado": "Especificado",
    "derivado": "Derivado",
    "referencia": "Referência",
    "pseudo-automatico": "Pseudoautomático",
    "humano": "Humano",
}
ORACLE_SHORT = {
    "especificado": "especificado",
    "derivado": "derivado",
    "referencia": "referência",
    "pseudo-automatico": "pseudoaut.",
    "humano": "humano",
}
SHORT_NAME = {"bfcl": "BFCL", "lm-eval-harness": "lm-eval-harness"}

# -- macro registry -------------------------------------------------------------
# Every macro the paper uses. Values are filled by the analysis; a macro left
# empty is written as a placeholder, so the paper always compiles.
MACROS: dict[str, str] = {
    # parameters (provisional author decisions, ADR-046)
    "ResMinIdade": "parameter --min-age-months",
    "ResMinN": "parameter --min-n",
    "ResBoot": "parameter --bootstrap",
    "ResListicle": "parameter --listicle-k",
    "ResLimiarTau": "parameter --tau-threshold",
    "ResLimiarRho": "parameter --rho-threshold",
    "ResMinPar": "parameter --min-together",
    "ResTopoPct": "parameter --top-share, as a percentage (top stratum share of QP3)",
    # corpus (package files)
    "ResRunId": "run/manifest.json: run_id",
    "ResProtocoloSha": "run/manifest.json: protocol_sha256 (first 12 hex)",
    "ResPerQueryLimit": "run/manifest.json: run_options.per_query_limit",
    "ResItens": "corpus/items.jsonl: items",
    "ResFontes": "corpus/items.jsonl: sources with items",
    "ResPctGithub": "corpus/items.jsonl: % of items from GitHub",
    "ResItensPt": "corpus/items.jsonl: items with a Portuguese lexicon match",
    "ResDataMin": "corpus/items.jsonl: earliest created_at (YYYY-MM)",
    "ResDataMax": "corpus/items.jsonl: latest created_at (YYYY-MM)",
    "ResUnidades": "units (msrkit.analysis.units_from_detections)",
    "ResUnidadesGithub": "units that are GitHub repositories",
    "ResDeteccoes": "evidence/detections.jsonl: detections",
    "ResDeteccoesFerramenta": "evidence/detections.jsonl: tool detections",
    "ResDeteccoesMetodo": "evidence/detections.jsonl: method detections",
    "ResReposEnriquecidos": "evidence/github_repos.jsonl: repositories",
    "ResReposErro": "evidence/github_repos.jsonl: repositories with error",
    "ResPrismaIdentificados": "reports/prisma.json: records identified",
    "ResPrismaFiltrados": "reports/prisma.json: removed by local filters",
    "ResPrismaDuplicatas": "reports/prisma.json: duplicates removed",
    "ResPrismaTriagem": "reports/prisma.json: screening pool",
    "ResPrismaIncluidos": "reports/prisma.json: included after screening",
    "ResPrismaPendentes": "reports/prisma.json: without screening decision",
    "ResPrismaCodificados": "reports/prisma.json: coded",
    "ResPrismaNaoExaminados": "reports/prisma.json: not examined by item-limit queries (est.)",
    "ResItensAutomaticos": "corpus: automated GitHub issues set aside by title (all analyses)",
    # sampling weights (run/manifest.json through msrkit.weights, ADR-058)
    "ResPesos": "whether the manifest gives sampling fractions (design weights)",
    "ResPesoMax": "largest design weight of an item",
    "ResNEfetivo": "Kish effective sample size of the items",
    "ResItensPesoDesconhecido": "items of truncated queries without a total (weight 1)",
    # QP1 (detections basis, units, best level, self excluded)
    "ResFerramentasTeste": "gazetteer: testing tools (families other than framework-sut)",
    "ResFerramentasDetectadas": "QP1: testing tools in >= 1 unit",
    "ResUnidadesComFerramenta": "QP1: units with >= 1 testing tool (self excluded)",
    "ResUnidadesComNTres": "QP1: units with >= 1 testing tool at external N3",
    "ResSelfPares": "QP1: unit-tool pairs set apart as self",
    "ResTopNTres": "QP1: top tool by external N3",
    "ResTopNTresN": "QP1: units of the top tool at external N3",
    "ResTopNDois": "QP1: top tool by N2",
    "ResTopNDoisN": "QP1: units of the top tool at N2",
    "ResTopNUm": "QP1: top tool by N1 (mention)",
    "ResTopNUmN": "QP1: units of the top tool at N1",
    "ResTauUmTres": "QP1.3: Kendall tau-b, N1 x external N3 rankings",
    "ResTauUmTresLo": "QP1.3: bootstrap 95% CI, lower",
    "ResTauUmTresHi": "QP1.3: bootstrap 95% CI, upper",
    "ResTauUmTresP": "QP1.3: p-value",
    "ResTauDoisTres": "QP1.3: Kendall tau-b, N2 x external N3 rankings",
    "ResTauDoisTresLo": "QP1.3: bootstrap 95% CI, lower",
    "ResTauDoisTresHi": "QP1.3: bootstrap 95% CI, upper",
    "ResTauUmDois": "QP1.3: Kendall tau-b, N1 x N2 rankings",
    "ResTauFerramentas": "QP1.3: tools in the rank correlation",
    "ResPUmVeredito": "P1 verdict against --tau-threshold (sample counts)",
    "ResTauUmTresPond": "QP1.3: Kendall tau-b, N1 x external N3, design-weighted counts",
    "ResTauUmTresPondLo": "QP1.3 weighted: bootstrap 95% CI, lower",
    "ResTauUmTresPondHi": "QP1.3 weighted: bootstrap 95% CI, upper",
    "ResPUmVereditoPond": "P1 verdict on the design-weighted counts",
    "ResPUmConclusao": "P1: supported only when sample and weighted verdicts agree",
    "ResTauUmTresNome": "QP1.3 sensitivity: tau with name-only CI evidence counted as N3",
    "ResUnidadesComNTresNome": "QP1 sensitivity: units at external N3 under that looser rule",
    "ResMaisFaladas": "QP1.3: tools most above the diagonal (mention >> adoption)",
    "ResMaisAdotadas": "QP1.3: tools most below the diagonal (adoption >> mention)",
    "ResFwTop": "context: framework in most units",
    "ResFwTopN": "context: its units",
    "ResFwSegundo": "context: second framework",
    "ResFwSegundoN": "context: its units",
    "ResPctUnidadesComFw": "context: % of units with a testing tool that also use a framework",
    "ResJaccardDeteccoes": "QP5 preview: Jaccard of tool sets, rag x agente units (detections)",
    # QP2 (detections basis)
    "ResTopMetodo": "QP2: method in most units",
    "ResTopMetodoN": "QP2: its units",
    "ResUnidadesComOraculo": "QP2: units with >= 1 oracle rung (typical rung of methods)",
    "ResPctPseudo": "QP2: % pseudo-automatic among units with a rung",
    "ResPctReferencia": "QP2: % reference",
    "ResPctEspecificado": "QP2: % specified",
    "ResPctHumano": "QP2: % human",
    "ResPctDerivado": "QP2: % derived",
    # QP3 (discursive items, engagement percentile within source x kind)
    "ResEngItens": "QP3: eligible discursive items",
    "ResEngExclIdade": "QP3: excluded for age below --min-age-months",
    "ResEngExclBot": "QP3: excluded as automated (bot) issues",
    "ResEngExclDor": "QP3: tool bug issues set apart (pain analysis)",
    "ResEngExclSemMetrica": "QP3: excluded without engagement metric or date",
    "ResEngFerramentas": "QP3: tools with n >= --min-n",
    "ResEngMetodos": "QP3: methods with n >= --min-n",
    "ResEngEstatistica": "QP3: statistic that summarizes E per tool (ADR-059)",
    "ResEngPctPiso": "QP3: % of eligible items tied at the lowest value of their stratum",
    "ResEngTopFerramenta": "QP3: tool with highest mean engagement",
    "ResEngTopFerramentaMed": "QP3: its mean E",
    "ResEngTopMetodo": "QP3: method with highest mean engagement",
    "ResEngTopMetodoMed": "QP3: its mean E",
    "ResKwH": "QP3: Kruskal-Wallis H across tools",
    "ResKwP": "QP3: Kruskal-Wallis p-value",
    "ResKwEps": "QP3: epsilon squared",
    "ResDunnSig": "QP3: Dunn pairs significant after Holm",
    "ResDunnPares": "QP3: Dunn pairs tested",
    "ResRho": "P2: Spearman rho, mean E x external N3",
    "ResRhoLo": "P2: bootstrap 95% CI over items (N3 fixed), lower",
    "ResRhoHi": "P2: bootstrap 95% CI over items (N3 fixed), upper",
    "ResRhoP": "P2: p-value",
    "ResRhoN": "P2: tools in the correlation",
    "ResPDoisVeredito": "P2 verdict against --rho-threshold (sample statistics)",
    "ResRhoPond": "P2: rho, design-weighted mean E x weighted external N3",
    "ResPDoisVereditoPond": "P2 verdict on the design-weighted statistics",
    "ResPDoisConclusao": "P2: supported only when sample and weighted verdicts agree",
    "ResRhoMediana": "P2 as first registered: rho with the median E (n/d when it is constant)",
    "ResRhoTopo": "P2 sensitivity: rho with the share of items in the top of their stratum",
    "ResRhoSemListicle": "P2 sensitivity: rho without listicles",
    "ResRhoNaoTruncadas": "P2 sensitivity: rho with non-truncated queries only",
    "ResItensNaoTruncadas": "P2 sensitivity: eligible items from non-truncated queries",
    "ResNbRho": "robustness: rho between NB tool effects and mean E",
    "ResQuadFalado": "QP3 quadrant: high engagement, low adoption",
    "ResQuadAdotado": "QP3 quadrant: low engagement, high adoption",
    "ResPTresDelta": "P3: Cliff's delta, agente x rag items (2025-2026)",
    "ResPTresBeta": "P3: OLS coefficient of agente (E ~ agente + log age + stratum)",
    "ResPTresLo": "P3: 95% CI lower (HC3)",
    "ResPTresHi": "P3: 95% CI upper (HC3)",
    "ResPTresP": "P3: p-value",
    "ResPTresN": "P3: items (agente + rag)",
    "ResPTresVeredito": "P3 verdict",
    "ResDorIssues": "pain: bug issues of the tools' own repositories",
    "ResDorFerramentas": "pain: tools with bug issues",
}

# Values known from the guide/comparison of the d28a mining, shown (marked for
# checking) only by --pending until the analysis runs on the package.
KNOWN_D28A = {
    "ResItens": "11.433",
    "ResDeteccoes": "9.871",
    "ResReposEnriquecidos": "5.500",
    "ResPctGithub": "81",
    "ResItensPt": "90",
    "ResFwTop": "LangChain",
    "ResFwTopN": "815",
    "ResFwSegundo": "LangGraph",
    "ResFwSegundoN": "550",
    "ResJaccardDeteccoes": "0,97",
    "ResTopNDois": "Ragas",
    "ResTopNTres": "promptfoo",
}
TABLES = (
    "tab_qp1",
    "tab_qp1_pond",
    "tab_qp1_pares",
    "tab_qp2",
    "tab_qp2_oraculos",
    "tab_qp3",
    "tab_qp3_metodos",
)
TABLE_COLUMNS = {
    "tab_qp1": 8,
    "tab_qp1_pond": 5,
    "tab_qp1_pares": 5,
    "tab_qp2": 5,
    "tab_qp2_oraculos": 4,
    "tab_qp3": 4,
    "tab_qp3_metodos": 4,
}


# -- formatting -----------------------------------------------------------------


def fmt_int(n: int | float) -> str:
    return f"{int(round(n)):,}".replace(",", ".")


def fmt_float(x: float | None, decimals: int = 2) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/d"
    text = f"{abs(x):.{decimals}f}".replace(".", ",")
    return ("\\ensuremath{-}" + text) if x < 0 and text.strip("0,") else text


def fmt_p(p: float | None) -> str:
    if p is None or math.isnan(p):
        return "n/d"
    return "\\ensuremath{<}\\,0,001" if p < 0.001 else fmt_float(p, 3)


def fmt_pct(part: float, whole: float) -> str:
    return fmt_int(100 * part / whole) if whole else "n/d"


def latex_escape(text: str) -> str:
    for a, b in (
        ("\\", "\\textbackslash{}"),
        ("&", "\\&"),
        ("%", "\\%"),
        ("$", "\\$"),
        ("#", "\\#"),
        ("_", "\\_"),
        ("{", "\\{"),
        ("}", "\\}"),
    ):
        text = text.replace(a, b)
    return text


def join_pt(names: list[str]) -> str:
    if not names:
        return "nenhuma"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]


class Macros:
    """Values for the macro registry, written as `\\newcommand`s with their origin."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.notes: dict[str, str] = {}

    def set(self, name: str, value: str, note: str = "") -> None:
        if name not in MACROS:
            raise KeyError(f"macro {name} is not in the registry")
        self.values[name] = value
        if note:
            self.notes[name] = note

    def write(self, path: Path, header: str, pending: bool = False) -> None:
        lines = [f"% {line}" for line in header.splitlines()]
        for name, origin in MACROS.items():
            note = f" — {self.notes[name]}" if name in self.notes else ""
            if name in self.values:
                value = self.values[name]
            elif pending and name in KNOWN_D28A:
                value = f"\\verificar{{{KNOWN_D28A[name]}}}"
                note = " — d28a, guia do Artigo 2 / comparativo [VERIFICAR]"
            else:
                value = "\\pend{a calcular}" if pending else "n/d"
            lines.append(f"% {origin}{note}")
            lines.append(f"\\newcommand{{\\{name}}}{{{value}}}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {k: (json.dumps(v) if isinstance(v, dict | list) else v) for k, v in row.items()}
            )


def write_table(path: Path, rows: list[str], columns: int) -> None:
    if not rows:
        rows = [f"\\multicolumn{{{columns}}}{{c}}{{\\pend{{sem dados}}}} \\\\"]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


# -- package --------------------------------------------------------------------


@dataclass
class Package:
    origin: str
    items: list[Item]
    detections: list[Detection]
    repos: list[dict[str, Any]]
    gazetteer: Gazetteer
    manifest: dict[str, Any] = field(default_factory=dict)
    package_manifest: dict[str, Any] = field(default_factory=dict)
    prisma: dict[str, Any] = field(default_factory=dict)
    skipped: Counter[str] = field(default_factory=Counter)


def _reader(path: Path) -> tuple[list[str], Callable[[str], bytes]]:
    if path.is_dir():
        files = {p.relative_to(path).as_posix(): p for p in path.rglob("*") if p.is_file()}
        return sorted(files), lambda name: files[name].read_bytes()
    archive = zipfile.ZipFile(path)
    return sorted(archive.namelist()), archive.read


def load_package(path: Path, gazetteer_path: Path | None = None) -> Package:
    names, read = _reader(path)

    def find(suffix: str) -> str | None:
        hits = [n for n in names if n == suffix or n.endswith("/" + suffix)]
        return min(hits, key=len) if hits else None

    def text(suffix: str) -> str:
        name = find(suffix)
        return read(name).decode("utf-8") if name else ""

    def records(suffix: str) -> list[str]:
        # One record per "\n". `str.splitlines` also breaks on U+2028 and other separators
        # that JSON leaves unescaped inside a string, and cut such items into pieces.
        return [line for line in text(suffix).split("\n") if line.strip()]

    skipped: Counter[str] = Counter()
    items: list[Item] = []
    for line in records("corpus/items.jsonl"):
        try:
            items.append(Item.model_validate_json(line))
        except ValidationError:
            skipped["items"] += 1
    detections: list[Detection] = []
    for line in records("evidence/detections.jsonl"):
        try:
            detections.append(Detection.model_validate_json(line))
        except ValidationError:
            skipped["detections"] += 1
    repos = [json.loads(line) for line in records("evidence/github_repos.jsonl")]

    if gazetteer_path is not None:
        raw = yaml.safe_load(gazetteer_path.read_text(encoding="utf-8"))
    else:
        raw = None
        for name in names:
            if "protocol/" in name and name.endswith((".yaml", ".yml")):
                data = yaml.safe_load(read(name).decode("utf-8"))
                if isinstance(data, dict) and "tools" in data:
                    raw = data
                    break
        if raw is None:
            raise SystemExit("gazetteer not found in the package; pass --gazetteer")
    manifest = json.loads(text("run/manifest.json") or "{}")
    package_manifest = json.loads(text("package_manifest.json") or "{}")
    prisma = json.loads(text("reports/prisma.json") or "{}")
    if not items:
        raise SystemExit(f"no items in {path} (expected corpus/items.jsonl)")
    return Package(
        origin=str(path),
        items=items,
        detections=detections,
        repos=repos,
        gazetteer=Gazetteer.model_validate(raw),
        manifest=manifest,
        package_manifest=package_manifest,
        prisma=prisma,
        skipped=skipped,
    )


# -- gazetteer helpers ----------------------------------------------------------


@dataclass
class Catalog:
    names: dict[str, str]
    family: dict[str, str]
    applies_to: dict[str, list[str]]
    test_tools: list[str]  # families other than framework-sut
    frameworks: list[str]
    self_repos: dict[str, set[str]]
    method_rungs: dict[str, str]
    method_names: dict[str, str]
    self_orgs: dict[str, set[str]] = field(default_factory=dict)

    def is_self(self, unit_id: str, tool: str) -> bool:
        """Return whether a unit belongs to the tool's repository or maintainer organization."""
        unit_lower = unit_id.lower()
        if unit_lower in self.self_repos.get(tool, set()):
            return True
        if "/" in unit_lower:
            owner = unit_lower.split("/", 1)[0]
            return owner in self.self_orgs.get(tool, set())
        return False

    def display(self, entry: str) -> str:
        return latex_escape(
            SHORT_NAME.get(entry) or self.names.get(entry) or self.method_names.get(entry) or entry
        )


def catalog(gz: Gazetteer) -> Catalog:
    return Catalog(
        names={t.id: t.name for t in gz.tools},
        family={t.id: t.family for t in gz.tools},
        applies_to={t.id: list(t.applies_to) for t in gz.tools},
        test_tools=[t.id for t in gz.tools if t.family != FAMILY_SUT],
        frameworks=[t.id for t in gz.tools if t.family == FAMILY_SUT],
        self_repos={t.id: {r.lower() for r in t.repos} for t in gz.tools},
        self_orgs={
            t.id: {r.lower().split("/", 1)[0] for r in t.repos if "/" in r} for t in gz.tools
        },
        method_rungs={m.id: m.oracle for m in gz.methods if m.oracle},
        method_names={m.id: m.name for m in gz.methods},
    )


def external_tools(unit: Unit, cat: Catalog, wanted: Iterable[str]) -> set[str]:
    keep = set(wanted)
    return {t for t in unit.tools if t in keep and not cat.is_self(unit.id, t)}


# -- QP1 ------------------------------------------------------------------------


def pyramid(
    units: list[Unit], cat: Catalog, pair_w: dict[tuple[str, str], float] | None = None
) -> list[dict[str, Any]]:
    """Units per testing tool at their best level, self repositories apart.

    `n3_ext`, `n2` and `n1` count the units of the sample. With `pair_w` (the design
    weight of each unit-tool pair, msrkit.weights.pair_weights), the `_w` columns add
    the weights instead: estimates for what the sources hold, where a 100-item cap
    takes a small share of a large query.
    """
    pair_w = pair_w or {}
    rows = {
        t: {
            "tool": t,
            "name": cat.names[t],
            "family": cat.family[t],
            "applies_to": "/".join(cat.applies_to[t]),
            "n3_ext": 0,
            "n2": 0,
            "n1": 0,
            "self": 0,
            "rag": 0,
            "agente": 0,
            "n3_ext_w": 0.0,
            "n2_w": 0.0,
            "n1_w": 0.0,
        }
        for t in cat.test_tools
    }
    for u in units:
        for tool, level in u.tools.items():
            row = rows.get(tool)
            if row is None:
                continue
            if cat.is_self(u.id, tool):
                row["self"] += 1
                continue
            key = "n3_ext" if level == "N3" else level.lower()
            row[key] += 1
            row[f"{key}_w"] += pair_w.get((u.id, tool), 1.0)
            row["rag"] += "rag" in u.systems
            row["agente"] += "agente" in u.systems
    out = list(rows.values())
    for row in out:
        row["total"] = row["n3_ext"] + row["n2"] + row["n1"]
        for key in ("n3_ext_w", "n2_w", "n1_w"):
            row[key] = round(row[key], 1)
    for key in ("n3_ext", "n2", "n1", "n3_ext_w"):
        values = [-r[key] for r in out]
        ranks = stats.rankdata(values, method="min")
        for row, rank in zip(out, ranks, strict=True):
            row[f"rank_{key}"] = int(rank)
    return sorted(out, key=lambda r: (-r["n3_ext"], -r["n2"], -r["n1"], r["tool"]))


def level_matrix(units: list[Unit], tools: list[str], cat: Catalog) -> np.ndarray:
    index = {t: j for j, t in enumerate(tools)}
    matrix = np.zeros((len(units), len(tools)), dtype=np.int8)
    for i, u in enumerate(units):
        for tool, level in u.tools.items():
            j = index.get(tool)
            if j is not None and not cat.is_self(u.id, tool):
                matrix[i, j] = LEVEL_RANK[level]
    return matrix


def weight_matrix(
    units: list[Unit], tools: list[str], pair_w: dict[tuple[str, str], float]
) -> np.ndarray:
    """Design weight of each unit-tool pair, in the layout of level_matrix (1 when unknown)."""
    index = {t: j for j, t in enumerate(tools)}
    matrix = np.ones((len(units), len(tools)), dtype=np.float64)
    for i, u in enumerate(units):
        for tool in u.tools:
            j = index.get(tool)
            if j is not None:
                matrix[i, j] = pair_w.get((u.id, tool), 1.0)
    return matrix


def kendall_ci(
    matrix: np.ndarray,
    a: int,
    b: int,
    n_boot: int,
    rng: np.random.Generator,
    chunk: int = 250,
    weights: np.ndarray | None = None,
) -> dict[str, Any]:
    """Kendall tau-b between per-tool counts at best levels `a` and `b`.

    Only tools with at least one unit enter. The CI resamples units (multinomial
    weights), recounts and recomputes tau; percentile interval. With `weights` (the
    design weight of each unit-tool pair), a unit counts by its weight.
    """
    keep = (matrix > 0).sum(axis=0) > 0
    first = (matrix[:, keep] == a).astype(np.float64)
    second = (matrix[:, keep] == b).astype(np.float64)
    if weights is not None:
        first *= weights[:, keep]
        second *= weights[:, keep]
    x, y = first.sum(axis=0), second.sum(axis=0)
    out: dict[str, Any] = {
        "tools": int(keep.sum()),
        "tau": math.nan,
        "p": math.nan,
        "lo": math.nan,
        "hi": math.nan,
        "boot": 0,
    }
    if keep.sum() < 3:
        return out
    res = stats.kendalltau(x, y)
    out["tau"], out["p"] = float(res.statistic), float(res.pvalue)
    n_units = matrix.shape[0]
    taus: list[float] = []
    probs = np.full(n_units, 1.0 / n_units)
    for start in range(0, n_boot, chunk):
        weights = rng.multinomial(n_units, probs, size=min(chunk, n_boot - start))
        weights = weights.astype(np.float64)
        for xa, xb in zip(weights @ first, weights @ second, strict=True):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tau = stats.kendalltau(xa, xb).statistic
            if not math.isnan(tau):
                taus.append(float(tau))
    if taus:
        out["lo"], out["hi"] = (float(v) for v in np.percentile(taus, [2.5, 97.5]))
        out["boot"] = len(taus)
    return out


def gap(rows: list[dict[str, Any]], top: int = 3) -> tuple[list[str], list[str]]:
    """Tools farthest from the mention = adoption diagonal (log scale)."""
    present = [r for r in rows if r["total"] > 0]
    for r in present:
        r["log_ratio"] = math.log10((r["n1"] + 1) / (r["n3_ext"] + 1))
    talked = sorted(
        (r for r in present if r["log_ratio"] > 0), key=lambda r: (-r["log_ratio"], r["tool"])
    )[:top]
    adopted = sorted(
        (r for r in present if r["log_ratio"] < 0), key=lambda r: (r["log_ratio"], r["tool"])
    )[:top]
    return [r["tool"] for r in talked], [r["tool"] for r in adopted]


def pairs(
    units: list[Unit],
    left: Callable[[Unit], set[str]],
    right: Callable[[Unit], set[str]] | None,
    min_together: int,
) -> list[dict[str, Any]]:
    """Co-occurrence within units; `right=None` pairs the left set with itself."""
    n = len(units)
    count_left: Counter[str] = Counter()
    count_right: Counter[str] = Counter()
    both: Counter[tuple[str, str]] = Counter()
    for u in units:
        lefts = left(u)
        count_left.update(lefts)
        if right is None:
            both.update(combinations(sorted(lefts), 2))
        else:
            rights = right(u)
            count_right.update(rights)
            both.update((a, b) for a in lefts for b in rights)
    rows = []
    for (a, b), together in both.items():
        if together < min_together:
            continue
        na = count_left[a]
        nb = count_left[b] if right is None else count_right[b]
        rows.append(
            {
                "a": a,
                "b": b,
                "together": together,
                "a_units": na,
                "b_units": nb,
                "jaccard": round(together / (na + nb - together), 4),
                "lift": round(together * n / (na * nb), 4),
            }
        )
    return sorted(rows, key=lambda r: (-r["lift"], -r["together"], r["a"], r["b"]))


def jaccard_systems(units: list[Unit], cat: Catalog) -> float:
    rag = {t for u in units if "rag" in u.systems for t in external_tools(u, cat, cat.test_tools)}
    ag = {t for u in units if "agente" in u.systems for t in external_tools(u, cat, cat.test_tools)}
    union = rag | ag
    return len(rag & ag) / len(union) if union else math.nan


# -- QP2 ------------------------------------------------------------------------


def methods_table(units: list[Unit], gz: Gazetteer) -> list[dict[str, Any]]:
    rows = {
        m.id: {
            "method": m.id,
            "name": m.name,
            "oracle": m.oracle or "",
            "units": 0,
            "rag": 0,
            "agente": 0,
        }
        for m in gz.methods
    }
    for u in units:
        for m in u.methods:
            if m in rows:
                rows[m]["units"] += 1
                rows[m]["rag"] += "rag" in u.systems
                rows[m]["agente"] += "agente" in u.systems
    return sorted(rows.values(), key=lambda r: (-r["units"], r["method"]))


# -- QP3 ------------------------------------------------------------------------


@dataclass
class EngItem:
    id: str
    source: str
    kind: str
    value: int
    age: float  # months between creation and collection
    systems: frozenset[str]
    year: int
    query: str
    e: float = 0.0  # percentile within source x kind
    w: float = 1.0  # design weight (msrkit.weights)
    e_w: float = 0.0  # the same percentile over the design-weighted stratum
    floor: bool = False  # tied at the lowest value of its stratum (e.g. zero reactions)


def percentiles(values: list[int], weights: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """Mid-rank percentile of each value among the others of its stratum, plain and weighted.

    E = (items below + half of the items tied) / (other items): the chance that the
    item beats another item of the stratum drawn at random, ties counting half. Its
    mean over a stratum is 0.5 whatever the distribution, zero-inflated or not.
    """
    v = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    n = len(v)
    if n < 2:
        return np.full(n, 0.5), np.full(n, 0.5)
    plain = (stats.rankdata(v, method="average") - 1) / (n - 1)
    _, inverse = np.unique(v, return_inverse=True)
    tied = np.bincount(inverse, weights=w)
    below = (np.cumsum(tied) - tied)[inverse]
    others = w.sum() - w
    weighted = np.where(
        others > 0, (below + 0.5 * (tied[inverse] - w)) / np.maximum(others, 1e-12), 0.5
    )
    return plain, weighted


def metric_for(source: str, kind: str, with_repos: bool) -> str | None:
    if source == "github":
        if kind == "issue":
            return "reactions"
        return "stars" if kind == "repo" and with_repos else None
    return SOURCE_METRIC.get(source)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def engagement_items(
    pkg: Package,
    min_age: float,
    with_repos: bool,
    keep_bots: bool,
    weights: dict[str, float] | None = None,
) -> tuple[list[EngItem], Counter[str], list[Item]]:
    weights = weights or {}
    eligible: list[EngItem] = []
    excluded: Counter[str] = Counter()
    bug_issues: list[Item] = []
    for it in pkg.items:
        query = it.provenance.query_string or ""
        if it.source == "github" and it.kind == "issue" and BUG_QUERY.search(query):
            bug_issues.append(it)
            excluded["dor"] += 1
            continue
        metric = metric_for(it.source, str(it.kind), with_repos)
        if metric is None:
            excluded["sem_metrica"] += 1
            continue
        value = getattr(it.engagement, metric)
        if value is None or it.created_at is None:
            excluded["sem_metrica"] += 1
            continue
        if not keep_bots and it.source == "github" and automated_title(it.title or ""):
            excluded["bot"] += 1
            continue
        age = (_aware(it.provenance.fetched_at) - _aware(it.created_at)).days / DAYS_PER_MONTH
        if age < min_age:
            excluded["idade"] += 1
            continue
        eligible.append(
            EngItem(
                id=it.id,
                source=it.source,
                kind=str(it.kind),
                value=int(value),
                age=max(age, 1 / DAYS_PER_MONTH),
                systems=frozenset(c for c in it.concepts if c in SYSTEMS),
                year=it.created_at.year,
                query=query,
                w=weights.get(it.id, 1.0),
            )
        )
    strata: dict[tuple[str, str], list[EngItem]] = defaultdict(list)
    for e in eligible:
        strata[(e.source, e.kind)].append(e)
    for members in strata.values():
        plain, weighted = percentiles([m.value for m in members], [m.w for m in members])
        lowest = min(m.value for m in members)
        for m, e_plain, e_weighted in zip(members, plain, weighted, strict=True):
            m.e, m.e_w = float(e_plain), float(e_weighted)
            m.floor = m.value == lowest
    return eligible, excluded, bug_issues


def mean_ci(
    values: list[float], n_boot: int, rng: np.random.Generator, chunk: int = 500
) -> tuple[float, float, float]:
    """Mean with a percentile bootstrap 95% CI."""
    v = np.asarray(values, dtype=np.float64)
    means = []
    for start in range(0, n_boot, chunk):
        idx = rng.integers(0, len(v), size=(min(chunk, n_boot - start), len(v)))
        means.append(v[idx].mean(axis=1))
    lo, hi = np.percentile(np.concatenate(means), [2.5, 97.5])
    return float(v.mean()), float(lo), float(hi)


def holm(pvalues: list[float]) -> list[float]:
    m = len(pvalues)
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(sorted(range(m), key=lambda k: pvalues[k])):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def cliffs_delta(x: list[float], y: list[float]) -> float:
    u = stats.mannwhitneyu(x, y, alternative="two-sided").statistic
    return float(2 * u / (len(x) * len(y)) - 1)


def kruskal_dunn(groups: dict[str, list[float]]) -> dict[str, Any]:
    """Kruskal-Wallis across groups, Dunn post hoc (tie-corrected) with Holm.

    An item that mentions several tools is in several groups, so the test is
    descriptive (exploratory), as the guide states.
    """
    names = sorted(groups)
    if len(names) < 2:
        return {"groups": len(names), "h": math.nan, "p": math.nan, "eps2": math.nan, "pairs": []}
    samples = [np.asarray(groups[k], dtype=np.float64) for k in names]
    try:
        h, p = stats.kruskal(*samples)
    except ValueError:  # all values identical
        return {"groups": len(names), "h": math.nan, "p": math.nan, "eps2": math.nan, "pairs": []}
    pooled = np.concatenate(samples)
    n_total = len(pooled)
    ranks = stats.rankdata(pooled)
    _, ties = np.unique(pooled, return_counts=True)
    tie_term = float(np.sum(ties**3 - ties)) / (12 * (n_total - 1))
    mean_rank: dict[str, float] = {}
    pos = 0
    for name, sample in zip(names, samples, strict=True):
        mean_rank[name] = float(ranks[pos : pos + len(sample)].mean())
        pos += len(sample)
    rows = []
    for a, b in combinations(names, 2):
        na, nb = len(groups[a]), len(groups[b])
        se = math.sqrt((n_total * (n_total + 1) / 12 - tie_term) * (1 / na + 1 / nb))
        z = (mean_rank[a] - mean_rank[b]) / se if se else 0.0
        rows.append(
            {
                "a": a,
                "b": b,
                "z": round(z, 4),
                "p": float(2 * stats.norm.sf(abs(z))),
                "delta": round(cliffs_delta(groups[a], groups[b]), 4),
            }
        )
    for row, adj in zip(rows, holm([r["p"] for r in rows]), strict=True):
        row["p_holm"] = adj
    return {
        "groups": len(names),
        "n": n_total,
        "h": float(h),
        "p": float(p),
        "eps2": float(h / (n_total - 1)),
        "pairs": rows,
    }


def _members(items: list[EngItem], entities: dict[str, set[str]]) -> dict[str, list[EngItem]]:
    groups: dict[str, list[EngItem]] = defaultdict(list)
    for it in items:
        for entry in entities.get(it.id, ()):
            groups[entry].append(it)
    return groups


def score(members: list[EngItem], stat: str, top_share: float = 0.1) -> float:
    """One number for the engagement of a tool's (or method's) items (ADR-059).

    - `mean`: mean of E, the statistic of P2. E is a mid-rank percentile, so its mean
      is the chance that an item of the tool beats another item of its stratum (0.5 =
      a typical item). The Kruskal-Wallis and Dunn tests compare these same mean ranks.
    - `mean_w`: the same with design weights, in the percentile and in the mean.
    - `top`: share of the items in the top `top_share` of their stratum.
    - `median`: the statistic first registered. With most issues at zero reactions it
      sits on the floor for every tool and cannot rank them.
    """
    if stat == "mean":
        return float(np.mean([m.e for m in members]))
    if stat == "mean_w":
        return float(np.average([m.e_w for m in members], weights=[m.w for m in members]))
    if stat == "top":
        return float(np.mean([m.e >= 1.0 - top_share for m in members]))
    if stat == "median":
        return float(np.median([m.e for m in members]))
    raise ValueError(f"unknown engagement statistic: {stat}")


def engagement_by(
    items: list[EngItem],
    entities: dict[str, set[str]],
    min_n: int,
    n_boot: int,
    rng: np.random.Generator,
    top_share: float = 0.1,
) -> list[dict[str, Any]]:
    rows = []
    for entry, members in _members(items, entities).items():
        row: dict[str, Any] = {"entry": entry, "n": len(members)}
        if len(members) >= min_n:
            mean, lo, hi = mean_ci([m.e for m in members], n_boot, rng)
            row.update(
                mean=round(mean, 4),
                lo=round(lo, 4),
                hi=round(hi, 4),
                median=round(score(members, "median"), 4),
                share_floor=round(float(np.mean([m.floor for m in members])), 4),
                share_top=round(score(members, "top", top_share), 4),
                mean_w=round(score(members, "mean_w"), 4),
            )
        rows.append(row)
    return sorted(rows, key=lambda r: (-(r.get("mean") or -1), -r["n"], r["entry"]))


def spearman(xs: list[float], ys: list[float]) -> tuple[float, float]:
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return math.nan, math.nan
    res = stats.spearmanr(xs, ys)
    return float(res.statistic), float(res.pvalue)


def scores_for(
    items: list[EngItem],
    entities: dict[str, set[str]],
    min_n: int,
    stat: str = "mean",
    top_share: float = 0.1,
) -> dict[str, float]:
    return {
        entry: score(members, stat, top_share)
        for entry, members in _members(items, entities).items()
        if len(members) >= min_n
    }


def rho_with_adoption(
    scores: dict[str, float], adoption: dict[str, float]
) -> tuple[float, float, int]:
    tools = sorted(t for t in scores if t in adoption)
    rho, p = spearman([scores[t] for t in tools], [adoption[t] for t in tools])
    return rho, p, len(tools)


def rho_ci(
    items: list[EngItem],
    entities: dict[str, set[str]],
    scores: dict[str, float],
    adoption: dict[str, float],
    n_boot: int,
    rng: np.random.Generator,
    chunk: int = 250,
) -> tuple[float, float]:
    """Percentile bootstrap 95% CI of rho(mean E, adoption), resampling the items.

    Adoption is held fixed: the interval shows how far the engagement side alone can
    move the correlation, so it is narrower than the full uncertainty.
    """
    tools = sorted(t for t in scores if t in adoption)
    if len(tools) < 3 or not items:
        return math.nan, math.nan
    index = {t: j for j, t in enumerate(tools)}
    member = np.zeros((len(items), len(tools)))
    for i, it in enumerate(items):
        for t in entities.get(it.id, ()):
            if t in index:
                member[i, index[t]] = 1.0
    e = np.array([it.e for it in items])
    y = [adoption[t] for t in tools]
    probs = np.full(len(items), 1.0 / len(items))
    rhos: list[float] = []
    for start in range(0, n_boot, chunk):
        draws = rng.multinomial(len(items), probs, size=min(chunk, n_boot - start))
        draws = draws.astype(np.float64)
        counts = draws @ member
        sums = (draws * e) @ member
        for total, count in zip(sums, counts, strict=True):
            if (count == 0).any():
                continue
            rho, _ = spearman(list(total / count), y)
            if not math.isnan(rho):
                rhos.append(rho)
    if not rhos:
        return math.nan, math.nan
    lo, hi = np.percentile(rhos, [2.5, 97.5])
    return float(lo), float(hi)


def nb_robustness(
    items: list[EngItem],
    tools_of: dict[str, set[str]],
    keep: list[str],
    scores: dict[str, float],
) -> dict[str, Any]:
    """Negative binomial on raw counts: tool and source x kind fixed effects, offset log(age)."""
    try:
        from statsmodels.discrete.discrete_model import NegativeBinomial
    except ImportError:  # pragma: no cover - optional dependency
        return {"status": "statsmodels not installed"}
    rows = [(it, t) for it in items for t in sorted(tools_of.get(it.id, ())) if t in keep]
    if len(rows) < 30 or len(keep) < 3:
        return {"status": "too few rows", "rows": len(rows)}
    counts = Counter(t for _, t in rows)
    reference = max(sorted(keep), key=lambda t: counts[t])
    others = [t for t in sorted(keep) if t != reference]
    strata = sorted({(it.source, it.kind) for it, _ in rows})
    columns = ["const", *others, *[f"{s}/{k}" for s, k in strata[1:]]]
    x = np.zeros((len(rows), len(columns)))
    x[:, 0] = 1.0
    for i, (it, tool) in enumerate(rows):
        if tool != reference:
            x[i, 1 + others.index(tool)] = 1.0
        stratum = (it.source, it.kind)
        if stratum != strata[0]:
            x[i, 1 + len(others) + strata.index(stratum) - 1] = 1.0
    y = np.array([it.value for it, _ in rows], dtype=np.float64)
    offset = np.log(np.array([it.age for it, _ in rows]))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = NegativeBinomial(y, x, offset=offset).fit(disp=0, maxiter=300)
    except Exception as e:  # noqa: BLE001 - report, never fail the analysis
        return {"status": f"failed: {type(e).__name__}"}
    effects = {reference: 0.0, **{t: float(fit.params[1 + i]) for i, t in enumerate(others)}}
    tools = sorted(t for t in effects if t in scores)
    rho, p = spearman([effects[t] for t in tools], [scores[t] for t in tools])
    return {
        "status": "ok",
        "reference": reference,
        "rows": len(rows),
        "irr": {t: round(math.exp(v), 4) for t, v in effects.items()},
        "rho_with_mean": rho,
        "p": p,
        "converged": bool(fit.mle_retvals.get("converged")),
    }


def proposition_p3(items: list[EngItem], min_n: int) -> dict[str, Any]:
    chosen = [
        it
        for it in items
        if 2025 <= it.year <= 2026 and it.systems in (frozenset({"rag"}), frozenset({"agente"}))
    ]
    agent = [it.e for it in chosen if it.systems == frozenset({"agente"})]
    rag = [it.e for it in chosen if it.systems == frozenset({"rag"})]
    out: dict[str, Any] = {"n_agente": len(agent), "n_rag": len(rag)}
    if len(agent) < min_n or len(rag) < min_n:
        out["status"] = "too few items"
        return out
    out["delta"] = cliffs_delta(agent, rag)
    out["mw_p"] = float(stats.mannwhitneyu(agent, rag, alternative="two-sided").pvalue)
    try:
        import statsmodels.api as sm
    except ImportError:  # pragma: no cover - optional dependency
        out["status"] = "ok (no OLS: statsmodels not installed)"
        return out
    # Only strata where both systems occur identify the within-stratum difference.
    by_stratum: dict[tuple[str, str], set[frozenset[str]]] = defaultdict(set)
    for it in chosen:
        by_stratum[(it.source, it.kind)].add(it.systems)
    strata = sorted(k for k, v in by_stratum.items() if len(v) == 2)
    sample = [it for it in chosen if (it.source, it.kind) in strata]
    if not strata or len(sample) < 2 * min_n:
        out["status"] = "ok (no OLS: no stratum with both systems)"
        return out
    ages = [math.log(it.age) for it in sample]
    use_age = len(set(ages)) > 1
    x = np.zeros((len(sample), 2 + use_age + len(strata) - 1))
    for i, it in enumerate(sample):
        x[i, 0] = 1.0
        x[i, 1] = float(it.systems == frozenset({"agente"}))
        if use_age:
            x[i, 2] = ages[i]
        stratum = strata.index((it.source, it.kind))
        if stratum:
            x[i, 1 + use_age + stratum] = 1.0
    if np.linalg.matrix_rank(x) < x.shape[1]:
        out["status"] = "ok (no OLS: rank-deficient design)"
        return out
    fit = sm.OLS(np.array([it.e for it in sample]), x).fit(cov_type="HC3")
    lo, hi = fit.conf_int()[1]
    out.update(
        status="ok",
        beta=float(fit.params[1]),
        lo=float(lo),
        hi=float(hi),
        p=float(fit.pvalues[1]),
        ols_items=len(sample),
        age_covariate=use_age,
    )
    return out


def pain(bug_issues: list[Item], cat: Catalog) -> list[dict[str, Any]]:
    owner = {repo: tool for tool, repos in cat.self_repos.items() for repo in repos}
    grouped: dict[str, list[Item]] = defaultdict(list)
    for it in bug_issues:
        match = BUG_QUERY.search(it.provenance.query_string or "")
        tool = owner.get(match.group(1).lower()) if match else None
        if tool:
            grouped[tool].append(it)
    rows = []
    for tool, issues in grouped.items():
        comments = [it.engagement.comments or 0 for it in issues]
        reactions = [it.engagement.reactions or 0 for it in issues]
        rows.append(
            {
                "tool": tool,
                "family": cat.family.get(tool, ""),
                "issues": len(issues),
                "median_comments": float(np.median(comments)),
                "median_reactions": float(np.median(reactions)),
                "pct_with_comment": round(100 * sum(c > 0 for c in comments) / len(issues), 1),
            }
        )
    return sorted(rows, key=lambda r: (-r["issues"], r["tool"]))


def non_truncated_queries(manifest: dict[str, Any]) -> set[tuple[str, str]] | None:
    sources = manifest.get("sources")
    if not sources:
        return None
    return {
        (s.get("name", ""), q.get("query_string", ""))
        for s in sources
        for q in s.get("queries", [])
        if not q.get("truncated")
    }


# -- run ------------------------------------------------------------------------


def human_corpus(pkg: Package, keep_bots: bool) -> tuple[list[Item], list[Detection], int]:
    """Items and detections without the automated GitHub issues, and how many those are.

    A dependency alert or a daily list of papers is not somebody discussing testing: it
    is left out of every analysis, units included (`--keep-bots` keeps it).
    """
    if keep_bots:
        return pkg.items, pkg.detections, 0
    automated = {
        it.id
        for it in pkg.items
        if it.source == "github" and str(it.kind) == "issue" and automated_title(it.title or "")
    }
    return (
        [it for it in pkg.items if it.id not in automated],
        [d for d in pkg.detections if d.item_id not in automated],
        len(automated),
    )


def design_weights(pkg: Package) -> tuple[dict[str, ItemWeight], dict[str, Any]]:
    """Design weight of each item, from the run manifest (ADR-058), and what is known.

    `available` is False when the manifest cannot give sampling fractions: a package
    from before the per-query totals, or one whose truncated queries have no total.
    The weighted analyses are then left out instead of run with weights of 1.
    """
    info: dict[str, Any] = {
        "available": False,
        "max": 1.0,
        "n_eff": float(len(pkg.items)),
        "unknown_items": 0,
        "strata": 0,
        "strata_cut_short": 0,
    }
    try:
        manifest = Manifest.model_validate(pkg.manifest)
    except ValidationError:
        return {}, info
    rows = [s for s in strata(manifest) if s.key]
    weights = item_weights(manifest, pkg.items)
    cut = [s for s in rows if s.cut_short]
    values = [w.weight for w in weights.values()]
    info.update(
        available=bool(rows) and (not cut or any(s.known for s in cut)),
        max=max(values, default=1.0),
        n_eff=kish(values),
        unknown_items=sum(not w.known for w in weights.values()),
        strata=len(rows),
        strata_cut_short=len(cut),
    )
    return weights, info


def sustained_repos(repos: list[dict[str, Any]]) -> set[str]:
    """Repositories that meet the N3 thresholds, from the package's repository signals."""
    return {
        str(r.get("repo", "")).lower()
        for r in repos
        if not r.get("error")
        and len(r.get("commit_months") or []) >= N3_MIN_MONTHS
        and (r.get("contributors") or 0) >= N3_MIN_CONTRIBUTORS
    }


def name_as_adoption(detections: list[Detection], sustained: set[str]) -> list[Detection]:
    """The detections under a looser N3 rule, for the sensitivity analysis.

    The extractor keeps at N2 a CI workflow that only carries the tool's name in a
    command (a path, an argument). Here that evidence counts as N3 when the repository
    meets the thresholds, as every CI hit did before ADR-056.
    """
    return [
        d.model_copy(update={"level": "N3"})
        if d.signal == "ci" and d.via == "name" and (d.repo or "").lower() in sustained
        else d
        for d in detections
    ]


def verdict(value: float, threshold: float, label: str, absolute: bool = False) -> str:
    if math.isnan(value):
        return "n/d"
    below = (abs(value) if absolute else value) < threshold
    return f"sustenta {label}" if below else f"não sustenta {label}"


def conclusion(sample: str, weighted: str) -> str:
    """A proposition holds only when the sample and the weighted readings agree."""
    if weighted == "n/d":
        return sample
    if sample == "n/d":
        return weighted
    return sample if sample == weighted else "inconclusivo (depende da ponderação)"


def units_at_n3(units: list[Unit], cat: Catalog) -> int:
    return sum(
        any(u.tools[t] == "N3" for t in external_tools(u, cat, cat.test_tools)) for u in units
    )


def analyse(pkg: Package, out: Path, args: argparse.Namespace) -> dict[str, Any]:
    rng = np.random.default_rng(args.seed)
    cat = catalog(pkg.gazetteer)
    items, detections, n_automated = human_corpus(pkg, args.keep_bots)
    units = units_from_detections(items, detections)
    item_w, w_info = design_weights(pkg)
    pair_w = pair_weights(items, detections, item_w)
    m = Macros()
    for name, value in (
        ("ResMinIdade", fmt_int(args.min_age_months)),
        ("ResMinN", fmt_int(args.min_n)),
        ("ResBoot", fmt_int(args.bootstrap)),
        ("ResListicle", fmt_int(args.listicle_k)),
        ("ResLimiarTau", fmt_float(args.tau_threshold, 1)),
        ("ResLimiarRho", fmt_float(args.rho_threshold, 1)),
        ("ResMinPar", fmt_int(args.min_together)),
        ("ResTopoPct", fmt_int(100 * args.top_share)),
    ):
        m.set(name, value)

    # corpus
    by_source = Counter(it.source for it in pkg.items)
    by_kind = Counter(f"{it.source}/{it.kind}" for it in pkg.items)
    dates = sorted(it.created_at for it in pkg.items if it.created_at)
    tool_dets = [d for d in pkg.detections if d.entry_type == "tool"]
    run_id = pkg.manifest.get("run_id") or pkg.package_manifest.get("run_id") or "n/d"
    sha = pkg.manifest.get("protocol_sha256") or pkg.package_manifest.get("protocol_sha256") or ""
    pql = (pkg.manifest.get("run_options") or {}).get("per_query_limit")
    m.set("ResRunId", latex_escape(str(run_id)))
    m.set("ResProtocoloSha", f"\\texttt{{{sha[:12]}}}" if sha else "n/d")
    m.set("ResPerQueryLimit", fmt_int(pql) if pql else "sem teto")
    m.set("ResItens", fmt_int(len(pkg.items)))
    m.set("ResFontes", fmt_int(len(by_source)))
    m.set("ResPctGithub", fmt_pct(by_source.get("github", 0), len(pkg.items)))
    m.set(
        "ResItensPt",
        fmt_int(sum(any(h.lang == "pt" for h in it.matched_terms) for it in pkg.items)),
    )
    m.set("ResDataMin", dates[0].strftime("%m/%Y") if dates else "n/d")
    m.set("ResDataMax", dates[-1].strftime("%m/%Y") if dates else "n/d")
    m.set("ResItensAutomaticos", fmt_int(n_automated))
    m.set("ResUnidades", fmt_int(len(units)))
    m.set("ResUnidadesGithub", fmt_int(sum(u.source == "github" for u in units)))
    m.set("ResDeteccoes", fmt_int(len(pkg.detections)))
    m.set("ResDeteccoesFerramenta", fmt_int(len(tool_dets)))
    m.set("ResDeteccoesMetodo", fmt_int(len(pkg.detections) - len(tool_dets)))
    m.set("ResReposEnriquecidos", fmt_int(len(pkg.repos)))
    m.set("ResReposErro", fmt_int(sum(bool(r.get("error")) for r in pkg.repos)))
    flow = pkg.prisma
    if flow:
        m.set("ResPrismaIdentificados", fmt_int(sum((flow.get("identified") or {}).values())))
        m.set("ResPrismaFiltrados", fmt_int(sum((flow.get("filtered") or {}).values())))
        m.set("ResPrismaDuplicatas", fmt_int(sum((flow.get("duplicates") or {}).values())))
        m.set("ResPrismaNaoExaminados", fmt_int(sum((flow.get("sampled_out") or {}).values())))
        for key, macro in (
            ("screened", "ResPrismaTriagem"),
            ("included", "ResPrismaIncluidos"),
            ("pending", "ResPrismaPendentes"),
            ("coded", "ResPrismaCodificados"),
        ):
            m.set(macro, fmt_int(flow.get(key) or 0))
    weighted = bool(w_info["available"])
    m.set(
        "ResPesos",
        "ponderado pelas frações de amostragem" if weighted else "sem pesos",
        "" if weighted else "manifest without per-query totals",
    )
    m.set("ResPesoMax", fmt_float(w_info["max"], 1))
    m.set("ResNEfetivo", fmt_int(w_info["n_eff"]))
    m.set("ResItensPesoDesconhecido", fmt_int(w_info["unknown_items"]))

    # QP1
    rows = pyramid(units, cat, pair_w)
    present = [r for r in rows if r["total"] > 0]
    m.set("ResFerramentasTeste", fmt_int(len(cat.test_tools)))
    m.set("ResFerramentasDetectadas", fmt_int(len(present)))
    with_tool = [u for u in units if external_tools(u, cat, cat.test_tools)]
    m.set("ResUnidadesComFerramenta", fmt_int(len(with_tool)))
    m.set("ResUnidadesComNTres", fmt_int(units_at_n3(units, cat)))
    m.set("ResSelfPares", fmt_int(sum(r["self"] for r in rows)))
    for key, macro in (("n3_ext", "ResTopNTres"), ("n2", "ResTopNDois"), ("n1", "ResTopNUm")):
        best = max(rows, key=lambda r, k=key: (r[k], -r["rank_n3_ext"]))
        m.set(macro, cat.display(best["tool"]) if best[key] else "nenhuma")
        m.set(macro + "N", fmt_int(best[key]))

    matrix = level_matrix(units, cat.test_tools, cat)
    taus = {}
    for name, (a, b) in {"UmTres": (1, 3), "DoisTres": (2, 3), "UmDois": (1, 2)}.items():
        taus[name] = kendall_ci(matrix, a, b, args.bootstrap, rng)
    m.set("ResTauUmTres", fmt_float(taus["UmTres"]["tau"]))
    m.set("ResTauUmTresLo", fmt_float(taus["UmTres"]["lo"]))
    m.set("ResTauUmTresHi", fmt_float(taus["UmTres"]["hi"]))
    m.set("ResTauUmTresP", fmt_p(taus["UmTres"]["p"]))
    m.set("ResTauDoisTres", fmt_float(taus["DoisTres"]["tau"]))
    m.set("ResTauDoisTresLo", fmt_float(taus["DoisTres"]["lo"]))
    m.set("ResTauDoisTresHi", fmt_float(taus["DoisTres"]["hi"]))
    m.set("ResTauUmDois", fmt_float(taus["UmDois"]["tau"]))
    m.set("ResTauFerramentas", fmt_int(taus["UmTres"]["tools"]))
    p1 = verdict(taus["UmTres"]["tau"], args.tau_threshold, "P1")
    m.set("ResPUmVeredito", p1)
    # The same correlation on design-weighted counts: what the sources hold, not what
    # a 100-item cap left in the sample. P1 is read as supported when both agree.
    tau_w = None
    if weighted:
        wmat = weight_matrix(units, cat.test_tools, pair_w)
        tau_w = kendall_ci(matrix, 1, 3, args.bootstrap, rng, weights=wmat)
        m.set("ResTauUmTresPond", fmt_float(tau_w["tau"]))
        m.set("ResTauUmTresPondLo", fmt_float(tau_w["lo"]))
        m.set("ResTauUmTresPondHi", fmt_float(tau_w["hi"]))
    p1_w = verdict(tau_w["tau"], args.tau_threshold, "P1") if tau_w else "n/d"
    m.set("ResPUmVereditoPond", p1_w)
    m.set("ResPUmConclusao", conclusion(p1, p1_w))
    # Sensitivity to the N3 rule: the bare name of a tool in a CI command as adoption.
    loose_units = units_from_detections(
        items, name_as_adoption(detections, sustained_repos(pkg.repos))
    )
    tau_loose = kendall_ci(
        level_matrix(loose_units, cat.test_tools, cat), 1, 3, args.bootstrap, rng
    )
    m.set("ResTauUmTresNome", fmt_float(tau_loose["tau"]))
    m.set("ResUnidadesComNTresNome", fmt_int(units_at_n3(loose_units, cat)))
    talked, adopted = gap(rows)
    write_csv(rows, out / "qp1_piramide.csv")
    m.set("ResMaisFaladas", join_pt([cat.display(t) for t in talked]))
    m.set("ResMaisAdotadas", join_pt([cat.display(t) for t in adopted]))

    fw_units = Counter(t for u in units for t in external_tools(u, cat, cat.frameworks))
    fw_rank = fw_units.most_common()
    if fw_rank:
        m.set("ResFwTop", cat.display(fw_rank[0][0]))
        m.set("ResFwTopN", fmt_int(fw_rank[0][1]))
    if len(fw_rank) > 1:
        m.set("ResFwSegundo", cat.display(fw_rank[1][0]))
        m.set("ResFwSegundoN", fmt_int(fw_rank[1][1]))
    m.set(
        "ResPctUnidadesComFw",
        fmt_pct(
            sum(bool(external_tools(u, cat, cat.frameworks)) for u in with_tool), len(with_tool)
        ),
    )
    m.set("ResJaccardDeteccoes", fmt_float(jaccard_systems(units, cat)))

    def test_ext(u: Unit) -> set[str]:
        return external_tools(u, cat, cat.test_tools)

    pair_rows = {
        "ferramenta-ferramenta": pairs(units, test_ext, None, args.min_together),
        "ferramenta-metodo": pairs(units, test_ext, lambda u: set(u.methods), args.min_together),
        "ferramenta-framework": pairs(
            units, test_ext, lambda u: external_tools(u, cat, cat.frameworks), args.min_together
        ),
    }
    write_csv(
        [{"tipo": k, **r} for k, rs in pair_rows.items() for r in rs], out / "qp1_coocorrencia.csv"
    )

    # QP2
    method_rows = methods_table(units, pkg.gazetteer)
    write_csv(method_rows, out / "qp2_metodos.csv")
    if method_rows and method_rows[0]["units"]:
        m.set("ResTopMetodo", cat.display(method_rows[0]["method"]))
        m.set("ResTopMetodoN", fmt_int(method_rows[0]["units"]))
    oracle_rows = [r.model_dump() for r in oracle_distribution(units, cat.method_rungs)]
    write_csv(oracle_rows, out / "qp2_oraculos.csv")
    with_rung = sum(
        bool({cat.method_rungs[x] for x in u.methods if x in cat.method_rungs}) for u in units
    )
    m.set("ResUnidadesComOraculo", fmt_int(with_rung))
    share = {r["rung"]: r["share"] for r in oracle_rows if r["system"] == "all"}
    for rung, macro in (
        ("pseudo-automatico", "ResPctPseudo"),
        ("referencia", "ResPctReferencia"),
        ("especificado", "ResPctEspecificado"),
        ("humano", "ResPctHumano"),
        ("derivado", "ResPctDerivado"),
    ):
        m.set(macro, fmt_int(100 * share.get(rung, 0.0)))

    # QP3
    eng, excluded, bug_issues = engagement_items(
        pkg,
        args.min_age_months,
        args.with_repos,
        args.keep_bots,
        {item_id: w.weight for item_id, w in item_w.items()},
    )
    m.set("ResEngItens", fmt_int(len(eng)))
    m.set("ResEngExclIdade", fmt_int(excluded["idade"]))
    m.set("ResEngExclBot", fmt_int(excluded["bot"]))
    m.set("ResEngExclDor", fmt_int(excluded["dor"]))
    m.set("ResEngExclSemMetrica", fmt_int(excluded["sem_metrica"]))
    m.set("ResEngEstatistica", "média")
    m.set("ResEngPctPiso", fmt_pct(sum(it.floor for it in eng), len(eng)))
    test_set, method_set = set(cat.test_tools), set(cat.method_names)
    tools_of: dict[str, set[str]] = defaultdict(set)
    methods_of: dict[str, set[str]] = defaultdict(set)
    for d in detections:
        if d.entry_type == "tool" and d.entry_id in test_set:
            tools_of[d.item_id].add(d.entry_id)
        elif d.entry_type == "method" and d.entry_id in method_set:
            methods_of[d.item_id].add(d.entry_id)
    tool_eng = engagement_by(eng, tools_of, args.min_n, args.bootstrap, rng, args.top_share)
    method_eng = engagement_by(eng, methods_of, args.min_n, args.bootstrap, rng, args.top_share)
    write_csv(tool_eng, out / "qp3_engajamento_ferramentas.csv")
    write_csv(method_eng, out / "qp3_engajamento_metodos.csv")
    tool_ok = [r for r in tool_eng if "mean" in r]
    method_ok = [r for r in method_eng if "mean" in r]
    m.set("ResEngFerramentas", fmt_int(len(tool_ok)))
    m.set("ResEngMetodos", fmt_int(len(method_ok)))
    if tool_ok:
        m.set("ResEngTopFerramenta", cat.display(tool_ok[0]["entry"]))
        m.set("ResEngTopFerramentaMed", fmt_float(tool_ok[0]["mean"]))
    if method_ok:
        m.set("ResEngTopMetodo", cat.display(method_ok[0]["entry"]))
        m.set("ResEngTopMetodoMed", fmt_float(method_ok[0]["mean"]))
    keep = [r["entry"] for r in tool_ok]
    groups: dict[str, list[float]] = defaultdict(list)
    for it in eng:
        for t in tools_of.get(it.id, ()):
            if t in keep:
                groups[t].append(it.e)
    kw = kruskal_dunn(dict(groups))
    write_csv(kw.get("pairs", []), out / "qp3_dunn.csv")
    m.set("ResKwH", fmt_float(kw["h"]))
    m.set("ResKwP", fmt_p(kw["p"]))
    m.set("ResKwEps", fmt_float(kw["eps2"], 3))
    m.set("ResDunnSig", fmt_int(sum(r["p_holm"] < 0.05 for r in kw.get("pairs", []))))
    m.set("ResDunnPares", fmt_int(len(kw.get("pairs", []))))

    # P2: the mean of E per tool against external N3 (ADR-059). The median, first
    # registered, is the same for every tool when most issues have no reaction.
    adoption = {r["tool"]: float(r["n3_ext"]) for r in rows}
    means = scores_for(eng, tools_of, args.min_n, "mean")
    rho, rho_p, rho_n = rho_with_adoption(means, adoption)
    rho_lo, rho_hi = rho_ci(eng, tools_of, means, adoption, args.bootstrap, rng)
    m.set("ResRho", fmt_float(rho))
    m.set("ResRhoLo", fmt_float(rho_lo))
    m.set("ResRhoHi", fmt_float(rho_hi))
    m.set("ResRhoP", fmt_p(rho_p))
    m.set("ResRhoN", fmt_int(rho_n))
    p2 = verdict(rho, args.rho_threshold, "P2", absolute=True)
    m.set("ResPDoisVeredito", p2)
    rho_w = math.nan
    if weighted:
        adoption_w = {r["tool"]: float(r["n3_ext_w"]) for r in rows}
        rho_w, _, _ = rho_with_adoption(scores_for(eng, tools_of, args.min_n, "mean_w"), adoption_w)
        m.set("ResRhoPond", fmt_float(rho_w))
    p2_w = verdict(rho_w, args.rho_threshold, "P2", absolute=True)
    m.set("ResPDoisVereditoPond", p2_w)
    m.set("ResPDoisConclusao", conclusion(p2, p2_w))
    rho_median, _, _ = rho_with_adoption(scores_for(eng, tools_of, args.min_n, "median"), adoption)
    m.set("ResRhoMediana", fmt_float(rho_median))
    rho_top, _, _ = rho_with_adoption(
        scores_for(eng, tools_of, args.min_n, "top", args.top_share), adoption
    )
    m.set("ResRhoTopo", fmt_float(rho_top))
    no_listicle = [it for it in eng if len(tools_of.get(it.id, ())) < args.listicle_k]
    rho_nl, _, _ = rho_with_adoption(scores_for(no_listicle, tools_of, args.min_n), adoption)
    m.set("ResRhoSemListicle", fmt_float(rho_nl))
    allowed = non_truncated_queries(pkg.manifest)
    if allowed is None:
        m.set("ResRhoNaoTruncadas", "n/d", "manifest without queries")
        m.set("ResItensNaoTruncadas", "n/d")
        n_trunc = None
    else:
        subset = [it for it in eng if (it.source, it.query) in allowed]
        n_trunc = len(subset)
        rho_nt, _, _ = rho_with_adoption(scores_for(subset, tools_of, args.min_n), adoption)
        m.set("ResRhoNaoTruncadas", fmt_float(rho_nt))
        m.set("ResItensNaoTruncadas", fmt_int(n_trunc))
    nb = nb_robustness(eng, tools_of, keep, means)
    m.set(
        "ResNbRho",
        fmt_float(nb.get("rho_with_mean")) if nb.get("status") == "ok" else "n/d",
        nb.get("status", ""),
    )
    if means:
        mid_e = float(np.median(list(means.values())))
        mid_a = float(np.median([adoption[t] for t in means]))
        quad = [
            {
                "tool": t,
                "mean_e": round(means[t], 4),
                "n3_ext": int(adoption[t]),
                "quadrant": ("alto E" if means[t] > mid_e else "baixo E")
                + " / "
                + ("alta adoção" if adoption[t] > mid_a else "baixa adoção"),
            }
            for t in sorted(means)
        ]
        write_csv(quad, out / "qp3_quadrantes.csv")
        talked_q = sorted(
            (q for q in quad if q["quadrant"] == "alto E / baixa adoção"),
            key=lambda q: -q["mean_e"],
        )[:3]
        adopted_q = sorted(
            (q for q in quad if q["quadrant"] == "baixo E / alta adoção"),
            key=lambda q: -q["n3_ext"],
        )[:3]
        m.set("ResQuadFalado", join_pt([cat.display(q["tool"]) for q in talked_q]))
        m.set("ResQuadAdotado", join_pt([cat.display(q["tool"]) for q in adopted_q]))
    p3 = proposition_p3(eng, args.min_n)
    m.set("ResPTresN", fmt_int(p3["n_agente"] + p3["n_rag"]))
    if "delta" in p3:
        m.set("ResPTresDelta", fmt_float(p3["delta"]))
    if "beta" in p3:
        m.set("ResPTresBeta", fmt_float(p3["beta"], 3))
        m.set("ResPTresLo", fmt_float(p3["lo"], 3))
        m.set("ResPTresHi", fmt_float(p3["hi"], 3))
        m.set("ResPTresP", fmt_p(p3["p"]))
        p3_verdict = "sustenta P3" if p3["lo"] > 0 else "não sustenta P3"
    else:
        p3_verdict = "n/d"
    m.set("ResPTresVeredito", p3_verdict)
    pain_rows = pain(bug_issues, cat)
    write_csv(pain_rows, out / "qp3_dor.csv")
    m.set("ResDorIssues", fmt_int(len(bug_issues)))
    m.set("ResDorFerramentas", fmt_int(len(pain_rows)))

    # LaTeX tables and figure
    write_tables(
        out, cat, rows, pair_rows, method_rows, oracle_rows, tool_eng, method_eng, adoption, args
    )
    write_scatter(out / "fig_qp1_dispersao.tex", rows, cat)
    header = (
        "Gerado por analysis/artigo2.py — não edite à mão.\n"
        f"Pacote: {Path(pkg.origin).name} · run_id: {run_id} · base: detecções automáticas\n"
        f"Gerado em {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · parâmetros: {vars_text(args)}"
    )
    m.write(out / "macros.tex", header)
    summary = {
        "package": Path(pkg.origin).name,
        "run_id": run_id,
        "protocol_sha256": sha,
        "msrkit_version": pkg.package_manifest.get("msrkit_version"),
        "git_commit": pkg.package_manifest.get("git_commit"),
        "basis": "detections",
        "parameters": vars_text(args, as_dict=True),
        "skipped_records": dict(pkg.skipped),
        "items_by_source": dict(by_source),
        "items_by_source_kind": dict(by_kind),
        "units": len(units),
        "automated_items": n_automated,
        "weights": w_info,
        "kendall": taus,
        "kendall_weighted": tau_w,
        "kendall_name_as_n3": tau_loose,
        "p1": {"sample": p1, "weighted": p1_w, "conclusion": conclusion(p1, p1_w)},
        "kruskal": {k: v for k, v in kw.items() if k != "pairs"},
        "spearman": {
            "statistic": "mean of E per tool",
            "rho": rho,
            "ci95": [rho_lo, rho_hi],
            "p": rho_p,
            "tools": rho_n,
            "rho_weighted": rho_w,
            "rho_median": rho_median,
            "rho_top_share": rho_top,
            "rho_without_listicles": rho_nl,
            "non_truncated_items": n_trunc,
        },
        "p2": {"sample": p2, "weighted": p2_w, "conclusion": conclusion(p2, p2_w)},
        "negative_binomial": nb,
        "p3": p3,
        "engagement_excluded": dict(excluded),
        "macros": m.values,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return summary


def vars_text(args: argparse.Namespace, as_dict: bool = False) -> Any:
    keys = (
        "min_age_months",
        "min_n",
        "bootstrap",
        "listicle_k",
        "tau_threshold",
        "rho_threshold",
        "min_together",
        "top_share",
        "with_repos",
        "keep_bots",
        "seed",
    )
    values = {k: getattr(args, k) for k in keys}
    return values if as_dict else ", ".join(f"{k}={v}" for k, v in values.items())


def write_tables(
    out: Path,
    cat: Catalog,
    rows: list[dict[str, Any]],
    pair_rows: dict[str, list[dict[str, Any]]],
    method_rows: list[dict[str, Any]],
    oracle_rows: list[dict[str, Any]],
    tool_eng: list[dict[str, Any]],
    method_eng: list[dict[str, Any]],
    adoption: dict[str, float],
    args: argparse.Namespace,
) -> None:
    present = [r for r in rows if r["total"] > 0][: args.top]
    # Design-weighted estimates, most adopted first, with the rank in the sample.
    weighted = sorted(
        (r for r in rows if r["total"] > 0),
        key=lambda r: (-r["n3_ext_w"], -r["n2_w"], -r["n1_w"], r["tool"]),
    )[: args.top]
    write_table(
        out / "tab_qp1_pond.tex",
        [
            f"{cat.display(r['tool'])} & {fmt_float(r['n3_ext_w'], 1)} & "
            f"{fmt_float(r['n2_w'], 1)} & {fmt_float(r['n1_w'], 1)} & "
            f"{fmt_int(r['rank_n3_ext'])} \\\\"
            for r in weighted
        ],
        TABLE_COLUMNS["tab_qp1_pond"],
    )
    write_table(
        out / "tab_qp1.tex",
        [
            f"{cat.display(r['tool'])} & {FAMILY_LABEL.get(r['family'], r['family'])} & "
            f"{fmt_int(r['n3_ext'])} & {fmt_int(r['n2'])} & {fmt_int(r['n1'])} & "
            f"{fmt_int(r['self'])} & {fmt_int(r['rag'])} & {fmt_int(r['agente'])} \\\\"
            for r in present
        ],
        TABLE_COLUMNS["tab_qp1"],
    )
    label = {
        "ferramenta-ferramenta": "F\\,$\\times$\\,F",
        "ferramenta-metodo": "F\\,$\\times$\\,M",
        "ferramenta-framework": "F\\,$\\times$\\,SUT",
    }
    pair_lines = []
    for kind, items in pair_rows.items():
        for r in items[:4]:
            pair_lines.append(
                f"{label[kind]} & {cat.display(r['a'])} + {cat.display(r['b'])} & "
                f"{fmt_int(r['together'])} & {fmt_float(r['jaccard'])} & "
                f"{fmt_float(r['lift'], 1)} \\\\"
            )
    write_table(out / "tab_qp1_pares.tex", pair_lines, TABLE_COLUMNS["tab_qp1_pares"])
    write_table(
        out / "tab_qp2.tex",
        [
            f"{cat.display(r['method'])} & {ORACLE_SHORT.get(r['oracle'], '---')} & "
            f"{fmt_int(r['units'])} & {fmt_int(r['rag'])} & {fmt_int(r['agente'])} \\\\"
            for r in method_rows
            if r["units"]
        ],
        TABLE_COLUMNS["tab_qp2"],
    )
    oracle_lines = []
    by = {(r["system"], r["rung"]): r for r in oracle_rows}
    for rung in LADDER:
        cells = []
        for system in ("all", "rag", "agente"):
            r = by.get((system, rung))
            cells.append(f"{fmt_int(r['units'])} ({fmt_int(100 * r['share'])}\\%)" if r else "---")
        oracle_lines.append(f"{ORACLE_LABEL[rung]} & {' & '.join(cells)} \\\\")
    write_table(out / "tab_qp2_oraculos.tex", oracle_lines, TABLE_COLUMNS["tab_qp2_oraculos"])
    write_table(
        out / "tab_qp3.tex",
        [
            f"{cat.display(r['entry'])} & {fmt_int(r['n'])} & {fmt_float(r['mean'])} "
            f"[{fmt_float(r['lo'])}; {fmt_float(r['hi'])}] & "
            f"{fmt_int(adoption.get(r['entry'], 0))} \\\\"
            for r in tool_eng
            if "mean" in r
        ][: args.top],
        TABLE_COLUMNS["tab_qp3"],
    )
    write_table(
        out / "tab_qp3_metodos.tex",
        [
            f"{cat.display(r['entry'])} & {fmt_int(r['n'])} & {fmt_float(r['mean'])} "
            f"[{fmt_float(r['lo'])}; {fmt_float(r['hi'])}] & "
            f"{ORACLE_LABEL.get(cat.method_rungs.get(r['entry'], ''), '---')} \\\\"
            for r in method_eng
            if "mean" in r
        ],
        TABLE_COLUMNS["tab_qp3_metodos"],
    )


def write_scatter(
    path: Path, rows: list[dict[str, Any]], cat: Catalog | None, labels: int = 8
) -> None:
    """Mention (N1) x external adoption (N3) per tool, log scale with a +1 offset."""
    present = [r for r in rows if r["total"] > 0]
    if not present or cat is None:
        path.write_text(
            "\\fbox{\\parbox[c][3.2cm][c]{0.9\\columnwidth}{\\centering"
            "\\pend{figura gerada por analysis/artigo2.py}}}\n",
            encoding="utf-8",
        )
        return
    top = max(max(r["n1"], r["n3_ext"]) for r in present) + 1
    ticks = [1] + [10**k + 1 for k in range(0, int(math.log10(top)) + 1)]
    labels_at = ",".join(str(t) for t in ticks)
    tick_text = ",".join(["0"] + [fmt_int(10**k) for k in range(0, int(math.log10(top)) + 1)])
    chosen = sorted(present, key=lambda r: -max(r["n1"], r["n3_ext"]))[: labels // 2]
    chosen += sorted(
        (r for r in present if r not in chosen), key=lambda r: -abs(r.get("log_ratio", 0.0))
    )[: labels - len(chosen)]
    coords = " ".join(f"({r['n1'] + 1},{r['n3_ext'] + 1})" for r in present)
    nodes = []
    for r in chosen:
        anchor = "south west" if r["n3_ext"] >= r["n1"] else "north west"
        nodes.append(
            f"    \\node[anchor={anchor}, font=\\tiny, inner sep=1.5pt] "
            f"at (axis cs:{r['n1'] + 1},{r['n3_ext'] + 1}) {{{cat.display(r['tool'])}}};"
        )
    upper = max(top, ticks[-1]) * 1.6
    path.write_text(
        "\n".join(
            [
                "% Gerado por analysis/artigo2.py: menções (N1) x adoção externa (N3)",
                "\\begin{tikzpicture}",
                "  \\begin{axis}[width=\\columnwidth, height=0.78\\columnwidth,",
                "      xmode=log, ymode=log,",
                f"      xmin=0.8, ymin=0.8, xmax={upper:.0f}, ymax={upper:.0f},",
                f"      xtick={{{labels_at}}}, xticklabels={{{tick_text}}},",
                f"      ytick={{{labels_at}}}, yticklabels={{{tick_text}}},",
                "      xlabel={Menções (unidades N1)}, ylabel={Adoção externa (unidades N3)},",
                "      label style={font=\\footnotesize}, tick label style={font=\\scriptsize},",
                "      grid=major, major grid style={gray!20}, axis line style={gray!60}]",
                f"    \\addplot[gray!50, dashed, domain=0.8:{upper:.0f}, samples=2] {{x}};",
                "    \\addplot[only marks, mark=*, mark size=1.6pt, black!75]",
                f"      coordinates {{{coords}}};",
                *nodes,
                "  \\end{axis}",
                "\\end{tikzpicture}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_pending(out: Path) -> None:
    m = Macros()
    m.write(
        out / "macros.tex",
        "Placeholders (--pending): rode analysis/artigo2.py no pacote.",
        pending=True,
    )
    for table in TABLES:
        write_table(out / f"{table}.tex", [], TABLE_COLUMNS[table])
    write_scatter(out / "fig_qp1_dispersao.tex", [], None)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("package", nargs="?", type=Path, help="zip or folder from `msrkit package`")
    ap.add_argument("--out", type=Path, required=True, help="output folder")
    ap.add_argument("--pending", action="store_true", help="write placeholders only")
    ap.add_argument("--gazetteer", type=Path, help="gazetteer YAML (default: from the package)")
    ap.add_argument("--min-age-months", type=float, default=3.0)
    ap.add_argument("--min-n", type=int, default=10)
    ap.add_argument("--bootstrap", type=int, default=10_000)
    ap.add_argument("--listicle-k", type=int, default=4)
    ap.add_argument("--tau-threshold", type=float, default=0.5)
    ap.add_argument("--rho-threshold", type=float, default=0.4)
    ap.add_argument("--min-together", type=int, default=5)
    ap.add_argument(
        "--top-share",
        type=float,
        default=0.1,
        help="QP3 sensitivity: an item is in the top of its stratum above this share",
    )
    ap.add_argument("--top", type=int, default=15, help="rows in the LaTeX tables")
    ap.add_argument("--with-repos", action="store_true", help="QP3: include GitHub repos (stars)")
    ap.add_argument("--keep-bots", action="store_true", help="keep automated GitHub issues")
    ap.add_argument("--seed", type=int, default=20261031)
    args = ap.parse_args(argv)
    if not args.pending and args.package is None:
        ap.error("PACKAGE is required unless --pending")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.pending:
        write_pending(args.out)
        print(f"placeholders written to {args.out}")
        return 0
    pkg = load_package(args.package, args.gazetteer)
    summary = analyse(pkg, args.out, args)
    print(f"{len(pkg.items)} items, {summary['units']} units -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
