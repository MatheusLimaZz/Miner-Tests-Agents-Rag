#!/usr/bin/env python3
"""Coleta completa local: as etapas do workflow Coleta, sem o teto de tempo do Actions.

O job do Actions tem 240 min. Uma coleta no tamanho da d28a (100 itens por consulta e
todos os repositórios do GitHub enriquecidos) passa disso, então a coleta oficial roda
na máquina local ou no container. Este script executa as mesmas etapas de
.github/workflows/collect.yml, grava tudo num log em data/logs/ e, se cair no meio,
continua de onde parou com --retomar.

Uso, na raiz do repositório:

    python scripts/coleta_local.py                     # coleta completa (padrões abaixo)
    python scripts/coleta_local.py --retomar <run_id>  # continua uma execução interrompida
    python scripts/coleta_local.py --help

Credenciais vêm do .env: GITHUB_TOKEN é obrigatório para o GitHub; STACKEXCHANGE_KEY e
HF_TOKEN aumentam as cotas. Janela e termos vêm só do protocolo: MSRKIT_WINDOW_* e
MSRKIT_TERMS* do .env são ignoradas aqui, como no Actions, para que a coleta oficial
siga o protocolo versionado.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from rich.console import Console, RenderableType
    from rich.text import Text

    from msrkit.config import ProtocolConfig

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = "protocols/v0_rag_agents_testing.yaml"
# Os grupos de fontes do workflow Coleta, sem o RSS: os feeds só trazem posts recentes e
# nunca têm itens da janela do protocolo; o RSS fica no monitoramento diário do Actions.
PUBLIC = "hackernews,devto,stackexchange,huggingface"
MANUAL = "github"
PAID = "x_twitter"
UNAVAILABLE = "linkedin,discord,bluesky"
# Parâmetros de busca que só o protocolo define na coleta oficial.
SEARCH_OVERRIDES = ("MSRKIT_WINDOW_", "MSRKIT_TERMS")

# Abertura: "MSR-KIT" na fonte ANSI Shadow, pintado em degradê da esquerda para a direita.
LOGO = """\
███╗   ███╗███████╗██████╗       ██╗  ██╗██╗████████╗
████╗ ████║██╔════╝██╔══██╗      ██║ ██╔╝██║╚══██╔══╝
██╔████╔██║███████╗██████╔╝█████╗█████╔╝ ██║   ██║
██║╚██╔╝██║╚════██║██╔══██╗╚════╝██╔═██╗ ██║   ██║
██║ ╚═╝ ██║███████║██║  ██║      ██║  ██╗██║   ██║
╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝      ╚═╝  ╚═╝╚═╝   ╚═╝
"""
GRADIENT = ((45, 212, 191), (59, 130, 246), (168, 85, 247))  # turquesa, azul, violeta
ACCENT = "#818cf8"


@dataclass
class Step:
    name: str
    args: list[str]  # argumentos do `msrkit`
    fatal: bool = True  # se falhar, para a coleta (as demais seguem e são relatadas)


Runner = Callable[[Step, Mapping[str, str], Path, TextIO], int]


def child_env(
    environ: Mapping[str, str], dotenv: Mapping[str, str], public: str, manual: str
) -> dict[str, str]:
    """Ambiente dos comandos: grupos de fontes fixos e busca só pelo protocolo.

    Uma variável vazia não sobrepõe o protocolo e impede o msrkit de ler a do .env
    (variáveis já definidas têm precedência sobre o arquivo).
    """
    env = dict(environ)
    for key in {*environ, *dotenv}:
        if key.startswith(SEARCH_OVERRIDES):
            env[key] = ""
    env.update(
        MSRKIT_SOURCES_PUBLIC=public,
        MSRKIT_SOURCES_MANUAL=manual,
        MSRKIT_SOURCES_PAID=PAID,
        MSRKIT_SOURCES_UNAVAILABLE=UNAVAILABLE,
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
    return env


def pipeline(
    run_id: str,
    protocol: str,
    *,
    enrich: bool,
    enrich_limit: int = 0,
    graphql: bool = True,
    reuse_days: int = 0,
    analyses: bool = True,
) -> list[Step]:
    """Etapas depois da coleta, na ordem do workflow, todas presas a `run_id`."""
    p, r = ["-p", protocol], ["--run", run_id]
    steps = [Step("Deduplicação", ["dedupe", *r])]
    if enrich:
        cmd = ["enrich", *p, *r]
        if enrich_limit:
            cmd += ["--limit", str(enrich_limit)]
        if graphql:
            cmd.append("--graphql")
        if reuse_days:
            cmd += ["--reuse-days", str(reuse_days)]
        steps.append(Step("Enriquecimento do GitHub (N2/N3)", cmd))
    steps += [
        Step("Extração de ferramentas e métodos", ["extract", *p, *r]),
        Step("Planilha de triagem", ["screen", "export", *p, *r]),
        Step("Fluxo PRISMA", ["prisma", *r]),
        Step("Relatório da mineração", ["report", *r]),
        Step(
            "Exportação CSV", ["export", *r, "-f", "csv", "-o", f"data/exports/itens_{run_id}.csv"]
        ),
        Step("Pacote de reprodutibilidade", ["package", *p, *r]),
    ]
    if analyses:
        detections = ["--basis", "detections"]
        steps += [
            Step(
                "A1: frequência",
                ["analyze", "frequency", *detections, "--top", "30", *p, *r],
                False,
            ),
            Step(
                "A2: coocorrência",
                [
                    "analyze",
                    "frequency",
                    *detections,
                    "--pairs",
                    "tool-tool",
                    "--top",
                    "15",
                    *p,
                    *r,
                ],
                False,
            ),
            Step("A4: escada de oráculos", ["analyze", "oracles", *detections, *p, *r], False),
            Step("A6: RAG × agentes", ["analyze", "compare", *detections, *p, *r], False),
            Step(
                "A5: descoberta aberta",
                [
                    "analyze",
                    "topics",
                    "--status",
                    "all",
                    "--topics",
                    "12",
                    "--clusters",
                    "12",
                    *p,
                    *r,
                ],
                False,
            ),
            Step("Amostra de precisão", ["precision", "sample", "--n", "200", *r], False),
        ]
    steps.append(Step("Estatísticas", ["stats", *r], False))
    return steps


def run_dirs(runs_dir: Path) -> set[str]:
    if not runs_dir.is_dir():
        return set()
    return {p.name for p in runs_dir.iterdir() if p.is_dir() and p.name != "consolidated"}


def new_run_id(before: set[str], runs_dir: Path) -> str | None:
    """O run criado pela coleta: o diretório novo em data/runs/."""
    created = sorted(run_dirs(runs_dir) - before)
    return created[-1] if created else None


def run_step(step: Step, env: Mapping[str, str], cwd: Path, log: TextIO) -> int:
    """Roda `msrkit <args>` com a saída na tela, recuada sob o título da etapa, e no log."""
    from rich.console import Console
    from rich.text import Text

    screen = Console(highlight=False, soft_wrap=True)
    cmd = [sys.executable, "-m", "msrkit.cli", *step.args]
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        screen.print(Text.assemble(("  │ ", f"dim {ACCENT}"), line.rstrip("\r\n")))
        log.write(line)
    return proc.wait()


def keep_awake(on: bool) -> None:
    """No Windows, impede a suspensão por inatividade enquanto a coleta roda."""
    if sys.platform != "win32":
        return
    import ctypes

    es_continuous, es_system_required = 0x80000000, 0x00000001
    flags = es_continuous | (es_system_required if on else 0)
    # windll só existe no Windows: mypy fora dele não conhece o atributo.
    ctypes.windll.kernel32.SetThreadExecutionState(flags)  # type: ignore[attr-defined,unused-ignore]


def utf8_output() -> None:
    """Saída em UTF-8: no Windows, redirecionada para arquivo, o padrão é cp1252."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def ansi_console() -> None:
    """No Windows, liga as sequências ANSI no console (como o terminal do VS Code já faz).

    Sem elas o rich cai nas 16 cores da API antiga do console e o degradê some. Saída
    redirecionada para arquivo não tem modo de console e fica como está.
    """
    if sys.platform != "win32":
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def check_stackexchange_key(key: str) -> tuple[bool, str]:
    """Confere a chave com uma chamada a /info (gasta 1 requisição da cota).

    Chave inválida faz toda busca voltar 400 e o Stack Exchange sair vazio sem que a
    coleta falhe; por isso o script para antes. Falha de rede não impede a coleta.
    """
    import httpx

    try:
        resp = httpx.get(
            "https://api.stackexchange.com/2.3/info",
            params={"site": "stackoverflow", "key": key},
            headers={"Accept-Encoding": "gzip"},
            timeout=30,
        )
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return True, f"aviso: não deu para conferir a STACKEXCHANGE_KEY ({exc})."
    if "error_id" in data:
        return False, f"{data.get('error_name')}: {data.get('error_message')}"
    return True, (
        f"STACKEXCHANGE_KEY válida: cota de {data.get('quota_max')} req/dia, "
        f"restam {data.get('quota_remaining')}."
    )


