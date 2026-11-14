"""Mining report of a run: what was collected, readable without opening the manifest.

Written after each collection (`msrkit report`, also called by the Coleta workflow)
and shipped in the reproducibility package, since `msrkit package` copies
reports/<run_id>/. It answers four questions — when (window and item dates), where
(sources and their status), what was searched (terms and literal queries) and how
much (counts at every stage) — and lists what may be missing.

Every number comes from the run's own files:

| Section                    | Source                                                        |
|----------------------------|---------------------------------------------------------------|
| Window, terms, options     | manifest `effective_protocol`, `env_overrides`, `run_options` |
| Sources and queries        | manifest `sources` (availability, planned, queries)           |
| Identified, filtered, dups | the same counts as the PRISMA flow (msrkit.prisma)            |
| Dates, kinds, terms        | items/<id>/items_deduped.jsonl (unique items)                 |
| Evidence (when present)    | enrich/<id>/github_repos.jsonl, extract/<id>/detections.jsonl |

The manifest lists executed queries only, so planned queries that never ran (a source
cap reached, an interrupted run) are reported as planned − executed.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

from msrkit.dedupe import canonicalize_url
from msrkit.extract import summarize
from msrkit.prisma import build_flow

if TYPE_CHECKING:
    from pathlib import Path

    from msrkit.enrich import RepoSignals
    from msrkit.extract import Detection
    from msrkit.models import Item, Manifest, SourceManifestEntry

# Ensure RepoSignals has reused_from dynamically if not present (ADR-054)
try:
    from msrkit.enrich import RepoSignals

    if "reused_from" not in RepoSignals.model_fields:
        from pydantic.fields import FieldInfo

        RepoSignals.model_fields["reused_from"] = FieldInfo(
            annotation=cast("Any", str | None), default=None
        )
        RepoSignals.model_rebuild(force=True)
except Exception:
    pass

TOP_TERMS = 15
TOP_ENTRIES = 15


class SourceSummary(BaseModel):
    name: str
    status: str
    reason: str
    planned: int
    executed: int
    requests: int
    returned: int  # items kept by the queries, before deduplication
    discarded: int  # dropped by the local term filter
    unique: int  # after deduplication
    truncated: dict[str, int] = {}  # truncation reason -> queries
    failed: int = 0  # queries with failed partitions or not completed
    literal: dict[str, int] = {}  # literal query label (template prefix) -> planned queries
    enabled: bool | None = None  # None: manifest older than `enabled_sources`
    skip_reason: str = ""  # why no query ran, when none did

    @property
    def not_executed(self) -> int:
        return max(self.planned - self.executed, 0)


class EntrySummary(BaseModel):
    entry: str
    kind: str  # tool or method
    n1: int
    n2: int
    n3: int

    @property
    def units(self) -> int:
        return self.n1 + self.n2 + self.n3


class EvidenceSummary(BaseModel):
    repos_enriched: int
    repos_with_ci: int
    repos_with_tests: int
    repos_reused: int = 0  # signals copied from an earlier run (ADR-054)
    detections: dict[str, int]  # level -> detections
    items_with_detection: int
    top: list[EntrySummary]


class MiningReport(BaseModel):
    run_id: str
    msrkit_version: str = ""
    protocol_path: str = ""
    protocol_sha256: str = ""
    started_at: str | None = None  # ISO 8601, UTC
    finished_at: str | None = None
    duration_s: float | None = None
    since: str | None = None
    until: str | None = None
    window_overridden: bool = False  # MSRKIT_WINDOW_* changed the protocol window
    run_options: dict[str, Any] = {}
    census: list[str] = []  # sources collected whole, without the per-query cap
    strata: str = "none"  # "year": date-filtered queries ran once per year (ADR-055)
    terms: dict[str, list[str]] = {}  # language -> protocol terms
    sources: list[SourceSummary] = []
    identified: int
    filtered: dict[str, int]  # local filter reason -> records
    kept: int
    duplicates: dict[str, int] | None  # url/content/near; None when dedupe has not run
    unique: int
    by_source_kind: dict[str, dict[str, int]]
    by_year: dict[str, int]
    undated: int
    oldest: str | None
    newest: str | None
    out_of_window: dict[str, int]  # source -> unique items dated outside the window
    # The protocol file's own window, when MSRKIT_WINDOW_* replaced it for this run.
    protocol_since: str | None = None
    protocol_until: str | None = None
    outside_protocol_window: int = 0  # dated unique items outside the protocol window
    # Earlier runs in data/ (e.g. the daily RSS accumulation restored from cache).
    earlier_runs: int = 0
    new_items: int | None = None  # unique items whose URL no earlier run had
    accumulated: int | None = None  # distinct URLs over earlier runs and this one
    seen_before: dict[str, int] = {}  # source -> unique items an earlier run also had
    term_hits: dict[str, int]  # term -> unique items that matched it
    term_languages: dict[str, int]  # "en", "pt", "en+pt"...; "" = no term matched
    concepts: dict[str, int]  # concept group -> unique items; "" = no concept
    evidence: EvidenceSummary | None = None
    warnings: list[str] = []


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _skip_reason(
    name: str, status: str, reason: str, enabled: list[str] | None, groups: dict[str, str]
) -> str:
    """Why a source ran no query: excluded from this run, or unavailable, or neither."""
    if enabled is None:
        return f"{status}: {reason}"
    group = groups.get(name)
    if group == "paid":
        return "fonte paga, fora da coleta (grupo `paid`)"
    if group == "unavailable":
        return f"indisponível (grupo `unavailable`): {reason}"
    if name not in enabled:
        if groups:
            return "fora desta execução (não está em `MSRKIT_SOURCES_PUBLIC` nem `_MANUAL`)"
        return "desativada no protocolo"
    if status != "OK":
        return f"habilitada, mas {status}: {reason}"
    return "habilitada e disponível, mas nenhuma consulta foi executada"


def _source_summary(
    src: SourceManifestEntry, unique: int, enabled: list[str] | None, groups: dict[str, str]
) -> SourceSummary:
    queries = src.queries
    status = src.availability.status.value
    return SourceSummary(
        name=src.name,
        status=status,
        reason=src.availability.reason,
        planned=len(src.planned),
        executed=len(queries),
        requests=sum(q.requests for q in queries),
        returned=sum(q.items for q in queries),
        discarded=sum(q.discarded for q in queries),
        unique=unique,
        truncated=dict(Counter(r for q in queries for r in q.truncation_reasons)),
        failed=sum(1 for q in queries if q.failed_partitions or not q.completed),
        # Template queries expand per anchor (`ci:ragas`); count them by template.
        literal=dict(
            Counter(
                str(p.get("label") or "").split(":", 1)[0] or "(sem rótulo)"
                for p in src.planned
                if p.get("raw")
            )
        ),
        enabled=None if enabled is None else src.name in enabled,
        skip_reason=""
        if queries
        else _skip_reason(src.name, status, src.availability.reason, enabled, groups),
    )


def _evidence(
    signals: dict[str, RepoSignals], detections: list[Detection]
) -> EvidenceSummary | None:
    if not signals and not detections:
        return None
    kinds = {d.entry_id: d.entry_type for d in detections}
    rows = [
        EntrySummary(entry=e, kind=kinds[e], n1=r["N1"], n2=r["N2"], n3=r["N3"])
        for e, r in summarize(detections).items()
    ]
    rows.sort(key=lambda r: (-r.units, r.entry))
    return EvidenceSummary(
        repos_enriched=len(signals),
        repos_with_ci=sum(s.has_ci for s in signals.values()),
        repos_with_tests=sum(s.has_tests for s in signals.values()),
        repos_reused=sum(getattr(s, "reused_from", None) is not None for s in signals.values()),
        detections=dict(sorted(Counter(d.level for d in detections).items())),
        items_with_detection=len({d.item_id for d in detections}),
        top=rows[:TOP_ENTRIES],
    )


def build_report(
    run_id: str,
    manifest: Manifest | None,
    items: list[Item],
    pool: list[Item],
    dedupe_report: dict[str, Any] | None,
    discard_file: Path,
    signals: dict[str, RepoSignals] | None = None,
    detections: list[Detection] | None = None,
    protocol_window: tuple[str, str] | None = None,
    earlier_urls: set[str] | None = None,
    earlier_runs: int = 0,
) -> MiningReport:
    """Summarize a run. `items` are the run's items, `pool` the deduplicated ones.

    `protocol_window` is the protocol file's (since, until), read without the
    MSRKIT_WINDOW_* overrides; `earlier_urls` are the item URLs of `earlier_runs`
    previous runs, to count what this run added.
    """
    flow = build_flow(run_id, manifest, items, pool, dedupe_report, discard_file, {}, set())
    eff = manifest.effective_protocol if manifest else {}
    window = eff.get("window") or {}
    since, until = _as_date(window.get("since")), _as_date(window.get("until"))
    p_since, p_until = (_as_date(d) for d in protocol_window or (None, None))
    if (p_since, p_until) == (since, until):
        p_since = p_until = None  # same window: nothing to contrast

    terms: dict[str, list[str]] = {}
    if eff.get("terms"):
        terms["en"] = list(eff["terms"])
    for lang, lexicon in (eff.get("terms_by_language") or {}).items():
        bucket = terms.setdefault(lang, [])
        bucket += [t for t in lexicon if t not in bucket]

    unique_by_source = Counter(it.source for it in pool)
    enabled = eff.get("enabled_sources")
    groups = eff.get("source_groups") or {}
    sources = [
        _source_summary(src, unique_by_source[src.name], enabled, groups)
        for src in (manifest.sources if manifest else [])
    ]

    by_source_kind: dict[str, Counter[str]] = {}
    by_year: Counter[str] = Counter()
    out_of_window: Counter[str] = Counter()
    term_hits: Counter[str] = Counter()
    term_languages: Counter[str] = Counter()
    concepts: Counter[str] = Counter()
    dates: list[date] = []
    outside_protocol = 0
    for it in pool:
        by_source_kind.setdefault(it.source, Counter())[it.kind.value] += 1
        if it.created_at is not None:
            day = it.created_at.date()
            dates.append(day)
            by_year[str(day.year)] += 1
            if (since and day < since) or (until and day > until):
                out_of_window[it.source] += 1
            if (p_since and day < p_since) or (p_until and day > p_until):
                outside_protocol += 1
        term_hits.update({h.term for h in it.matched_terms})
        langs = sorted({h.lang for h in it.matched_terms if h.lang})
        term_languages["+".join(langs) if it.matched_terms else ""] += 1
        concepts.update(it.concepts or [""])

    new_items = accumulated = None
    seen_before: Counter[str] = Counter()
    if earlier_runs:
        earlier = {canonicalize_url(u) for u in earlier_urls or set()}
        current = {canonicalize_url(str(it.url)) for it in pool}
        new_items, accumulated = len(current - earlier), len(current | earlier)
        seen_before.update({s: 0 for s in unique_by_source})
        seen_before.update(it.source for it in pool if canonicalize_url(str(it.url)) in earlier)

    started = manifest.started_at if manifest else None
    finished = manifest.finished_at if manifest else None
    report = MiningReport(
        run_id=run_id,
        msrkit_version=manifest.msrkit_version if manifest else "",
        protocol_path=manifest.protocol_path if manifest else "",
        protocol_sha256=manifest.protocol_sha256 if manifest else "",
        started_at=started.isoformat() if started else None,
        finished_at=finished.isoformat() if finished else None,
        duration_s=(finished - started).total_seconds() if started and finished else None,
        since=since.isoformat() if since else None,
        until=until.isoformat() if until else None,
        window_overridden=any(
            v.startswith("MSRKIT_WINDOW") for v in (manifest.env_overrides if manifest else [])
        ),
        run_options=manifest.run_options if manifest else {},
        census=list(eff.get("census_sources") or []),
        strata=eff.get("strata") or "none",
        terms=terms,
        sources=sources,
        identified=flow.total_identified,
        filtered=flow.filtered,
        # Same basis as the PRISMA identification: the manifest, when it has counts.
        kept=sum(s.returned for s in sources) or flow.after_filters,
        duplicates=flow.duplicates,
        unique=len(pool),
        by_source_kind={s: dict(c.most_common()) for s, c in sorted(by_source_kind.items())},
        by_year=dict(sorted(by_year.items())),
        undated=len(pool) - len(dates),
        oldest=min(dates).isoformat() if dates else None,
        newest=max(dates).isoformat() if dates else None,
        out_of_window=dict(out_of_window.most_common()),
        protocol_since=p_since.isoformat() if p_since else None,
        protocol_until=p_until.isoformat() if p_until else None,
        outside_protocol_window=outside_protocol,
        earlier_runs=earlier_runs,
        seen_before=dict(seen_before),
        new_items=new_items,
        accumulated=accumulated,
        term_hits=dict(term_hits.most_common()),
        term_languages=dict(term_languages.most_common()),
        concepts=dict(concepts.most_common()),
        evidence=_evidence(signals or {}, detections or []),
    )
    report.warnings = _warnings(report, manifest)
    return report


def _warnings(report: MiningReport, manifest: Manifest | None) -> list[str]:
    out: list[str] = []
    if manifest is None:
        out.append(
            "Sem manifesto: fontes, consultas e termos não puderam ser descritos; "
            "as contagens vêm só dos itens."
        )
    elif manifest.finished_at is None:
        out.append("A execução não registrou o término (interrompida ou ainda em andamento).")
    for s in report.sources:
        if s.enabled and s.status == "OK" and not s.executed:
            out.append(f"{s.name}: habilitada e disponível, mas nenhuma consulta foi executada.")
        if s.not_executed:
            out.append(
                f"{s.name}: {s.not_executed} de {s.planned} consultas planejadas não foram "
                "executadas (teto de itens da fonte atingido ou execução interrompida)."
            )
        if s.failed:
            out.append(f"{s.name}: {s.failed} consultas falharam ou ficaram incompletas.")
        if s.truncated.get("source_cap"):
            out.append(
                f"{s.name}: {s.truncated['source_cap']} consultas bateram no teto de resultados "
                "da API sem conseguir particionar; parte dos resultados não foi recuperada."
            )
        if s.truncated.get("no_historical_coverage"):
            out.append(f"{s.name}: a fonte só entrega itens recentes (sem cobertura histórica).")
    capped = sum(s.truncated.get("item_limit", 0) for s in report.sources)
    if capped:
        limit = report.run_options.get("per_query_limit") or report.run_options.get("limit")
        how = f" (limite = {limit})" if limit else ""
        out.append(
            f"{capped} consultas pararam no limite de itens configurado{how}: "
            "a coleta é uma amostra, não o total disponível nas fontes."
        )
    if report.out_of_window:
        detail = ", ".join(f"{s} {n}" for s, n in report.out_of_window.items())
        out.append(
            f"{sum(report.out_of_window.values())} itens únicos têm data fora da janela ({detail})."
        )
    if report.protocol_since or report.protocol_until:
        out.append(
            f"A janela desta execução ({report.since} a {report.until}) difere da do protocolo "
            f"({report.protocol_since} a {report.protocol_until}): "
            f"{report.outside_protocol_window} itens únicos ficam fora da janela do protocolo e "
            "só entram no corpus do estudo com desvio registrado."
        )
    if report.duplicates is None:
        out.append("Deduplicação não executada (`msrkit dedupe`): os únicos são os itens brutos.")
    return out


# -- Markdown ---------------------------------------------------------------------


def _n(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _when(iso: str | None) -> str:
    return f"{iso[:16].replace('T', ' ')} UTC" if iso else "?"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "duração desconhecida"
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min" if minutes else f"{secs} s"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _summary(r: MiningReport) -> list[str]:
    consulted = sorted(s.name for s in r.sources if s.executed)
    dup = sum(r.duplicates.values()) if r.duplicates is not None else None
    text = (
        f"A execução consultou {len(consulted)} fonte(s)"
        + (f" ({', '.join(consulted)})" if consulted else "")
        + f" entre {_when(r.started_at)} e {_when(r.finished_at)}"
        + f" ({_duration(r.duration_s)}), buscando publicações de {r.since or '?'} a "
        + f"{r.until or '?'}. Foram identificados **{_n(r.identified)} registros**; "
        + f"{_n(sum(r.filtered.values()))} foram descartados pelo filtro local de termos"
        + (f" e {_n(dup)} eram duplicatas" if dup is not None else "")
        + f", restando **{_n(r.unique)} itens únicos**."
    )
    if r.new_items is not None:
        text += (
            f" Destes, {_n(r.new_items)} não apareciam nas {_n(r.earlier_runs)} execuções"
            f" anteriores; o acumulado soma {_n(r.accumulated or 0)} itens."
        )
    if ev := r.evidence:
        if ev.repos_enriched:
            text += f" O enriquecimento analisou {_n(ev.repos_enriched)} repositórios do GitHub."
        text += (
            f" A extração encontrou {_n(sum(ev.detections.values()))} detecções"
            " de ferramentas e métodos."
        )
    return ["## Resumo", "", text, ""]


def _time(r: MiningReport) -> list[str]:
    window = f"{r.since or '?'} a {r.until or '?'}"
    lines = ["## Espaço temporal", ""]
    lines.append(
        f"- Janela de publicação buscada: **{window}**"
        + (" (ajustada por variável de ambiente `MSRKIT_WINDOW_*`)" if r.window_overridden else "")
    )
    if r.protocol_since or r.protocol_until:
        lines.append(
            f"- Janela do protocolo: **{r.protocol_since} a {r.protocol_until}** "
            f"({_n(r.outside_protocol_window)} itens únicos fora dela)"
        )
    lines.append(
        f"- Execução: {_when(r.started_at)} → {_when(r.finished_at)} ({_duration(r.duration_s)})"
    )
    dated = r.unique - r.undated
    if dated:
        undated = f"; {_n(r.undated)} sem data (ex.: hits de código do GitHub)" if r.undated else ""
        lines.append(f"- Itens únicos com data: {_n(dated)}, de {r.oldest} a {r.newest}{undated}")
        lines += ["", "| Ano | Itens únicos |", "|---|---:|"]
        lines += [f"| {y} | {_n(n)} |" for y, n in r.by_year.items()]
    else:
        lines.append(f"- Nenhum item único com data ({_n(r.undated)} sem data)")
    return [*lines, ""]


def _places(r: MiningReport) -> list[str]:
    lines = ["## Locais (fontes)", ""]
    used = [s for s in r.sources if s.executed]
    if used:
        lines += [
            "| Fonte | Consultas (exec./plan.) | Requisições | Retornados | "
            "Descartados no filtro | Únicos | Truncadas (motivo) |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
        for s in used:
            trunc = ", ".join(f"{k} {v}" for k, v in s.truncated.items()) or "—"
            lines.append(
                f"| {s.name} | {_n(s.executed)}/{_n(s.planned)} | {_n(s.requests)} | "
                f"{_n(s.returned)} | {_n(s.discarded)} | {_n(s.unique)} | {trunc} |"
            )
        lines.append("")
    skipped = [s for s in r.sources if not s.executed]
    if skipped:
        lines += ["Fontes não consultadas:", ""]
        for s in skipped:
            if s.enabled is None:
                lines.append(f"- **{s.name}** ({s.status}): {_cell(s.reason)}")
            else:
                lines.append(f"- **{s.name}**: {_cell(s.skip_reason)}")
        lines.append("")
    if r.by_source_kind:
        lines += [
            "Itens únicos por fonte e tipo:",
            "",
            "| Fonte | Tipo | Itens |",
            "|---|---|---:|",
        ]
        for source, kinds in r.by_source_kind.items():
            lines += [f"| {source} | {k} | {_n(n)} |" for k, n in kinds.items()]
        lines.append("")
    return lines


def _searched(r: MiningReport) -> list[str]:
    names = {"en": "Inglês", "pt": "Português"}
    lines = ["## Termos buscados", ""]
    for lang, lexicon in r.terms.items():
        joined = ", ".join(f"`{t}`" for t in lexicon)
        lines.append(f"- **{names.get(lang, lang)} ({len(lexicon)}):** {joined}")
    for s in r.sources:
        if s.literal:
            labels = ", ".join(f"{k} ({v})" for k, v in s.literal.items())
            lines.append(f"- **Consultas literais em {s.name}** (protocolo `queries:`): {labels}")
    options = ", ".join(f"`{k}={v}`" for k, v in r.run_options.items()) or "nenhuma"
    lines.append(f"- Opções da execução: {options}")
    if r.strata == "year":
        lines.append(
            "- Amostra estratificada por ano: cada consulta das fontes que filtram por data "
            "rodou uma vez por ano da janela, cada ano com o limite por consulta"
        )
    if r.census:
        lines.append(
            f"- Coletadas inteiras na janela, sem o limite por consulta: {', '.join(r.census)}"
        )
    lines.append("")

    lang_of = {t: lang for lang, lexicon in r.terms.items() for t in lexicon}
    if r.term_hits:
        lines += [
            f"Termos que mais casaram (até {TOP_TERMS}):",
            "",
            "| Termo | Idioma | Itens únicos |",
            "|---|---|---:|",
        ]
        for term, n in list(r.term_hits.items())[:TOP_TERMS]:
            lines.append(f"| `{_cell(term)}` | {lang_of.get(term, '?')} | {_n(n)} |")
        lines.append("")
    unmatched = [t for t in lang_of if t not in r.term_hits]
    if unmatched:
        lines += ["Termos sem nenhum casamento: " + ", ".join(f"`{t}`" for t in unmatched), ""]
    if r.term_languages:
        parts = [
            f"{lang or 'nenhum termo (consultas literais)'}: {_n(n)}"
            for lang, n in r.term_languages.items()
        ]
        lines += ["Idioma dos termos casados por item: " + " · ".join(parts), ""]
    return lines


def _counts(r: MiningReport) -> list[str]:
    lines = ["## Quantidades", "", "| Etapa | Registros |", "|---|---:|"]
    lines.append(f"| Identificados nas fontes | {_n(r.identified)} |")
    for reason, n in r.filtered.items():
        lines.append(f"| − Descartados pelo filtro local ({reason}) | {_n(n)} |")
    lines.append(f"| Mantidos | {_n(r.kept)} |")
    if r.duplicates is None:
        lines.append("| − Duplicatas | n/a |")
    else:
        for reason, n in r.duplicates.items():
            lines.append(f"| − Duplicatas ({reason}) | {_n(n)} |")
    lines += [f"| **Itens únicos** | **{_n(r.unique)}** |", ""]
    if r.new_items is not None:
        lines += [
            "### Acúmulo entre execuções",
            "",
            f"- Execuções anteriores em `data/`: {_n(r.earlier_runs)}",
            f"- Itens desta execução que nenhuma anterior trouxe: **{_n(r.new_items)}** "
            f"de {_n(r.unique)}",
            f"- Total acumulado (URLs distintas em todas as execuções): {_n(r.accumulated or 0)}",
            "",
        ]
    if r.seen_before:
        lines += [
            "Itens únicos que alguma execução anterior também trouxe, por fonte (o quanto a "
            "busca se repete entre coletas):",
            "",
            "| Fonte | Itens únicos | Também em anteriores | % |",
            "|---|---:|---:|---:|",
        ]
        for source, seen in r.seen_before.items():
            unique = sum(r.by_source_kind.get(source, {}).values())
            share = f"{seen / unique:.0%}" if unique else "—"
            lines.append(f"| {source} | {_n(unique)} | {_n(seen)} | {share} |")
        lines.append("")
    if r.concepts:
        lines += [
            "Conceitos do protocolo nos itens únicos:",
            "",
            "| Conceito | Itens |",
            "|---|---:|",
        ]
        lines += [f"| {c or '(nenhum)'} | {_n(n)} |" for c, n in r.concepts.items()]
        lines.append("")
    if ev := r.evidence:
        levels = " · ".join(f"{lvl} {_n(n)}" for lvl, n in ev.detections.items()) or "nenhuma"
        lines += ["### Evidência (enriquecimento e extração)", ""]
        if ev.repos_enriched:
            reused = (
                f"; {_n(ev.repos_reused)} reaproveitados de execuções anteriores"
                if ev.repos_reused
                else ""
            )
            lines.append(
                f"- Repositórios do GitHub enriquecidos: {_n(ev.repos_enriched)} "
                f"(com CI: {_n(ev.repos_with_ci)}; com testes: {_n(ev.repos_with_tests)}{reused})"
            )
        lines += [f"- Detecções: {levels}, em {_n(ev.items_with_detection)} itens", ""]
        if ev.top:
            lines += [
                f"Entradas do gazetteer com mais unidades (até {TOP_ENTRIES}; unidade = "
                "repositório ou item, contada no maior nível):",
                "",
                "| Entrada | Tipo | N1 | N2 | N3 | Unidades |",
                "|---|---|---:|---:|---:|---:|",
            ]
            lines += [
                f"| {e.entry} | {e.kind} | {_n(e.n1)} | {_n(e.n2)} | {_n(e.n3)} | {_n(e.units)} |"
                for e in ev.top
            ]
            lines.append("")
    return lines


def render_markdown(report: MiningReport) -> str:
    sha = f" (`{report.protocol_sha256[:12]}`)" if report.protocol_sha256 else ""
    lines = [f"# Relatório de mineração — {report.run_id}", ""]
    lines += [
        f"Gerado por `msrkit report` a partir dos arquivos da execução · MSR-Kit "
        f"{report.msrkit_version or '?'} · protocolo `{report.protocol_path or '?'}`{sha}",
        "",
    ]
    lines += _summary(report) + _time(report) + _places(report)
    lines += _searched(report) + _counts(report)
    lines += ["## Avisos", ""]
    lines += [f"- {w}" for w in report.warnings] or ["Nenhum aviso."]
    return "\n".join([*lines, ""])
