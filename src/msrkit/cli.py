"""MSR-Kit CLI: command-line interface for grey literature mining.

Commands:
    msrkit sources [--md]           List adapters, availability, policies
    msrkit validate <protocol>      Validate schema + credentials (no network)
    msrkit plan <protocol>          Dry run: partitions and request budget
    msrkit run <protocol>           Execute collection
    msrkit normalize --run <id>     Reprocess from raw (no network)
    msrkit dedupe --run <id>        Deduplicate items
    msrkit stats --run <id>         Item counts, discard rates, truncations
    msrkit export --run <id>        Export to CSV/JSONL/DuckDB
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from msrkit import __version__

# Ensure UTF-8 output on Windows consoles to avoid charmap encoding errors
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(
    name="msrkit",
    help="MSR-Kit: Mining grey literature through official APIs.",
    epilog="💡 Dica: Digite 'msrkit menu' para navegar de forma interativa com menu visual.",
    no_args_is_help=True,
)
console = Console()

# Default data directory and protocol
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path("data") if Path("data").exists() else (PROJECT_ROOT / "data")
DEFAULT_PROTOCOL = (
    "protocols/v0_rag_agents_testing.yaml"
    if Path("protocols/v0_rag_agents_testing.yaml").exists()
    else str(PROJECT_ROOT / "protocols" / "v0_rag_agents_testing.yaml")
)


def _get_latest_run_id() -> str | None:
    """Find the most recent run ID in DATA_DIR/runs or DATA_DIR/items."""
    runs_dir = DATA_DIR / "runs"
    if runs_dir.exists():
        subdirs = [p for p in runs_dir.iterdir() if p.is_dir()]
        if subdirs:
            return max(subdirs, key=lambda p: p.stat().st_mtime).name

    items_dir = DATA_DIR / "items"
    if items_dir.exists():
        subdirs = [p for p in items_dir.iterdir() if p.is_dir()]
        if subdirs:
            return max(subdirs, key=lambda p: p.stat().st_mtime).name

    return None


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _get_registry() -> dict[str, type]:
    """Import and return the adapter registry."""
    from msrkit.registry import all_adapters, discover_adapters

    discover_adapters()
    return all_adapters()


def _unwrap(val: object) -> Any:
    """Unwrap Typer default parameter if called directly from Python code."""
    from typer.models import ArgumentInfo, OptionInfo

    if isinstance(val, (OptionInfo, ArgumentInfo)):
        return val.default
    return val


@app.command()
def sources(
    md: bool = typer.Option(False, "--md", help="Output in Markdown format"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List all adapters with availability, credentials, and policies."""
    md = _unwrap(md)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)
    registry = _get_registry()

    if md:
        _sources_md(registry)
    else:
        _sources_table(registry)


def _sources_table(registry: dict[str, type]) -> None:
    """Display sources as a Rich table."""
    table = Table(title="MSR-Kit Sources", show_lines=True)
    table.add_column("Source", style="bold")
    table.add_column("Status")
    table.add_column("Reason")
    table.add_column("Auth Vars")
    table.add_column("Rate Limit")
    table.add_column("Search")
    table.add_column("Date Filter")

    for name in sorted(registry.keys()):
        adapter_cls = registry[name]
        adapter = adapter_cls()
        avail = adapter.available()
        policy = adapter_cls.policy

        status_style = {
            "OK": "green",
            "DEGRADED": "yellow",
            "UNSUPPORTED": "red",
        }.get(avail.status, "white")

        missing_info = ""
        if avail.missing_env:
            missing_info = f"\n⚠ Missing: {', '.join(avail.missing_env)}"

        rate_str = f"{policy.rate_limit.requests}/{policy.rate_limit.per_seconds}s"
        if policy.rate_limit.daily_cap:
            rate_str += f"\nDaily: {policy.rate_limit.daily_cap}"

        table.add_row(
            name,
            f"[{status_style}]{avail.status}[/{status_style}]",
            avail.reason[:80] + ("..." if len(avail.reason) > 80 else ""),
            "\n".join(policy.auth_env_vars) + missing_info if policy.auth_env_vars else "none",
            rate_str,
            "✓" if policy.supports_full_text_search else "✗",
            "✓" if policy.supports_date_filter else "✗",
        )

    console.print(table)