def _duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min {secs:02d} s"


class Output:
    """A tela, em cores, e o log, em texto puro, recebem o mesmo conteúdo."""

    def __init__(self) -> None:
        from rich.console import Console

        self.screen = Console(highlight=False)
        self.log: Console | None = None

    def open_log(self, file: TextIO) -> None:
        from rich.console import Console

        self.log = Console(
            file=file, width=100, color_system=None, highlight=False, legacy_windows=False
        )

    def show(
        self, *items: RenderableType, screen_only: bool = False, soft_wrap: bool = False
    ) -> None:
        """Cada item numa linha (print(*items) os juntaria na mesma, separados por espaço)."""
        for item in items:
            self.screen.print(item, soft_wrap=soft_wrap)
            if self.log is not None and not screen_only:
                self.log.print(item, soft_wrap=soft_wrap)

    def fail(self, title: str, detail: str = "") -> None:
        """Erro que impede a coleta de começar, sem quebra de linha para copiar o texto."""
        from rich.text import Text

        self.show(Text.assemble(("✗ ", "bold red"), (title, "bold")), soft_wrap=True)
        if detail:
            self.show(Text(detail), soft_wrap=True)


def _blend(t: float) -> tuple[int, int, int]:
    """Cor do degradê na posição t, de 0 (esquerda) a 1 (direita)."""
    pos = t * (len(GRADIENT) - 1)
    i = min(int(pos), len(GRADIENT) - 2)
    (r0, g0, b0), (r1, g1, b1) = GRADIENT[i], GRADIENT[i + 1]
    f = pos - i
    return round(r0 + (r1 - r0) * f), round(g0 + (g1 - g0) * f), round(b0 + (b1 - b0) * f)


