"""Reproducibility package for Zenodo (Protocol E2 v2, §10.3, §17, §18.2 D1–D5).

`msrkit package` bundles what another team needs to audit or re-run a study
into one zip:

- protocol/: the protocol YAML and the files it references (gazetteer);
- run/: manifest (queries, partitions, response hashes), local-filter discards;
- corpus/: items after redaction (below), dedupe report;
- evidence/: repository signals without file contents, detections;
- review/: screening decisions, codings, double-coding sample, agreement;
- validation/: gold set (if given), recall/precision records;
- reports/: PRISMA flow and analyses;
- README.md, .zenodo.json, package_manifest.json and SHA256SUMS.

Redaction follows §17. Each item keeps its metadata, URL, provenance and
extracted fields. The full text (`body` and match contexts) is kept only
when the source's policy allows redistribution (`full_text*`, e.g. Stack
Exchange under CC BY-SA, with attribution). Author handles are replaced by a
salted pseudonym unless `keep_authors` is set. Raw API responses are never
included, and neither is the content of repository files read by
`msrkit enrich`, which is licensed by each repository.
"""

from __future__ import annotations

import hashlib
import json
import platform
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from msrkit import __version__
from msrkit.config import protocol_sha256
from msrkit.provenance import code_state

if TYPE_CHECKING:
    from pathlib import Path

    from msrkit.models import Item

FULL_TEXT_POLICIES = {"full_text", "full_text_with_attribution"}


def pseudonym(handle: str, salt: str) -> str:
    return "anon-" + hashlib.sha256(f"{salt}:{handle}".encode()).hexdigest()[:12]


def redact_item(item: Item, policy: str, keep_authors: bool, salt: str) -> dict[str, Any]:
    data = item.model_dump(mode="json")
    full_text = policy in FULL_TEXT_POLICIES
    if not full_text:
        data["body"] = None  # body_hash stays: it lets others verify a re-collection
        for hit in data.get("matched_terms", []):
            hit["context"] = ""
    if data.get("author_handle") and not keep_authors:
        data["author_handle"] = pseudonym(data["author_handle"], salt)
    data["redistribution"] = policy
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lf(data: bytes) -> bytes:
    """Text with LF line endings, as the repository stores it.

    A Windows checkout holds CRLF. Packaged that way, the protocol's hash in the package
    differed from the one in the manifest and in the report, which hash the LF form
    (msrkit.config.protocol_sha256), although the three were the same file.
    """
    return data.replace(b"\r\n", b"\n")


def _strip_repo_contents(line: str) -> str:
    record = json.loads(line)
    record["workflows"] = sorted(record.get("workflows", {}))  # paths only
    record["manifests"] = sorted(record.get("manifests", {}))
    return json.dumps(record, ensure_ascii=False)


