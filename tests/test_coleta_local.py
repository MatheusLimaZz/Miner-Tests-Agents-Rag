"""Script de coleta completa local (scripts/coleta_local.py, Phase 3, R8)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from msrkit.config import load_protocol

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "coleta_local.py"
_SPEC = importlib.util.spec_from_file_location("coleta_local", _PATH)
assert _SPEC and _SPEC.loader
coleta = importlib.util.module_from_spec(_SPEC)
sys.modules["coleta_local"] = coleta
_SPEC.loader.exec_module(coleta)

PROTOCOL = Path(__file__).resolve().parents[1] / "protocols" / "v0_rag_agents_testing.yaml"
RUN = "2026-11-01T10-00-00Z-abcd"


class TestChildEnv:
    def test_search_comes_from_the_protocol_only(self) -> None:
        environ = {"MSRKIT_WINDOW_UNTIL": "2026-10-31", "GITHUB_TOKEN": "t", "PATH": "p"}
        dotenv = {"MSRKIT_TERMS": "a;b", "MSRKIT_TERMS_PT": "c", "HF_TOKEN": "h"}
        env = coleta.child_env(environ, dotenv, "hackernews", "github")
        assert env["MSRKIT_WINDOW_UNTIL"] == env["MSRKIT_TERMS"] == env["MSRKIT_TERMS_PT"] == ""
        assert env["GITHUB_TOKEN"] == "t" and env["PATH"] == "p"
        assert "HF_TOKEN" not in env  # credentials in .env are loaded by msrkit itself
        # Empty variables leave the protocol's window and lexicon in force.
        file_cfg = load_protocol(PROTOCOL, environ={})
        cfg = load_protocol(PROTOCOL, environ=env)
        assert cfg.window == file_cfg.window and cfg.terms == file_cfg.terms

    def test_source_groups_match_the_workflow_without_rss(self) -> None:
        env = coleta.child_env({}, {}, coleta.PUBLIC, "github")
        assert env["MSRKIT_SOURCES_PUBLIC"] == "hackernews,devto,stackexchange,huggingface"
        assert env["MSRKIT_SOURCES_MANUAL"] == "github"
        assert env["MSRKIT_SOURCES_PAID"] == "x_twitter"
        assert env["MSRKIT_SOURCES_UNAVAILABLE"] == "linkedin,discord,bluesky"
        assert env["PYTHONUTF8"] == "1"


class TestPipeline:
    def test_defaults(self) -> None:
        steps = coleta.pipeline(RUN, "p.yaml", enrich=True)
        commands = [" ".join(s.args) for s in steps]
        assert all(f"--run {RUN}" in c for c in commands)
        enrich = next(s for s in steps if s.args[0] == "enrich")
        assert "--graphql" in enrich.args and "--limit" not in enrich.args
        assert "--reuse-days" not in enrich.args
        steps = coleta.pipeline(RUN, "p.yaml", enrich=True, reuse_days=14)
        enrich = next(s for s in steps if s.args[0] == "enrich")
        assert enrich.args[-2:] == ["--reuse-days", "14"]
        heads = [s.args[0] for s in steps]
        assert heads.index("report") < heads.index("package")  # the report ships in the package
        assert any(c.startswith("analyze topics") for c in commands)
        assert not next(s for s in steps if s.args[:2] == ["analyze", "topics"]).fatal

    def test_flags(self) -> None:
        steps = coleta.pipeline(
            RUN, "p.yaml", enrich=True, enrich_limit=300, graphql=False, analyses=False
        )
        enrich = next(s for s in steps if s.args[0] == "enrich")
        assert enrich.args[-2:] == ["--limit", "300"]
        assert not any(s.args[0] in ("analyze", "precision") for s in steps)
        assert all(s.args[0] != "enrich" for s in coleta.pipeline(RUN, "p.yaml", enrich=False))


def test_logo_fits_the_terminal() -> None:
    art = coleta.logo(120).plain.splitlines()
    assert len(art) == 6 and max(map(len, art)) <= 80
    assert coleta.logo(40).plain.strip() == "MSR-KIT"  # narrow terminal: just the name


def test_run_step_indents_the_output_on_screen_only(tmp_path: Path, capsys) -> None:
    log_path = tmp_path / "step.log"
    with open(log_path, "w", encoding="utf-8") as log:
        rc = coleta.run_step(coleta.Step("Ajuda", ["--help"]), dict(os.environ), tmp_path, log)
    assert rc == 0
    screen = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert screen and all(line.startswith("  │ ") for line in screen)
    log = log_path.read_text(encoding="utf-8").splitlines()
    assert log and not any(line.startswith("  │ ") for line in log)


def test_new_run_id(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    (runs / "old").mkdir(parents=True)
    before = coleta.run_dirs(runs)
    assert coleta.new_run_id(before, runs) is None
    (runs / RUN).mkdir()
    (runs / "consolidated").mkdir()
    assert coleta.new_run_id(before, runs) == RUN


def test_code_check_warns_when_the_commit_does_not_reproduce_the_run() -> None:
    """Run 015e was packaged with a commit made after it: the banner now says so before."""
    clean = coleta.code_check({"git_commit": "555d47f4a3c0", "git_dirty": False}).plain
    assert "✓" in clean and "commit 555d47f, sem alterações pendentes" in clean
    dirty = coleta.code_check({"git_commit": "555d47f4a3c0", "git_dirty": True}).plain
    assert "▲" in dirty and "alterações sem commit sobre 555d47f" in dirty
    assert "faça o commit antes" in dirty
    outside = coleta.code_check({"git_commit": None, "git_dirty": None}).plain
    assert "▲" in outside and "fora de um repositório git" in outside


class FakeRunner:
    """Records the msrkit commands; `run` creates the run and `report` writes its JSON."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.fail_on = fail_on

    def __call__(self, step, env, cwd: Path, log) -> int:  # type: ignore[no-untyped-def]
        self.calls.append(step.args)
        self.envs.append(dict(env))
        head = step.args[0]
        if head == self.fail_on:
            return 1
        if head == "run" and "--resume" not in step.args:
            (cwd / "data" / "runs" / RUN).mkdir(parents=True)
        if head == "report":
            out = cwd / "data" / "reports" / RUN
            out.mkdir(parents=True)
            data = {"identified": 10, "unique": 7, "warnings": ["rss: sem histórico"]}
            (out / "relatorio_mineracao.json").write_text(json.dumps(data), encoding="utf-8")
        return 0