def logo(width: int) -> Text:
    """O logotipo em degradê; as bordas dos blocos, mais escuras, fazem a sombra.

    Num terminal mais estreito que o desenho, só o nome.
    """
    from rich.text import Text

    lines = LOGO.splitlines()
    size = max(map(len, lines))
    if width < size + 4:
        r, g, b = _blend(0.5)
        return Text("  MSR-KIT", style=f"bold #{r:02x}{g:02x}{b:02x}")
    art = Text()
    for line in lines:
        art.append("  ")
        for x, char in enumerate(line):
            if char == " ":
                art.append(char)
                continue
            r, g, b = _blend(x / (size - 1))
            if char != "█":
                r, g, b = round(r * 0.55), round(g * 0.55), round(b * 0.55)
            art.append(char, style=f"#{r:02x}{g:02x}{b:02x}")
        art.append("\n")
    art.rstrip()
    return art


def tagline() -> Text:
    from rich.text import Text

    return Text.assemble(
        "  ",
        ("Mining Software Repositories", "bold"),
        (" · testes de sistemas RAG e de agentes", "dim"),
    )


def _sample(args: argparse.Namespace, strata: bool) -> str:
    if args.retomar:
        return "a da execução retomada"
    if not args.por_consulta:
        text = "sem teto por consulta"
    else:
        text = f"{args.por_consulta} itens por consulta" + (" e por ano" if strata else "")
    return text + (f" · até {args.limite} por fonte" if args.limite else "")


def _enrichment(args: argparse.Namespace) -> str:
    scope = f"até {args.enriquecer}" if args.enriquecer else "todos os"
    text = f"{scope} repositórios do GitHub · {'REST' if args.sem_graphql else 'GraphQL'}"
    if args.reaproveitar_dias:
        text += f" · reaproveita até {args.reaproveitar_dias} dias"
    return text


