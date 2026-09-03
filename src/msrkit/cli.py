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
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from msrkit import __version__

app = typer.Typer(
    name="msrkit",
    help="MSR-Kit: Mining grey literature through official APIs.",
    no_args_is_help=True,
)
console = Console()

# Default data directory
DATA_DIR = Path("data")


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
    protocol: str = typer.Argument(..., help="Path to protocol YAML file"),
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
    protocol: str = typer.Argument(..., help="Path to protocol YAML file"),
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

    table = Table(title="Collection Plan (Dry Run)", show_lines=True)
    table.add_column("Source", style="bold")
    table.add_column("Status")
    table.add_column("Queries")
    table.add_column("Max Results/Query")
    table.add_column("Max Pages")
    table.add_column("Est. Requests")

    total_est_requests = 0

    for source_name in config.enabled_sources():
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
    protocol: str = typer.Argument(..., help="Path to protocol YAML file"),
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

    for source_name in sorted(config.sources.keys()):
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
                        item = adapter.normalize(raw_item)
                        # Update provenance
                        item.provenance.run_id = run_id
                        item.provenance.query_string = " ".join(query.terms)
                        item.provenance.partition = f"q{qi}"
                        item.provenance.raw_ref = raw_ref

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
    run_id: str = typer.Option(..., "--run", help="Run ID to reprocess"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Reprocess items from raw data (no network requests)."""
    _setup_logging(verbose)
    console.print(f"[bold]Normalizing run:[/bold] {run_id}")

    from msrkit.provenance import load_manifest
    from msrkit.storage import ItemStorage, RawStorage

    try:
        manifest = load_manifest(DATA_DIR, run_id)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None

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
                    item = adapter.normalize(raw)
                    item.provenance.run_id = run_id
                    normalized.append(item)
                except Exception as e:
                    logging.getLogger(__name__).warning(
                        "Normalization error: %s", e
                    )
            if normalized:
                item_storage.save_items(normalized, run_id)
                total_items += len(normalized)

    console.print(f"[green]✓ Normalized {total_items} items from raw data[/green]")


@app.command()
def dedupe_cmd(
    run_id: str = typer.Option(..., "--run", help="Run ID to deduplicate"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Deduplicate items for a run."""
    _setup_logging(verbose)
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


# Use the function name that typer expects
app.command(name="dedupe")(dedupe_cmd)


@app.command()
def stats(
    run_id: str = typer.Option(..., "--run", help="Run ID"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show collection statistics for a run."""
    _setup_logging(verbose)

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
    run_id: str = typer.Option(..., "--run", help="Run ID"),
    fmt: str = typer.Option("jsonl", "--format", help="Output format: csv, jsonl, duckdb"),
    include_body: bool = typer.Option(False, "--include-body", help="Include body text in export"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Export collected items."""
    _setup_logging(verbose)

    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(DATA_DIR)
    items = item_storage.read_items(run_id)

    if not items:
        console.print("[yellow]No items found for this run.[/yellow]")
        return

    # Enforce redistribution policy
    registry = _get_registry()
    if include_body:
        for item in items:
            if item.source in registry:
                adapter_cls = registry[item.source]
                if adapter_cls.policy.redistribution == "metadata_only":
                    console.print(
                        f"[red]✗ Cannot export body for '{item.source}': "
                        f"redistribution policy is 'metadata_only'.[/red]"
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
        inserted = db.ingest_items(items)
        db.close()
        console.print(f"[green]✓ Inserted {inserted} items into {db_path}[/green]")
        return
    else:
        console.print(f"[red]Unknown format: {fmt}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓ Exported {len(items)} items to {out_path}[/green]")


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-V", help="Show version"),
) -> None:
    """MSR-Kit: Mining grey literature through official APIs."""
    if version:
        console.print(f"msrkit {__version__}")
        raise typer.Exit()


if __name__ == "__main__":
    app()
