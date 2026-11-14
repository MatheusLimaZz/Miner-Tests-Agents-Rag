"""Collection commands: sources, validate, plan, run, normalize, fetch."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.table import Table

from msrkit import __version__
from msrkit import cli as _cli
from msrkit.cli._common import (
    DEFAULT_PROTOCOL,
    _get_latest_run_id,
    _get_registry,
    _setup_logging,
    _tag_concepts,
    _tag_term_languages,
    _unwrap,
    app,
    console,
)

if TYPE_CHECKING:
    from msrkit.adapters.base import BaseAdapter
    from msrkit.models import Query


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


def _sources_table(registry: dict[str, type[BaseAdapter]]) -> None:
    """Display sources as a Rich table."""
    table = Table(title="MSR-Kit Sources", show_lines=True)
    table.add_column("Source", style="bold", no_wrap=True)
    table.add_column("Status")
    table.add_column("API Oficial")
    table.add_column("Reason / Exclusion Rationale")
    table.add_column("Auth Vars")
    table.add_column("Rate Limit")
    table.add_column("Search")
    table.add_column("Date Filter")

    for name in sorted(registry.keys()):
        adapter_cls = registry[name]
        adapter = adapter_cls()
        avail = adapter.available()
        policy = adapter_cls.policy

        if policy.exclusion_category == "methodological":
            status_text = "DESATIVADA"
            status_style = "cyan"
        elif policy.exclusion_category in ("access_gated", "commercial_paywall"):
            status_text = "DESATIVADA"
            status_style = "yellow"
        elif not policy.has_official_api:
            status_text = "DESATIVADA"
            status_style = "dim red"
        elif avail.status == "OK":
            status_text = "OK"
            status_style = "green"
        elif avail.status == "DEGRADED":
            status_text = "DEGRADED"
            status_style = "yellow"
        else:
            status_text = "DESATIVADA"
            status_style = "dim"

        missing_info = ""
        if avail.missing_env and not policy.exclusion_category:
            missing_info = f"\n⚠ Missing: {', '.join(avail.missing_env)}"

        rate_str = f"{policy.rate_limit.requests}/{policy.rate_limit.per_seconds}s"
        if policy.rate_limit.daily_cap:
            rate_str += f"\nDaily: {policy.rate_limit.daily_cap}"

        api_col = "[green]✓ Sim[/green]" if policy.has_official_api else "[red]✗ Não[/red]"

        display_reason = policy.exclusion_rationale if policy.exclusion_rationale else avail.reason

        table.add_row(
            name,
            f"[{status_style}]{status_text}[/{status_style}]",
            api_col,
            display_reason[:70] + ("..." if len(display_reason) > 70 else ""),
            "\n".join(policy.auth_env_vars) + missing_info if policy.auth_env_vars else "none",
            rate_str,
            "✓" if policy.supports_full_text_search else "✗",
            "✓" if policy.supports_date_filter else "✗",
        )

    console.print(table)


def _sources_md(registry: dict[str, type[BaseAdapter]]) -> None:
    """Output sources in Markdown format for docs/sources.md."""
    lines = ["# MSR-Kit Sources\n"]
    lines.append(f"Generated at: {datetime.now(UTC).isoformat()}\n")

    for name in sorted(registry.keys()):
        adapter_cls = registry[name]
        adapter = adapter_cls()
        avail = adapter.available()
        policy = adapter_cls.policy

        status_str: str = str(avail.status)
        if policy.exclusion_category:
            status_str = "DESATIVADA"

        lines.append(f"## {name}\n")
        lines.append(f"- **Status:** {status_str}")
        api_text = (
            f"Yes ({policy.official_api_name})" if policy.has_official_api else "No (no public API)"
        )
        lines.append(f"- **Official API:** {api_text}")
        if policy.exclusion_category:
            lines.append(f"- **Exclusion category:** {policy.exclusion_category}")
        if policy.exclusion_rationale:
            lines.append(f"- **Exclusion rationale:** {policy.exclusion_rationale}")
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
    protocol_opt: str | None = typer.Option(
        None, "--protocol", "-p", help="Protocol file (flag alias)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Validate protocol schema and check credential availability (no network)."""
    protocol = _unwrap(protocol_opt) or _unwrap(protocol)
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
    for lang, lexicon in config.terms_by_language.items():
        console.print(f"  Terms ({lang}): {len(lexicon)}")
    console.print(f"  Enabled sources: {', '.join(config.enabled_sources())}")
    if config.env_overrides:
        console.print(f"  From .env: {', '.join(config.env_overrides)}")
        groups: dict[str, list[str]] = {}
        for name, group in config.source_groups.items():
            groups.setdefault(group, []).append(name)
        labels = {
            "public": "public (collected, no setup)",
            "manual": "manual setup (collected when credentials are set)",
            "paid": "paid (never collected)",
            "unavailable": "unavailable (never collected)",
        }
        for group in ("public", "manual", "paid", "unavailable"):
            if group in groups:
                console.print(f"    {labels[group]}: {', '.join(sorted(groups[group]))}")
        differs = config.differs_from_file()
        if differs:
            console.print(
                f"  [yellow]⚠ .env changes the protocol file ({', '.join(differs)}); "
                "the run manifest records the values in force.[/yellow]"
            )

    registry = _get_registry()

    # Literal per-source queries need an adapter that accepts its own syntax
    bad_raw = [
        name
        for name, src in config.sources.items()
        if src.queries and name in registry and not registry[name].policy.supports_raw_queries
    ]
    if bad_raw:
        console.print(
            f"[red]✗ `queries:` is not supported by source(s): {', '.join(bad_raw)} "
            "(they have no search syntax; use terms or tags).[/red]"
        )
        raise typer.Exit(1)
    gaz = config.gazetteer_data
    if gaz is not None:
        console.print(
            f"  Gazetteer: v{gaz.version} — {len(gaz.tools)} tools "
            f"({len(gaz.anchors())} anchors), {len(gaz.methods)} methods"
        )
    form = config.coding
    if form.fields:
        needs_gaz = [f.name for f in form.fields if f.vocabulary in ("tools", "methods")]
        if needs_gaz and gaz is None:
            console.print(f"[red]✗ Coding fields {needs_gaz} need a gazetteer.[/red]")
            raise typer.Exit(1)
        console.print(
            f"  Coding form: {len(form.fields)} fields, {len(form.failure_modes)} failure modes"
        )
    try:
        n_raw = sum(q.raw for name in config.enabled_sources() for q in config.build_queries(name))
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None
    if n_raw:
        console.print(f"  Literal queries (after template expansion): {n_raw}")

    # Check credentials for enabled sources
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