class PackageBuilder:
    """Collects files in memory-light fashion and writes the zip with checksums."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def add_bytes(self, arcname: str, data: bytes) -> None:
        self.files[arcname] = data

    def add_file(self, arcname: str, path: Path, transform: Any = None) -> bool:
        if not path.exists():
            return False
        if transform is None:
            self.add_bytes(arcname, path.read_bytes())
        else:
            with open(path, encoding="utf-8-sig") as f:
                lines = [transform(ln.strip()) for ln in f if ln.strip()]
            text = "\n".join(lines)
            self.add_bytes(arcname, (text + "\n" if text else "").encode("utf-8"))
        return True

    def add_tree(self, prefix: str, root: Path) -> int:
        count = 0
        if root.exists():
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                self.add_file(f"{prefix}/{path.relative_to(root).as_posix()}", path)
                count += 1
        return count

    def write(self, out: Path) -> Path:
        sums = "".join(f"{_sha256(d)}  {n}\n" for n, d in sorted(self.files.items()))
        self.files["SHA256SUMS"] = sums.encode("utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(self.files):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # reproducible
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, self.files[name])
        return out


def zenodo_metadata(
    title: str, creators: list[str], version: str, description: str, keywords: list[str]
) -> dict[str, Any]:
    return {
        "upload_type": "dataset",
        "title": title,
        "creators": [{"name": c} for c in creators] or [{"name": "FILL IN: Surname, Name"}],
        "description": description,
        "version": version,
        "license": "cc-by-4.0",
        "keywords": keywords,
        "notes": (
            "Items from sources whose terms allow it keep their full text under the "
            "source's license (Stack Exchange: CC BY-SA 4.0, with attribution by URL); "
            "all other items carry metadata, URL and extracted fields only."
        ),
    }


def _code_note(packaged: dict[str, Any], collected: dict[str, Any]) -> str:
    """Which code produced the package, in words: commits, and whether they reproduce it."""

    def describe(state: dict[str, Any]) -> str:
        commit = state.get("git_commit")
        if not commit:
            return "an unknown commit (not run from a git checkout)"
        dirty = " **with uncommitted changes**" if state.get("git_dirty") else ""
        return f"commit `{commit[:12]}`{dirty}"

    note = f"The package was built by {describe(packaged)}."
    if collected:
        same = collected.get("git_commit") == packaged.get("git_commit")
        note += (
            " The collection ran on the same commit."
            if same
            else f" The collection ran on {describe(collected)}."
        )
    if packaged.get("git_dirty") or collected.get("git_dirty"):
        note += (
            " Uncommitted changes mean the commit alone does not reproduce the run: commit"
            " and build the package again (`msrkit package`) before publishing."
        )
    return note


def readme(
    run_id: str,
    contents: dict[str, int],
    protocol_name: str,
    keep_authors: bool,
    code_note: str = "",
) -> str:
    rows = "\n".join(f"| `{d}/` | {n} |" for d, n in sorted(contents.items()))
    authors = (
        "kept as public handles"
        if keep_authors
        else "replaced by salted pseudonyms (`anon-…`); the salt is not published"
    )
    return f"""# MSR-Kit reproducibility package — run `{run_id}`

Protocol: `{protocol_name}` · MSR-Kit {__version__}

| Folder | Files |
|---|---:|
{rows}

## How to verify

`sha256sum -c SHA256SUMS` checks every file. `package_manifest.json` records the
MSR-Kit version, git commit, Python version and protocol hash of the run. The files
in `protocol/` have LF line endings, so the SHA-256 of the protocol file here is the
`protocol_sha256` of `run/manifest.json` and of the mining report.

{code_note}

## Sampling weights

A query that stopped at the run's item limit is a sample. `run/manifest.json` keeps,
per query, the items kept, the items a local filter dropped and the results the source
reports (`estimated_total`); `msrkit.weights` turns them into the design weight of each
item (by `provenance.partition`). `reports/relatorio_mineracao.md` has the fractions.

## Redistribution (protocol §17)

- Full text only where the source allows it (field `redistribution` of each item);
  other items keep metadata, URL, provenance, `body_hash` and extracted fields.
- Author handles are {authors}.
- Raw API responses and the content of repository files are not included; the
  manifest keeps the SHA-256 of every response for verification against a
  re-collection.

## Re-running