class FakeKeyCheck:
    """Stands in for the /info call; records the keys it was asked about."""

    def __init__(self, ok: bool = True, message: str = "STACKEXCHANGE_KEY válida") -> None:
        self.keys: list[str] = []
        self.result = (ok, message)

    def __call__(self, key: str) -> tuple[bool, str]:
        self.keys.append(key)
        return self.result


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(coleta, "keep_awake", lambda on: None)
    monkeypatch.setattr(coleta, "check_stackexchange_key", FakeKeyCheck())
    monkeypatch.setattr(
        "msrkit.provenance.code_state",
        lambda cwd=None: {"git_commit": "abc1234def56", "git_dirty": True},
    )
    for name in ("GITHUB_TOKEN", "STACKEXCHANGE_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


class TestMain:
    def test_requires_a_github_token(self, work: Path, capsys) -> None:
        (work / ".env").write_text("HF_TOKEN=h\n")
        runner = FakeRunner()
        assert coleta.main(["--pasta", str(work)], runner=runner) == 2
        assert runner.calls == []
        assert "GITHUB_TOKEN" in capsys.readouterr().out

    def test_runs_every_step_and_summarizes(self, work: Path, capsys) -> None:
        (work / ".env").write_text("GITHUB_TOKEN=t\nMSRKIT_TERMS=a;b\n")
        runner = FakeRunner()
        assert coleta.main(["--pasta", str(work)], runner=runner) == 0
        heads = [c[0] if c[0] not in ("screen", "analyze", "precision") else " ".join(c[:2])
                 for c in runner.calls]  # fmt: skip
        assert heads[:10] == [
            "validate", "run", "dedupe", "enrich", "extract", "screen export",
            "prisma", "report", "export", "package",
        ]  # fmt: skip
        assert heads[-1] == "stats"
        assert runner.calls[1][-2:] == ["--per-query-limit", "100"]
        assert runner.calls[3][-2:] == ["--reuse-days", "14"]  # enrich reuses by default
        assert all(e["MSRKIT_TERMS"] == "" for e in runner.envs)
        out = capsys.readouterr().out
        assert "Concluído em" in out and RUN in out
        assert "10 identificados → 7 itens únicos" in out
        assert "aviso: rss: sem histórico" in out
        assert "ignorando do .env (vale o protocolo): MSRKIT_TERMS" in out
        steps = len(runner.calls)
        assert f"◆ 1/{steps}  Validação" in out and f"◆ {steps}/{steps}  Estatísticas" in out
        assert len(list((work / "data" / "logs").glob("coleta_*.log"))) == 1

    def test_banner_shows_which_credentials_exist_never_their_values(
        self, work: Path, capsys
    ) -> None:
        (work / ".env").write_text("GITHUB_TOKEN=ghp_segredo123\nHF_TOKEN=hf_segredo456\n")
        assert coleta.main(["--pasta", str(work)], runner=FakeRunner()) == 0
        out = capsys.readouterr().out
        (log_path,) = (work / "data" / "logs").glob("coleta_*.log")
        log = log_path.read_text(encoding="utf-8")
        for text in (out, log):
            assert "GITHUB_TOKEN encontrado" in text and "HF_TOKEN encontrado" in text
            assert "segredo" not in text
            assert "alterações sem commit sobre abc1234" in text  # the code's state, too
        assert "STACKEXCHANGE_KEY vazio" in out

    def test_log_gets_the_panel_as_plain_text_without_the_logo(self, work: Path, capsys) -> None:
        (work / ".env").write_text("GITHUB_TOKEN=t\n")
        assert coleta.main(["--pasta", str(work)], runner=FakeRunner()) == 0
        assert "███" in capsys.readouterr().out
        (log_path,) = (work / "data" / "logs").glob("coleta_*.log")
        log = log_path.read_text(encoding="utf-8")
        cfg = load_protocol(PROTOCOL, environ={})
        assert "Coleta completa local" in log
        assert f"{cfg.window.since} → {cfg.window.until}" in log
        assert "devto (censo)" in log  # census sources are flagged
        assert "███" not in log and "\x1b[" not in log
        assert "Dicas" not in log  # tips are for the screen only

    def test_fatal_failure_tells_how_to_resume(self, work: Path, capsys) -> None:
        (work / ".env").write_text("GITHUB_TOKEN=t\n")
        runner = FakeRunner(fail_on="enrich")
        assert coleta.main(["--pasta", str(work)], runner=runner) == 1
        assert runner.calls[-1][0] == "enrich"  # nothing runs after a fatal step
        assert f"python scripts/coleta_local.py --retomar {RUN}" in capsys.readouterr().out

    def test_optional_failure_does_not_stop(self, work: Path) -> None:
        (work / ".env").write_text("GITHUB_TOKEN=t\n")
        runner = FakeRunner(fail_on="analyze")
        assert coleta.main(["--pasta", str(work)], runner=runner) == 0
        assert runner.calls[-1][0] == "stats"

    def test_rejected_stackexchange_key_stops_before_collecting(
        self, work: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        (work / ".env").write_text("GITHUB_TOKEN=t\nSTACKEXCHANGE_KEY=bad\n")
        check = FakeKeyCheck(ok=False, message="bad_parameter: `key` doesn't match")
        monkeypatch.setattr(coleta, "check_stackexchange_key", check)
        runner = FakeRunner()
        assert coleta.main(["--pasta", str(work)], runner=runner) == 2
        assert check.keys == ["bad"] and runner.calls == []
        assert "doesn't match" in capsys.readouterr().out

    def test_valid_stackexchange_key_is_reported(
        self, work: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        (work / ".env").write_text("GITHUB_TOKEN=t\nSTACKEXCHANGE_KEY=k\n")
        check = FakeKeyCheck(message="STACKEXCHANGE_KEY válida: cota de 10000 req/dia")
        monkeypatch.setattr(coleta, "check_stackexchange_key", check)
        assert coleta.main(["--pasta", str(work)], runner=FakeRunner()) == 0
        out = capsys.readouterr().out
        assert "cota de 10000 req/dia" in out and "STACKEXCHANGE_KEY vazio" not in out

    def test_key_not_checked_without_stackexchange(
        self, work: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (work / ".env").write_text("GITHUB_TOKEN=t\nSTACKEXCHANGE_KEY=k\n")
        check = FakeKeyCheck(ok=False)
        monkeypatch.setattr(coleta, "check_stackexchange_key", check)
        assert coleta.main(["--pasta", str(work), "--fontes", "hackernews"], FakeRunner()) == 0
        assert check.keys == []

    def test_resume_and_without_github(self, work: Path) -> None:
        (work / ".env").write_text("")
        runner = FakeRunner()
        args = ["--pasta", str(work), "--retomar", RUN, "--fontes-manuais", "", "--sem-analises"]
        assert coleta.main(args, runner=runner) == 0
        run = runner.calls[1]
        assert run[-2:] == ["--resume", RUN] and "--per-query-limit" not in run
        assert all(c[0] != "enrich" for c in runner.calls)
        assert runner.envs[0]["MSRKIT_SOURCES_MANUAL"] == ""