def _year_strata(adapter: BaseAdapter, queries: list[Query]) -> list[Query]:
    """Split each query the source filters by date into one per year of the window (ADR-055)."""
    from msrkit.partition import split_by_year

    return [
        s for q in queries for s in (split_by_year(q) if adapter.stratifies_by_date(q) else [q])
    ]


@app.command()
def plan(
    protocol: str = typer.Argument(DEFAULT_PROTOCOL, help="Path to protocol YAML file"),
    protocol_opt: str | None = typer.Option(
        None, "--protocol", "-p", help="Protocol file (flag alias)"
    ),
    source: str | None = typer.Option(
        None, "--source", "-s", help="Filter plan to a single source"
    ),
    estimate: bool = typer.Option(
        False,
        "--estimate",
        help="Query result counts and partition for real (uses search quota; §6.4, §7.4)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Dry run: queries, partitions, predicted truncation and request budget per source.

    Without --estimate no request is made and the budget is an upper bound
    (max pages × queries). With --estimate every query is estimated and
    partitioned as `run` would, and the budget follows the estimated totals.
    """
    protocol = _unwrap(protocol_opt) or _unwrap(protocol)
    source = _unwrap(source)
    estimate = _unwrap(estimate)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    import math

    from msrkit.config import load_protocol
    from msrkit.governor import Governor
    from msrkit.partition import split_by_term

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

    title = "Collection Plan " + ("(Estimated)" if estimate else "(Dry Run)")
    table = Table(title=title, show_lines=True)
    table.add_column("Source", no_wrap=True)
    for col in ("Status", "Queries", "Partitions", "Truncated", "Est. items", "Est. requests"):
        table.add_column(col)

    total_est_requests = 0
    for source_name in sources_to_plan:
        if source_name not in registry:
            table.add_row(source_name, "[red]UNKNOWN[/red]", "-", "-", "-", "-", "-")
            continue

        adapter_cls = registry[source_name]
        policy = adapter_cls.policy
        governor = (
            Governor(
                source_name,
                adapter_cls.effective_rate_limit(),
                state_dir=_cli.DATA_DIR / "governor",
            )
            if estimate
            else None
        )
        adapter = adapter_cls(governor=governor)
        avail = adapter.available()
        if avail.status == "UNSUPPORTED":
            table.add_row(source_name, f"[red]{avail.status}[/red]", "-", "-", "-", "-", "0")
            continue

        queries = config.build_queries(source_name)
        if not policy.supports_raw_queries:
            queries = [q for q in queries if not q.raw]
        if config.sampling.strata == "year" and not config.sources[source_name].census:
            queries = _year_strata(adapter, queries)
        max_pages = policy.max_pages or 10

        if estimate:
            parts = []
            for q in queries:
                try:
                    parts.extend(adapter.partition(q))
                except Exception as e:
                    console.print(f"  [yellow]{source_name}: estimate failed ({e})[/yellow]")
                    parts.append(q)
            estimate_requests = adapter.request_count
            known = [p.estimated_total for p in parts if p.estimated_total is not None]
            est_items = sum(known) if known else None
            fetch_requests = 0
            for p in parts:
                if p.estimated_total is None:
                    fetch_requests += max_pages
                else:
                    capped = min(p.estimated_total, policy.max_results_per_query or 10**9)
                    fetch_requests += min(max_pages, math.ceil(capped / policy.max_page_size))
            est_requests = min(
                estimate_requests + fetch_requests, config.limits.max_requests_per_source
            )
            n_parts = str(len(parts))
            n_trunc = str(sum(p.truncated for p in parts))
            items_txt = "?" if est_items is None else str(est_items)
            adapter.close()
        else:
            # Each term is its own API query (adapters search term by term).
            per_term = sum(len(split_by_term(q)) for q in queries)
            est_requests = min(max_pages * per_term, config.limits.max_requests_per_source)
            n_parts = f"≥{per_term}"
            n_trunc = items_txt = "-"

        total_est_requests += est_requests
        status_color = {"OK": "green", "DEGRADED": "yellow"}.get(avail.status, "white")
        table.add_row(
            source_name,
            f"[{status_color}]{avail.status}[/{status_color}]",
            f"{len(queries)} ({sum(q.raw for q in queries)} literal)",
            n_parts,
            n_trunc,
            items_txt,
            str(est_requests),
        )

    console.print(table)
    console.print(f"\n[bold]Total estimated requests:[/bold] {total_est_requests}")
    console.print(
        f"[bold]Max requests/source limit:[/bold] {config.limits.max_requests_per_source}"
    )
    if not estimate:
        console.print(
            "\n[dim]Dry run: no request made. Use --estimate for partitions and "
            "truncation (consumes search quota).[/dim]"
        )


def _discard_incomplete(
    run_id: str, source_name: str, queries: list[Any], done: dict[str, Any], entry: Any
) -> None:
    """Before resuming, drop partial data of partitions that did not complete.

    Their raw file, their items and their manifest entries are removed, so the
    partition is collected again from scratch without duplicates.
    """
    from msrkit.partition import query_key
    from msrkit.storage import ItemStorage

    pending = {query_key(q) for q in queries} - set(done)
    if not pending:
        return
    raw_dir = _cli.DATA_DIR / "raw" / source_name / run_id
    for key in pending:
        for f in raw_dir.glob(f"{key}.jsonl*"):
            f.unlink()
    storage = ItemStorage(_cli.DATA_DIR)
    items = storage.read_items(run_id)
    kept = [
        it for it in items if not (it.source == source_name and it.provenance.partition in pending)
    ]
    if len(kept) != len(items):
        storage.rewrite_items(kept, run_id)
    entry.queries = [q for q in entry.queries if q.key not in pending]


@app.command()
def run(
    protocol: str = typer.Argument(DEFAULT_PROTOCOL, help="Path to protocol YAML file"),
    protocol_opt: str | None = typer.Option(
        None, "--protocol", "-p", help="Protocol file (flag alias)"
    ),
    source: str | None = typer.Option(
        None, "--source", "-s", help="Execute collection for a single source only"
    ),
    limit: int | None = typer.Option(
        None, "--limit", "-l", help="Override max items to collect for this run"
    ),
    per_query_limit: int | None = typer.Option(
        None,
        "--per-query-limit",
        help="Cap items per query (spreads a test sample over every term and partition)",
    ),
    resume: str | None = typer.Option(None, "--resume", help="Resume a previous run by ID"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Execute the collection protocol."""
    protocol = _unwrap(protocol_opt) or _unwrap(protocol)
    source = _unwrap(source)
    limit = _unwrap(limit)
    per_query_limit = _unwrap(per_query_limit)
    resume = _unwrap(resume)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.config import load_protocol, protocol_sha256
    from msrkit.governor import Governor, QuotaExhaustedError
    from msrkit.models import (
        Manifest,
        Query,
        QueryManifestEntry,
        SourceManifestEntry,
        SourceUnsupportedError,
    )
    from msrkit.partition import query_key
    from msrkit.provenance import generate_run_id, save_manifest
    from msrkit.storage import ItemStorage, RawStorage

    try:
        config = load_protocol(protocol)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise typer.Exit(1) from None

    def _upsert_source_manifest(target_manifest: Manifest, entry: SourceManifestEntry) -> None:
        idx = next((i for i, s in enumerate(target_manifest.sources) if s.name == entry.name), None)
        if idx is not None:
            target_manifest.sources[idx] = entry
        else:
            target_manifest.sources.append(entry)

    all_terms = config.all_terms()
    term_langs = config.term_languages()

    registry = _get_registry()
    run_id = resume or generate_run_id()
    proto_hash = protocol_sha256(protocol)

    console.print(f"[bold]Run ID:[/bold] {run_id}")
    console.print(f"[bold]Protocol:[/bold] {config.name}")

    raw_storage = RawStorage(_cli.DATA_DIR)
    item_storage = ItemStorage(_cli.DATA_DIR)

    if resume:
        from msrkit.provenance import load_manifest

        try:
            manifest = load_manifest(_cli.DATA_DIR, resume)
            console.print(
                f"[dim]Resuming existing run with {len(manifest.sources)} recorded source(s).[/dim]"
            )
        except FileNotFoundError:
            console.print(
                f"[red]✗ Cannot resume: Run '{resume}' not found in data directory.[/red]"
            )
            raise typer.Exit(1) from None
    else:
        manifest = Manifest(
            run_id=run_id,
            msrkit_version=__version__,
            protocol_path=protocol,
            protocol_sha256=proto_hash,
            started_at=datetime.now(UTC),
            effective_protocol=config.effective(),
            env_overrides=config.env_overrides,
            run_options={
                k: v
                for k, v in (
                    ("source", source),
                    ("limit", limit),
                    ("per_query_limit", per_query_limit),
                )
                if v is not None
            },
        )
    if config.env_overrides:
        console.print(f"[bold].env:[/bold] {', '.join(config.env_overrides)}")

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
        governor = Governor(
            source_name,
            adapter_cls.effective_rate_limit(),
            state_dir=_cli.DATA_DIR / "governor",
        )
        adapter = adapter_cls(governor=governor)
        avail = adapter.available()

        existing_entry = next((e for e in manifest.sources if e.name == source_name), None)
        if resume and existing_entry is not None:
            source_entry = existing_entry
            source_entry.availability = avail
        else:
            source_entry = SourceManifestEntry(
                name=source_name,
                adapter_version=adapter_cls.version,
                availability=avail,
            )

        if not src_cfg.enabled:
            console.print("  [dim]Disabled in protocol[/dim]")
            _upsert_source_manifest(manifest, source_entry)
            continue

        if avail.status == "UNSUPPORTED":
            console.print(f"  [red]{avail.status}: {avail.reason}[/red]")
            _upsert_source_manifest(manifest, source_entry)
            continue

        console.print(f"  Status: [{avail.status}] {avail.reason}")

        # A census source is collected whole: the per-query cap does not apply, and
        # strata, which give each year the same cap, are not needed.
        query_cap = None if src_cfg.census else per_query_limit
        strata = config.sampling.strata == "year" and not src_cfg.census
        if src_cfg.census and per_query_limit is not None:
            console.print("  [dim]Census: --per-query-limit does not apply[/dim]")
        if source_entry.planned:
            # Resume: reuse the frozen partition plan instead of re-estimating.
            queries = [Query.model_validate(d) for d in source_entry.planned]
        else:
            queries = config.build_queries(source_name)
            if strata:
                queries = _year_strata(adapter, queries)
            if query_cap is not None:
                for q in queries:
                    q.limit = min(q.limit or query_cap, query_cap)
            if limit is not None:
                for q in queries:
                    q.limit = limit
            if not adapter_cls.policy.supports_raw_queries and any(q.raw for q in queries):
                console.print(
                    f"  [yellow]`queries:` ignored: {source_name} "
                    "does not accept literal queries.[/yellow]"
                )
                queries = [q for q in queries if not q.raw]
            partitioned_queries = []
            for q in queries:
                try:
                    partitioned_queries.extend(adapter.partition(q))
                except Exception as e:
                    console.print(
                        f"  [yellow]Partitioning failed ({e}); "
                        "running query unpartitioned.[/yellow]"
                    )
                    partitioned_queries.append(q)
            queries = partitioned_queries
            if adapter_cls.policy.searches_each_term:
                # Sources that search each term separately must hold one query per term,
                # so caps, checkpoints, and limits apply evenly across the whole lexicon.
                from msrkit.partition import split_by_term

                queries = [s for q in queries for s in ([q] if q.raw else split_by_term(q))]
            source_entry.planned = [q.model_dump(mode="json") for q in queries]
        if limit is not None:
            for q in queries:
                q.limit = limit
        if query_cap is not None:
            for q in queries:
                q.limit = min(q.limit or query_cap, query_cap)
        elif not src_cfg.census and config.limits.max_items_per_source and len(queries) > 1:
            # Fair-share scheduling: if user did not set --per-query-limit,
            # calculate a sensible ceiling for non-literal queries so broad
            # text queries cannot exhaust the source budget and starve the rest.
            budget = limit if limit is not None else config.limits.max_items_per_source
            open_queries = [q for q in queries if not q.raw]
            raw_queries = [q for q in queries if q.raw]
            if raw_queries:
                raw_share = max(50, int(budget / len(queries) * 3))
                for q in raw_queries:
                    q.limit = min(q.limit or raw_share, raw_share)
            if open_queries:
                fair_share = max(25, int(budget / len(open_queries) * 2))
                for q in open_queries:
                    q.limit = min(q.limit or fair_share, fair_share)

        done = {e.key: e for e in source_entry.queries if e.completed and e.key}
        if resume:
            _discard_incomplete(run_id, source_name, queries, done, source_entry)
        _upsert_source_manifest(manifest, source_entry)
        save_manifest(manifest, _cli.DATA_DIR)  # checkpoint: plan recorded before collecting
        console.print(
            f"  Queries to execute: {len(queries) - len(done)}"
            + (f" ({len(done)} already completed)" if done else "")
        )

        total_source_items = sum(e.items for e in done.values())
        total_source_requests = sum(e.requests for e in done.values())
        seen_item_ids: set[str] = set()
        if done:
            with contextlib.suppress(Exception):
                seen_item_ids.update(
                    it.id for it in item_storage.read_items(run_id) if it.source == source_name
                )

        for qi, query in enumerate(queries, 1):
            key = query_key(query)
            if key in done:
                continue
            if limit is not None and total_source_items >= limit:
                console.print(
                    f"  [dim]Source '{source_name}' limit reached "
                    f"({total_source_items} items). Skipping remaining queries.[/dim]"
                )
                break
            if not src_cfg.census and total_source_items >= config.limits.max_items_per_source:
                console.print(
                    f"  [dim]Source '{source_name}' item limit reached "
                    f"({total_source_items} items). Skipping remaining queries.[/dim]"
                )
                break
            if total_source_requests >= config.limits.max_requests_per_source:
                console.print(
                    f"  [yellow]Source '{source_name}' request limit reached "
                    f"({total_source_requests} requests). Skipping remaining queries.[/yellow]"
                )
                break

            if src_cfg.census:
                remaining_for_source = limit - total_source_items if limit is not None else 50000
            else:
                remaining_for_source = config.limits.max_items_per_source - total_source_items
                if limit is not None:
                    remaining_for_source = min(
                        remaining_for_source, max(0, limit - total_source_items)
                    )
            query.limit = min(query.limit or 5000, remaining_for_source)

            query_label = f"[{query.kind}] " if query.kind else ""
            if query.raw:
                query_label += f"[raw{': ' + query.label if query.label else ''}] "
            console.print(f"  Query {qi}/{len(queries)}: {query_label}{query.terms[:3]}...")
            items_collected = 0
            raw_items_count = 0
            req_before = getattr(adapter, "request_count", 0)
            quota_hit = False
            failed = False
            response_hashes: list[str] = []

            query_display_parts = []
            if query.kind:
                query_display_parts.append(f"kind:{query.kind}")
            if query.since or query.until:
                query_display_parts.append(f"window:{query.since}..{query.until}")
            query_display_parts.append(" ".join(query.terms))
            query_display = " ".join(query_display_parts)

            try:
                if strata and query.estimated_total is None and adapter.stratifies_by_date(query):
                    # Size of the stratum at the source: the sampling weight (ADR-055).
                    query.estimated_total = adapter.population(query)
                for raw_item in adapter.search(query):
                    partition_hash = key
                    raw_ref = raw_storage.save_raw(raw_item, run_id, partition_hash)
                    raw_items_count += 1

                    # Normalize
                    try:
                        item = adapter.normalize(raw_item, terms=all_terms)
                        if item.id in seen_item_ids:
                            continue
                        seen_item_ids.add(item.id)
                        # Update provenance
                        item.provenance.run_id = run_id
                        item.provenance.query_string = query_display
                        item.provenance.partition = partition_hash
                        item.provenance.raw_ref = raw_ref
                        item.provenance.response_sha256 = getattr(
                            adapter, "last_response_sha256", ""
                        )

                        # Post-normalization term matching
                        if not item.matched_terms and all_terms:
                            from msrkit.keywords import match_terms

                            item.matched_terms = match_terms(
                                all_terms,
                                title=item.title,
                                body=item.body,
                                tags=item.tech.tags,
                                path=item.tech.path,
                            )

                        _tag_term_languages(item, term_langs)
                        _tag_concepts(item, config.concepts)
                        item_storage.save_items([item], run_id)
                        items_collected += 1
                        total_source_items += 1
                    except Exception as e:
                        logging.getLogger(__name__).warning(
                            "Normalization error for %s/%s: %s",
                            source_name,
                            raw_item.native_id,
                            e,
                        )

                    if total_source_items >= config.limits.max_items_per_source:
                        break
                    if limit is not None and total_source_items >= limit:
                        break
                    if query.limit is not None and raw_items_count >= query.limit:
                        break  # per-query cap, whether or not the adapter enforces it

            except SourceUnsupportedError as e:
                failed = True
                console.print(f"  [red]Unsupported: {e}[/red]")
            except QuotaExhaustedError as e:
                quota_hit = failed = True
                console.print(f"  [yellow]Quota exhausted: {e}[/yellow]")
            except Exception as e:
                failed = True
                console.print(f"  [yellow]Error during collection: {e}[/yellow]")
                logging.getLogger(__name__).exception("Collection error")

            requests_made = max(getattr(adapter, "request_count", 0) - req_before, 0)
            total_source_requests += requests_made
            if hasattr(adapter, "pop_response_hashes"):
                response_hashes = adapter.pop_response_hashes()
            discards = adapter.pop_discards() if hasattr(adapter, "pop_discards") else []
            if discards:
                discard_path = _cli.DATA_DIR / "runs" / run_id / "discarded.jsonl"
                discard_path.parent.mkdir(parents=True, exist_ok=True)
                with open(discard_path, "a", encoding="utf-8") as fh:
                    for d in discards:
                        record = {"source": source_name, "query": query_display, **d}
                        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            reasons: list[str] = []
            if query.truncated:
                reasons.append("source_cap")
            if not adapter_cls.policy.historical_coverage:
                reasons.append("no_historical_coverage")
            if query.limit is not None and raw_items_count >= query.limit:
                reasons.append("item_limit")
            if quota_hit:
                reasons.append("quota_exhausted")
            query_entry = QueryManifestEntry(
                query_string=query_display,
                partitions=1,
                requests=requests_made,
                items=items_collected,
                truncated=bool(reasons),
                truncation_reasons=reasons,
                estimated_total=query.estimated_total,
                discarded=len(discards),
                key=key,
                completed=not failed,
                response_sha256=response_hashes,
            )
            source_entry.queries.append(query_entry)
            _upsert_source_manifest(manifest, source_entry)
            save_manifest(manifest, _cli.DATA_DIR)  # checkpoint after every partition
            if quota_hit and config.limits.stop_on_quota_exhausted:
                console.print(
                    f"  [yellow]Stopping '{source_name}': daily quota exhausted "
                    "(stop_on_quota_exhausted).[/yellow]"
                )
                break
            discard_note = f", {len(discards)} discarded by local filter" if discards else ""
            console.print(
                f"    Collected: {items_collected} items "
                f"({raw_items_count} raw, {requests_made} requests{discard_note})"
            )

        adapter.close()
        _upsert_source_manifest(manifest, source_entry)

    manifest.finished_at = datetime.now(UTC)
    manifest_path = save_manifest(manifest, _cli.DATA_DIR)
    console.print(f"\n[green]✓ Run complete. Manifest: {manifest_path}[/green]")


@app.command(hidden=True)
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
        manifest = load_manifest(_cli.DATA_DIR, run_id)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None

    # Load protocol terms if available
    terms: list[str] = []
    term_langs: dict[str, str] = {}
    concepts: dict[str, list[str]] = {}
    protocol_path = protocol or manifest.protocol_path
    if protocol_path and Path(protocol_path).exists():
        try:
            from msrkit.config import load_protocol

            proto = load_protocol(protocol_path)
            terms = proto.all_terms()
            term_langs = proto.term_languages()
            concepts = proto.concepts
        except Exception as e:
            logging.getLogger(__name__).warning("Could not load protocol for terms: %s", e)

    registry = _get_registry()
    raw_storage = RawStorage(_cli.DATA_DIR)
    item_storage = ItemStorage(_cli.DATA_DIR)

    # Reset existing items file for this run before reprocessing to prevent duplication
    items_dir = _cli.DATA_DIR / "items" / run_id
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
            raw_file_path = (
                raw_storage.data_dir / "raw" / source_entry.name / run_id / f"{part_hash}.jsonl.gz"
            )
            normalized = []
            for line_idx, raw in enumerate(raw_items):
                try:
                    item = adapter.normalize(raw, terms=terms)
                    item.provenance.run_id = run_id
                    item.provenance.partition = part_hash
                    item.provenance.raw_ref = f"{raw_file_path}:{line_idx}"
                    if not item.matched_terms and terms:
                        from msrkit.keywords import match_terms

                        item.matched_terms = match_terms(
                            terms,
                            title=item.title,
                            body=item.body,
                            tags=item.tech.tags,
                            path=item.tech.path,
                        )
                    _tag_term_languages(item, term_langs)
                    _tag_concepts(item, concepts)
                    normalized.append(item)
                except Exception as e:
                    logging.getLogger(__name__).warning("Normalization error: %s", e)
            if normalized:
                item_storage.save_items(normalized, run_id)
                total_items += len(normalized)

    console.print(f"[green]✓ Normalized {total_items} items from raw data[/green]")


@app.command(hidden=True)
def fetch(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    source: list[str] = typer.Option(  # noqa: B008
        ["hackernews", "rss"], "--source", "-s", help="Sources whose links are fetched"
    ),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Max pages to fetch"),
    all_items: bool = typer.Option(
        False, "--all", help="Also fetch items with no matched term (default: matched only)"
    ),
    interval: float = typer.Option(5.0, "--interval", help="Min seconds between hits per host"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Download the text of pages linked by collected items (opt-in, kept local).

    Honors robots.txt and a per-host interval. Text goes to
    data/fulltext/<run_id>.jsonl and is never exported (ADR-019).
    """
    run_id = _unwrap(run_id)
    source = _unwrap(source)
    limit = _unwrap(limit)
    all_items = _unwrap(all_items)
    interval = _unwrap(interval)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.fulltext import FullTextFetcher
    from msrkit.storage import ItemStorage

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)

    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    out_path = _cli.DATA_DIR / "fulltext" / f"{run_id}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    done: set[str] = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                if line_s := line.strip():
                    done.add(json.loads(line_s)["item_id"])

    targets = [
        it
        for it in items
        if it.source in source
        and it.id not in done
        and (all_items or it.matched_terms)
        and (it.url.host or "") != "news.ycombinator.com"  # HN self-posts already carry text
    ]
    if limit is not None:
        targets = targets[:limit]
    console.print(
        f"[bold]Run:[/bold] {run_id} · {len(targets)} page(s) to fetch "
        f"({len(done)} already fetched)"
    )

    counts: dict[str, int] = {}
    fetcher = FullTextFetcher(min_interval_s=interval)
    try:
        with open(out_path, "a", encoding="utf-8") as fh:
            for it in targets:
                result = fetcher.fetch(str(it.url))
                counts[result.status] = counts.get(result.status, 0) + 1
                record = {"item_id": it.id, "source": it.source, **result.__dict__}
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
    finally:
        fetcher.close()

    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing to fetch"
    console.print(f"[green]✓ Full text saved to {out_path}[/green] ({summary})")