def banner(
    args: argparse.Namespace,
    cfg: ProtocolConfig | None,
    protocol: Path,
    sources: list[str],
    github: bool,
    log_path: Path,
    total: int,
) -> RenderableType:
    """O painel de abertura: o que a coleta vai fazer e onde grava."""
    from rich import box
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from msrkit import __version__
    from msrkit.config import protocol_sha256

    work = Path(args.pasta).resolve()
    strata = cfg is not None and cfg.sampling.strata == "year"
    census = {n for n, s in cfg.sources.items() if s.census} if cfg is not None else set()
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style=f"bold {ACCENT}", no_wrap=True)
    grid.add_column()
    if cfg is None:
        grid.add_row("Protocolo", _shown(protocol, ROOT))
    else:
        sha = protocol_sha256(protocol)[:12]
        version = (f" · versão {cfg.version} · sha256 {sha}", "dim")
        grid.add_row("Protocolo", Text.assemble((cfg.name, "bold"), version))
        by_year = (" · estratificada por ano" if strata else "", "dim")
        grid.add_row("Janela", Text.assemble(f"{cfg.window.since} → {cfg.window.until}", by_year))
    names = [
        Text.assemble((name, "bold"), (" (censo)" if name in census else "", "dim"))
        for name in sources
    ]
    grid.add_row("Fontes", Text(" · ", style="dim").join(names))
    grid.add_row("Amostra", Text(_sample(args, strata)))
    if github:
        grid.add_row("Enriquecimento", Text(_enrichment(args)))
    analyses = "puladas" if args.sem_analises else "A1–A6 e amostra de precisão"
    grid.add_row("Análises", Text(analyses))
    if args.retomar:
        grid.add_row("Retomando", Text(args.retomar, style="bold"))
    grid.add_row("Pasta", Text(_home(work)))
    grid.add_row("Log", _shown(log_path, work))
    return Panel(
        grid,
        title="[bold]◆ Coleta completa local[/]",
        title_align="left",
        subtitle=f"[dim]msrkit {__version__} · {len(sources)} fontes · {total} etapas[/]",
        subtitle_align="right",
        border_style=ACCENT,
        box=box.ROUNDED,
        padding=(1, 2),
        expand=False,
    )


def startup_checks(
    github: bool, se_status: str, credential: Callable[[str], bool], ignored: list[str]
) -> list[Text]:
    """Credenciais (só se existem, nunca o valor) e variáveis do .env que o protocolo ignora."""
    checks = [_check(True, "GITHUB_TOKEN encontrado")] if github else []
    if se_status:
        checks.append(_check(not se_status.startswith("aviso:"), se_status))
    for name in ("STACKEXCHANGE_KEY", "HF_TOKEN"):
        if not credential(name):
            checks.append(_check(False, f"{name} vazio; a fonte roda com cota menor."))
    if credential("HF_TOKEN"):
        checks.append(_check(True, "HF_TOKEN encontrado"))
    if ignored:
        checks.append(_check(False, f"ignorando do .env (vale o protocolo): {', '.join(ignored)}"))
    return checks


def code_check(state: Mapping[str, object]) -> Text:
    """O commit do código que vai coletar, e se a pasta de trabalho bate com ele.

    O manifesto e o pacote registram o commit. Com alterações ainda sem commit, esse
    commit não reproduz a execução: o pacote sai marcado, e é preciso commitar e
    regenerá-lo (`msrkit package --run <id>`) antes de publicar.
    """
    commit = str(state.get("git_commit") or "")
    if not commit:
        return _check(False, "Código fora de um repositório git: o pacote não registra o commit.")
    if state.get("git_dirty"):
        return _check(
            False,
            f"Há alterações sem commit sobre {commit[:7]}: o pacote vai sair marcado com "
            "alterações pendentes. Para o pacote reproduzir a coleta, faça o commit antes.",
        )
    return _check(True, f"Código no commit {commit[:7]}, sem alterações pendentes")


