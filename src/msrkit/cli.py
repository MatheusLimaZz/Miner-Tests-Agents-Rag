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
    no_args_is_help=True,
)
console = Console()

# Default data directory and protocol
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = (
    Path("data")
    if Path("data").exists()
    else (PROJECT_ROOT / "data")
)
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


@app.command()
def sources(
    md: bool = typer.Option(False, "--md", help="Output in Markdown format"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List all adapters with availability, credentials, and policies."""
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
        fts = 'Yes' if policy.supports_full_text_search else 'No'
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
                "-", "-", "-", "0",
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
        console.print(f"\n{'='*60}")
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
            requests_made = 0
            response_hashes: list[str] = []

            try:
                for raw_item in adapter.search(query):
                    # Store raw
                    import hashlib

                    partition_hash = hashlib.sha256(
                        f"{query.source}:{query.terms}:{qi}".encode()
                    ).hexdigest()[:12]

                    raw_ref = raw_storage.save_raw(raw_item, run_id, partition_hash)
                    requests_made += 1

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

            query_entry = QueryManifestEntry(
                query_string=" ".join(query.terms),
                partitions=1,
                requests=requests_made,
                items=items_collected,
                truncated=False,
                response_sha256=response_hashes,
            )
            source_entry.queries.append(query_entry)
            console.print(f"    Collected: {items_collected} items ({requests_made} raw)")

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
                    logging.getLogger(__name__).warning(
                        "Normalization error: %s", e
                    )
            if normalized:
                item_storage.save_items(normalized, run_id)
                total_items += len(normalized)

    console.print(f"[green]✓ Normalized {total_items} items from raw data[/green]")


@app.command(name="dedupe")
def dedupe(
    run_id: str | None = typer.Option(
        None, "--run", help="Run ID to deduplicate (defaults to latest)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Deduplicate items for a run."""
    _setup_logging(verbose)

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")

    console.print(f"[bold]Deduplicating run:[/bold] {run_id}")

    from msrkit.dedupe import deduplicate
    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(DATA_DIR)
    items = item_storage.read_items(run_id)

    if not items:
        console.print("[yellow]No items found for this run.[/yellow]")
        return

    unique, duplicates = deduplicate(items)
    console.print(f"  Input: {len(items)}")
    console.print(f"  Unique: {len(unique)}")
    console.print(f"  Duplicates removed: {len(duplicates)}")

    # Save deduplicated items
    items_dir = DATA_DIR / "items" / run_id
    items_dir.mkdir(parents=True, exist_ok=True)
    deduped_path = items_dir / "items_deduped.jsonl"
    with open(deduped_path, "w", encoding="utf-8") as f:
        for item in unique:
            f.write(item.model_dump_json() + "\n")

    console.print(f"[green]✓ Saved to {deduped_path}[/green]")


@app.command()
def stats(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show collection statistics for a run."""
    _setup_logging(verbose)

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")

    from msrkit.provenance import load_manifest
    from msrkit.storage import ItemStorage

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

    # Check for items file
    item_storage = ItemStorage(DATA_DIR)
    items_list = item_storage.read_items(run_id)
    if items_list:
        # Count by source
        by_source: dict[str, int] = {}
        for item in items_list:
            by_source[item.source] = by_source.get(item.source, 0) + 1

        console.print("\n[bold]Items on disk by source:[/bold]")
        for src, count in sorted(by_source.items()):
            console.print(f"  {src}: {count}")

        items_hash = item_storage.items_hash(run_id)
        console.print(f"\n[bold]Items file SHA-256:[/bold] {items_hash}")


@app.command()
def export(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    fmt: str = typer.Option("jsonl", "--format", "-f", help="Output format: csv, jsonl, duckdb"),
    include_body: bool = typer.Option(False, "--include-body", help="Include body text in export"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Export collected items."""
    _setup_logging(verbose)

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")

    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(DATA_DIR)
    items = item_storage.read_items(run_id)

    if not items:
        console.print("[yellow]No items found for this run.[/yellow]")
        return

    # Enforce redistribution policy
    registry = _get_registry()
    if include_body:
        sources_in_items = {item.source for item in items}
        violating = [
            s for s in sorted(sources_in_items)
            if s in registry and registry[s].policy.redistribution == "metadata_only"
        ]
        if violating:
            console.print(
                f"[red]✗ Cannot export body: source(s) {', '.join(violating)} "
                f"have redistribution policy 'metadata_only'.[/red]"
            )
            raise typer.Exit(1)

    out_path = output or str(DATA_DIR / "export" / run_id / f"items.{fmt}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

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
            "id", "source", "kind", "url", "title", "author_handle",
            "created_at", "updated_at",
        ]
        if include_body:
            fields.append("body")

        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for item in items:
                row = item.model_dump(mode="json")
                row["url"] = str(item.url)
                row["kind"] = item.kind.value
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

    console.print(f"[green]✓ Exported {len(items)} items to {out_path}[/green]")


@app.command(name="menu")
def menu() -> None:
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
        console.print("  [cyan]1[/cyan] - Status das fontes e APIs ([dim]sources[/dim])")
        console.print("  [cyan]2[/cyan] - Coleta rápida no Hacker News (5 itens)")
        console.print("  [cyan]3[/cyan] - Coleta rápida no dev.to (5 itens)")
        console.print("  [cyan]4[/cyan] - Coleta rápida nos feeds RSS de IA (5 itens)")
        console.print("  [cyan]5[/cyan] - Simulação de planejamento / Dry-Run ([dim]plan[/dim])")
        console.print("  [cyan]6[/cyan] - Desduplicar última coleta ([dim]dedupe[/dim])")
        console.print("  [cyan]7[/cyan] - Exportar última coleta em CSV ([dim]export -f csv[/dim])")
        console.print("  [cyan]8[/cyan] - Estatísticas da última coleta ([dim]stats[/dim])")
        console.print("  [cyan]9[/cyan] - Validar arquivo de protocolo ([dim]validate[/dim])")
        console.print("  [cyan]0[/cyan] - Sair")

        choice = Prompt.ask("\n[bold green]Digite o número da opção[/bold green]", default="0")

        if choice == "0":
            console.print("[dim]Encerrado.[/dim]")
            break
        if choice == "1":
            sources(md=False, verbose=False)
        elif choice == "2":
            run(protocol=DEFAULT_PROTOCOL, source="hackernews", limit=5)
        elif choice == "3":
            run(protocol=DEFAULT_PROTOCOL, source="devto", limit=5)
        elif choice == "4":
            run(protocol=DEFAULT_PROTOCOL, source="rss", limit=5)
        elif choice == "5":
            plan(protocol=DEFAULT_PROTOCOL, source=None, verbose=False)
        elif choice == "6":
            dedupe(run_id=None, verbose=False)
        elif choice == "7":
            export(
                run_id=None,
                fmt="csv",
                include_body=False,
                output="resultados.csv",
                verbose=False,
            )
        elif choice == "8":
            stats(run_id=None, verbose=False)
        elif choice == "9":
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
