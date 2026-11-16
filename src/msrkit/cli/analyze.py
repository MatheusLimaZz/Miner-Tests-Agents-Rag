"""Analysis commands (Protocol E2 v2, §14): A1–A6 over units of analysis."""

from __future__ import annotations

from typing import Any

import typer
from rich.table import Table

from msrkit import cli as _cli
from msrkit.cli._common import (
    _load_detections,
    _load_run_protocol,
    _resolve_run,
    _unwrap,
    app,
    console,
)

analyze_app = typer.Typer(
    help="Analyses A1–A6 (§14): frequency, coverage, oracles, topics, compare."
)
app.add_typer(analyze_app, name="analyze")

BASIS_HELP = "detections (automatic, msrkit extract) or coding (manual form, consensus)"


def _load_units(run_id: str, basis: str, gazetteer: Any = None) -> list[Any]:
    from msrkit.analysis import units_from_codings, units_from_detections
    from msrkit.coding import load_codings
    from msrkit.storage import ItemStorage

    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    if basis == "detections":
        detections = _load_detections(run_id)
        if not detections:
            console.print("[red]✗ No detections; run `msrkit extract` first.[/red]")
            raise typer.Exit(1)
        return units_from_detections(items, detections, gazetteer=gazetteer)
    if basis == "coding":
        codings = load_codings(_cli.DATA_DIR, run_id)
        if not codings:
            console.print("[red]✗ No codings; import them with `msrkit coding import`.[/red]")
            raise typer.Exit(1)
        return units_from_codings(items, codings, gazetteer=gazetteer)
    console.print("[red]✗ --basis must be detections or coding.[/red]")
    raise typer.Exit(1)


def _out_dir(run_id: str) -> Any:
    out = _cli.DATA_DIR / "reports" / run_id / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    return out