def tips(args: argparse.Namespace, github: bool) -> list[Text]:
    from rich.text import Text

    lines = ["Ctrl+C interrompe; depois, --retomar <run_id> continua de onde parou."]
    if github and args.reaproveitar_dias:
        lines.append(
            "A coleta leva de 1 a 2 h; o enriquecimento só busca os repositórios que "
            "nenhuma execução recente buscou."
        )
    elif github:
        lines.append(
            "A coleta leva de 1 a 2 h, e o enriquecimento de todos os repositórios, "
            "algumas horas a mais."
        )
    if sys.platform == "win32":
        lines.append("O Windows não entra em suspensão enquanto a coleta roda.")
    numbered = [Text.assemble((f"  {i}. ", ACCENT), tip) for i, tip in enumerate(lines, start=1)]
    return [Text(), Text("  Dicas", style="bold"), *numbered]


def _check(ok: bool, text: str) -> Text:
    from rich.text import Text

    return Text.assemble(("  ✓ ", "bold green") if ok else ("  ▲ ", "bold yellow"), text)


def _shown(path: Path, base: Path) -> Text:
    """Caminho relativo à pasta de trabalho, com link para o arquivo nos terminais que aceitam."""
    from rich.style import Style
    from rich.text import Text

    try:
        shown = str(path.relative_to(base))
    except ValueError:
        shown = str(path)
    return Text(shown, style=Style(link=path.as_uri()))


def _home(path: Path) -> str:
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def step_header(number: int, total: int, step: Step) -> list[RenderableType]:
    from rich.rule import Rule
    from rich.text import Text

    title = Text.assemble(("◆ ", ACCENT), (f"{number}/{total}  ", "dim"), (step.name, "bold"))
    started = datetime.now().strftime("%H:%M:%S")
    # Caminhos do repositório, como o do protocolo, aparecem relativos à raiz.
    args = " ".join(a.removeprefix(str(ROOT) + os.sep) for a in step.args)
    command = Text.assemble(("  " + started, "dim"), ("  msrkit " + args, "dim"))
    return [Text(), Rule(title, align="left", style=ACCENT), command]


def step_result(name: str, seconds: float, rc: int) -> Text:
    from rich.text import Text

    if rc == 0:
        return Text.assemble(("  ✓ ", "bold green"), (_duration(seconds), "dim"))
    return Text.assemble(
        ("  ✗ ", "bold red"),
        (f"{name} terminou com erro (código {rc})", "red"),
        (f" · {_duration(seconds)}", "dim"),
    )


def _resume_hint(out: Output, run_id: str | None) -> None:
    from rich.text import Text

    if run_id:
        out.show(Text("\nPara continuar de onde parou:"))
        out.show(
            Text(f"    python scripts/coleta_local.py --retomar {run_id}", style="bold cyan"),
            soft_wrap=True,
        )
    else:
        out.show(Text("\nA coleta não chegou a criar uma execução; rode o script de novo."))


