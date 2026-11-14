"""Evidence commands: repository enrichment and tool/method extraction."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import typer
from rich.table import Table

from msrkit import cli as _cli
from msrkit.cli._common import (
    _get_latest_run_id,
    _load_repo_signals,
    _load_run_protocol,
    _setup_logging,
    _unwrap,
    app,
    console,
)

if TYPE_CHECKING:
    from msrkit.enrich import RepoSignals

# Window and gazetteer config files a run was enriched with (ADR-054).
ENRICH_META = "meta.json"


def _enrich_key(since: Any, until: Any, config_files: set[str]) -> dict[str, Any]:
    return {
        "since": str(since) if since else None,
        "until": str(until) if until else None,
        "config_files": sorted(config_files),
    }


def _run_enrich_key(run_id: str) -> dict[str, Any] | None:
    """What another run was enriched with, or None when it cannot be told."""
    meta = _cli.DATA_DIR / "enrich" / run_id / ENRICH_META
    if meta.exists():
        data: dict[str, Any] = json.loads(meta.read_text(encoding="utf-8"))
        return data
    # Enriched before meta.json existed: the window from the manifest, and the config
    # files from the gazetteer of the protocol the manifest names.
    from msrkit.provenance import load_manifest

    try:
        window = load_manifest(_cli.DATA_DIR, run_id).effective_protocol.get("window") or {}
    except FileNotFoundError:
        return None
    proto = _load_run_protocol(run_id, None)
    if proto is None or not window:
        return None
    gazetteer = proto.gazetteer_data
    files = {c for t in gazetteer.tools for c in t.config_files} if gazetteer else set()
    return _enrich_key(window.get("since"), window.get("until"), files)


def _reusable_signals(
    run_id: str, key: dict[str, Any], wanted: set[str], max_age_days: int
) -> tuple[dict[str, RepoSignals], list[str]]:
    """Error-free signals of `wanted` repos, at most N days old, from runs with the same key.

    The newest copy of each repository wins; `reused_from` names the run that fetched it.
    """
    from msrkit.enrich import RepoSignals

    if "reused_from" not in RepoSignals.model_fields:
        from pydantic.fields import FieldInfo

        RepoSignals.model_fields["reused_from"] = FieldInfo(
            annotation=cast("Any", str | None), default=None
        )
        RepoSignals.model_rebuild(force=True)

    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    enrich_dir = _cli.DATA_DIR / "enrich"
    pool: dict[str, RepoSignals] = {}
    newest: dict[str, datetime] = {}
    runs: list[str] = []
    unstripped_wf: dict[tuple[str, str], str] = {}
    unstripped_mf: dict[tuple[str, str], str] = {}
    for run_dir in sorted(enrich_dir.iterdir()) if enrich_dir.is_dir() else []:
        path = run_dir / "github_repos.jsonl"
        if run_dir.name in (run_id, "consolidated") or not path.exists():
            continue
        if _run_enrich_key(run_dir.name) != key:
            continue
        runs.append(run_dir.name)
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                data = json.loads(line) if line.strip() else {}
                repo = data.get("repo")
                if repo not in wanted or data.get("error") or not data.get("fetched_at"):
                    continue
                wf_data = data.get("workflows")
                if isinstance(wf_data, dict):
                    for k_path, content in wf_data.items():
                        if content:
                            unstripped_wf[(repo, k_path)] = content
                mf_data = data.get("manifests")
                if isinstance(mf_data, dict):
                    for k_path, content in mf_data.items():
                        if content:
                            unstripped_mf[(repo, k_path)] = content
                fetched = datetime.fromisoformat(data["fetched_at"])
                fetched = fetched if fetched.tzinfo else fetched.replace(tzinfo=UTC)
                if fetched <= newest.get(repo, cutoff):
                    continue
                sig = RepoSignals.model_validate(data)
                sig.reused_from = sig.reused_from or run_dir.name
                pool[repo], newest[repo] = sig, fetched
    for repo, sig in pool.items():
        for k_path, content in list(sig.workflows.items()):
            if not content and (repo, k_path) in unstripped_wf:
                sig.workflows[k_path] = unstripped_wf[(repo, k_path)]
        for k_path, content in list(sig.manifests.items()):
            if not content and (repo, k_path) in unstripped_mf:
                sig.manifests[k_path] = unstripped_mf[(repo, k_path)]
    return pool, runs


@app.command()
def enrich(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Max repositories to enrich"),
    protocol: str | None = typer.Option(
        None, "--protocol", "-p", help="Protocol (defaults to the one in the run manifest)"
    ),
    graphql: bool = typer.Option(
        False,
        "--graphql",
        help="Use GitHub GraphQL API v4 for batched retrieval (faster, fewer requests)",
    ),
    reuse_days: int = typer.Option(
        0,
        "--reuse-days",
        help="Copy the signals of repositories another run enriched in the last N days, "
        "with the same window and gazetteer config files (0 = off)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Read tree, CI workflows, manifests, contributors and commit months of GitHub repos.

    Signals go to data/enrich/<run_id>/github_repos.jsonl and into the items
    (tech.has_ci, has_tests, contributors, active_months). Resumable.
    """
    run_id = _unwrap(run_id)
    limit = _unwrap(limit)
    protocol = _unwrap(protocol)
    graphql = _unwrap(graphql)
    reuse_days = _unwrap(reuse_days)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from datetime import date as date_type

    from msrkit.adapters.github import GitHubAdapter
    from msrkit.enrich import (
        GitHubEnricher,
        RepoSignals,
        apply_signals,
        effective_core_rate_limit,
        enrich_priority,
    )
    from msrkit.governor import Governor
    from msrkit.storage import ItemStorage

    if "reused_from" not in RepoSignals.model_fields:
        from pydantic.fields import FieldInfo

        RepoSignals.model_fields["reused_from"] = FieldInfo(
            annotation=cast("Any", str | None), default=None
        )
        RepoSignals.model_rebuild(force=True)

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)

    since = until = None
    config_files: set[str] = set()
    proto = _load_run_protocol(run_id, protocol)
    if proto is not None:
        since = date_type.fromisoformat(proto.window.since)
        until = date_type.fromisoformat(proto.window.until)
        if proto.gazetteer_data:
            config_files = {c for t in proto.gazetteer_data.tools for c in t.config_files}

    storage = ItemStorage(_cli.DATA_DIR)
    items = storage.read_items(run_id)
    # Most informative repositories first, so a --limit keeps the N3 candidates.
    branches: dict[str, str | None] = dict.fromkeys(enrich_priority(items))

    out_path = _cli.DATA_DIR / "enrich" / run_id / "github_repos.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    signals: dict[str, RepoSignals] = _load_repo_signals(run_id)
    key = _enrich_key(since, until, config_files)
    (out_path.parent / ENRICH_META).write_text(json.dumps(key), encoding="utf-8")

    todo = [r for r in branches if r not in signals]
    reused: dict[str, RepoSignals] = {}
    if reuse_days:
        reused, reuse_runs = _reusable_signals(run_id, key, set(todo), reuse_days)
        todo = [r for r in todo if r not in reused]
    if limit is not None:
        todo = todo[:limit]
    reused_note = f"{len(reused)} reused, " if reuse_days else ""
    console.print(
        f"[bold]Run:[/bold] {run_id} · {len(branches)} repositories, "
        f"{len(signals)} already enriched, {reused_note}{len(todo)} to go"
    )
    if reuse_days:
        origins = ", ".join(sorted({s.reused_from or "" for s in reused.values()})) or "none"
        console.print(
            f"  Reused from: {origins} (fetched in the last {reuse_days} days, same window "
            f"and config files; {len(reuse_runs)} earlier run(s) eligible)"
        )

    adapter = GitHubAdapter(
        governor=Governor(
            "github-core", effective_core_rate_limit(), state_dir=_cli.DATA_DIR / "governor"
        )
    )
    enricher = GitHubEnricher(
        adapter,
        config_files=config_files,
        since=since,
        until=until,
        use_graphql=bool(graphql),
    )
    errors = 0
    try:
        with open(out_path, "a", encoding="utf-8") as fh:
            for repo, sig in reused.items():
                missing_wf = [p for p, c in sig.workflows.items() if not c]
                missing_mf = [p for p, c in sig.manifests.items() if not c]
                if missing_wf or missing_mf:
                    branch = sig.default_branch or "HEAD"
                    hydrated = False
                    if graphql:
                        with contextlib.suppress(Exception):
                            hydrated = enricher._collect_graphql(
                                sig, branch, missing_wf, missing_mf
                            )
                    if not hydrated:
                        for p in missing_wf:
                            content = enricher._file(sig, p, branch)
                            if content is not None:
                                sig.workflows[p] = content
                        for p in missing_mf:
                            content = enricher._file(sig, p, branch)
                            if content is not None:
                                sig.manifests[p] = content
                signals[repo] = sig
                fh.write(sig.model_dump_json() + "\n")
                fh.flush()
            for repo in todo:
                sig = enricher.enrich(repo)
                signals[repo] = sig
                errors += sig.error is not None
                fh.write(sig.model_dump_json() + "\n")
                fh.flush()
    finally:
        adapter.close()
        if signals:
            updated = apply_signals(items, signals)
            storage.rewrite_items(items, run_id)
            deduped_path = _cli.DATA_DIR / "items" / run_id / "items_deduped.jsonl"
            if deduped_path.exists():
                deduped = storage.read_items(run_id, prefer_deduped=True)
                apply_signals(deduped, signals)
                storage.rewrite_items(deduped, run_id, deduped=True)

    console.print(
        f"[green]✓ Enriched {len(todo)} repositories ({errors} with errors); "
        f"{updated} items updated.[/green] Signals: {out_path}"
    )


