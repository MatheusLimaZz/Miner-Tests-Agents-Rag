"""Reproducibility package for Zenodo (Phase 2, V4)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.models import Item, ItemKind, Provenance, TermHit
from msrkit.package import build_package, pseudonym, redact_item
from msrkit.provenance import code_state
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

PROTO = "protocols/v0_rag_agents_testing.yaml"


def _item(iid: str, source: str) -> Item:
    return Item(
        id=iid,
        source=source,
        kind=ItemKind.THREAD,
        url=f"https://x.example/{iid}",  # type: ignore[arg-type]
        title="How to test RAG",
        body="full text of the post",
        author_handle="alice",
        matched_terms=[TermHit(term="rag", field="body", context="test RAG pipelines")],
        provenance=Provenance(
            run_id="r",
            query_string="q",
            partition="p",
            adapter="a",
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),  # fmt: skip
    )


def _zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


class TestRedaction:
    def test_metadata_only_drops_text_and_keeps_hash(self) -> None:
        item = _item("a", "hackernews")
        data = redact_item(item, "metadata_only", keep_authors=False, salt="s")
        assert data["body"] is None and data["body_hash"] == item.body_hash
        assert data["matched_terms"][0]["context"] == ""
        assert data["author_handle"] == pseudonym("alice", "s") != "alice"
        assert data["redistribution"] == "metadata_only"

    def test_full_text_with_attribution_keeps_text(self) -> None:
        data = redact_item(_item("b", "stackexchange"), "full_text_with_attribution", True, "s")
        assert data["body"] == "full text of the post"
        assert data["author_handle"] == "alice"


class TestBuild:
    def test_contents_checksums_and_repo_contents_stripped(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        (data / "runs" / "r1").mkdir(parents=True)
        (data / "runs" / "r1" / "manifest.json").write_text("{}")
        enrich = data / "enrich" / "r1"
        enrich.mkdir(parents=True)
        (enrich / "github_repos.jsonl").write_text(
            json.dumps(
                {
                    "repo": "o/r",
                    "workflows": {"ci.yml": "SECRET CODE"},
                    "manifests": {"pyproject.toml": "x"},
                }
            )
            + "\n"  # fmt: skip
        )
        (data / "reports" / "r1").mkdir(parents=True)
        (data / "reports" / "r1" / "prisma.md").write_text("# flow")
        proto = tmp_path / "p.yaml"
        proto.write_text("name: p")
        gaz = tmp_path / "gaz.yaml"
        gaz.write_text("version: '1'")

        out, contents = build_package(
            data, "r1", tmp_path / "pkg.zip",
            [_item("a", "hackernews"), _item("b", "stackexchange")],
            {"stackexchange": "full_text_with_attribution"},
            proto, [gaz], salt="s", creators=["Doe, Jane"],
        )  # fmt: skip
        files = _zip(out)
        assert {"protocol/p.yaml", "protocol/gaz.yaml", "run/manifest.json",
                "corpus/items.jsonl", "reports/prisma.md", "README.md", ".zenodo.json",
                "package_manifest.json", "SHA256SUMS"} <= set(files)  # fmt: skip
        assert "run/discarded.jsonl" not in files  # absent inputs are skipped
        assert b"SECRET CODE" not in files["evidence/github_repos.jsonl"]
        assert json.loads(files["evidence/github_repos.jsonl"])["workflows"] == ["ci.yml"]
        items = [json.loads(ln) for ln in files["corpus/items.jsonl"].decode().splitlines()]
        assert [it["body"] for it in items] == [None, "full text of the post"]
        for line in files["SHA256SUMS"].decode().splitlines():
            digest, name = line.split("  ")
            assert hashlib.sha256(files[name]).hexdigest() == digest
        zen = json.loads(files[".zenodo.json"])
        assert zen["creators"] == [{"name": "Doe, Jane"}]
        assert contents["protocol"] == 2

    def test_protocol_hash_is_the_one_of_the_manifest_whatever_the_line_endings(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A CRLF checkout gave the package another hash than the manifest and the report."""
        from msrkit.config import protocol_sha256

        monkeypatch.setattr(
            "msrkit.package.code_state", lambda: {"git_commit": "c" * 40, "git_dirty": False}
        )
        proto = tmp_path / "p.yaml"
        proto.write_bytes(b"name: p\r\nterms:\r\n  - a\r\n")
        gaz = tmp_path / "gaz.yaml"
        gaz.write_bytes(b"version: '1'\r\n")
        recorded = protocol_sha256(proto)
        assert recorded == hashlib.sha256(b"name: p\nterms:\n  - a\n").hexdigest()
        data = tmp_path / "data"
        (data / "runs" / "r1").mkdir(parents=True)
        (data / "runs" / "r1" / "manifest.json").write_text(
            json.dumps(
                {
                    "protocol_sha256": recorded,
                    "code": {"git_commit": "a" * 40, "git_dirty": True},
                }
            )
        )
        (data / "extract" / "r1").mkdir(parents=True)
        (data / "extract" / "r1" / "extract_stats.json").write_text('{"self_copies": 3}')

        out, _ = build_package(data, "r1", tmp_path / "pkg.zip", [], {}, proto, [gaz], salt="s")
        files = _zip(out)
        meta = json.loads(files["package_manifest.json"])
        assert meta["protocol_sha256"] == recorded and meta["protocol_matches_run"] is True
        assert hashlib.sha256(files["protocol/p.yaml"]).hexdigest() == recorded
        assert b"\r\n" not in files["protocol/p.yaml"] + files["protocol/gaz.yaml"]
        # the code that built the package, and the code that collected
        assert (meta["git_commit"], meta["git_dirty"]) == ("c" * 40, False)
        assert meta["collected_with"] == {"git_commit": "a" * 40, "git_dirty": True}
        readme = files["README.md"].decode()
        assert "built by commit `cccccccccccc`" in readme
        assert "collection ran on commit `aaaaaaaaaaaa` **with uncommitted changes**" in readme
        assert "commit and build the package again" in readme
        assert json.loads(files["evidence/extract_stats.json"]) == {"self_copies": 3}

    def test_a_protocol_changed_after_the_collection_is_flagged(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "msrkit.package.code_state", lambda: {"git_commit": None, "git_dirty": None}
        )
        proto = tmp_path / "p.yaml"
        proto.write_text("name: edited")
        data = tmp_path / "data"
        (data / "runs" / "r1").mkdir(parents=True)
        (data / "runs" / "r1" / "manifest.json").write_text('{"protocol_sha256": "0000"}')
        out, _ = build_package(data, "r1", tmp_path / "pkg.zip", [], {}, proto, salt="s")
        files = _zip(out)
        assert json.loads(files["package_manifest.json"])["protocol_matches_run"] is False
        assert "an unknown commit" in files["README.md"].decode()

    def test_zip_entries_have_fixed_timestamps(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        out, _ = build_package(data, "r", tmp_path / "a.zip", [], {}, None, salt="s")
        with zipfile.ZipFile(out) as zf:
            assert {i.date_time for i in zf.infolist()} == {(2026, 1, 1, 0, 0, 0)}

    def test_empty_items_produces_empty_corpus_file(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        out, _ = build_package(data, "r", tmp_path / "b.zip", [], {}, None, salt="s")
        files = _zip(out)
        assert files["corpus/items.jsonl"] == b""


def _git(repo: Path, *args: str) -> str:
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false"]
    done = subprocess.run(
        ["git", *identity, *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
class TestCodeState:
    """The commit recorded with a run is the one of the code, wherever the command starts."""

    def _checkout(self, tmp_path: Path) -> Path:
        repo = tmp_path / "checkout"
        (repo / "src" / "msrkit").mkdir(parents=True)
        (repo / "src" / "msrkit" / "provenance.py").write_text("x = 1\n")
        (repo / ".gitignore").write_text(".venv/\n")
        _git(repo, "init", "-q")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "first")
        return repo

    def test_commit_and_changes_of_a_checkout(self, tmp_path: Path) -> None:
        repo = self._checkout(tmp_path)
        head = _git(repo, "rev-parse", "HEAD")
        assert code_state(repo) == {"git_commit": head, "git_dirty": False}
        (repo / "new.py").write_text("y = 2\n")  # a file not committed yet also counts
        assert code_state(repo) == {"git_commit": head, "git_dirty": True}

    def test_a_folder_that_is_not_a_checkout(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        if _is_inside_a_checkout(plain):
            pytest.skip("the temporary folder is inside a git checkout")
        assert code_state(plain) == {"git_commit": None, "git_dirty": None}

    def test_default_is_the_checkout_of_the_code_not_the_current_folder(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A run started from a data folder with its own repository recorded that one."""
        repo = self._checkout(tmp_path)
        elsewhere = tmp_path / "data"
        elsewhere.mkdir()
        (elsewhere / "note.txt").write_text("other repository\n")
        _git(elsewhere, "init", "-q")
        _git(elsewhere, "add", ".")
        _git(elsewhere, "commit", "-q", "-m", "data")
        monkeypatch.chdir(elsewhere)
        monkeypatch.setattr(
            "msrkit.provenance.__file__", str(repo / "src" / "msrkit" / "provenance.py")
        )
        state = code_state()
        assert state == {"git_commit": _git(repo, "rev-parse", "HEAD"), "git_dirty": False}
        assert state["git_commit"] != _git(elsewhere, "rev-parse", "HEAD")

    def test_installed_copy_inside_a_checkout_has_no_commit(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """site-packages of a virtual environment in the project folder is not the checkout."""
        repo = self._checkout(tmp_path)
        installed = repo / ".venv" / "Lib" / "site-packages" / "msrkit"
        installed.mkdir(parents=True)
        (installed / "provenance.py").write_text("x = 0  # an older build\n")
        monkeypatch.setattr("msrkit.provenance.__file__", str(installed / "provenance.py"))
        assert code_state() == {"git_commit": None, "git_dirty": None}


def _is_inside_a_checkout(folder: Path) -> bool:
    done = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True, text=True
    )
    return done.returncode == 0


class TestPackageCommand:
    def test_package(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items([_item("a", "stackexchange")], "run-z")
        r = CliRunner().invoke(app, ["package", "--run", "run-z", "-p", PROTO])
        assert r.exit_code == 0, r.stdout
        files = _zip(tmp_path / "packages" / "msrkit_run-z.zip")
        assert "protocol/v0_rag_agents_testing.yaml" in files
        assert "protocol/gazetteer.yaml" in files
        item = json.loads(files["corpus/items.jsonl"])
        assert item["redistribution"] == "full_text_with_attribution"
        assert item["author_handle"].startswith("anon-")
