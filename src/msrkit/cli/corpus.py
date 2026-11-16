"""Corpus commands: PRISMA flow, mining report, deduplication, statistics, export."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from msrkit import cli as _cli
from msrkit.cli._common import (
    DEFAULT_PROTOCOL,
    _get_latest_run_id,
    _get_registry,
    _load_detections,
    _load_repo_signals,
    _load_run_protocol,
    _resolve_run,
    _setup_logging,
    _unwrap,
    app,
    console,
)
from msrkit.storage import ItemStorage


@app.command()
def prisma(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
) -> None:
    """PRISMA 2020 flow of a run from its manifest, discards, dedupe and screening (RF8).

    Writes data/reports/<run_id>/prisma.json and prisma.md (with a Mermaid diagram).
    """
    from msrkit.coding import load_codings
    from msrkit.prisma import build_flow, render_markdown, render_tikz
    from msrkit.provenance import load_manifest
    from msrkit.screening import load_decisions
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    storage = ItemStorage(_cli.DATA_DIR)
    manifest = None

    if run_id == "consolidated":
        runs = [r for r in storage.list_runs() if r != "consolidated"]
        raw_items = [it for r in runs for it in storage.read_items(r, prefer_deduped=False)]
        pool_items = storage.read_items("consolidated", prefer_deduped=True)
        if not pool_items:
            from msrkit.dedupe import deduplicate

            pool_items, _ = deduplicate(raw_items)

        combined_sources: dict[str, Any] = {}
        for r in runs:
            with contextlib.suppress(FileNotFoundError):
                m = load_manifest(_cli.DATA_DIR, r)
                for s in m.sources:
                    if s.name not in combined_sources:
                        combined_sources[s.name] = s.model_copy(deep=True)
                    else:
                        combined_sources[s.name].queries.extend(s.queries)
        if combined_sources:
            from datetime import UTC, datetime

            from msrkit import __version__
            from msrkit.models import Manifest

            manifest = Manifest(
                run_id="consolidated",
                msrkit_version=__version__,
                protocol_path="consolidated",
                protocol_sha256="",
                started_at=datetime.now(UTC),
                sources=list(combined_sources.values()),
            )

        discards_dir = _cli.DATA_DIR / "runs" / "consolidated"
        discards_dir.mkdir(parents=True, exist_ok=True)
        discard_file = discards_dir / "discarded.jsonl"
        seen_discards: set[str] = set()
        with discard_file.open("w", encoding="utf-8") as out_df:
            for r in runs:
                src_df = _cli.DATA_DIR / "runs" / r / "discarded.jsonl"
                if src_df.exists():
                    with open(src_df, encoding="utf-8") as in_df:
                        for line in in_df:
                            line_str = line.strip()
                            if line_str and line_str not in seen_discards:
                                seen_discards.add(line_str)
                                out_df.write(line_str + "\n")

        decisions = load_decisions(_cli.DATA_DIR, "consolidated")
        if not decisions:
            for r in runs:
                decisions.update(load_decisions(_cli.DATA_DIR, r))

        codings = dict(load_codings(_cli.DATA_DIR, "consolidated"))
        if not codings:
            for r in runs:
                codings.update(load_codings(_cli.DATA_DIR, r))
        coded_ids = {item_id for (item_id, _c) in codings}
    else:
        with contextlib.suppress(FileNotFoundError):
            manifest = load_manifest(_cli.DATA_DIR, run_id)
        raw_items = storage.read_items(run_id)
        pool_items = storage.read_items(run_id, prefer_deduped=True)
        discard_file = _cli.DATA_DIR / "runs" / run_id / "discarded.jsonl"
        decisions = load_decisions(_cli.DATA_DIR, run_id)
        coded_ids = {item_id for (item_id, _c) in load_codings(_cli.DATA_DIR, run_id)}

    report_path = _cli.DATA_DIR / "items" / run_id / "dedupe_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    flow = build_flow(
        run_id,
        manifest,
        raw_items,
        pool_items,
        report,
        discard_file,
        decisions,
        coded_ids,
    )
    out_dir = _cli.DATA_DIR / "reports" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prisma.json").write_text(flow.model_dump_json(indent=2), encoding="utf-8")
    markdown = render_markdown(flow)
    (out_dir / "prisma.md").write_text(markdown, encoding="utf-8")
    tikz = render_tikz(flow)
    (out_dir / "prisma.tikz").write_text(tikz, encoding="utf-8")
    dup = "n/a" if flow.duplicates is None else sum(flow.duplicates.values())
    console.print(
        f"Identified {flow.total_identified} → filtered {sum(flow.filtered.values())}, "
        f"duplicates {dup} → screened {flow.screened} → excluded {flow.excluded}, "
        f"uncertain {flow.uncertain}, pending {flow.pending} → included {flow.included} "
        f"(coded {flow.coded})"
    )
    if manifest is None:
        console.print("[yellow]⚠ No manifest: identification counts come from items only.[/yellow]")
    console.print(f"[green]✓ PRISMA flow written to {out_dir}[/green]")


@app.command()
def report(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
) -> None:
    """Mining report of a run: time window, sources, search terms and counts.

    Writes data/reports/<run_id>/relatorio_mineracao.md and .json. Run it after the
    collection; counts from dedupe, enrich and extract are included when present.
    Earlier runs in data/ (the daily RSS accumulation, for one) give the new items.
    """
    from msrkit.config import load_protocol
    from msrkit.provenance import load_manifest
    from msrkit.report import build_report
    from msrkit.report import render_markdown as render_report

    run_id = _resolve_run(_unwrap(run_id))
    storage = ItemStorage(_cli.DATA_DIR)
    manifest = None
    with contextlib.suppress(FileNotFoundError):
        manifest = load_manifest(_cli.DATA_DIR, run_id)
    # The protocol file's own window, without MSRKIT_WINDOW_* (the manifest keeps the
    # window in force only).
    protocol_window = None
    if manifest and Path(manifest.protocol_path).exists():
        with contextlib.suppress(Exception):
            w = load_protocol(manifest.protocol_path, environ={}).window
            protocol_window = (w.since, w.until)
    earlier = [r for r in storage.list_runs() if r < run_id]
    earlier_urls = {
        str(it.url) for r in earlier for it in storage.read_items(r, prefer_deduped=True)
    }
    dedupe_path = _cli.DATA_DIR / "items" / run_id / "dedupe_report.json"
    stats_path = _cli.DATA_DIR / "extract" / run_id / "extract_stats.json"
    rep = build_report(
        run_id,
        manifest,
        storage.read_items(run_id),
        storage.read_items(run_id, prefer_deduped=True),
        json.loads(dedupe_path.read_text(encoding="utf-8")) if dedupe_path.exists() else None,
        _cli.DATA_DIR / "runs" / run_id / "discarded.jsonl",
        signals=_load_repo_signals(run_id),
        detections=_load_detections(run_id),
        protocol_window=protocol_window,
        earlier_urls=earlier_urls,
        earlier_runs=len(earlier),
        extract_stats=json.loads(stats_path.read_text("utf-8")) if stats_path.exists() else None,
    )
    out_dir = _cli.DATA_DIR / "reports" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "relatorio_mineracao.json").write_text(rep.model_dump_json(indent=2), "utf-8")
    (out_dir / "relatorio_mineracao.md").write_text(render_report(rep), encoding="utf-8")
    console.print(
        f"Identified {rep.identified} → unique {rep.unique} from "
        f"{sum(1 for s in rep.sources if s.executed)} source(s); {len(rep.warnings)} warning(s)"
    )
    console.print(f"[green]✓ Mining report written to {out_dir / 'relatorio_mineracao.md'}[/green]")


@app.command(name="dedupe")
def dedupe(
    run_id: str | None = typer.Option(
        None, "--run", help="Run ID to deduplicate (defaults to latest)"
    ),
    all_runs: bool = typer.Option(
        False, "--all", "-a", help="Consolidate and deduplicate across all historical runs"
    ),
    near_threshold: float = typer.Option(
        0.85,
        "--near-threshold",
        help="Estimated Jaccard for near-duplicates (pass 3; calibrate in the pilot, 0 disables)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Deduplicate items for a run or across all runs."""
    run_id = _unwrap(run_id)
    all_runs = _unwrap(all_runs)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.dedupe import deduplicate_report
    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(_cli.DATA_DIR)

    if all_runs:
        runs = item_storage.list_runs()
        if not runs:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[bold]Deduplicating across {len(runs)} historical runs...[/bold]")
        all_items = []
        for r in runs:
            all_items.extend(item_storage.read_items(r, prefer_deduped=False))
        items = all_items
        target_dir = _cli.DATA_DIR / "items" / "consolidated"
    else:
        if not run_id:
            run_id = _get_latest_run_id()
            if not run_id:
                console.print("[red]✗ No runs found in data directory.[/red]")
                raise typer.Exit(1)
            console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")
        console.print(f"[bold]Deduplicating run:[/bold] {run_id}")
        items = item_storage.read_items(run_id, prefer_deduped=False)
        target_dir = _cli.DATA_DIR / "items" / run_id

    if not items:
        console.print("[yellow]No items found.[/yellow]")
        return

    threshold = _unwrap(near_threshold)
    report = deduplicate_report(items, near_threshold=threshold if threshold else None)
    unique, duplicates = report.unique, report.duplicates
    counts = report.counts()
    console.print(f"  Input: {len(items)}")
    console.print(f"  Unique: {len(unique)}")
    console.print(
        f"  Duplicates removed: {len(duplicates)} "
        f"(url: {counts['url']}, content: {counts['content']}, near: {counts['near']})"
    )

    # Save deduplicated items
    target_dir.mkdir(parents=True, exist_ok=True)
    deduped_path = target_dir / "items_deduped.jsonl"
    with open(deduped_path, "w", encoding="utf-8") as f:
        for item in unique:
            f.write(item.model_dump_json() + "\n")

    report_path = target_dir / "dedupe_report.json"
    report_path.write_text(
        json.dumps(
            {
                "input": len(items),
                "unique": len(unique),
                "near_threshold": threshold or None,
                "removed": counts,
                "reasons": report.reasons,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    console.print(f"[green]✓ Saved to {deduped_path}[/green] (reasons: {report_path.name})")


@app.command()
def stats(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    all_runs: bool = typer.Option(
        False, "--all", "-a", help="Show consolidated corpus statistics across all historical runs"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show collection statistics for a run or the consolidated corpus."""
    run_id = _unwrap(run_id)
    all_runs = _unwrap(all_runs)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(_cli.DATA_DIR)

    if all_runs or run_id == "consolidated":
        runs = [r for r in item_storage.list_runs() if r != "consolidated"]
        if not runs:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)

        consolidated_items = item_storage.read_items("consolidated", prefer_deduped=True)
        report_path = _cli.DATA_DIR / "items" / "consolidated" / "dedupe_report.json"
        if consolidated_items and report_path.exists():
            unique_items = consolidated_items
            try:
                rep_data = json.loads(report_path.read_text(encoding="utf-8"))
                total_raw = rep_data.get("input", len(unique_items))
                num_duplicates = total_raw - len(unique_items)
            except Exception:
                total_raw = len(unique_items)
                num_duplicates = 0
        else:
            from msrkit.dedupe import deduplicate

            all_items = []
            for r in runs:
                all_items.extend(item_storage.read_items(r, prefer_deduped=False))

            if not all_items:
                console.print("[yellow]No items found across runs.[/yellow]")
                return

            unique_items, duplicates = deduplicate(all_items, near_threshold=0.85)
            total_raw = len(all_items)
            num_duplicates = len(duplicates)

        hist_by_source: dict[str, int] = {}
        for it in unique_items:
            hist_by_source[it.source] = hist_by_source.get(it.source, 0) + 1

        hist_table = Table(
            title=f"Consolidated Historical Corpus ({len(runs)} runs)",
            show_lines=True,
        )
        hist_table.add_column("Source", style="bold")
        hist_table.add_column("Unique Items", justify="right")
        hist_table.add_column("Corpus Share", justify="right")

        total_hist = len(unique_items)
        for src, count in sorted(hist_by_source.items()):
            share = (count / total_hist * 100) if total_hist > 0 else 0
            hist_table.add_row(src, str(count), f"{share:.1f}%")

        hist_table.add_section()
        hist_table.add_row(
            "[bold]Total Consolidated[/bold]",
            f"[bold green]{total_hist}[/bold green]",
            "100.0%",
        )
        console.print(hist_table)
        console.print(
            f"\n[dim]Total across history: {total_raw} raw items collected | "
            f"{num_duplicates} duplicates removed[/dim]"
        )
        return

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")

    from msrkit.provenance import load_manifest

    try:
        manifest = load_manifest(_cli.DATA_DIR, run_id)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None

    table = Table(title=f"Run Statistics: {run_id}", show_lines=True)
    table.add_column("Source", style="bold")
    table.add_column("Status")
    table.add_column("Queries")
    table.add_column("Items")
    table.add_column("Requests")
    table.add_column("Truncated")

    total_items = 0
    total_requests = 0

    for source in manifest.sources:
        status_color = {
            "OK": "green",
            "DEGRADED": "yellow",
            "UNSUPPORTED": "red",
        }.get(source.availability.status, "white")

        items = sum(q.items for q in source.queries)
        requests = sum(q.requests for q in source.queries)
        truncated = any(q.truncated for q in source.queries)
        reason_counts: dict[str, int] = {}
        for q in source.queries:
            for reason in q.truncation_reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        reason_text = ", ".join(f"{r}×{n}" for r, n in sorted(reason_counts.items()))
        total_items += items
        total_requests += requests

        table.add_row(
            source.name,
            f"[{status_color}]{source.availability.status}[/{status_color}]",
            str(len(source.queries)),
            str(items),
            str(requests),
            (f"[red]Yes[/red] ({reason_text})" if reason_text else "[red]Yes[/red]")
            if truncated
            else "[green]No[/green]",
        )

    console.print(table)
    console.print(f"\n[bold]Total items:[/bold] {total_items}")
    console.print(f"[bold]Total requests:[/bold] {total_requests}")

    # Check for items file of this run
    items_list = item_storage.read_items(run_id, prefer_deduped=True)
    if items_list:
        by_source: dict[str, int] = {}
        for item in items_list:
            by_source[item.source] = by_source.get(item.source, 0) + 1

        console.print("\n[bold]Items on disk by source:[/bold]")
        for src, count in sorted(by_source.items()):
            console.print(f"  {src}: {count}")

        items_hash = item_storage.items_hash(run_id)
        console.print(f"\n[bold]Items file SHA-256:[/bold] {items_hash}")

    # Historical Consolidated Overview across all runs
    runs = [r for r in item_storage.list_runs() if r != "consolidated"]
    if len(runs) > 1:
        consolidated_items = item_storage.read_items("consolidated", prefer_deduped=True)
        report_path = _cli.DATA_DIR / "items" / "consolidated" / "dedupe_report.json"
        if consolidated_items and report_path.exists():
            unique_items = consolidated_items
            try:
                rep_data = json.loads(report_path.read_text(encoding="utf-8"))
                total_raw = rep_data.get("input", len(unique_items))
                duplicates_count = total_raw - len(unique_items)
            except Exception:
                total_raw = len(unique_items)
                duplicates_count = 0
        else:
            from msrkit.dedupe import deduplicate

            all_items = []
            for r in runs:
                all_items.extend(item_storage.read_items(r, prefer_deduped=False))

            if all_items:
                unique_items, duplicates = deduplicate(all_items, near_threshold=0.85)
                total_raw = len(all_items)
                duplicates_count = len(duplicates)
            else:
                unique_items = []
                total_raw = 0
                duplicates_count = 0

        if unique_items:
            hist_by_source = {}
            for it in unique_items:
                hist_by_source[it.source] = hist_by_source.get(it.source, 0) + 1

            hist_table = Table(
                title=f"\nConsolidated Historical Corpus ({len(runs)} runs)",
                show_lines=True,
            )
            hist_table.add_column("Source", style="bold")
            hist_table.add_column("Unique Items", justify="right")
            hist_table.add_column("Corpus Share", justify="right")

            total_hist = len(unique_items)
            for src, count in sorted(hist_by_source.items()):
                share = (count / total_hist * 100) if total_hist > 0 else 0
                hist_table.add_row(src, str(count), f"{share:.1f}%")

            hist_table.add_section()
            hist_table.add_row(
                "[bold]Total Consolidated[/bold]",
                f"[bold green]{total_hist}[/bold green]",
                "100.0%",
            )
            console.print(hist_table)
            console.print(
                f"[dim]Historical total: {total_raw} raw items collected across runs | "
                f"{duplicates_count} duplicates removed[/dim]"
            )


@app.command()
def export(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    all_runs: bool = typer.Option(
        False, "--all", "-a", help="Consolidate and export items from all historical runs"
    ),
    fmt: str = typer.Option("jsonl", "--format", "-f", help="Output format: csv, jsonl, duckdb"),
    delimiter: str = typer.Option(
        ";",
        "--delimiter",
        "-d",
        help="Delimiter for CSV export (default ';' for Excel compatibility, or ',')",
    ),
    include_body: bool = typer.Option(False, "--include-body", help="Include body text in export"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file path"),
    raw: bool = typer.Option(False, "--raw", help="Export raw items instead of deduplicated items"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Export collected items."""
    run_id = _unwrap(run_id)
    all_runs = _unwrap(all_runs)
    fmt = _unwrap(fmt)
    delimiter = _unwrap(delimiter)
    include_body = _unwrap(include_body)
    output = _unwrap(output)
    raw = _unwrap(raw)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(_cli.DATA_DIR)

    if all_runs:
        runs = [r for r in item_storage.list_runs() if r != "consolidated"]
        if not runs:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[bold]Consolidating items from {len(runs)} historical runs...[/bold]")
        all_items = []
        for r in runs:
            all_items.extend(item_storage.read_items(r, prefer_deduped=False))
        if not raw:
            deduped_existing = item_storage.read_items("consolidated", prefer_deduped=True)
            if deduped_existing:
                items = deduped_existing
                report_path = _cli.DATA_DIR / "items" / "consolidated" / "dedupe_report.json"
                dups_len = 0
                if report_path.exists():
                    try:
                        rep_data = json.loads(report_path.read_text(encoding="utf-8"))
                        dups_len = rep_data.get("input", len(items)) - len(items)
                    except Exception:
                        pass
                console.print(
                    f"  [green]Total consolidado (deduplicado em disco):[/green] "
                    f"{len(items)} únicos "
                    f"([dim]{dups_len} duplicatas removidas[/dim])"
                )
            else:
                from msrkit.dedupe import deduplicate

                items, dups = deduplicate(all_items, near_threshold=0.85)
                console.print(
                    f"  [green]Total consolidado:[/green] {len(items)} únicos "
                    f"([dim]{len(dups)} duplicatas removidas[/dim])"
                )
        else:
            items = all_items
            console.print(f"  [green]Total consolidado bruto:[/green] {len(items)} achados")
        target_name = "consolidated"
    else:
        if not run_id:
            run_id = _get_latest_run_id()
            if not run_id:
                console.print("[red]✗ No runs found in data directory.[/red]")
                raise typer.Exit(1)
            console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")
        items = item_storage.read_items(run_id, prefer_deduped=not raw)
        target_name = run_id

    if not items:
        console.print("[yellow]No items found to export.[/yellow]")
        return

    # Enforce redistribution policy
    registry = _get_registry()
    if include_body:
        sources_in_items = {item.source for item in items}
        violating = [
            s
            for s in sorted(sources_in_items)
            if s in registry and registry[s].policy.redistribution == "metadata_only"
        ]
        if violating:
            console.print(
                f"[red]✗ Cannot export body: source(s) {', '.join(violating)} "
                f"have redistribution policy 'metadata_only'.[/red]"
            )
            raise typer.Exit(1)

    out_path = output or str(_cli.DATA_DIR / "export" / target_name / f"items.{fmt}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        if fmt == "jsonl":
            with open(out_path, "w", encoding="utf-8") as f:
                for item in items:
                    data = item.model_dump(mode="json")
                    if not include_body:
                        data.pop("body", None)
                    f.write(json.dumps(data, ensure_ascii=False) + "\n")
        elif fmt == "csv":
            import csv

            fields = [
                "id",
                "source",
                "kind",
                "url",
                "title",
                "author_handle",
                "created_at",
                "updated_at",
                "matched_terms",
                "matched_languages",
                "concepts",
                "stars",
                "votes",
                "tags",
            ]
            if include_body:
                fields.append("body")

            with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(
                    f, fieldnames=fields, delimiter=delimiter, extrasaction="ignore"
                )
                writer.writeheader()
                for item in items:
                    row = item.model_dump(mode="json")
                    row["url"] = str(item.url)
                    row["kind"] = item.kind.value
                    terms_set: set[str] = set()
                    langs_set: set[str] = set()
                    for hit in item.matched_terms:
                        t = (
                            hit.term
                            if hasattr(hit, "term")
                            else (hit.get("term", str(hit)) if isinstance(hit, dict) else str(hit))
                        )
                        if t:
                            terms_set.add(t)
                        l_val = (
                            hit.lang
                            if hasattr(hit, "lang")
                            else (hit.get("lang") if isinstance(hit, dict) else None)
                        )
                        if l_val:
                            langs_set.add(l_val)
                    row["matched_terms"] = ", ".join(sorted(terms_set))
                    row["matched_languages"] = ", ".join(sorted(langs_set))
                    row["concepts"] = ", ".join(item.concepts)
                    row["stars"] = (
                        item.engagement.stars
                        if (item.engagement and item.engagement.stars is not None)
                        else ""
                    )
                    row["votes"] = (
                        item.engagement.votes
                        if (item.engagement and item.engagement.votes is not None)
                        else ""
                    )
                    row["tags"] = (
                        ", ".join(item.tech.tags) if (item.tech and item.tech.tags) else ""
                    )
                    writer.writerow({k: row.get(k) for k in fields})
        elif fmt == "duckdb":
            from msrkit.storage import DuckDBStorage

            db_path = Path(out_path).with_suffix(".duckdb")
            db = DuckDBStorage(db_path)
            inserted = db.ingest_items(items, include_body=include_body)
            db.close()
            console.print(f"[green]✓ Inserted {inserted} items into {db_path}[/green]")
            return
        else:
            console.print(f"[red]Unknown format: {fmt}[/red]")
            raise typer.Exit(1)
    except PermissionError:
        console.print(
            f"[red]✗ Permissão negada ao salvar '{out_path}'.\n"
            "O arquivo pode estar aberto em outro aplicativo (como Excel). "
            "Feche o arquivo e tente novamente.[/red]"
        )
        raise typer.Exit(1) from None

    console.print(f"[green]✓ Exported {len(items)} items to {out_path}[/green]")


@app.command(hidden=True)
def package(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    output: str | None = typer.Option(None, "--output", "-o", help="Zip path"),
    gold: str | None = typer.Option(None, "--gold", help="Gold set YAML to include (D4)"),
    creator: list[str] = typer.Option(  # noqa: B008
        [], "--creator", help="Creator for .zenodo.json, 'Surname, Name' (repeatable)"
    ),
    keep_authors: bool = typer.Option(
        False, "--keep-authors", help="Keep public author handles (default: pseudonymize, §17)"
    ),
) -> None:
    """Reproducibility package for Zenodo: protocol, manifest, redacted corpus, reviews, reports.

    Full text only where the source allows redistribution; no raw responses.
    """
    import secrets

    from msrkit.package import build_package

    run_id = _resolve_run(_unwrap(run_id))
    proto_path_str = _unwrap(protocol)
    if not proto_path_str:
        from msrkit.provenance import load_manifest

        with contextlib.suppress(FileNotFoundError):
            proto_path_str = load_manifest(_cli.DATA_DIR, run_id).protocol_path
    if not proto_path_str and Path(DEFAULT_PROTOCOL).exists():
        proto_path_str = DEFAULT_PROTOCOL
    proto_path = Path(proto_path_str) if proto_path_str else None
    extras: list[Path] = []
    proto = _load_run_protocol(run_id, proto_path_str)
    if proto is not None and proto.gazetteer and proto_path is not None:
        extras.append(proto_path.parent / proto.gazetteer)

    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    policies = {name: str(cls.policy.redistribution) for name, cls in _get_registry().items()}
    out = Path(_unwrap(output) or _cli.DATA_DIR / "packages" / f"msrkit_{run_id}.zip")
    gold_path = Path(_unwrap(gold)) if _unwrap(gold) else None
    path, contents = build_package(
        _cli.DATA_DIR,
        run_id,
        out,
        items,
        policies,
        proto_path,
        extras,
        gold=gold_path,
        keep_authors=_unwrap(keep_authors),
        salt=secrets.token_hex(16),
        creators=_unwrap(creator) or [],
    )
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(contents.items()))
    console.print(f"[green]✓ Package written to {path}[/green] ({summary})")
    if proto_path is None or not proto_path.exists():
        console.print("[yellow]⚠ Protocol file not found: pass --protocol.[/yellow]")
    if not _unwrap(creator):
        console.print("[dim]Fill `creators` in .zenodo.json or pass --creator.[/dim]")