Install MSR-Kit at the recorded commit and run the protocol in `protocol/`:
`msrkit run protocol/<file>.yaml`. Search APIs change over time, so a re-run is
compared against this snapshot, not expected to match it exactly.
"""


def build_package(
    data_dir: Path,
    run_id: str,
    out: Path,
    items: list[Item],
    policies: dict[str, str],
    protocol_path: Path | None,
    extra_protocol_files: list[Path] | None = None,
    gold: Path | None = None,
    keep_authors: bool = False,
    salt: str = "",
    creators: list[str] | None = None,
    title: str = "",
) -> tuple[Path, dict[str, int]]:
    """Assemble and write the package; returns its path and files per folder."""
    b = PackageBuilder()
    protocol_sha = None
    if protocol_path is not None and protocol_path.exists():
        b.add_bytes(f"protocol/{protocol_path.name}", _lf(protocol_path.read_bytes()))
        protocol_sha = protocol_sha256(protocol_path)
        for extra in extra_protocol_files or []:
            if extra.exists():
                b.add_bytes(f"protocol/{extra.name}", _lf(extra.read_bytes()))

    run_dir = data_dir / "runs" / run_id
    b.add_file("run/manifest.json", run_dir / "manifest.json")
    b.add_file("run/discarded.jsonl", run_dir / "discarded.jsonl")
    collected_with: dict[str, Any] = {}
    manifest_sha = None
    if (run_dir / "manifest.json").exists():
        try:
            manifest_data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        except ValueError:
            manifest_data = {}
        collected_with = manifest_data.get("code") or {}
        manifest_sha = manifest_data.get("protocol_sha256") or None

    lines = [
        json.dumps(redact_item(it, policies.get(it.source, "metadata_only"), keep_authors, salt),
                   ensure_ascii=False)
        for it in items
    ]  # fmt: skip
    b.add_bytes(
        "corpus/items.jsonl",
        (("\n".join(lines) + "\n") if lines else "").encode("utf-8"),
    )
    b.add_file("corpus/dedupe_report.json", data_dir / "items" / run_id / "dedupe_report.json")

    b.add_file(
        "evidence/github_repos.jsonl",
        data_dir / "enrich" / run_id / "github_repos.jsonl",
        transform=_strip_repo_contents,
    )
    det_file = data_dir / "extract" / run_id / "detections.jsonl"
    if not b.add_file("evidence/detections.jsonl", det_file):
        from msrkit.cli._common import _load_detections

        dets = _load_detections(run_id)
        if dets:
            lines = [d.model_dump_json() for d in dets]
            b.add_bytes("evidence/detections.jsonl", ("\n".join(lines) + "\n").encode("utf-8"))
    # What the extraction left out, and what the enrichment was run with.
    b.add_file("evidence/extract_stats.json", data_dir / "extract" / run_id / "extract_stats.json")
    b.add_file("evidence/enrich_meta.json", data_dir / "enrich" / run_id / "meta.json")

    dec_file = data_dir / "screening" / run_id / "decisions.jsonl"
    if not b.add_file("review/decisions.jsonl", dec_file):
        from msrkit.screening import load_decisions

        decisions = load_decisions(data_dir, run_id)
        if decisions:
            lines = [d.model_dump_json() for d in decisions.values()]
            b.add_bytes("review/decisions.jsonl", ("\n".join(lines) + "\n").encode("utf-8"))

    coding_file = data_dir / "coding" / run_id / "codings.jsonl"
    if not b.add_file("review/codings.jsonl", coding_file):
        from msrkit.coding import load_codings

        codings = load_codings(data_dir, run_id)
        if codings:
            lines = [r.model_dump_json() for r in codings.values()]
            b.add_bytes("review/codings.jsonl", ("\n".join(lines) + "\n").encode("utf-8"))

    for name in ("double_sample.json", "agreement.json", "disagreements.csv"):
        b.add_file(f"review/{name}", data_dir / "coding" / run_id / name)

    if gold is not None:
        b.add_file(f"validation/{gold.name}", gold)
    b.add_tree("validation", data_dir / "validation" / run_id)
    b.add_tree("reports", data_dir / "reports" / run_id)

    contents: dict[str, int] = {}
    for name in b.files:
        folder = name.split("/", 1)[0] if "/" in name else "."
        contents[folder] = contents.get(folder, 0) + 1
    protocol_name = protocol_path.name if protocol_path else "(not found)"
    packaged_with = code_state()
    b.add_bytes(
        "README.md",
        readme(
            run_id, contents, protocol_name, keep_authors, _code_note(packaged_with, collected_with)
        ).encode(),
    )
    b.add_bytes(
        ".zenodo.json",
        json.dumps(
            zenodo_metadata(
                title or f"MSR-Kit dataset — run {run_id}",
                creators or [],
                __version__,
                f"Gray-literature mining dataset produced by MSR-Kit {__version__}, run {run_id}.",
                ["mining software repositories", "gray literature", "multivocal review"],
            ),
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8"),
    )
    b.add_bytes(
        "package_manifest.json",
        json.dumps(
            {
                "run_id": run_id,
                "msrkit_version": __version__,
                # The code that built the package, and the code that collected (from the
                # run manifest; empty for runs collected before it was recorded).
                "git_commit": packaged_with["git_commit"],
                "git_dirty": packaged_with["git_dirty"],
                "collected_with": collected_with,
                "python": platform.python_version(),
                "protocol": protocol_name,
                "protocol_sha256": protocol_sha,
                # False when the protocol file changed since the collection.
                "protocol_matches_run": (
                    None if not (protocol_sha and manifest_sha) else protocol_sha == manifest_sha
                ),
                "items": len(items),
                "keep_authors": keep_authors,
                "created_at": datetime.now(UTC).isoformat(),
                "files": {n: _sha256(d) for n, d in sorted(b.files.items())},
            },
            indent=2,
        ).encode("utf-8"),
    )
    return b.write(out), contents
