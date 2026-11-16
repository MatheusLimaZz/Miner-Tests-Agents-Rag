"""MSR-Kit CLI: command-line interface for grey literature mining.

The commands live in modules of this package, by stage of the study:

    collect     sources, validate, plan, run, normalize, fetch
    evidence    enrich, extract
    review      screen (export/import/status), coding (export/sample/import/status),
                agreement
    validation  precision (sample/score), recall
    corpus      prisma, dedupe, stats, export
    analyze     frequency, coverage, oracles, topics, compare (A1–A6)
    menu        interactive menu

`app`, `DATA_DIR` and `DEFAULT_PROTOCOL` are exposed here; command modules read
`DATA_DIR` from this module at call time (`_cli.DATA_DIR`), so tests and
embedding code can override it in one place.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from msrkit import __version__
from msrkit.cli._common import (
    DEFAULT_PROTOCOL,
    PROJECT_ROOT,
    app,
    console,
    is_valid_coder_name,
)

__all__ = [
    "DATA_DIR",
    "DEFAULT_PROTOCOL",
    "app",
    "run",
    "_tag_concepts",
    "_tag_term_languages",
    "_toggle_source_in_protocol",
    "is_valid_coder_name",
]

# Ensure UTF-8 output on Windows consoles to avoid charmap encoding errors
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# Default data directory (overridable: command modules read it from here at call time)
DATA_DIR = Path("data") if Path("data").exists() else (PROJECT_ROOT / "data")


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"msrkit {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
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
    from msrkit.envfile import load_dotenv

    # Search parameters and credentials from ./.env or PROJECT_ROOT/.env (existing variables win).
    env_path = Path(".env") if Path(".env").exists() else (PROJECT_ROOT / ".env")
    load_dotenv(env_path)

    if ctx.invoked_subcommand is None:
        import os
        import sys

        is_interactive = (
            sys.stdin
            and sys.stdin.isatty()
            and sys.stdout
            and sys.stdout.isatty()
            and not os.environ.get("CI")
        )

        if is_interactive:
            from msrkit.cli.menu import menu

            menu()
        else:
            console.print(ctx.get_help())


# Register the commands (order = order in --help). Imported last: the command
# modules read DATA_DIR and DEFAULT_PROTOCOL from this module at call time, so
# tests can monkeypatch msrkit.cli.DATA_DIR.
from msrkit.cli import collect  # noqa: E402, F401, I001
from msrkit.cli import evidence  # noqa: E402, F401
from msrkit.cli import review  # noqa: E402, F401
from msrkit.cli import validation  # noqa: E402, F401
from msrkit.cli import corpus  # noqa: E402, F401
from msrkit.cli import analyze  # noqa: E402, F401
from msrkit.cli import menu  # noqa: E402, F401
from msrkit.cli._common import _tag_concepts, _tag_term_languages  # noqa: E402, F401
from msrkit.cli.collect import run  # noqa: E402, F401
from msrkit.cli.menu import _toggle_source_in_protocol  # noqa: E402, F401