def _sources_md(registry: dict[str, type]) -> None:
    """Output sources in Markdown format for docs/sources.md."""
    lines = ["# MSR-Kit Sources\n"]
    lines.append(f"Generated at: {datetime.now(UTC).isoformat()}\n")

    for name in sorted(registry.keys()):
        adapter_cls = registry[name]
        adapter = adapter_cls()
        avail = adapter.available()
        policy = adapter_cls.policy

        lines.append(f"## {name}\n")
        lines.append(f"- **Status:** {avail.status}")
        lines.append(f"- **Reason:** {avail.reason}")
        lines.append(f"- **Version:** {adapter_cls.version}")
        lines.append(f"- **Auth required:** {'Yes' if policy.requires_auth else 'No'}")
        if policy.auth_env_vars:
            lines.append(f"- **Auth variables:** {', '.join(policy.auth_env_vars)}")
        lines.append(
            f"- **Rate limit:** {policy.rate_limit.requests} req / {policy.rate_limit.per_seconds}s"
        )
        if policy.rate_limit.daily_cap:
            lines.append(f"- **Daily cap:** {policy.rate_limit.daily_cap}")
        fts = "Yes" if policy.supports_full_text_search else "No"
        lines.append(f"- **Full-text search:** {fts}")
        lines.append(f"- **Date filter:** {'Yes' if policy.supports_date_filter else 'No'}")
        lines.append(f"- **Redistribution:** {policy.redistribution}")
        lines.append(f"- **ToS:** {policy.tos_url}")
        lines.append(f"- **Docs:** {policy.docs_url}")
        if policy.notes:
            lines.append(f"- **Notes:** {policy.notes}")
        lines.append("")

    output = "\n".join(lines)
    console.print(output)


