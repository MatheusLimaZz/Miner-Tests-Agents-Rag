"""PRISMA 2020 flow for a run (RF8), computed from the run's own records.

Every number is traceable to a file:

| Stage                               | Source                                          |
|-------------------------------------|-------------------------------------------------|
| Identified, per source              | items.jsonl + manifest `discarded`              |
| Not retrieved (source caps, est.)   | manifest `estimated_total` of truncated queries |
| Not examined (item limit, est.)     | the same, for queries cut by the run's item cap |
| Planned queries not executed        | manifest `planned` − `queries`, per source      |
| Removed by local filters            | runs/<id>/discarded.jsonl, by reason            |
| Duplicates removed                  | items/<id>/dedupe_report.json, by pass          |
| Screened / excluded / uncertain     | screening/<id>/decisions.jsonl (final decision) |
| Exclusion reasons                   | criteria marked by the excluding coders         |
| Included / coded                    | final decisions / coding/<id>/codings.jsonl     |

Duplicates are reported as missing ("n/a") when dedupe has not run, never as zero.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from pathlib import Path

    from msrkit.models import Item, Manifest
    from msrkit.screening import ScreeningDecision


class PrismaFlow(BaseModel):
    run_id: str
    identified: dict[str, int]  # per source: returned by the search (kept + filtered)
    not_retrieved_estimate: dict[str, int]  # per source, from truncated queries
    truncated_queries: int
    # Queries that stopped at the run's item limit are a sample of their results. Per
    # source: results they match beyond what was examined. An upper bound, since one
    # record can match several queries (ADR-058).
    sampled_out: dict[str, int] = {}
    sampled_queries: int = 0
    sampled_queries_uncounted: int = 0  # of those, the source reported no total
    # Per source: planned queries that never ran (source cap reached, interrupted run).
    not_executed: dict[str, int] = {}
    filtered: dict[str, int]  # local filter reason -> count
    after_filters: int
    duplicates: dict[str, int] | None  # url/content/near; None when dedupe not run
    screened: int  # the screening pool: deduplicated items when dedupe ran
    excluded: int
    exclusion_reasons: dict[str, int] = {}
    uncertain: int
    pending: int  # no decision yet
    included: int
    included_by_source: dict[str, int] = {}
    coded: int

    @property
    def total_identified(self) -> int:
        return sum(self.identified.values())


def _discard_reasons(path: Path) -> Counter[str]:
    reasons: Counter[str] = Counter()
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line_s := line.strip():
                    reasons[json.loads(line_s).get("reason") or "unspecified"] += 1
    return reasons


def build_flow(
    run_id: str,
    manifest: Manifest | None,
    items: list[Item],
    pool: list[Item],
    dedupe_report: dict[str, Any] | None,
    discard_file: Path,
    decisions: dict[tuple[str, str], ScreeningDecision],
    coded_ids: set[str],
) -> PrismaFlow:
    kept = Counter(it.source for it in items)
    filtered_by_source: Counter[str] = Counter()
    manifest_kept: Counter[str] = Counter()
    not_retrieved: Counter[str] = Counter()
    not_executed: Counter[str] = Counter()
    sampled_out: Counter[str] = Counter()
    truncated = sampled = uncounted = 0
    for src in manifest.sources if manifest else []:
        not_executed[src.name] = max(len(src.planned) - len(src.queries), 0)
        for q in src.queries:
            manifest_kept[src.name] += q.items
            filtered_by_source[src.name] += q.discarded
            gap = (
                None
                if q.estimated_total is None
                else max(q.estimated_total - q.items - q.discarded, 0)
            )
            if "source_cap" in q.truncation_reasons:
                truncated += 1
                not_retrieved[src.name] += gap or 0
            elif "item_limit" in q.truncation_reasons:
                # The run's own cap: the query is a sample of what the source holds.
                sampled += 1
                uncounted += gap is None
                sampled_out[src.name] += gap or 0
    effective_kept = manifest_kept if manifest and sum(manifest_kept.values()) > 0 else kept
    sources = sorted(effective_kept.keys() | filtered_by_source.keys())
    identified = {s: effective_kept[s] + filtered_by_source[s] for s in sources}

    reasons = _discard_reasons(discard_file)
    # The manifest count is authoritative; the file gives the reasons.
    missing = sum(filtered_by_source.values()) - sum(reasons.values())
    if missing > 0:
        reasons["unspecified"] += missing

    duplicates = None
    if dedupe_report is not None:
        duplicates = {k: int(v) for k, v in (dedupe_report.get("removed") or {}).items()}

    from msrkit.screening import final_decisions

    pool_ids = {it.id for it in pool}
    final = {i: d for i, d in final_decisions(decisions).items() if i in pool_ids}
    excl_reasons: Counter[str] = Counter()
    for item_id, decision in final.items():
        if decision != "exclude":
            continue
        marked = {
            c
            for (i, _coder), d in decisions.items()
            if i == item_id and d.decision == "exclude"
            for c in d.criteria_met
            if isinstance(c, str) and c.upper().startswith("E")
        }
        for c in sorted(marked) or ["other (reason given)"]:
            excl_reasons[c] += 1
    included = {i for i, d in final.items() if d == "include"}
    by_source = Counter(it.source for it in pool if it.id in included)
    return PrismaFlow(
        run_id=run_id,
        identified=identified,
        not_retrieved_estimate={s: n for s, n in sorted(not_retrieved.items()) if n},
        truncated_queries=truncated,
        sampled_out={s: n for s, n in sorted(sampled_out.items()) if n},
        sampled_queries=sampled,
        sampled_queries_uncounted=uncounted,
        not_executed={s: n for s, n in sorted(not_executed.items()) if n},
        filtered=dict(reasons.most_common()),
        after_filters=len(items),
        duplicates=duplicates,
        screened=len(pool),
        excluded=sum(d == "exclude" for d in final.values()),
        exclusion_reasons=dict(sorted(excl_reasons.items())),
        uncertain=sum(d == "uncertain" for d in final.values()),
        pending=len(pool) - len(final),
        included=len(included),
        included_by_source=dict(sorted(by_source.items())),
        coded=len(coded_ids & included),
    )


def render_markdown(flow: PrismaFlow) -> str:
    lines = [f"# PRISMA flow — {flow.run_id}", "", "## Identification", ""]
    lines += [
        "| Source | Identified | Not retrieved (est., source caps) "
        "| Not examined (est., item limit) |",
        "|---|---:|---:|---:|",
    ]
    for s, n in flow.identified.items():
        lines.append(
            f"| {s} | {n} | {flow.not_retrieved_estimate.get(s, 0)} "
            f"| {flow.sampled_out.get(s, 0)} |"
        )
    lines.append(
        f"| **total** | **{flow.total_identified}** | "
        f"**{sum(flow.not_retrieved_estimate.values())}** | "
        f"**{sum(flow.sampled_out.values())}** |"
    )
    lines += ["", f"Queries truncated by a source cap: {flow.truncated_queries}"]
    uncounted = (
        f" ({flow.sampled_queries_uncounted} without a total from the source, "
        "left out of the estimate)"
        if flow.sampled_queries_uncounted
        else ""
    )
    lines.append(
        "Queries stopped at the run's item limit (a sample of their results): "
        f"{flow.sampled_queries}{uncounted}"
    )
    if flow.sampled_queries:
        lines.append(
            "The item-limit column is an upper bound: it adds the results each query "
            "matches, and one record can match several queries."
        )
    skipped = ", ".join(f"{s} {n}" for s, n in flow.not_executed.items())
    lines.append(
        f"Planned queries not executed (source cap or interruption): "
        f"{sum(flow.not_executed.values())}" + (f" ({skipped})" if skipped else "")
    )
    lines.append("")
    lines += ["## Records removed before screening", ""]
    for reason, n in flow.filtered.items():
        lines.append(f"- Local filter — {reason}: {n}")
    if flow.duplicates is None:
        lines.append("- Duplicates: n/a (run `msrkit dedupe`)")
    else:
        for reason, n in flow.duplicates.items():
            lines.append(f"- Duplicates — {reason}: {n}")
    lines += ["", "## Screening", ""]
    lines.append(f"- Records screened: {str(flow.screened)}")
    lines.append(f"- Excluded: {str(flow.excluded)}")
    for reason, n in flow.exclusion_reasons.items():
        lines.append(f"  - {reason}: {n}")
    lines.append(f"- Uncertain (third coder): {str(flow.uncertain)}")
    lines.append(f"- Not yet screened: {str(flow.pending)}")
    lines += ["", "## Included", ""]
    lines.append(f"- Included: {str(flow.included)}")
    for s, n in flow.included_by_source.items():
        lines.append(f"  - {s}: {n}")
    lines.append(f"- Coded (extraction form): {str(flow.coded)}")
    lines += ["", "```mermaid", render_mermaid(flow), "```", ""]
    return "\n".join(lines)


def _latex_escape(text: str) -> str:
    """Escape LaTeX special characters for safe rendering in TikZ nodes."""
    specials = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    res = text.replace("\\", specials["\\"])
    for char, replacement in specials.items():
        if char != "\\":
            res = res.replace(char, replacement)
    return res


def _mermaid_escape(text: str) -> str:
    """Sanitize strings for Mermaid node text by replacing double quotes."""
    return text.replace('"', "'")


def render_mermaid(flow: PrismaFlow) -> str:
    dup = "n/a" if flow.duplicates is None else str(sum(flow.duplicates.values()))
    filtered = sum(flow.filtered.values())
    per_source = "<br/>".join(f"{_mermaid_escape(s)}: {n}" for s, n in flow.identified.items())
    excl = "<br/>".join(f"{_mermaid_escape(r)}: {n}" for r, n in flow.exclusion_reasons.items())
    sampled = (
        f"<br/>not examined, item limit (est., upper bound): {sum(flow.sampled_out.values())}"
        if flow.sampled_queries
        else ""
    )
    return "\n".join(
        [
            "flowchart TD",
            f'  A["Records identified: {flow.total_identified}<br/>{per_source}{sampled}"]',
            f'  B["Removed before screening<br/>local filters: {filtered}<br/>duplicates: {dup}"]',
            f'  C["Records screened: {str(flow.screened)}"]',
            f'  D["Excluded: {str(flow.excluded)}' + (f"<br/>{excl}" if excl else "") + '"]',
            f'  E["Uncertain: {str(flow.uncertain)} · not screened: {str(flow.pending)}"]',
            f'  F["Included: {str(flow.included)}<br/>coded: {str(flow.coded)}"]',
            "  A --> B",
            "  A --> C",
            "  C --> D",
            "  C --> E",
            "  C --> F",
        ]
    )


def render_tikz(flow: PrismaFlow) -> str:
    """Render PRISMA 2020 flow diagram as a standalone LaTeX TikZ snippet."""
    dup = "n/a" if flow.duplicates is None else str(sum(flow.duplicates.values()))
    filtered = sum(flow.filtered.values())
    per_source = ", ".join(f"{_latex_escape(s)}: {n}" for s, n in flow.identified.items())
    excl_reasons = (
        ", ".join(f"{_latex_escape(r)}: {n}" for r, n in flow.exclusion_reasons.items())
        or "critérios E1--E6"
    )

    box_style = (
        r"  box/.style={rectangle, draw=black, thick, fill=gray!5, "
        r"text width=6.2cm, align=left, minimum height=1.1cm, inner sep=5pt, font=\small},"
    )
    sidebox_style = (
        r"  sidebox/.style={rectangle, draw=black!80, dashed, fill=gray!10, "
        r"text width=5.0cm, align=left, minimum height=1.1cm, inner sep=5pt, font=\footnotesize},"
    )
    sampled = (
        rf"\\Não examinados pelo teto de itens (est., limite superior): "
        rf"{sum(flow.sampled_out.values())}"
        if flow.sampled_queries
        else ""
    )
    ident_text = (
        rf"\node[box] (ident) {{\textbf{{Identificação}}\\"
        rf"Registros identificados: {flow.total_identified}\\\scriptsize {per_source}{sampled}}};"
    )
    rem_text = (
        rf"\node[sidebox, right=of ident] (rem) {{\textbf{{Removidos pré-triagem}}\\"
        rf"Filtros locais: {filtered}\\Duplicatas: {dup}}};"
    )
    screen_text = (
        rf"\node[box, below=of ident] (screen) {{\textbf{{Triagem}}\\"
        rf"Registros triados: {flow.screened}}};"
    )
    excl_text = (
        rf"\node[sidebox, right=of screen] (excl) {{\textbf{{Excluídos}}\\"
        rf"Total: {flow.excluded}\\\scriptsize {excl_reasons}}};"
    )
    inc_text = (
        rf"\node[box, below=of screen] (inc) {{\textbf{{Incluídos no Estudo}}\\"
        rf"Registros incluídos: {flow.included}\\Extração/Codificação: {flow.coded}}};"
    )

    lines = [
        r"% PRISMA 2020 Flow Diagram - Auto-generated by MSR-Kit",
        r"\begin{tikzpicture}[",
        r"  node distance=1.4cm and 1.2cm,",
        box_style,
        sidebox_style,
        r"  arrow/.style={-latex, thick, draw=black!80}",
        r"]",
        r"",
        r"% Identificação",
        ident_text,
        rem_text,
        r"",
        r"% Triagem",
        screen_text,
        excl_text,
        r"",
        r"% Incluídos",
        inc_text,
        r"",
        r"% Conexões",
        r"\draw[arrow] (ident) -- (rem);",
        r"\draw[arrow] (ident) -- (screen);",
        r"\draw[arrow] (screen) -- (excl);",
        r"\draw[arrow] (screen) -- (inc);",
        r"",
        r"\end{tikzpicture}",
    ]
    return "\n".join(lines)