def final_summary(
    timings: list[tuple[str, float, int]], run_id: str, elapsed: float, work: Path, log_path: Path
) -> list[RenderableType]:
    """O fecho: tempo de cada etapa, o tamanho do corpus e onde estão os arquivos."""
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    failed = sum(1 for _, _, rc in timings if rc)
    color = "yellow" if failed else "green"
    headline = Text.assemble(
        ("▲ " if failed else "✓ ", f"bold {color}"),
        (f"Concluído em {_duration(elapsed)}", "bold"),
        (" · execução ", "dim"),
        (run_id, f"bold {ACCENT}"),
        (f" · {failed} etapa(s) com erro" if failed else "", color),
    )
    steps = Table.grid(padding=(0, 2))
    steps.add_column(no_wrap=True)
    steps.add_column(justify="right", style="dim", no_wrap=True)
    steps.add_column()
    for name, seconds, rc in timings:
        mark = Text("✗", style="bold red") if rc else Text("✓", style="green")
        steps.add_row(mark, _duration(seconds), name)
    items: list[RenderableType] = [
        Text(),
        Panel(headline, border_style=color, expand=False, padding=(0, 2)),
        Padding(steps, (1, 0, 0, 2), expand=False),
    ]

    data = work / "data"
    report = data / "reports" / run_id / "relatorio_mineracao.json"
    if report.is_file():
        rep = json.loads(report.read_text(encoding="utf-8"))
        identified, unique = str(rep["identified"]), str(rep["unique"])
        items += [
            Text(),
            Text.assemble(
                "  ", (identified, "bold"), " identificados → ",
                (unique, f"bold {ACCENT}"), " itens únicos",
            ),
        ]  # fmt: skip
        items += [Text.assemble(("  ▲ aviso: ", "yellow"), w) for w in rep.get("warnings", [])]

    files = Table.grid(padding=(0, 3))
    files.add_column(style=f"bold {ACCENT}", no_wrap=True)
    files.add_column()
    for label, path in (
        ("Relatório", data / "reports" / run_id / "relatorio_mineracao.md"),
        ("PRISMA", data / "reports" / run_id / "prisma.md"),
        ("CSV", data / "exports" / f"itens_{run_id}.csv"),
        ("Pacote", data / "packages" / f"msrkit_{run_id}.zip"),
        ("Log", log_path),
    ):
        files.add_row(label, _shown(path, work))
    items += [Text(), Text("  Arquivos", style="bold"), Padding(files, (0, 0, 0, 4), expand=False)]
    return items


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Coleta completa local (as etapas do workflow Coleta, sem teto de tempo).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--por-consulta", type=int, default=100,
                    help="máximo de itens por consulta (0 = sem teto)")  # fmt: skip
    ap.add_argument("--limite", type=int, default=0,
                    help="máximo de itens por fonte (0 = o do protocolo)")  # fmt: skip
    ap.add_argument("--enriquecer", type=int, default=0,
                    help="máximo de repositórios do GitHub enriquecidos (0 = todos)")  # fmt: skip
    ap.add_argument("--sem-graphql", action="store_true",
                    help="enriquecer só pela API REST (mais lento)")  # fmt: skip
    ap.add_argument("--reaproveitar-dias", type=int, default=14,
                    help="reaproveita o enriquecimento de repositórios feito por outra "
                         "execução nos últimos N dias, com a mesma janela "
                         "(0 = enriquece tudo de novo)")  # fmt: skip
    ap.add_argument("--fontes", default=PUBLIC, help="fontes públicas, separadas por vírgula")
    ap.add_argument("--fontes-manuais", default=MANUAL,
                    help="fontes com credencial; vazio (\"\") deixa o GitHub de fora")  # fmt: skip
    ap.add_argument("--retomar", metavar="RUN_ID", help="continua uma execução interrompida")
    ap.add_argument("--sem-analises", action="store_true",
                    help="pula as análises preliminares A1–A6 e a amostra de precisão")  # fmt: skip
    ap.add_argument("--protocolo", default=DEFAULT_PROTOCOL, help="arquivo do protocolo")
    ap.add_argument("--pasta", type=Path, default=ROOT,
                    help="pasta de trabalho; os dados vão para <pasta>/data")  # fmt: skip
    return ap.parse_args(argv)