@app.command()
def validate(
    protocol: str = typer.Argument(DEFAULT_PROTOCOL, help="Path to protocol YAML file"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Validate protocol schema and check credential availability (no network)."""
    protocol = _unwrap(protocol)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.config import load_protocol

    try:
        config = load_protocol(protocol)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]✗ Validation failed:[/red] {e}")
        raise typer.Exit(1) from None

    console.print(f"[green]✓[/green] Protocol '{config.name}' v{config.version} is valid")
    console.print(f"  Window: {config.window.since} → {config.window.until}")
    console.print(f"  Terms: {len(config.terms)}")
    console.print(f"  Enabled sources: {', '.join(config.enabled_sources())}")

    # Check credentials for enabled sources
    registry = _get_registry()
    all_ok = True
    for source_name in config.enabled_sources():
        if source_name not in registry:
            console.print(f"  [red]✗ Unknown source: {source_name}[/red]")
            all_ok = False
            continue
        adapter = registry[source_name]()
        avail = adapter.available()
        status_color = {"OK": "green", "DEGRADED": "yellow", "UNSUPPORTED": "red"}.get(
            avail.status, "white"
        )
        console.print(
            f"  [{status_color}]{avail.status}[/{status_color}] {source_name}: {avail.reason}"
        )
        if avail.status == "UNSUPPORTED":
            all_ok = False

    if not all_ok:
        console.print("\n[yellow]⚠ Some sources have issues. Review above.[/yellow]")


@app.command()
def plan(
    protocol: str = typer.Argument(DEFAULT_PROTOCOL, help="Path to protocol YAML file"),
    source: str | None = typer.Option(
        None, "--source", "-s", help="Filter plan to a single source"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Dry run: show partitions and estimated request budget per source."""
    protocol = _unwrap(protocol)
    source = _unwrap(source)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.config import load_protocol

    try:
        config = load_protocol(protocol)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise typer.Exit(1) from None

    registry = _get_registry()

    if source:
        if source not in config.sources:
            console.print(
                f"[red]✗ Source '{source}' is not configured in protocol '{config.name}'[/red]"
            )
            raise typer.Exit(1)
        if not config.sources[source].enabled:
            console.print(
                f"[yellow]Note: Source '{source}' is disabled in protocol, "
                "enabling for this plan.[/yellow]"
            )
            config.sources[source].enabled = True
        sources_to_plan = [source]
    else:
        sources_to_plan = config.enabled_sources()

    table = Table(title="Collection Plan (Dry Run)", show_lines=True)
    table.add_column("Source", style="bold")
    table.add_column("Status")
    table.add_column("Queries")
    table.add_column("Max Results/Query")
    table.add_column("Max Pages")
    table.add_column("Est. Requests")

    total_est_requests = 0

    for source_name in sources_to_plan:
        if source_name not in registry:
            table.add_row(source_name, "[red]UNKNOWN[/red]", "-", "-", "-", "-")
            continue

        adapter_cls = registry[source_name]
        adapter = adapter_cls()
        avail = adapter.available()

        if avail.status == "UNSUPPORTED":
            table.add_row(
                source_name,
                f"[red]{avail.status}[/red]",
                "-",
                "-",
                "-",
                "0",
            )
            continue

        queries = config.build_queries(source_name)
        policy = adapter_cls.policy

        max_pages = policy.max_pages or "∞"
        max_results = policy.max_results_per_query or "∞"

        # Conservative request estimate
        est_per_query = (policy.max_pages or 10) * len(queries)
        est_requests = min(est_per_query, config.limits.max_requests_per_source)
        total_est_requests += est_requests

        status_color = {"OK": "green", "DEGRADED": "yellow"}.get(avail.status, "white")

        table.add_row(
            source_name,
            f"[{status_color}]{avail.status}[/{status_color}]",
            str(len(queries)),
            str(max_results),
            str(max_pages),
            str(est_requests),
        )

    console.print(table)
    console.print(f"\n[bold]Total estimated requests:[/bold] {total_est_requests}")
    console.print(
        f"[bold]Max requests/source limit:[/bold] {config.limits.max_requests_per_source}"
    )
    console.print("\n[dim]This is a dry run. No data was collected.[/dim]")


@app.command()
def run(
    protocol: str = typer.Argument(DEFAULT_PROTOCOL, help="Path to protocol YAML file"),
    source: str | None = typer.Option(
        None, "--source", "-s", help="Execute collection for a single source only"
    ),
    limit: int | None = typer.Option(
        None, "--limit", "-l", help="Override max items to collect for this run"
    ),
    resume: str | None = typer.Option(None, "--resume", help="Resume a previous run by ID"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Execute the collection protocol."""
    protocol = _unwrap(protocol)
    source = _unwrap(source)
    limit = _unwrap(limit)
    resume = _unwrap(resume)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.config import load_protocol, protocol_sha256
    from msrkit.models import (
        Manifest,
        QueryManifestEntry,
        SourceManifestEntry,
        SourceUnsupportedError,
    )
    from msrkit.provenance import generate_run_id, save_manifest
    from msrkit.storage import ItemStorage, RawStorage

    try:
        config = load_protocol(protocol)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise typer.Exit(1) from None

    registry = _get_registry()
    run_id = resume or generate_run_id()
    proto_hash = protocol_sha256(protocol)

    console.print(f"[bold]Run ID:[/bold] {run_id}")
    console.print(f"[bold]Protocol:[/bold] {config.name}")

    raw_storage = RawStorage(DATA_DIR)
    item_storage = ItemStorage(DATA_DIR)

    manifest = Manifest(
        run_id=run_id,
        msrkit_version=__version__,
        protocol_path=protocol,
        protocol_sha256=proto_hash,
        started_at=datetime.now(UTC),
    )

    if source:
        if source not in config.sources:
            console.print(
                f"[red]✗ Source '{source}' is not configured in protocol '{config.name}'[/red]"
            )
            raise typer.Exit(1)
        if not config.sources[source].enabled:
            console.print(
                f"[yellow]Note: Source '{source}' is disabled in protocol, "
                "enabling for this run.[/yellow]"
            )
            config.sources[source].enabled = True
        sources_to_run = [source]
    else:
        sources_to_run = sorted(config.sources.keys())

    if limit is not None:
        config.limits.max_items_per_source = limit

    for source_name in sources_to_run:
        src_cfg = config.sources[source_name]
        console.print(f"\n{'=' * 60}")
        console.print(f"[bold]Source: {source_name}[/bold]")

        if source_name not in registry:
            console.print(f"  [red]Unknown adapter: {source_name}[/red]")
            continue

        adapter_cls = registry[source_name]
        adapter = adapter_cls()
        avail = adapter.available()

        source_entry = SourceManifestEntry(
            name=source_name,
            adapter_version=adapter_cls.version,
            availability=avail,
        )

        if not src_cfg.enabled:
            console.print("  [dim]Disabled in protocol[/dim]")
            manifest.sources.append(source_entry)
            continue

        if avail.status == "UNSUPPORTED":
            console.print(f"  [red]{avail.status}: {avail.reason}[/red]")
            manifest.sources.append(source_entry)
            continue

        console.print(f"  Status: [{avail.status}] {avail.reason}")

        queries = config.build_queries(source_name)
        if limit is not None:
            for q in queries:
                q.limit = limit
        console.print(f"  Queries to execute: {len(queries)}")

        for qi, query in enumerate(queries, 1):
            console.print(f"  Query {qi}/{len(queries)}: {query.terms[:3]}...")
            items_collected = 0
            raw_items_count = 0
            req_before = getattr(adapter, "request_count", 0)
            response_hashes: list[str] = []

            try:
                for raw_item in adapter.search(query):
                    # Store raw
                    import hashlib

                    partition_hash = hashlib.sha256(
                        f"{query.source}:{query.terms}:{qi}".encode()
                    ).hexdigest()[:12]

                    raw_ref = raw_storage.save_raw(raw_item, run_id, partition_hash)
                    raw_items_count += 1

                    # Normalize
                    try:
                        item = adapter.normalize(raw_item, terms=config.terms)
                        # Update provenance
                        item.provenance.run_id = run_id
                        item.provenance.query_string = " ".join(query.terms)
                        item.provenance.partition = f"q{qi}"
                        item.provenance.raw_ref = raw_ref

                        # Post-normalization term matching
                        if not item.matched_terms and config.terms:
                            from msrkit.keywords import match_terms

                            item.matched_terms = match_terms(
                                config.terms,
                                title=item.title,
                                body=item.body,
                                tags=item.tech.tags,
                                path=item.tech.path,
                            )

                        item_storage.save_items([item], run_id)
                        items_collected += 1
                    except Exception as e:
                        logging.getLogger(__name__).warning(
                            "Normalization error for %s/%s: %s",
                            source_name,
                            raw_item.native_id,
                            e,
                        )

                    if items_collected >= config.limits.max_items_per_source:
                        break

            except SourceUnsupportedError as e:
                console.print(f"  [red]Unsupported: {e}[/red]")
            except Exception as e:
                console.print(f"  [yellow]Error during collection: {e}[/yellow]")
                logging.getLogger(__name__).exception("Collection error")

            requests_made = max(getattr(adapter, "request_count", 0) - req_before, 0)
            query_entry = QueryManifestEntry(
                query_string=" ".join(query.terms),
                partitions=1,
                requests=requests_made,
                items=items_collected,
                truncated=False,
                response_sha256=response_hashes,
            )
            source_entry.queries.append(query_entry)
            console.print(
                f"    Collected: {items_collected} items "
                f"({raw_items_count} raw, {requests_made} requests)"
            )

        adapter.close()
        manifest.sources.append(source_entry)

    manifest.finished_at = datetime.now(UTC)
    manifest_path = save_manifest(manifest, DATA_DIR)
    console.print(f"\n[green]✓ Run complete. Manifest: {manifest_path}[/green]")


@app.command()
def normalize(
    run_id: str | None = typer.Option(
        None, "--run", help="Run ID to reprocess (defaults to latest)"
    ),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Path to protocol YAML"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Reprocess items from raw data (no network requests)."""
    run_id = _unwrap(run_id)
    protocol = _unwrap(protocol)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")

    console.print(f"[bold]Normalizing run:[/bold] {run_id}")

    from msrkit.provenance import load_manifest
    from msrkit.storage import ItemStorage, RawStorage

    try:
        manifest = load_manifest(DATA_DIR, run_id)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None

    # Load protocol terms if available
    terms: list[str] = []
    protocol_path = protocol or manifest.protocol_path
    if protocol_path and Path(protocol_path).exists():
        try:
            from msrkit.config import load_protocol

            proto = load_protocol(protocol_path)
            terms = proto.terms
        except Exception as e:
            logging.getLogger(__name__).warning("Could not load protocol for terms: %s", e)

    registry = _get_registry()
    raw_storage = RawStorage(DATA_DIR)
    item_storage = ItemStorage(DATA_DIR)

    # Reset existing items file for this run before reprocessing to prevent duplication
    items_dir = DATA_DIR / "items" / run_id
    items_file = items_dir / "items.jsonl"
    if items_file.exists():
        items_file.unlink()
    deduped_file = items_dir / "items_deduped.jsonl"
    if deduped_file.exists():
        deduped_file.unlink()

    total_items = 0
    for source_entry in manifest.sources:
        if source_entry.availability.status == "UNSUPPORTED":
            continue
        if source_entry.name not in registry:
            continue

        adapter_cls = registry[source_entry.name]
        adapter = adapter_cls()

        partitions = raw_storage.list_partitions(source_entry.name, run_id)
        for part_hash in partitions:
            raw_items = raw_storage.read_raw(source_entry.name, run_id, part_hash)
            normalized = []
            for raw in raw_items:
                try:
                    item = adapter.normalize(raw, terms=terms)
                    item.provenance.run_id = run_id
                    if not item.matched_terms and terms:
                        from msrkit.keywords import match_terms

                        item.matched_terms = match_terms(
                            terms,
                            title=item.title,
                            body=item.body,
                            tags=item.tech.tags,
                            path=item.tech.path,
                        )
                    normalized.append(item)
                except Exception as e:
                    logging.getLogger(__name__).warning("Normalization error: %s", e)
            if normalized:
                item_storage.save_items(normalized, run_id)
                total_items += len(normalized)

    console.print(f"[green]✓ Normalized {total_items} items from raw data[/green]")


@app.command(name="dedupe")
def dedupe(
    run_id: str | None = typer.Option(
        None, "--run", help="Run ID to deduplicate (defaults to latest)"
    ),
    all_runs: bool = typer.Option(
        False, "--all", "-a", help="Consolidate and deduplicate across all historical runs"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Deduplicate items for a run or across all runs."""
    run_id = _unwrap(run_id)
    all_runs = _unwrap(all_runs)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.dedupe import deduplicate
    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(DATA_DIR)

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
        target_dir = DATA_DIR / "items" / "consolidated"
    else:
        if not run_id:
            run_id = _get_latest_run_id()
            if not run_id:
                console.print("[red]✗ No runs found in data directory.[/red]")
                raise typer.Exit(1)
            console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")
        console.print(f"[bold]Deduplicating run:[/bold] {run_id}")
        items = item_storage.read_items(run_id, prefer_deduped=False)
        target_dir = DATA_DIR / "items" / run_id

    if not items:
        console.print("[yellow]No items found.[/yellow]")
        return

    unique, duplicates = deduplicate(items)
    console.print(f"  Input: {len(items)}")
    console.print(f"  Unique: {len(unique)}")
    console.print(f"  Duplicates removed: {len(duplicates)}")

    # Save deduplicated items
    target_dir.mkdir(parents=True, exist_ok=True)
    deduped_path = target_dir / "items_deduped.jsonl"
    with open(deduped_path, "w", encoding="utf-8") as f:
        for item in unique:
            f.write(item.model_dump_json() + "\n")

    console.print(f"[green]✓ Saved to {deduped_path}[/green]")


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

    item_storage = ItemStorage(DATA_DIR)

    if all_runs:
        runs = item_storage.list_runs()
        if not runs:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)

        from msrkit.dedupe import deduplicate

        all_items = []
        for r in runs:
            all_items.extend(item_storage.read_items(r, prefer_deduped=True))

        if not all_items:
            console.print("[yellow]No items found across runs.[/yellow]")
            return

        unique_items, duplicates = deduplicate(all_items)
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
            f"\n[dim]Total across history: {len(all_items)} raw items collected | "
            f"{len(duplicates)} duplicates removed[/dim]"
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
        manifest = load_manifest(DATA_DIR, run_id)
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
        total_items += items
        total_requests += requests

        table.add_row(
            source.name,
            f"[{status_color}]{source.availability.status}[/{status_color}]",
            str(len(source.queries)),
            str(items),
            str(requests),
            "[red]Yes[/red]" if truncated else "[green]No[/green]",
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
    runs = item_storage.list_runs()
    if len(runs) > 1:
        from msrkit.dedupe import deduplicate

        all_items = []
        for r in runs:
            all_items.extend(item_storage.read_items(r, prefer_deduped=True))

        if all_items:
            unique_items, duplicates = deduplicate(all_items)
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
                f"[dim]Historical total: {len(all_items)} raw items collected across runs | "
                f"{len(duplicates)} duplicates removed[/dim]"
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Export collected items."""
    run_id = _unwrap(run_id)
    all_runs = _unwrap(all_runs)
    fmt = _unwrap(fmt)
    delimiter = _unwrap(delimiter)
    include_body = _unwrap(include_body)
    output = _unwrap(output)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(DATA_DIR)

    if all_runs:
        runs = item_storage.list_runs()
        if not runs:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[bold]Consolidating items from {len(runs)} historical runs...[/bold]")
        all_items = []
        for r in runs:
            all_items.extend(item_storage.read_items(r, prefer_deduped=True))
        from msrkit.dedupe import deduplicate

        items, dups = deduplicate(all_items)
        console.print(
            f"  [green]Total consolidado:[/green] {len(items)} únicos "
            f"([dim]{len(dups)} duplicatas removidas[/dim])"
        )
        target_name = "consolidated"
    else:
        if not run_id:
            run_id = _get_latest_run_id()
            if not run_id:
                console.print("[red]✗ No runs found in data directory.[/red]")
                raise typer.Exit(1)
            console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")
        items = item_storage.read_items(run_id, prefer_deduped=True)
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

    out_path = output or str(DATA_DIR / "export" / target_name / f"items.{fmt}")
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
                    row["matched_terms"] = (
                        ", ".join(sorted(set(hit.term for hit in item.matched_terms)))
                        if item.matched_terms
                        else ""
                    )
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


def _toggle_source_in_protocol(protocol_path: str, source_name: str, new_state: bool) -> bool:
    """Toggle a source's enabled state in the protocol YAML file while preserving comments."""
    import re

    p = Path(protocol_path)
    if not p.exists():
        return False
    try:
        content = p.read_text(encoding="utf-8")
        pattern = (
            rf"(^\s*{re.escape(source_name)}:\s*\n"
            r"(?:[ \t]*#[^\n]*\n)*[ \t]*enabled:\s*)(true|false)"
        )
        match = re.search(pattern, content, flags=re.MULTILINE)
        if match:
            new_val = "true" if new_state else "false"
            content = re.sub(pattern, rf"\g<1>{new_val}", content, count=1, flags=re.MULTILINE)
            p.write_text(content, encoding="utf-8")
            return True

        import yaml

        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if "sources" in data and source_name in data["sources"]:
            data["sources"][source_name]["enabled"] = new_state
            with p.open("w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            return True
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to update protocol file: %s", e)
    return False


def _manage_sources_menu(protocol_path: str) -> None:  # pragma: no cover
    """Interactive screen to toggle sources on/off based on availability."""
    from rich.prompt import Prompt

    from msrkit.config import load_protocol

    while True:
        try:
            config = load_protocol(protocol_path)
        except Exception as e:
            console.print(f"[red]Erro ao carregar protocolo: {e}[/red]")
            return

        registry = _get_registry()
        all_sources = sorted(set(list(config.sources.keys()) + list(registry.keys())))

        table = Table(
            title="[bold cyan]Gerenciamento de Fontes de Pesquisa[/bold cyan]",
            border_style="cyan",
            header_style="bold magenta",
        )
        table.add_column("#", style="bold cyan", width=4)
        table.add_column("Fonte", style="bold")
        table.add_column("No Protocolo", justify="center")
        table.add_column("Disponibilidade API", justify="center")
        table.add_column("Status / Requisitos")

        source_list: list[tuple[str, bool]] = []
        for idx, s_name in enumerate(all_sources, 1):
            is_enabled = config.sources[s_name].enabled if s_name in config.sources else False
            source_list.append((s_name, is_enabled))

            if s_name in registry:
                adapter = registry[s_name]()
                avail = adapter.available()
                if avail.status == "OK":
                    avail_str = "[bold green]✓ OK[/bold green]"
                    notes = "[green]Pronta (Pública/Sem chaves)[/green]"
                elif avail.status == "DEGRADED":
                    avail_str = "[bold yellow]⚠ PARCIAL[/bold yellow]"
                    notes = f"[yellow]{avail.reason}[/yellow]"
                else:
                    avail_str = "[bold red]✗ INDISPONÍVEL[/bold red]"
                    notes = f"[red]{avail.reason}[/red]"
            else:
                avail_str = "[dim]DESCONHECIDA[/dim]"
                notes = "-"

            status_str = (
                "[bold green]● ATIVADA[/bold green]" if is_enabled else "[dim]○ Desativada[/dim]"
            )
            table.add_row(str(idx), s_name, status_str, avail_str, notes)

        console.print()
        console.print(table)
        console.print("[dim]• Digite o número da fonte para alternar (Ativar ⇄ Desativar).[/dim]")
        console.print(
            "[dim]• Fontes com '✓ OK' podem ser ativadas e usadas imediatamente sem chaves.[/dim]"
        )

        choice = Prompt.ask(
            "\n[bold green]Digite o número da fonte para alternar (ou 0 para voltar)[/bold green]",
            default="0",
        )
        if choice == "0":
            break

        try:
            chosen_idx = int(choice)
            if 1 <= chosen_idx <= len(source_list):
                target_source, curr_state = source_list[chosen_idx - 1]
                new_state = not curr_state
                success = _toggle_source_in_protocol(protocol_path, target_source, new_state)
                if success:
                    word = (
                        "[bold green]ativada[/bold green]"
                        if new_state
                        else "[yellow]desativada[/yellow]"
                    )
                    console.print(
                        f"\n[green]✓ Fonte[/green] [bold]{target_source}[/bold] {word} "
                        "[green]com sucesso no protocolo![/green]"
                    )
                else:
                    console.print(
                        f"\n[red]✗ Não foi possível alterar a fonte '{target_source}'.[/red]"
                    )
            else:
                console.print("[red]Número inválido![/red]")
        except ValueError:
            console.print("[red]Entrada inválida! Digite um número.[/red]")


def _interactive_mining_menu(protocol_path: str) -> None:  # pragma: no cover
    """Interactive mining execution with custom quantity and source selection."""
    from rich.prompt import Prompt

    from msrkit.config import load_protocol

    try:
        config = load_protocol(protocol_path)
    except Exception as e:
        console.print(f"[red]Erro ao carregar protocolo: {e}[/red]")
        return

    registry = _get_registry()

    console.print("\n[bold cyan]─── 1. Escolha a Fonte para Minerar ───[/bold cyan]")
    console.print("  [bold cyan]0[/bold cyan] - [bold]Todas as fontes ativadas no protocolo[/bold]")

    sources_options: list[str] = []
    for idx, (s_name, s_cfg) in enumerate(sorted(config.sources.items()), 1):
        sources_options.append(s_name)
        status_label = "[green]● Ativada[/green]" if s_cfg.enabled else "[dim]○ Desativada[/dim]"
        avail_label = ""
        if s_name in registry:
            avail = registry[s_name]().available()
            if avail.status == "OK":
                avail_label = "[bold green][API: OK][/bold green]"
            elif avail.status == "DEGRADED":
                avail_label = "[yellow][API: Parcial/Faltam Chaves][/yellow]"
            else:
                avail_label = "[red][API: Não Suportada][/red]"

        console.print(f"  [cyan]{idx}[/cyan] - {s_name:<14} {status_label:<22} {avail_label}")

    src_choice = Prompt.ask(
        "\n[bold green]Escolha o número da fonte desejada[/bold green]",
        default="0",
    )

    selected_source: str | None = None
    if src_choice != "0":
        try:
            s_idx = int(src_choice)
            if 1 <= s_idx <= len(sources_options):
                selected_source = sources_options[s_idx - 1]
            else:
                console.print(
                    "[yellow]Opção inválida, minerando todas as fontes ativadas.[/yellow]"
                )
        except ValueError:
            console.print("[yellow]Entrada inválida, minerando todas as fontes ativadas.[/yellow]")

    console.print("\n[bold cyan]─── 2. Escolha a Quantidade de Itens para Minerar ───[/bold cyan]")
    console.print("  [cyan]1[/cyan] - ⚡ Teste Rápido (5 itens)")
    console.print("  [cyan]2[/cyan] - 🔍 Amostra Pequena (20 itens)")
    console.print("  [cyan]3[/cyan] - 📊 Amostra Média (50 itens)")
    console.print("  [cyan]4[/cyan] - 🚀 Coleta Ampla (200 itens)")
    console.print("  [cyan]5[/cyan] - ♾️  Máximo do Protocolo (sem limite rápido)")
    console.print("  [cyan]6[/cyan] - ✏️  Digitar quantidade personalizada")

    qty_choice = Prompt.ask("\n[bold green]Escolha a opção de quantidade[/bold green]", default="2")

    limit: int | None = 20
    if qty_choice == "1":
        limit = 5
    elif qty_choice == "2":
        limit = 20
    elif qty_choice == "3":
        limit = 50
    elif qty_choice == "4":
        limit = 200
    elif qty_choice == "5":
        limit = None
    elif qty_choice == "6":
        custom = Prompt.ask(
            "[bold green]Digite a quantidade exata desejada[/bold green]",
            default="20",
        )
        try:
            limit = max(1, int(custom))
        except ValueError:
            limit = 20
    else:
        limit = 20

    src_label = selected_source if selected_source else "Todas as fontes ativadas"
    limit_label = str(limit) if limit is not None else "Ilimitado (máximo do protocolo)"
    console.print(
        f"\n[bold green]Iniciando Mineração:[/bold green] "
        f"Fonte: [bold cyan]{src_label}[/bold cyan] | "
        f"Limite: [bold cyan]{limit_label}[/bold cyan]\n"
    )

    try:
        run(protocol=protocol_path, source=selected_source, limit=limit, verbose=False)
    except Exception as e:
        console.print(f"[red]Erro durante a coleta: {e}[/red]")
        return

    console.print()
    auto_export = Prompt.ask(
        "[bold cyan]Deseja desduplicar e exportar para CSV agora mesmo?[/bold cyan] [S/n]",
        default="S",
    )
    if auto_export.strip().lower() in ("s", "sim", "y", "yes", ""):
        console.print("\n[bold]1. Desduplicando itens coletados...[/bold]")
        dedupe(run_id=None, verbose=False)
        console.print("\n[bold]2. Exportando para CSV...[/bold]")
        export(
            run_id=None,
            fmt="csv",
            include_body=False,
            output="data/resultados.csv",
            verbose=False,
        )
        console.print("\n[bold green]✓ Processamento concluído com sucesso![/bold green]")
        console.print("[dim]Planilha salva em: data/resultados.csv[/dim]")


@app.command(name="menu")
def menu() -> None:  # pragma: no cover
    """Interactive terminal menu to navigate MSR-Kit easily."""
    from rich.panel import Panel
    from rich.prompt import Prompt

    while True:
        console.print()
        console.print(
            Panel.fit(
                f"[bold cyan]MSR-Kit[/bold cyan] [dim]v{__version__}[/dim]\n"
                "[italic]Mining grey literature through official APIs[/italic]",
                border_style="cyan",
            )
        )
        console.print("\n[bold]Escolha uma ação:[/bold]")
        console.print(
            "  [bold cyan]1[/bold cyan] - [bold]🎯 Iniciar Mineração[/bold] "
            "([dim]escolher fonte e quantidade flexível[/dim])"
        )
        console.print(
            "  [bold cyan]2[/bold cyan] - [bold]⚙️  Gerenciar Fontes[/bold] "
            "([dim]ativar/desativar com base na disponibilidade[/dim])"
        )
        console.print(
            "  [cyan]3[/cyan] - Status detalhado das fontes e políticas ([dim]sources[/dim])"
        )
        console.print("  [cyan]4[/cyan] - Simulação de planejamento / Dry-Run ([dim]plan[/dim])")
        console.print("  [cyan]5[/cyan] - Desduplicar última coleta ([dim]dedupe[/dim])")
        console.print("  [cyan]6[/cyan] - Exportar última coleta em CSV ([dim]export -f csv[/dim])")
        console.print("  [cyan]7[/cyan] - Estatísticas da última coleta ([dim]stats[/dim])")
        console.print("  [cyan]8[/cyan] - Validar arquivo de protocolo ([dim]validate[/dim])")
        console.print("  [cyan]0[/cyan] - Sair")

        choice = Prompt.ask("\n[bold green]Digite o número da opção[/bold green]", default="0")

        if choice == "0":
            console.print("[dim]Encerrado.[/dim]")
            break
        if choice == "1":
            _interactive_mining_menu(DEFAULT_PROTOCOL)
        elif choice == "2":
            _manage_sources_menu(DEFAULT_PROTOCOL)
        elif choice == "3":
            sources(md=False, verbose=False)
        elif choice == "4":
            plan(protocol=DEFAULT_PROTOCOL, source=None, verbose=False)
        elif choice == "5":
            which = Prompt.ask(
                "Desduplicar [1] Apenas a última coleta ou [2] Todas as coletas históricas?",
                default="1",
            )
            dedupe(run_id=None, all_runs=(which == "2"), verbose=False)
        elif choice == "6":
            which = Prompt.ask(
                "Exportar [1] Apenas a última coleta ou [2] Consolidado de todas as coletas?",
                default="1",
            )
            out_file = "data/resultados.csv" if which == "1" else "data/resultados_consolidados.csv"
            export(
                run_id=None,
                all_runs=(which == "2"),
                fmt="csv",
                include_body=False,
                output=out_file,
                verbose=False,
            )
        elif choice == "7":
            which = Prompt.ask(
                "Estatísticas de [1] Última coleta + Panorama histórico "
                "ou [2] Apenas corpus consolidado?",
                default="1",
            )
            stats(run_id=None, all_runs=(which == "2"), verbose=False)
        elif choice == "8":
            validate(protocol=DEFAULT_PROTOCOL, verbose=False)
        else:
            console.print("[red]Opção inválida![/red]")

        Prompt.ask("\n[dim]Pressione Enter para continuar...[/dim]")


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"msrkit {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """MSR-Kit: Mining grey literature through official APIs."""


if __name__ == "__main__":
    app()
