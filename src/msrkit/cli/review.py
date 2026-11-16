"""Manual review commands: screening, extraction form, inter-coder agreement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from msrkit import cli as _cli
from msrkit.cli._common import (
    _cell,
    _load_detections,
    _load_run_protocol,
    _resolve_run,
    _screening_criteria,
    _unwrap,
    app,
    console,
    is_valid_coder_name,
)

screen_app = typer.Typer(help="Screening sheets: export, import decisions, status (§8, §11).")


app.add_typer(screen_app, name="screen")


@screen_app.command("export")
def screen_export(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", help="Pre-fill the coder column"),
    output: str | None = typer.Option(None, "--output", "-o", help="Sheet path (.csv)"),
    pending_only: bool = typer.Option(
        True, "--pending-only/--all", help="Skip items this coder already decided"
    ),
    order: str = typer.Option(
        "relevance", "--order", help="relevance (fixed ranked batches), collected or random"
    ),
    batch: list[int] = typer.Option(  # noqa: B008
        [], "--batch", "-b", help="Export only these batch numbers (repeatable)"
    ),
    blind: bool = typer.Option(
        False, "--blind", help="Mask score, terms, concepts and rank to prevent confirmation bias"
    ),
    seed: int = typer.Option(42, "--seed", help="Random seed when --order random is used"),
) -> None:
    """Write a screening sheet (CSV) with one column per eligibility criterion.

    By default items are ranked by relevance (matched terms, concepts, evidence
    level from `msrkit extract`, CI/tests) and cut into fixed batches over the
    whole run (§11), so batch numbers stay stable across coders and re-exports.
    With --blind, automated scores, matched terms, and ranking are masked.
    """
    import random

    from msrkit.screening import Ranked, export_sheet, load_decisions, rank_items
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    coder = _unwrap(coder).strip()
    if coder:
        is_valid, reason = is_valid_coder_name(coder)
        if not is_valid:
            console.print(f"[red]✗ Coder inválido: {reason}[/red]")
            raise typer.Exit(1)
    order = _unwrap(order)
    blind_val = _unwrap(blind)
    if blind_val and order == "relevance":
        console.print(
            "[yellow]⚠ Modo cego ativado: ordenação por relevância forçada para 'random' "
            "para evitar vazamento de ranking.[/yellow]"
        )
        order = "random"
    batches = set(_unwrap(batch) or [])
    if order not in ("relevance", "collected", "random"):
        console.print("[red]✗ --order must be 'relevance', 'collected' or 'random'.[/red]")
        raise typer.Exit(1)
    criteria = _screening_criteria(run_id, _unwrap(protocol))
    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    detections = _load_detections(run_id) if order == "relevance" else []
    if order == "relevance":
        ranking = rank_items(items, detections, criteria.batch_size)
    elif order == "random":
        rng = random.Random(_unwrap(seed))
        rng.shuffle(items)
        ranking = {
            it.id: Ranked(
                item_id=it.id, rank=i + 1, batch=i // max(criteria.batch_size, 1) + 1, score=0
            )
            for i, it in enumerate(items)
        }
    else:
        ranking = {
            it.id: Ranked(
                item_id=it.id, rank=i + 1, batch=i // max(criteria.batch_size, 1) + 1, score=0
            )
            for i, it in enumerate(items)
        }
    if _unwrap(pending_only) and coder:
        done = {item_id for (item_id, c) in load_decisions(_cli.DATA_DIR, run_id) if c == coder}
        items = [it for it in items if it.id not in done]
    if batches:
        items = [it for it in items if ranking[it.id].batch in batches]
    out = Path(
        _unwrap(output) or _cli.DATA_DIR / "screening" / run_id / f"sheet_{coder or 'blank'}.csv"
    )
    n = export_sheet(items, criteria, out, coder=coder, ranking=ranking, blind=_unwrap(blind))
    total_batches = max((r.batch for r in ranking.values()), default=0)
    extra_info = f"order: {order}" + ("; blind: yes" if _unwrap(blind) else "")
    console.print(
        f"[green]✓ {n} items written to {out}[/green] "
        f"({extra_info}; {total_batches} batches of {criteria.batch_size})"
    )
    if order == "relevance" and not detections:
        console.print(
            "[dim]No detections for this run: run `msrkit extract` first to rank by "
            "evidence level.[/dim]"
        )


@screen_app.command("import")
def screen_import(
    sheet: str = typer.Argument(..., help="Filled screening sheet (.csv)"),
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", help="Coder for rows with an empty coder column"),
) -> None:
    """Validate a filled sheet and record its decisions (nothing is imported on error)."""
    from msrkit.screening import SheetError, append_decisions, import_sheet
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    criteria = _screening_criteria(run_id, _unwrap(protocol))
    known = {it.id for it in ItemStorage(_cli.DATA_DIR).read_items(run_id)}
    try:
        decisions = import_sheet(Path(sheet), criteria, known, default_coder=_unwrap(coder))
    except SheetError as e:
        console.print(f"[red]✗ {e}[/red]")
        for problem in e.problems[:50]:
            console.print(f"  - {problem}")
        raise typer.Exit(1) from None
    path = append_decisions(_cli.DATA_DIR, run_id, decisions)
    skipped = getattr(decisions, "skipped", [])
    if skipped:
        sample_str = ", ".join(skipped[:5]) + ("..." if len(skipped) > 5 else "")
        console.print(
            f"[yellow]⚠ {len(skipped)} linha(s) ignorada(s) por falta de decisão: "
            f"{sample_str}[/yellow]"
        )
    console.print(f"[green]✓ {len(decisions)} decisions recorded in {path}[/green]")


@screen_app.command("status")
def screen_status(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
) -> None:
    """Decisions per coder and the resulting include/exclude/uncertain counts."""
    from msrkit.screening import final_decisions, load_decisions
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    latest = load_decisions(_cli.DATA_DIR, run_id)
    total = len(ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True))
    table = Table(title=f"Screening — {run_id} ({total} items)")
    for col in ("Coder", "include", "exclude", "uncertain"):
        table.add_column(col)
    per_coder: dict[str, dict[str, int]] = {}
    for (_item, coder), d in latest.items():
        row = per_coder.setdefault(coder, {"include": 0, "exclude": 0, "uncertain": 0})
        row[d.decision] += 1
    for coder, row in sorted(per_coder.items()):
        table.add_row(coder, str(row["include"]), str(row["exclude"]), str(row["uncertain"]))
    final = final_decisions(latest)
    table.add_row(
        "[bold]final[/bold]",
        *(str(sum(v == k for v in final.values())) for k in ("include", "exclude", "uncertain")),
    )
    console.print(table)
    console.print(f"Pending (no decision): {total - len(final)}")


@screen_app.command("interactive")
def screen_interactive(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", "-c", help="Coder name / identifier"),
    batch: int | None = typer.Option(None, "--batch", "-b", help="Specific batch number to screen"),
    limit: int = typer.Option(
        25, "--limit", "-l", help="Maximum number of items to screen in this session"
    ),
    blind: bool = typer.Option(
        False,
        "--blind",
        help="Mask score, terms, signals and randomize order to prevent confirmation bias",
    ),
    seed: int = typer.Option(42, "--seed", help="Random seed when --blind is used"),
) -> None:
    """Screen items interactively in the terminal with colored highlighting and rapid keystrokes."""
    from datetime import UTC, datetime

    from rich.panel import Panel
    from rich.prompt import Prompt

    from msrkit.screening import ScreeningDecision, append_decisions, load_decisions, rank_items
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    coder_str = _unwrap(coder).strip()
    if not coder_str:
        coder_str = Prompt.ask(
            "[bold cyan]Informe seu nome de avaliador (coder)[/bold cyan]"
        ).strip()
    is_valid, reason = is_valid_coder_name(coder_str)
    if not is_valid:
        console.print(f"[red]✗ Coder inválido: {reason}[/red]")
        raise typer.Exit(1)

    criteria = _screening_criteria(run_id, _unwrap(protocol))
    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    detections = _load_detections(run_id)
    ranking = rank_items(items, detections, criteria.batch_size)

    done = {item_id for (item_id, c) in load_decisions(_cli.DATA_DIR, run_id) if c == coder_str}
    pending = [it for it in items if it.id not in done]

    target_batch = _unwrap(batch)
    if target_batch is not None:
        pending = [it for it in pending if ranking[it.id].batch == target_batch]

    blind_val = _unwrap(blind)
    if blind_val:
        import random

        rng = random.Random(_unwrap(seed))
        rng.shuffle(pending)

    if not pending:
        console.print(
            f"[green]✓ Nenhum item pendente de triagem para o avaliador '{coder_str}'.[/green]"
        )
        return

    session_items = pending[:limit]
    blind_tag = " [cyan](Modo Cego Ativado)[/cyan]" if blind_val else ""
    console.print(
        f"[bold green]Iniciando triagem interativa "
        f"({len(session_items)} itens para {coder_str}){blind_tag}[/bold green]"
    )
    console.print(
        "[dim]Comandos: [I]ncluir | [E]xcluir (E1-E6) | [U]ncertain | [S]kip | [Q]uit[/dim]\n"
    )

    recorded: list[ScreeningDecision] = []
    now = datetime.now(UTC).isoformat()

    for idx, it in enumerate(session_items, 1):
        if not blind_val:
            rk = ranking.get(it.id)
            b_info = f"Batch {rk.batch} (Rank #{rk.rank}, Escore {rk.score:.1f})" if rk else ""
            matched_str = (
                ", ".join(t.term if hasattr(t, "term") else str(t) for t in it.matched_terms)
                if it.matched_terms
                else "nenhum"
            )

            info_lines = [
                f"[bold]Fonte:[/bold] {it.source} ({it.kind.value})   [bold]ID:[/bold] {it.id}",
                f"[bold]URL:[/bold] {it.url}",
                f"[bold]Termos casados:[/bold] [yellow]{matched_str}[/yellow]",
            ]
            tech_parts: list[str] = []
            if it.engagement.stars is not None:
                tech_parts.append(f"★ {it.engagement.stars} estrelas")
            if it.engagement.forks is not None:
                tech_parts.append(f"⑂ {it.engagement.forks} forks")
            if it.tech.has_ci is not None:
                ci_val = (
                    "[green]CI/CD: [✓ Sim][/green]"
                    if it.tech.has_ci
                    else "[dim]CI/CD: [✗ Não][/dim]"
                )
                tech_parts.append(ci_val)
            if it.tech.has_tests is not None:
                tests_val = (
                    "[green]Testes: [✓ Sim][/green]"
                    if it.tech.has_tests
                    else "[dim]Testes: [✗ Não][/dim]"
                )
                tech_parts.append(tests_val)
            if it.tech.contributors is not None:
                tech_parts.append(f"Contribuidores: {it.tech.contributors}")
            if it.tech.active_months is not None:
                tech_parts.append(f"Atividade: {it.tech.active_months}m")
            if it.tech.tags:
                tech_parts.append(f"Tags: [cyan]{', '.join(it.tech.tags[:5])}[/cyan]")
            if tech_parts:
                info_lines.append(f"[bold]Sinais de Código:[/bold] {' | '.join(tech_parts)}")
        else:
            b_info = "[bold cyan][Modo Cego — Scores e Sinais Ocultados][/bold cyan]"
            info_lines = [
                f"[bold]Fonte:[/bold] {it.source} ({it.kind.value})   [bold]ID:[/bold] {it.id}",
                f"[bold]URL:[/bold] {it.url}",
                "[dim][Termos casados, escores e popularidade ocultados para triagem cega][/dim]",
            ]

        if it.body:
            preview = it.body[:600] + ("..." if len(it.body) > 600 else "")
            info_lines.append(f"\n[dim]{preview}[/dim]")

        title_str = (it.title or "Sem título")[:50]
        panel = Panel(
            "\n".join(info_lines),
            title=f"[bold cyan]Item {idx}/{len(session_items)} — {title_str}[/bold cyan]",
            subtitle=f"[dim]{b_info}[/dim]",
            border_style="cyan",
        )
        console.print(panel)

        prompt_label = (
            "[bold white]Decisão[/bold white] "
            "([green]I[/green]ncluir, [red]E[/red]xcluir, [yellow]U[/yellow]ncertain, "
            "[dim]S[/dim]kip, [magenta]Q[/magenta]uit)"
        )
        choice = Prompt.ask(
            prompt_label,
            choices=["i", "e", "u", "s", "q", "I", "E", "U", "S", "Q"],
            default="s",
        ).lower()

        if choice == "q":
            console.print("[yellow]Sessão de triagem interrompida pelo usuário.[/yellow]")
            break
        elif choice == "s":
            console.print("[dim]Item pulado.[/dim]\n")
            continue
        elif choice == "i":
            recorded.append(
                ScreeningDecision(
                    item_id=it.id,
                    coder=coder_str,
                    decision="include",
                    criteria_met=["I1"],
                    reason="Incluído via triagem interativa",
                    decided_at=now,
                    sheet_sha256="interactive_cli",
                )
            )
            console.print("[green]✓ Marcado como INCLUÍDO.[/green]\n")
        elif choice == "u":
            recorded.append(
                ScreeningDecision(
                    item_id=it.id,
                    coder=coder_str,
                    decision="uncertain",
                    criteria_met=[],
                    reason="Dúvida para terceiro avaliador",
                    decided_at=now,
                    sheet_sha256="interactive_cli",
                )
            )
            console.print("[yellow]? Marcado como UNCERTAIN.[/yellow]\n")
        elif choice == "e":
            console.print(
                "[dim]Critérios: 1=E1 (não é teste), 2=E2 (só modelo), 3=E3 (promo), "
                "4=E4 (duplicata), 5=E5 (tutorial), 6=E6 (sem manutenção)[/dim]"
            )
            crit_choice = Prompt.ask(
                "Motivo da exclusão (1-6 ou Enter para E1)", default="1"
            ).strip()
            clean_choice = crit_choice.upper().strip()
            if clean_choice.startswith("E") and len(clean_choice) > 1:
                clean_choice = clean_choice[1:]
            crit_id = f"E{clean_choice}" if clean_choice in ("1", "2", "3", "4", "5", "6") else "E1"
            recorded.append(
                ScreeningDecision(
                    item_id=it.id,
                    coder=coder_str,
                    decision="exclude",
                    criteria_met=[crit_id],
                    reason=f"Excluído por {crit_id}",
                    decided_at=now,
                    sheet_sha256="interactive_cli",
                )
            )
            console.print(f"[red]✗ Marcado como EXCLUÍDO ({crit_id}).[/red]\n")

    if recorded:
        path = append_decisions(_cli.DATA_DIR, run_id, recorded)
        console.print(
            f"[bold green]✓ {len(recorded)} decisões gravadas com sucesso em "
            f"{path.name}[/bold green]"
        )
        try:
            from msrkit.cli.corpus import prisma

            console.print("\n[bold cyan]Atualizando fluxo PRISMA 2020...[/bold cyan]")
            prisma(run_id=run_id)
        except Exception as e:
            console.print(f"[dim](Nota: geração do PRISMA automático: {e})[/dim]")
    else:
        console.print("[dim]Nenhuma decisão registrada nesta sessão.[/dim]")


@screen_app.command("tui")
def screen_tui(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", "-c", help="Coder name / identifier"),
    batch: int | None = typer.Option(None, "--batch", "-b", help="Specific batch number to screen"),
    limit: int | None = typer.Option(
        None, "--limit", "-l", help="Maximum items to load (default: all)"
    ),
) -> None:
    """Launch full-screen Textual TUI for interactive screening with keyboard shortcuts."""
    from rich.prompt import Prompt

    from msrkit.screening import load_decisions, rank_items
    from msrkit.storage import ItemStorage

    try:
        from msrkit.tui import ScreeningApp
    except ImportError:
        console.print(
            "\n[bold yellow]⚠ Biblioteca 'textual' não instalada neste ambiente.[/bold yellow]\n"
            "[cyan]→ Redirecionando para a triagem interativa nativa (Rich CLI)...[/cyan]\n"
        )
        return screen_interactive(
            coder=coder,
            run_id=run_id,
            protocol=protocol,
            batch=batch,
            limit=limit or 50,
        )

    run_id = _resolve_run(_unwrap(run_id))
    coder_str = _unwrap(coder).strip()
    if not coder_str:
        coder_str = Prompt.ask(
            "[bold cyan]Informe seu nome de avaliador (coder)[/bold cyan]"
        ).strip()
    is_valid, reason = is_valid_coder_name(coder_str)
    if not is_valid:
        console.print(f"[red]✗ Coder inválido: {reason}[/red]")
        raise typer.Exit(1)

    criteria = _screening_criteria(run_id, _unwrap(protocol))
    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    detections = _load_detections(run_id)
    ranking = rank_items(items, detections, criteria.batch_size)

    done = {item_id for (item_id, c) in load_decisions(_cli.DATA_DIR, run_id) if c == coder_str}
    pending = [it for it in items if it.id not in done]

    target_batch = _unwrap(batch)
    if target_batch is not None:
        pending = [it for it in pending if ranking[it.id].batch == target_batch]

    pending.sort(key=lambda it: ranking[it.id].rank)

    if not pending:
        console.print(
            f"[green]✓ Nenhum item pendente de triagem para o avaliador '{coder_str}'.[/green]"
        )
        return

    session_items = pending if limit is None or limit <= 0 else pending[:limit]
    app_tui = ScreeningApp(
        items=session_items,
        criteria=criteria,
        data_dir=_cli.DATA_DIR,
        run_id=run_id,
        coder=coder_str,
        detections=detections,
    )
    app_tui.run()
    if app_tui.recorded_decisions:
        from msrkit.cli.corpus import prisma

        n_dec = len(app_tui.recorded_decisions)
        console.print(f"\n[bold green]✓ {n_dec} decisões gravadas com sucesso![/bold green]")
        try:
            prisma(run_id=run_id)
        except Exception as e:
            console.print(f"[dim](Nota: geração do PRISMA automático: {e})[/dim]")


# Top-level convenient alias
app.command(name="review-tui", help="Launch full-screen Textual TUI for literature screening.")(
    screen_tui
)


coding_app = typer.Typer(help="Extraction form (Annex A) and quality (§9): export, import, status.")


app.add_typer(coding_app, name="coding")


@coding_app.command("export")
def coding_export(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", help="Pre-fill the coder column"),
    output: str | None = typer.Option(None, "--output", "-o", help="Sheet path (.csv)"),
    status: str = typer.Option(
        "include", "--status", help="Items by final screening decision: include, uncertain, all"
    ),
    prefill: bool = typer.Option(
        False, "--prefill", help="Copy auto_* suggestions into the form fields that declare it"
    ),
    pending_only: bool = typer.Option(
        True, "--pending-only/--recode", help="Skip items this coder already coded"
    ),
    double_sample: bool = typer.Option(
        False, "--double-sample", help="Only items of the double-coding sample (coding sample)"
    ),
) -> None:
    """Write the extraction sheet: one column per form field, plus auto_* suggestions.

    By default only items included in screening are exported; suggestions come from
    item concepts, `msrkit extract` detections and `msrkit enrich` signals.
    """
    from msrkit.coding import export_form, load_codings, suggestions
    from msrkit.screening import final_decisions, load_decisions
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    coder = _unwrap(coder).strip()
    if coder:
        is_valid, reason = is_valid_coder_name(coder)
        if not is_valid:
            console.print(f"[red]✗ Coder inválido: {reason}[/red]")
            raise typer.Exit(1)
    status = _unwrap(status)
    if status not in ("include", "uncertain", "all"):
        console.print("[red]✗ --status must be include, uncertain or all.[/red]")
        raise typer.Exit(1)
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    if proto is None or not proto.coding.fields:
        console.print("[red]✗ The protocol has no extraction form (set `coding.fields`).[/red]")
        raise typer.Exit(1)
    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    if status != "all":
        final = final_decisions(load_decisions(_cli.DATA_DIR, run_id))
        if not final:
            console.print(
                "[red]✗ No screening decisions for this run; import them first "
                "(`msrkit screen import`) or pass --status all.[/red]"
            )
            raise typer.Exit(1)
        items = [it for it in items if final.get(it.id) == status]
    if _unwrap(double_sample):
        sample_path = _cli.DATA_DIR / "coding" / run_id / "double_sample.json"
        if not sample_path.exists():
            console.print("[red]✗ No double-coding sample; run `msrkit coding sample`.[/red]")
            raise typer.Exit(1)
        chosen = set(json.loads(sample_path.read_text(encoding="utf-8"))["item_ids"])
        items = [it for it in items if it.id in chosen]
    if _unwrap(pending_only) and coder:
        done = {item_id for (item_id, c) in load_codings(_cli.DATA_DIR, run_id) if c == coder}
        items = [it for it in items if it.id not in done]
    systems = tuple(c for c in ("rag", "agente") if c in proto.concepts)
    auto = suggestions(items, _load_detections(run_id), systems)
    out = Path(
        _unwrap(output) or _cli.DATA_DIR / "coding" / run_id / f"form_{coder or 'blank'}.csv"
    )
    n = export_form(items, proto.coding, out, auto=auto, coder=coder, prefill=_unwrap(prefill))
    console.print(f"[green]✓ {n} items written to {out}[/green]")


@coding_app.command("import")
def coding_import(
    sheet: str = typer.Argument(..., help="Filled extraction sheet (.csv)"),
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", help="Coder for rows with an empty coder column"),
) -> None:
    """Validate a filled extraction sheet and record it (nothing is imported on error)."""
    from msrkit.coding import append_codings, import_form
    from msrkit.screening import SheetError
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    if proto is None or not proto.coding.fields:
        console.print("[red]✗ The protocol has no extraction form (set `coding.fields`).[/red]")
        raise typer.Exit(1)
    known = {it.id for it in ItemStorage(_cli.DATA_DIR).read_items(run_id)}
    try:
        records = import_form(
            Path(sheet), proto.coding, known, proto.gazetteer_data, default_coder=_unwrap(coder)
        )
    except SheetError as e:
        console.print(f"[red]✗ {e}[/red]")
        for problem in e.problems[:50]:
            console.print(f"  - {problem}")
        raise typer.Exit(1) from None
    path = append_codings(_cli.DATA_DIR, run_id, records)
    skipped = getattr(records, "skipped", [])
    if skipped:
        sample_str = ", ".join(skipped[:5]) + ("..." if len(skipped) > 5 else "")
        console.print(
            f"[yellow]⚠ {len(skipped)} linha(s) ignorada(s) por falta de dados: "
            f"{sample_str}[/yellow]"
        )
    console.print(f"[green]✓ {len(records)} coded items recorded in {path}[/green]")


@coding_app.command("sample")
def coding_sample(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    rate: float | None = typer.Option(
        None, "--rate", help="Share of eligible items (default: coding.double_coding_rate)"
    ),
    seed: int = typer.Option(20260928, "--seed", help="Random seed (recorded)"),
) -> None:
    """Draw the double-coding sample (§15), stratified by source, among included items."""
    from msrkit.agreement import double_sample
    from msrkit.screening import final_decisions, load_decisions
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    rate = _unwrap(rate) or (proto.coding.double_coding_rate if proto else 0.2)
    seed = _unwrap(seed)
    final = final_decisions(load_decisions(_cli.DATA_DIR, run_id))
    eligible = [
        it
        for it in ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
        if final.get(it.id) == "include"
    ]
    if not eligible:
        console.print("[red]✗ No included items yet (`msrkit screen import`).[/red]")
        raise typer.Exit(1)
    try:
        ids = double_sample(eligible, rate=rate, seed=seed)
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None
    out = _cli.DATA_DIR / "coding" / run_id / "double_sample.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {"rate": rate, "seed": seed, "eligible": len(eligible), "item_ids": ids}
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    console.print(
        f"[green]✓ {len(ids)} of {len(eligible)} included items sampled for double "
        f"coding[/green] → {out}"
    )


@coding_app.command("status")
def coding_status(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
) -> None:
    """Coded items per coder and how many items have two or more coders."""
    from msrkit.coding import load_codings

    run_id = _resolve_run(_unwrap(run_id))
    latest = load_codings(_cli.DATA_DIR, run_id)
    per_coder: dict[str, int] = {}
    coders_by_item: dict[str, set[str]] = {}
    for item_id, coder in latest:
        per_coder[coder] = per_coder.get(coder, 0) + 1
        coders_by_item.setdefault(item_id, set()).add(coder)
    table = Table(title=f"Coding — {run_id}")
    table.add_column("Coder")
    table.add_column("Items")
    for coder, n in sorted(per_coder.items()):
        table.add_row(coder, str(n))
    console.print(table)
    double = sum(len(c) >= 2 for c in coders_by_item.values())
    console.print(f"Coded items: {len(coders_by_item)} · double-coded: {double}")


@app.command(hidden=True)
def agreement(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    all_fields: bool = typer.Option(
        False, "--all-fields", help="Every categorical field, not only coding.agreement"
    ),
) -> None:
    """Cohen's κ per dimension between coders (screening and extraction form, §15).

    Writes data/coding/<run_id>/agreement.json and disagreements.csv (one row per
    item and dimension, for the third coder).
    """
    import csv

    from msrkit.agreement import agreement as compute_agreement
    from msrkit.coding import load_codings
    from msrkit.screening import load_decisions

    run_id = _resolve_run(_unwrap(run_id))
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    if proto is None:
        console.print("[red]✗ Protocol not found; pass --protocol.[/red]")
        raise typer.Exit(1)
    form = proto.coding
    dims = None if _unwrap(all_fields) or not form.agreement else form.agreement
    codings = load_codings(_cli.DATA_DIR, run_id)
    decisions = load_decisions(_cli.DATA_DIR, run_id)
    results = compute_agreement(form, codings, decisions, dimensions=dims)
    if not results:
        console.print("[yellow]No item was coded by two coders yet.[/yellow]")
        raise typer.Exit(0)

    table = Table(title=f"Inter-coder agreement — {run_id}")
    for col in ("Dimension", "Coders", "n", "Agreement", "κ", "Landis & Koch"):
        table.add_column(col)
    for r in results:
        table.add_row(
            r.dimension,
            " × ".join(r.coders),
            str(r.n),
            f"{r.percent:.0%}",
            "—" if r.kappa is None else f"{r.kappa:.2f}",
            r.interpretation,
        )
    console.print(table)

    out_dir = _cli.DATA_DIR / "coding" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "agreement.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    values: dict[tuple[str, str], dict[str, Any]] = {}
    for (item_id, coder), rec in codings.items():
        values[(item_id, coder)] = dict(rec.values)
    for (item_id, coder), d in decisions.items():
        values.setdefault((item_id, coder), {})["screening"] = d.decision
    with open(out_dir / "disagreements.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(
            [
                "item_id",
                "dimension",
                "coder_a",
                "value_a",
                "coder_b",
                "value_b",
                "consensus_value",
                "resolved_by",
                "reason",
            ]
        )
        for r in results:
            a, b = r.coders
            for item_id in r.disagreements:
                va = values.get((item_id, a), {}).get(r.dimension)
                vb = values.get((item_id, b), {}).get(r.dimension)
                writer.writerow([item_id, r.dimension, a, _cell(va), b, _cell(vb), "", "", ""])
    n_dis = sum(len(r.disagreements) for r in results)
    console.print(f"[green]✓ {n_dis} disagreement(s) written to {out_dir}[/green]")


@app.command("resolve", help="Apply consensus resolutions from filled disagreements.csv (§15).")
def resolve_disagreements_cmd(
    sheet: str | None = typer.Option(
        None, "--sheet", "-s", help="Path to filled disagreements.csv"
    ),
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    resolver: str = typer.Option(
        "consenso", "--resolver", "-r", help="Default resolver coder name"
    ),
) -> None:
    """Apply resolved consensus decisions from disagreements.csv to screening and coding."""
    import csv
    import hashlib
    from datetime import UTC, datetime
    from pathlib import Path

    from msrkit.coding import CodingRecord, _parse, append_codings, load_codings
    from msrkit.screening import DECISION_ALIASES, ScreeningDecision, append_decisions

    run_id = _resolve_run(_unwrap(run_id))
    resolver = _unwrap(resolver).strip() or "consenso"
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    field_map = {f.name: f for f in proto.coding.fields} if proto and proto.coding else {}
    allowed = (
        {f.name: proto.coding.allowed(f, proto.gazetteer_data) for f in proto.coding.fields}
        if proto and proto.coding
        else {}
    )

    sheet_val = _unwrap(sheet)
    sheet_path = (
        Path(sheet_val) if sheet_val else _cli.DATA_DIR / "coding" / run_id / "disagreements.csv"
    )
    if not sheet_path.exists():
        console.print(f"[red]✗ Arquivo de divergências não encontrado: {sheet_path}[/red]")
        raise typer.Exit(1)

    raw_bytes = sheet_path.read_bytes()
    sheet_hash = hashlib.sha256(raw_bytes).hexdigest()
    text = raw_bytes.decode("utf-8-sig")
    import io

    delim = ";" if text.split("\n", 1)[0].count(";") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)

    screening_resolutions: list[ScreeningDecision] = []
    coding_resolutions: dict[tuple[str, str], dict[str, Any]] = {}
    coding_reasons: dict[tuple[str, str], str] = {}
    skipped_unresolved = 0
    now = datetime.now(UTC).isoformat()

    for line_no, row in enumerate(reader, start=2):
        item_id = (row.get("item_id") or "").strip()
        dimension = (row.get("dimension") or "").strip()
        consensus_val = (row.get("consensus_value") or "").strip()
        row_resolver = (row.get("resolved_by") or resolver).strip() or resolver
        reason = (row.get("reason") or "").strip()

        if not consensus_val:
            skipped_unresolved += 1
            continue

        if dimension.lower() in ("screening", "triagem"):
            norm_dec = consensus_val.lower()
            norm_dec = DECISION_ALIASES.get(norm_dec, norm_dec)
            if norm_dec not in ("include", "exclude", "uncertain"):
                console.print(
                    f"[yellow]⚠ Linha {line_no}: decisão inválida '{consensus_val}' para item "
                    f"{item_id}. Ignorada.[/yellow]"
                )
                continue
            screening_resolutions.append(
                ScreeningDecision(
                    item_id=item_id,
                    coder=row_resolver,
                    decision=norm_dec,  # type: ignore[arg-type]
                    criteria_met=[],
                    reason=reason,
                    notes=(
                        f"Resolvido via disagreements.csv "
                        f"({row.get('coder_a')} × {row.get('coder_b')})"
                    ),
                    decided_at=now,
                    sheet_sha256=sheet_hash,
                )
            )
        else:
            item_vals = coding_resolutions.setdefault((item_id, row_resolver), {})
            if field_map and dimension in field_map:
                f = field_map[dimension]
                parse_problems: list[str] = []
                val = _parse(
                    f,
                    consensus_val,
                    allowed.get(f.name, []),
                    parse_problems,
                    f"linha {line_no} ({dimension})",
                )
                if parse_problems:
                    for p in parse_problems:
                        console.print(f"[yellow]⚠ {p}. Ignorada.[/yellow]")
                    continue
                item_vals[dimension] = val
            else:
                if "," in consensus_val or ";" in consensus_val:
                    item_vals[dimension] = [
                        v.strip() for v in consensus_val.replace(";", ",").split(",") if v.strip()
                    ]
                elif consensus_val.lower() in ("true", "sim", "yes", "s", "1"):
                    item_vals[dimension] = True
                elif consensus_val.lower() in ("false", "não", "nao", "no", "n", "0"):
                    item_vals[dimension] = False
                else:
                    item_vals[dimension] = consensus_val
            if reason:
                coding_reasons[(item_id, row_resolver)] = reason

    saved_scr = 0
    if screening_resolutions:
        append_decisions(_cli.DATA_DIR, run_id, screening_resolutions)
        saved_scr = len(screening_resolutions)

    saved_cod = 0
    if coding_resolutions:
        existing_codings = load_codings(_cli.DATA_DIR, run_id)
        new_codings: list[CodingRecord] = []
        for (item_id, row_res), vals in coding_resolutions.items():
            base_rec = existing_codings.get((item_id, row_res))
            merged_vals = dict(base_rec.values) if base_rec else {}
            merged_vals.update(vals)
            new_codings.append(
                CodingRecord(
                    item_id=item_id,
                    coder=row_res,
                    values=merged_vals,
                    notes=coding_reasons.get((item_id, row_res), "Resolvido via disagreements.csv"),
                    coded_at=now,
                    sheet_sha256=sheet_hash,
                )
            )
        append_codings(_cli.DATA_DIR, run_id, new_codings)
        saved_cod = len(new_codings)

    console.print(
        f"\n[bold green]✓ Resolução concluída:[/bold green] "
        f"[cyan]{saved_scr}[/cyan] item(ns) de triagem e "
        f"[cyan]{saved_cod}[/cyan] de codificação gravados como consenso."
    )
    if skipped_unresolved:
        console.print(
            f"[dim]({skipped_unresolved} linha(s) sem 'consensus_value' foram ignoradas)[/dim]"
        )

    if saved_scr > 0:
        try:
            from msrkit.cli.corpus import prisma

            console.print("\n[bold cyan]Atualizando fluxo PRISMA 2020...[/bold cyan]")
            prisma(run_id=run_id)
        except Exception as e:
            console.print(f"[dim](Nota: geração do PRISMA automático: {e})[/dim]")


@coding_app.command("tui")
def coding_tui(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", "-c", help="Coder name / identifier"),
    status: str = typer.Option(
        "include", "--status", help="Items by screening decision: include, all"
    ),
    limit: int | None = typer.Option(
        None, "--limit", "-l", help="Maximum items to load (default: all)"
    ),
) -> None:
    """Launch full-screen Textual TUI for interactive Annex A data extraction."""
    from rich.prompt import Prompt

    from msrkit.coding import load_codings, suggestions
    from msrkit.screening import final_decisions, load_decisions
    from msrkit.storage import ItemStorage

    try:
        from msrkit.tui import CodingApp
    except ImportError:
        console.print(
            "\n[bold yellow]⚠ Biblioteca 'textual' não instalada neste ambiente.[/bold yellow]\n"
            "[cyan]→ Use 'msrkit coding export' para preencher a planilha CSV.[/cyan]\n"
        )
        return

    run_id = _resolve_run(_unwrap(run_id))
    coder_str = _unwrap(coder).strip()
    if not coder_str:
        coder_str = Prompt.ask(
            "[bold cyan]Informe seu nome de avaliador (coder)[/bold cyan]"
        ).strip()
    is_valid, reason = is_valid_coder_name(coder_str)
    if not is_valid:
        console.print(f"[red]✗ Coder inválido: {reason}[/red]")
        raise typer.Exit(1)

    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    if not items:
        console.print(f"[yellow]Nenhum item encontrado na coleta '{run_id}'.[/yellow]")
        return

    status_str = _unwrap(status)
    if status_str != "all":
        decisions = load_decisions(_cli.DATA_DIR, run_id)
        final = final_decisions(decisions)
        included_items = [it for it in items if final.get(it.id) == "include"]
    else:
        included_items = items

    done = {item_id for (item_id, c) in load_codings(_cli.DATA_DIR, run_id) if c == coder_str}
    pending = [it for it in included_items if it.id not in done]

    if not pending:
        if not included_items:
            console.print(
                "[yellow]⚠ Nenhum item marcado como 'include' na triagem para esta coleta.[/yellow]"
            )
            console.print(
                "[dim]Dica: realize a triagem primeiro (Opção 2 - Triagem) "
                "para incluir itens.[/dim]"
            )
        else:
            console.print(
                f"[green]✓ Todos os {len(included_items)} itens elegíveis já foram codificados por "
                f"'{coder_str}'.[/green]"
            )
        return

    proto = _load_run_protocol(run_id, _unwrap(protocol))
    system_concepts = (
        tuple(proto.concepts.keys()) if proto and proto.concepts else ("rag", "agente")
    )
    detections = _load_detections(run_id)
    sug_map = suggestions(items, detections, system_concepts)

    session_items = pending if limit is None or limit <= 0 else pending[:limit]
    console.print(
        "\n[bold yellow]⚠ AVISO METODOLÓGICO (ADR-028):[/bold yellow] "
        "[yellow]A interface 'coding-tui' grava apenas 5 variáveis básicas "
        "(sistema, nível, oráculo, confiança, ferramentas).\n"
        "Para a extração científica do Anexo A com todas as 20 variáveis "
        "(métodos, modos de falha, agregação e critérios q_*),\n"
        "utilize o fluxo oficial CSV: 'msrkit coding export' e 'msrkit coding import'.[/yellow]\n"
    )
    app_coding = CodingApp(
        items=session_items,
        data_dir=_cli.DATA_DIR,
        run_id=run_id,
        coder=coder_str,
        suggestions_map=sug_map,
    )
    app_coding.run()
    if app_coding.recorded_codings:
        from msrkit.cli.corpus import prisma

        n_cod = len(app_coding.recorded_codings)
        console.print(f"\n[bold green]✓ {n_cod} itens codificados com sucesso![/bold green]")
        try:
            prisma(run_id=run_id)
        except Exception as e:
            console.print(f"[dim](Nota: geração do PRISMA automático: {e})[/dim]")


# Top-level convenient alias
app.command(name="coding-tui", help="Launch full-screen Textual TUI for Annex A data extraction.")(
    coding_tui
)