@analyze_app.command("frequency")
def analyze_frequency(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    basis: str = typer.Option("detections", "--basis", help=BASIS_HELP),
    pairs: str = typer.Option(
        "tool-method", "--pairs", help="Co-occurrence: tool-method, tool-tool, method-method"
    ),
    top: int = typer.Option(20, "--top", help="Rows shown"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    channel: str = typer.Option(
        "all",
        "--channel",
        help="Channel filter: all, open (neutral discovery) or anchored (targeted searches)",
    ),
) -> None:
    """A1/A2: units per tool and method (by evidence level, channel and system) and co-occurrence.

    Tools of the `framework-sut` family (LangChain, LlamaIndex...) are frameworks of the
    system under test, mined through their own tests; the Family column tells them apart.
    """
    from msrkit.analysis import cooccurrence, frequency, write_csv

    run_id = _resolve_run(_unwrap(run_id))
    basis = _unwrap(basis)
    top = _unwrap(top)
    channel = _unwrap(channel).lower()
    if channel not in ("all", "open", "anchored"):
        console.print("[red]✗ --channel must be 'all', 'open' or 'anchored'.[/red]")
        raise typer.Exit(1)

    proto = _load_run_protocol(run_id, _unwrap(protocol))
    gaz = proto.gazetteer_data if proto is not None else None
    units = _load_units(run_id, basis, gazetteer=gaz)
    try:
        pair_rows = cooccurrence(units, _unwrap(pairs))
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None

    families = {t.id: t.family for t in gaz.tools} if gaz else {}
    families.update({m.id: "method" for m in gaz.methods} if gaz else {})
    rows = frequency(units, families, channel=channel)  # type: ignore[arg-type]
    out = _out_dir(run_id)
    csv_suffix = f"_{channel}" if channel != "all" else ""
    write_csv(rows, out / f"frequency_{basis}{csv_suffix}.csv")
    write_csv(pair_rows, out / f"cooccurrence_{_unwrap(pairs)}_{basis}.csv")

    ch_title = f" [{channel.upper()}]" if channel != "all" else ""
    table = Table(title=f"Frequency ({basis}){ch_title} — {len(units)} units")
    for col in ("Entry", "Family", "Units", "N1", "N2", "N3", "Open", "Anchored", "rag", "agente"):
        table.add_column(col)
    for r in rows[:top]:
        levels = (str(r.n1), str(r.n2), str(r.n3)) if r.kind == "tool" else ("—", "—", "—")
        family = r.family or r.kind
        table.add_row(
            r.entry,
            family,
            str(r.units),
            *levels,
            str(r.open_units),
            str(r.anchored_units),
            str(r.rag),
            str(r.agente),
        )
    console.print(table)
    pt = Table(title=f"Co-occurrence {_unwrap(pairs)}")
    for col in ("A", "B", "Together", "Jaccard", "Lift"):
        pt.add_column(col)
    for p in pair_rows[:top]:
        pt.add_row(p.a, p.b, str(p.together), f"{p.jaccard:.2f}", f"{p.lift:.2f}")
    console.print(pt)
    console.print(f"[green]✓ CSV written to {out}[/green]")


@analyze_app.command("coverage")
def analyze_coverage(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
) -> None:
    """A3: failure mode × tool/method coverage, for RAG and agents (coding basis).

    Writes the long table, one wide matrix per system and a per-mode summary that
    lists catalog modes nobody addresses.
    """
    from msrkit.analysis import coverage, coverage_matrix, write_csv, write_matrix

    run_id = _resolve_run(_unwrap(run_id))
    units = _load_units(run_id, "coding")
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    catalog = proto.coding.failure_modes if proto is not None else {}
    cells, summaries = coverage(units, catalog)
    out = _out_dir(run_id)
    write_csv(cells, out / "coverage_long.csv")
    write_csv(summaries, out / "coverage_summary.csv")
    modes = [s.failure_mode for s in summaries]
    for system in ("rag", "agente", None):
        columns, rows = coverage_matrix(cells, modes, system)
        write_matrix(columns, rows, modes, out / f"coverage_matrix_{system or 'all'}.csv")

    table = Table(title=f"Failure-mode coverage (coding) — {len(units)} units")
    for col in ("Mode", "Label", "Units", "rag", "agente", "Tools", "Methods", "Top"):
        table.add_column(col)
    for s in summaries:
        style = "" if s.covered else "dim"
        table.add_row(
            s.failure_mode, s.label, str(s.units), str(s.rag), str(s.agente),
            str(s.tools), str(s.methods), s.top_entries, style=style,
        )  # fmt: skip
    console.print(table)
    uncovered = [s.failure_mode for s in summaries if not s.covered]
    if uncovered:
        console.print(f"Not addressed by any unit: {', '.join(uncovered)}")
    console.print(f"[green]✓ CSV written to {out}[/green]")


@analyze_app.command("oracles")
def analyze_oracles(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    basis: str = typer.Option("coding", "--basis", help=BASIS_HELP),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    threshold: float = typer.Option(
        0.5, "--threshold", help="H2: share of atomic oracles the majority must exceed"
    ),
) -> None:
    """A4: distribution on the oracle ladder and by aggregation; tests H2.

    On the detections basis each method contributes its typical rung from the
    gazetteer; aggregation (and so H2) needs the coding basis.
    """
    from msrkit.analysis import aggregation_test, oracle_distribution, write_csv

    run_id = _resolve_run(_unwrap(run_id))
    basis = _unwrap(basis)
    threshold = _unwrap(threshold)
    units = _load_units(run_id, basis)
    rungs: dict[str, str] = {}
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    if proto is not None and proto.gazetteer_data is not None:
        rungs = {m.id: m.oracle for m in proto.gazetteer_data.methods if m.oracle}
    rows = oracle_distribution(units, rungs if basis == "detections" else None)
    out = _out_dir(run_id)
    write_csv(rows, out / f"oracles_{basis}.csv")

    table = Table(title=f"Oracle ladder ({basis}) — {len(units)} units")
    for col in ("Rung", "all", "rag", "agente"):
        table.add_column(col)
    by = {(r.system, r.rung): r for r in rows}
    for rung in dict.fromkeys(r.rung for r in rows):
        table.add_row(
            rung,
            *(
                f"{by[(s, rung)].units} ({by[(s, rung)].share:.0%})"
                for s in ("all", "rag", "agente")
            ),
        )
    console.print(table)

    if basis != "coding":
        console.print("[dim]Aggregation (H2) needs --basis coding.[/dim]")
        return
    tests = [aggregation_test(units, s, threshold) for s in ("all", "rag", "agente")]
    write_csv(tests, out / "h2_aggregation.csv")
    h2 = Table(title=f"H2 — atomic oracles > {threshold:.0%}")
    for col in ("System", "Atomic", "Aggregated", "Unidentified", "Atomic %", "95% CI", "p", "H2"):
        h2.add_column(col)
    for t in tests:
        h2.add_row(
            t.system, str(t.atomic), str(t.aggregated), str(t.unidentified),
            f"{t.proportion_atomic:.0%}", f"{t.ci95[0]:.0%}–{t.ci95[1]:.0%}",
            f"{t.p_value:.4f}", "supported" if t.supported else "not supported",
        )  # fmt: skip
    console.print(h2)
    console.print(f"[green]✓ CSV written to {out}[/green]")


@analyze_app.command("topics")
def analyze_topics(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    status: str = typer.Option(
        "include", "--status", help="Items by final screening decision: include or all"
    ),
    n_topics: int = typer.Option(10, "--topics", help="LDA topics"),
    n_clusters: int = typer.Option(10, "--clusters", help="k-means clusters"),
    min_df: int = typer.Option(2, "--min-df", help="Minimum documents per term"),
    seed: int = typer.Option(20260928, "--seed", help="Random seed (recorded)"),
) -> None:
    """A5: open discovery pass — LDA topics and k-means clusters over item text (§7.6).

    Flags top terms outside the gazetteer and the lexicon as candidates for the
    catalog. Needs the `analysis` extra (scikit-learn).
    """
    import json

    from msrkit.analysis import write_csv
    from msrkit.discovery import catalog_origin, discover, known_vocabulary
    from msrkit.screening import final_decisions, load_decisions
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    status = _unwrap(status)
    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    if status == "include":
        final = final_decisions(load_decisions(_cli.DATA_DIR, run_id))
        items = [it for it in items if final.get(it.id) == "include"]
    elif status != "all":
        console.print("[red]✗ --status must be include or all.[/red]")
        raise typer.Exit(1)
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    gaz = proto.gazetteer_data if proto is not None else None
    lexicon = (
        [*proto.all_terms(), *(t for v in proto.concepts.values() for t in v)] if proto else []
    )
    try:
        result = discover(
            items,
            known_vocabulary(gaz, lexicon),
            n_topics=_unwrap(n_topics),
            n_clusters=_unwrap(n_clusters),
            min_df=_unwrap(min_df),
            seed=_unwrap(seed),
        )
    except (RuntimeError, ValueError) as e:
        from rich.markup import escape

        console.print(f"[red]✗ {escape(str(e))}[/red]")
        if status == "include" and "need at least as many as topics" in str(e):
            console.print(
                "[yellow]Dica: Poucos itens incluídos na triagem. Use --status all para "
                "analisar todo o corpus minerado, ou reduza --topics e --clusters.[/yellow]"
            )
        raise typer.Exit(1) from None

    out = _out_dir(run_id)
    write_csv(result.topics, out / "topics.csv")
    write_csv(result.clusters, out / "clusters.csv")
    write_csv(result.assignments, out / "topic_assignments.csv")
    meta = {
        "status": status,
        "seed": _unwrap(seed),
        "documents": result.documents,
        "skipped_short": result.skipped_short,
        "vocabulary": result.vocabulary,
        "candidate_terms": result.candidate_terms,
        "catalog_origin": catalog_origin(gaz) if gaz else None,
    }
    (out / "discovery.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), "utf-8")

    table = Table(title=f"LDA topics — {result.documents} documents, {result.vocabulary} terms")
    for col in ("Topic", "Docs", "Top terms", "Candidates"):
        table.add_column(col)
    for t in result.topics:
        table.add_row(str(t.topic), str(t.documents), ", ".join(t.top_terms[:8]),
                      ", ".join(t.candidates[:5]))  # fmt: skip
    console.print(table)
    ct = Table(title="k-means clusters")
    for col in ("Cluster", "Size", "Top terms", "Example"):
        ct.add_column(col)
    for c in result.clusters:
        ct.add_row(str(c.cluster), str(c.size), ", ".join(c.top_terms[:8]),
                   c.examples[0] if c.examples else "")  # fmt: skip
    console.print(ct)
    console.print(f"Candidate terms for the catalog: {len(result.candidate_terms)}")
    if gaz:
        origin = catalog_origin(gaz)
        total = sum(origin.values())
        disc = origin.get("discovery", 0)
        pct = f"({disc / total:.0%})" if total else "(0%)"
        console.print(f"Catalog from the discovery pass: {disc}/{total} entries {pct}")
    console.print(f"[green]✓ Written to {out}[/green]")


@analyze_app.command("compare")
def analyze_compare(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    basis: str = typer.Option("coding", "--basis", help=BASIS_HELP),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    window: int = typer.Option(2, "--window", help="Saturation: quiet batches in a row"),
    tolerance: int = typer.Option(0, "--tolerance", help="Saturation: new codes allowed per batch"),
) -> None:
    """A6: RAG × agents (what transfers, what is new) and saturation by screening batch."""
    from msrkit.analysis import compare_systems, saturation, write_csv
    from msrkit.screening import rank_items
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    basis = _unwrap(basis)
    units = _load_units(run_id, basis)
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    rungs: dict[str, str] = {}
    if basis == "detections" and proto is not None and proto.gazetteer_data is not None:
        rungs = {m.id: m.oracle for m in proto.gazetteer_data.methods if m.oracle}
    rows, summaries = compare_systems(units, rungs)
    out = _out_dir(run_id)
    write_csv(rows, out / f"compare_{basis}.csv")
    write_csv(summaries, out / f"compare_summary_{basis}.csv")

    table = Table(title=f"RAG × agents ({basis}) — {len(units)} units")
    for col in ("Kind", "RAG", "Agents", "Shared", "Jaccard", "Agent entries seen in RAG"):
        table.add_column(col)
    for s in summaries:
        table.add_row(
            s.kind, str(s.rag_entries), str(s.agente_entries), str(s.shared),
            f"{s.jaccard:.2f}", f"{s.agent_transfer_rate:.0%}",
        )  # fmt: skip
    console.print(table)
    new_for_agents = [r.entry for r in rows if r.category == "agent-only"]
    if new_for_agents:
        console.print(f"Only in agent units: {', '.join(new_for_agents[:20])}")

    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    batch_size = proto.screening.batch_size if proto is not None else 25
    ranking = rank_items(items, _load_detections(run_id), batch_size)
    sat = saturation(
        units,
        {i: r.batch for i, r in ranking.items()},
        window=_unwrap(window),
        tolerance=_unwrap(tolerance),
        method_rungs=rungs,
    )
    write_csv(sat.points, out / f"saturation_{basis}.csv")
    curve = " ".join(f"{p.batch}:{p.cumulative_entries}" for p in sat.points)
    console.print(f"Saturation curve (batch:cumulative codes): {curve or '—'}")
    if sat.saturated:
        console.print(
            f"[green]Saturated from batch {sat.saturated_at}[/green] "
            f"(last {sat.window} batches added ≤ {sat.tolerance} new codes)."
        )
    else:
        console.print("[yellow]Not saturated: recent batches still add new codes.[/yellow]")
    console.print(f"[green]✓ CSV written to {out}[/green]")