@app.command()
def extract(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(
        None, "--protocol", "-p", help="Protocol (defaults to the one in the run manifest)"
    ),
    top: int = typer.Option(25, "--top", help="Rows shown in the summary table"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force overwrite even if existing detections are more complete",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Detect gazetteer tools and methods with evidence level N1/N2/N3 (§3.4, §12.1).

    Uses item text, code search hits and, when available, `msrkit enrich` signals.
    Writes data/extract/<run_id>/detections.jsonl and detections.csv.
    """
    run_id = _unwrap(run_id)
    protocol = _unwrap(protocol)
    top = _unwrap(top)
    force = _unwrap(force)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    import csv

    from msrkit.extract import Extractor, summarize
    from msrkit.storage import ItemStorage

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)

    proto = _load_run_protocol(run_id, protocol)
    if proto is None or proto.gazetteer_data is None:
        console.print("[red]✗ The protocol has no gazetteer (set `gazetteer:`).[/red]")
        raise typer.Exit(1)

    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    signals = _load_repo_signals(run_id)
    context_terms = [*proto.all_terms(), *(t for lex in proto.concepts.values() for t in lex)]
    extractor = Extractor(proto.gazetteer_data, context_terms=context_terms)
    detections = extractor.run(items, signals)

    out_dir = _cli.DATA_DIR / "extract" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    det_file = out_dir / "detections.jsonl"
    if det_file.exists() and not force:
        with open(det_file, encoding="utf-8-sig") as f:
            existing_count = sum(1 for line in f if line.strip())

        is_stripped_signals = (
            bool(signals)
            and any((sig.workflows or sig.manifests) for sig in signals.values())
            and all(
                all(not c for c in sig.workflows.values())
                and all(not c for c in sig.manifests.values())
                for sig in signals.values()
                if (sig.workflows or sig.manifests)
            )
        )
        has_redacted_bodies = any(
            it.body is None and it.source in ("stackexchange", "reddit", "twitter") for it in items
        )
        if existing_count > len(detections) and (is_stripped_signals or has_redacted_bodies):
            console.print(
                f"[yellow]⚠ Existing detections file has {existing_count} records, but current "
                f"extraction only produced {len(detections)} (input data appears package-redacted "
                "with stripped repository contents or omitted item bodies). Preserving existing "
                f"'{det_file}'. Pass --force to overwrite.[/yellow]"
            )
            return

    with open(det_file, "w", encoding="utf-8") as fh:
        for d in detections:
            fh.write(d.model_dump_json() + "\n")
    fields = list(detections[0].model_dump()) if detections else []
    with open(out_dir / "detections.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        if fields:
            writer.writeheader()
            writer.writerows(d.model_dump() for d in detections)

    summary = summarize(detections)
    table = Table(title=f"Detections by evidence level — {run_id}")
    for col in ("Entry", "N1", "N2", "N3"):
        table.add_column(col)
    ranked = sorted(summary.items(), key=lambda kv: (-kv[1]["N3"], -kv[1]["N2"], -kv[1]["N1"]))
    for entry, row in ranked[:top]:
        table.add_row(entry, str(row["N1"]), str(row["N2"]), str(row["N3"]))
    console.print(table)
    console.print(
        f"[green]✓ {len(detections)} detections from {len(items)} items and "
        f"{len(signals)} enriched repositories.[/green] Files: {out_dir}"
    )
    if extractor.dropped_ambiguous:
        console.print(
            f"[dim]{extractor.dropped_ambiguous} mention(s) of ambiguous tool names dropped "
            "(no lexicon context and no structural signal, §12.2).[/dim]"
        )