def main(argv: list[str] | None = None, runner: Runner = run_step) -> int:
    utf8_output()
    args = parse_args(argv)
    try:
        from msrkit.config import load_protocol
        from msrkit.envfile import parse_dotenv
        from msrkit.provenance import code_state
    except ImportError:
        print(
            "msrkit não está instalado neste Python. Na raiz do repositório:\n"
            "    python -m venv .venv\n"
            '    .venv\\Scripts\\pip install -e ".[analysis]"   (Linux/macOS: .venv/bin/pip)\n'
            "e rode o script com o Python do .venv."
        )
        return 2
    from rich.text import Text

    ansi_console()
    out = Output()
    out.show(Text(), logo(out.screen.width), Text(), tagline(), Text())

    work = Path(args.pasta).resolve()
    protocol = Path(args.protocolo)
    protocol = protocol if protocol.is_absolute() else ROOT / protocol
    if not protocol.is_file():
        out.fail("Protocolo não encontrado", str(protocol))
        return 2
    # O mesmo .env que o msrkit carrega: o da pasta de trabalho, senão o da raiz.
    env_file = work / ".env" if (work / ".env").is_file() else ROOT / ".env"
    dotenv = parse_dotenv(env_file.read_text(encoding="utf-8-sig")) if env_file.is_file() else {}

    def credential(name: str) -> bool:
        return bool(os.environ.get(name) or dotenv.get(name))

    manual = [s.strip() for s in args.fontes_manuais.split(",") if s.strip()]
    github = "github" in manual
    if github and not credential("GITHUB_TOKEN"):
        out.fail(
            "GITHUB_TOKEN não encontrado (nem no ambiente nem em .env).",
            "Sem token o GitHub responde a 60 req/h, sem busca de código, e a coleta levaria\n"
            "muitas horas. Crie o .env a partir do .env.example e preencha GITHUB_TOKEN\n"
            '(https://github.com/settings/tokens), ou rode sem o GitHub com --fontes-manuais "".',
        )
        return 2

    se_key = os.environ.get("STACKEXCHANGE_KEY") or dotenv.get("STACKEXCHANGE_KEY")
    se_status = ""
    if se_key and "stackexchange" in args.fontes.split(","):
        with out.screen.status("Conferindo a STACKEXCHANGE_KEY…", spinner="dots"):
            ok, se_status = check_stackexchange_key(se_key)
        if not ok:
            out.fail(
                f"A API do Stack Exchange recusou a STACKEXCHANGE_KEY ({se_status}).",
                "Copie o campo Key (não o Client Secret) da página do seu app em\n"
                "https://stackapps.com/apps/oauth, ou deixe a variável vazia para coletar\n"
                "sem chave (300 req/dia).",
            )
            return 2

    # Só para o painel: quem mostra o erro de um protocolo inválido é o passo de validação.
    try:
        cfg = load_protocol(protocol, environ={})
    except Exception:
        cfg = None

    env = child_env(os.environ, dotenv, args.fontes, ",".join(manual))
    data = work / "data"
    (data / "logs").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    log_path = data / "logs" / f"coleta_{stamp}.log"
    started = time.monotonic()
    timings: list[tuple[str, float, int]] = []
    run_id: str | None = args.retomar

    def after_collect(run: str) -> list[Step]:
        return pipeline(
            run,
            str(protocol),
            enrich=github,
            enrich_limit=args.enriquecer,
            graphql=not args.sem_graphql,
            reuse_days=args.reaproveitar_dias,
            analyses=not args.sem_analises,
        )

    total = 2 + len(after_collect(""))
    sources = [s.strip() for s in args.fontes.split(",") if s.strip()] + manual
    ignored = sorted(k for k in env if k.startswith(SEARCH_OVERRIDES) and dotenv.get(k))

    with open(log_path, "w", encoding="utf-8") as log:
        out.open_log(log)
        out.show(banner(args, cfg, protocol, sources, github, log_path, total))
        out.show(Text(), *startup_checks(github, se_status, credential, ignored))
        out.show(code_check(code_state(ROOT)))
        out.show(*tips(args, github), screen_only=True)

        def step(s: Step) -> int:
            out.show(*step_header(len(timings) + 1, total, s))
            t0 = time.monotonic()
            rc = runner(s, env, work, log)
            timings.append((s.name, time.monotonic() - t0, rc))
            out.show(step_result(s.name, timings[-1][1], rc))
            return rc

        keep_awake(True)
        try:
            if step(Step("Validação do protocolo e credenciais", ["validate", str(protocol)])):
                return 1
            collect = ["run", str(protocol)]
            if args.retomar:
                collect += ["--resume", args.retomar]
            else:
                if args.por_consulta:
                    collect += ["--per-query-limit", str(args.por_consulta)]
                if args.limite:
                    collect += ["--limit", str(args.limite)]
            before = run_dirs(data / "runs")
            rc = step(Step("Coleta nas fontes", collect))
            run_id = run_id or new_run_id(before, data / "runs")
            if rc or not run_id:
                _resume_hint(out, run_id)
                return rc or 1

            for s in after_collect(run_id):
                if step(s) and s.fatal:
                    _resume_hint(out, run_id)
                    return 1
        except KeyboardInterrupt:
            out.show(Text("\n▲ Interrompido.", style="bold yellow"))
            _resume_hint(out, run_id)
            return 130
        finally:
            keep_awake(False)

        elapsed = time.monotonic() - started
        out.show(*final_summary(timings, run_id, elapsed, work, log_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
